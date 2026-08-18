"""개체 해소(ER) — 이름 → corp_code (corp_code_master 기반, 방법서 12-3).

전략: ①정규화 → ②corp_code_master 정확 매칭(in-memory) → ③pg_trgm 퍼지 폴백.
동명 충돌 시 상장사 우선 타이브레이크

exact가 대부분을 처리.
퍼지는 소수 케이스 안전망. 결과는 캐시해 재질의를 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import psycopg

from app.core.config import POSTGRES_DSN
from pipeline.normalizer.base import normalize_company_name

# 퍼지 매칭 최소 유사도. 정규화 후 정답(KAI)은 0.60 수준. 0.3은 개인 주주명이
# 회사로 오매칭되는 false positive가 많아 0.50으로 상향(정밀도 우선).
_FUZZY_THRESHOLD = 0.50
_FUZZY_CANDIDATES = 10


@dataclass(frozen=True)
class Resolution:
    corp_code: str
    corp_name: str
    stock_code: Optional[str]
    method: str          # "exact" | "fuzzy"
    score: float
    # DART가 그 법인 기록을 마지막으로 고친 날. 동명 판별의 세 번째 근거다 —
    # 합병·해산으로 사라진 법인은 명부에 남지만 갱신이 멈춘다(대개 2017-06-30,
    # DART 일괄 등록일). 현존 법인은 공시할 때마다 갱신된다.
    modify_date: Optional[object] = None


# ── corp_code_master in-memory 정확 매칭 인덱스 ──────────────────
@lru_cache(maxsize=1)
def _exact_index() -> dict[str, tuple[Resolution, ...]]:
    """corp_code_master 전량을 정규화명 → **후보 전부**로 인덱싱(1회).

    ★전에는 여기서 하나만 남기고 버렸다(2026-08-13 수정).

      명부 118,535건 중 이름이 겹치는 법인이 **13,452곳(11.3%)**이다.
      「신우」 11곳 · 「에스엠」 11곳 · 「세원」 10곳 …
      그런데 인덱스가 정규화명당 1건만 들고 있어서, 어느 「신우」인지 모르는
      상태에서도 **말없이 하나를 골라** corp_code를 붙였다.

      실측(2026-08-13): 우리 그래프에서 동명에 해당하는 96곳 중
          상장사가 후보에 하나뿐 → 그걸 고름     57곳  ✓ 근거 있다
          후보에 상장사가 없음 → 그냥 하나 고름   39곳  ✗ 근거가 없다
      「태성산업」은 후보 7곳 중 하나를, 「스페이스」는 3곳 중 하나를 골랐다.

      이 프로젝트의 원칙은 **「삭제보다 표시」**다. 모르면 비워 두는 대신
      모른다고 적는다. 그런데 여기서는 모르면서 **골라 버리고 있었다.**
      후보를 전부 들고 있다가 판단은 `resolve()`가 하게 바꾼다.

    정렬은 상장사(stock_code 有) 우선 → 이름 짧은 순.
    """
    rows: list[tuple[str, str, Optional[str]]] = []
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT corp_code, corp_name, stock_code, modify_date "
                    "FROM corp_code_master")
        rows = cur.fetchall()

    buckets: dict[str, list[tuple]] = {}
    for corp_code, corp_name, stock_code, modify_date in rows:
        norm = normalize_company_name(corp_name)
        if norm:
            buckets.setdefault(norm, []).append(
                (corp_code, corp_name, stock_code, modify_date))

    index: dict[str, tuple[Resolution, ...]] = {}
    for norm, cands in buckets.items():
        ordered = sorted(cands, key=lambda c: (c[2] is None, len(c[1])))
        index[norm] = tuple(
            Resolution(c[0], c[1], c[2], "exact", 1.0, c[3]) for c in ordered
        )
    return index


def candidates(name: Optional[str]) -> list[Resolution]:
    """이름에 해당하는 **모든** 법인 후보. 검색 화면·모호 표시용.

    `resolve()`가 하나로 못 좁힐 때, 무엇들 사이에서 못 좁혔는지 보여주려면
    이게 필요하다. 사용자에게 「신우 — 11곳 중 어느 곳입니까」를 물을 수 있다.
    """
    if not name:
        return []
    norm = normalize_company_name(name)
    return list(_exact_index().get(norm, ())) if norm else []


def _pick(cands: tuple[Resolution, ...]) -> Optional[Resolution]:
    """후보 중 **근거를 대며** 하나로 좁힐 수 있으면 고른다. 아니면 None.

    좁힐 수 있는 경우는 둘뿐이다:
      · 후보가 하나           — 애초에 모호하지 않다
      · 상장사가 정확히 하나   — 뉴스·공시에 나오는 회사는 대개 상장사다
                              (실측: 카카오·기아·태성 전부 이 규칙으로 맞았다)

    비상장 후보끼리 여럿이면 **고르지 않는다.** 「이름이 짧은 쪽」은 근거가 아니다.
    """
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    listed = [c for c in cands if c.stock_code]
    if len(listed) == 1:
        return listed[0]
    # ★상장사가 여럿이면 **갱신일**로 가른다(2026-08-13).
    #   합병·해산으로 사라진 법인이 명부에 남아 동명이 된다. 실측:
    #       삼성물산    028260 (2026-03-23) ← 현존   000830 (2017-06-30) 합병 소멸
    #       SK        034730 (2024-03-28) ← 현존   003600 (2017-06-30)
    #       미래에셋증권 006800 (2023-12-07) ← 현존   037620 (2017-06-30)
    #   소멸 법인은 갱신이 멈춰 전부 DART 일괄 등록일에 머문다.
    #   **가장 최근 것이 유일하게 최근일 때만** 고른다(동률이면 안 고른다).
    dated = [c for c in listed if c.modify_date is not None]
    if len(dated) >= 2:
        dated = sorted(dated, key=lambda c: c.modify_date, reverse=True)
        if dated[0].modify_date > dated[1].modify_date:
            return dated[0]
    return None


# ── 퍼지 매칭 (pg_trgm) ─────────────────────────────────────────
_conn: Optional[psycopg.Connection] = None
_fuzzy_cache: dict[str, Optional[Resolution]] = {}


def _get_conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(POSTGRES_DSN)
    return _conn


def _fuzzy(norm: str) -> Optional[Resolution]:
    if norm in _fuzzy_cache:
        return _fuzzy_cache[norm]

    conn = _get_conn()
    with conn.cursor() as cur:
        # pg_trgm 블로킹 → 상위 후보. 상장사 우선 + 유사도 순으로 최적 선택.
        cur.execute(
            """
            SELECT corp_code, corp_name, stock_code,
                   similarity(corp_name, %(q)s) AS sim
            FROM corp_code_master
            WHERE corp_name %% %(q)s
            ORDER BY (stock_code IS NULL), sim DESC, length(corp_name)
            LIMIT %(k)s
            """,
            {"q": norm, "k": _FUZZY_CANDIDATES},
        )
        rows = cur.fetchall()
    conn.rollback()  # 읽기 전용 — 트랜잭션 정리

    result: Optional[Resolution] = None
    for corp_code, corp_name, stock_code, sim in rows:
        if sim is not None and sim >= _FUZZY_THRESHOLD:
            result = Resolution(corp_code, corp_name, stock_code, "fuzzy", float(sim))
            break
    _fuzzy_cache[norm] = result
    return result


# 기업집단 지칭 접미어 — 뉴스는 "SK그룹"·"현대차그룹"처럼 그룹명을 자주 쓴다.
# 개별 법인이 아니라 집단이라 corp_code가 없으므로, 접미어를 떼고 지주·대표사로 해소한다.
_GROUP_SUFFIXES = ("그룹", "계열", "일가")
# 관용 축약 → 실제 법인명 (접미어 제거만으론 안 되는 것)
_GROUP_ALIASES = {
    "현대차": "현대자동차",
    "한화": "한화",
    "롯데": "롯데지주",
    "포스코": "포스코홀딩스",
    "GS": "GS",
    "CJ": "CJ",
    "LS": "LS",
}


def _strip_group(name: str) -> Optional[str]:
    """'SK그룹' → 'SK', '현대차그룹' → '현대자동차'. 그룹명이 아니면 None."""
    for suffix in _GROUP_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[: -len(suffix)].strip()
            return _GROUP_ALIASES.get(base, base)
    return None


def resolve(name: Optional[str]) -> Optional[Resolution]:
    """이름 → Resolution. 좁히지 못하면 None.

    None이 나오는 경우가 **두 가지**로 갈린다 — 구분이 필요하면 `candidates()`를
    같이 보라:
      · 후보가 아예 없다        → 정말 모르는 회사 (unresolved)
      · 후보가 여럿인데 못 좁혔다 → 어느 회사인지 모른다 (ambiguous)
    둘 다 corp_code를 붙이지 않는 건 같지만, 화면에서 할 말이 다르다.
    """
    if not name:
        return None
    norm = normalize_company_name(name)
    if not norm:
        return None

    picked = _pick(_exact_index().get(norm, ()))
    if picked is not None:
        return picked
    if norm in _exact_index():
        return None            # 후보는 있는데 못 좁혔다 — 퍼지로 넘기지 않는다

    # 그룹명 폴백 — "SK그룹"은 corp_code가 없지만 "SK"는 있다
    base = _strip_group(name)
    if base:
        base_norm = normalize_company_name(base)
        picked = _pick(_exact_index().get(base_norm, ()))
        if picked is not None:
            return picked

    return _fuzzy(norm)


def is_ambiguous(name: Optional[str]) -> bool:
    """후보는 여럿인데 근거로 좁히지 못하는 이름인가."""
    return resolve(name) is None and len(candidates(name)) > 1


def close() -> None:
    """배치 종료 시 퍼지용 연결 정리."""
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
        _conn = None
