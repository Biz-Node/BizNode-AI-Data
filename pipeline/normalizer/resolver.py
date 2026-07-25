"""개체 해소(ER) — 이름 → corp_code (corp_code_master 기반, 방법서 12-3).

전략: ①정규화 → ②corp_code_master 정확 매칭(in-memory) → ③pg_trgm 퍼지 폴백.
동명 충돌 시 상장사 우선(stock_code 有) 타이브레이크 — Sprint 0에서 발견한 결함 대응.

exact가 대부분을 처리(경로 A 대상명은 DART 명이라 정확 일치율 높음).
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


# ── corp_code_master in-memory 정확 매칭 인덱스 ──────────────────
@lru_cache(maxsize=1)
def _exact_index() -> dict[str, Resolution]:
    """corp_code_master 전량을 정규화명 → 최적 후보로 인덱싱(1회).
    동명은 상장사 우선(stock_code 有) → 이름 짧은 순으로 최적 1건 선택.
    """
    rows: list[tuple[str, str, Optional[str]]] = []
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT corp_code, corp_name, stock_code FROM corp_code_master")
        rows = cur.fetchall()

    # 정규화명별 후보 수집
    buckets: dict[str, list[tuple[str, str, Optional[str]]]] = {}
    for corp_code, corp_name, stock_code in rows:
        norm = normalize_company_name(corp_name)
        if norm:
            buckets.setdefault(norm, []).append((corp_code, corp_name, stock_code))

    index: dict[str, Resolution] = {}
    for norm, cands in buckets.items():
        # 상장사(stock_code 有) 우선, 그다음 이름 짧은 순
        best = sorted(cands, key=lambda c: (c[2] is None, len(c[1])))[0]
        index[norm] = Resolution(best[0], best[1], best[2], "exact", 1.0)
    return index


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


def resolve(name: Optional[str]) -> Optional[Resolution]:
    """이름 → Resolution. 매칭 실패 시 None(→ 호출측에서 unresolved stub 처리)."""
    if not name:
        return None
    norm = normalize_company_name(name)
    if not norm:
        return None

    exact = _exact_index().get(norm)
    if exact is not None:
        return exact
    return _fuzzy(norm)


def close() -> None:
    """배치 종료 시 퍼지용 연결 정리."""
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
        _conn = None
