"""L3 subtype 레지스트리 — 개방형을 **관리되는 개방형**으로.

방법서상 L3(subtype)는 개방형이다. 새 관계 뉘앙스가 나올 때마다 스키마를 고치지
않아도 되게 하려는 설계다. 그런데 **개방형이 자유형이 되면** 같은 뜻이 표현만 달리해
무한히 늘어난다.

실측(2026-07-28) — PARTNERS_WITH 29종:
    협력 108건 · 공동개발 7 · 특허라이선스 6 … 그리고 1건짜리 꼬리 20여 종
    「potential」「로비」「근저당권 설정」「인적용역 제공」「전략적 협력」…

`relations._SUBTYPE_CANON`은 **미리 적어둔 변형 패턴**만 접는다. 사전에 없는 새 표현은
그대로 들어온다. 그래서 사전을 아무리 늘려도 LLM이 만들어내는 새 표현을 못 따라간다.

이 모듈은 접근을 바꾼다:
    ① **이미 그래프에 있는 subtype**을 레지스트리로 관리한다
    ② 새 표현이 오면 레지스트리와 대조 — 비슷하면 **기존 것으로 병합**
    ③ 정말 새로우면 **신규 등록** (개방형 유지)
    ④ 추출 프롬프트에 레지스트리를 주입 — 애초에 기존 표현을 쓰게 유도

②의 유사 판정은 **문자 트라이그램 자카드**를 쓴다. 한국어는 형태소 분석 없이도
「지분 투자」/「지분투자」/「지분 출자」 같은 변형이 트라이그램으로 잘 잡힌다.
"""

from __future__ import annotations

import re
from typing import Optional

# 신규 등록 대신 기존 것으로 병합할 최소 유사도.
# 0.75로 높였다 — 0.5에서는 「ODM공급」↔「OEM공급」·「도급계약」↔「판매 지원 수수료」
# 같은 오병합이 났다(실측 2026-07-28). 수식어 확장(0.9)과 표기 변형(1.0)만 통과한다.
_MERGE_THRESHOLD = 0.75
_MIN_LEN = 2

# ★정리 대상에서 제외하는 엣지 타입.
# IS_EXECUTIVE_OF의 subtype은 **직위**다(회장·부회장·사장·부사장·전무·상무·이사).
# 이건 문자 유사도로 다룰 대상이 아니라 서열 체계다 — 실제로 자동 정리를 돌리니
# 「사장→부사장」·「사내이사→사외이사」·「부회장→회장」 같은 오병합이 났다.
# 직위 정규화가 필요하면 서열 사전을 따로 만들어야 한다.
EXCLUDED_EDGE_TYPES = frozenset({"IS_EXECUTIVE_OF"})

_CREATE = """
CREATE TABLE IF NOT EXISTS edge_subtypes (
    edge_type    TEXT NOT NULL,
    subtype      TEXT NOT NULL,
    seen_count   INT  NOT NULL DEFAULT 1,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (edge_type, subtype)
)
"""
_LOAD = "SELECT edge_type, subtype, seen_count FROM edge_subtypes"
_UPSERT = """
INSERT INTO edge_subtypes (edge_type, subtype) VALUES (%s, %s)
ON CONFLICT (edge_type, subtype) DO UPDATE
    SET seen_count = edge_subtypes.seen_count + 1, last_seen = now()
"""


def _compact(text: str) -> str:
    return re.sub(r"[\s\-_·,.()\[\]]+", "", (text or "")).lower()


def _trigrams(text: str) -> set[str]:
    s = f"  {_compact(text)}  "
    return {s[i:i + 3] for i in range(len(s) - 2)}


MIN_MODIFIER_LEN = 2   # 「전략적」협력 ✓ / 「부」사장 ✗
_MAX_LEN_RATIO = 3     # 길이가 3배 넘게 차이나면 다른 개념으로 본다


def similarity(a: str, b: str) -> float:
    """subtype 표현 유사도 (0~1).

    ★문자 유사도만으로는 의미를 못 가른다(실측 2026-07-28):
        「부사장」⊃「사장」  — 문자열은 포함이지만 **다른 직위**
        「사내이사」↔「사외이사」 — 한 글자 차이인데 정반대
        「ODM공급」↔「OEM공급」 — 다른 사업 모델
      그래서 포함 관계를 **수식어 패턴일 때만** 인정한다:

        짧은 쪽이 긴 쪽의 **접미사**이고, 앞에 붙은 수식어가 2글자 이상
            「전략적」+협력 ✓   「공동」+개발 ✓   「점유율」+경쟁 ✓
            「부」+사장     ✗ (1글자 — 직급 접두사이지 수식어가 아니다)

    동의어(취득=인수, 출자=투자)는 여기서 못 잡는다 — `_SUBTYPE_CANON`의 몫이다.
    레지스트리는 **표기 변형과 수식어 확장**만 담당한다.
    """
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0

    short, long_ = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if len(long_) > len(short) * _MAX_LEN_RATIO:
        return 0.0                      # 「도급계약」↔「판매 지원 수수료 지급 계약」 차단

    # 수식어 + 명사 패턴만 포함으로 인정
    if long_.endswith(short):
        modifier = long_[: -len(short)]
        return 0.9 if len(modifier) >= MIN_MODIFIER_LEN else 0.0

    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))     # 포함도


class SubtypeRegistry:
    """엣지 타입별 subtype 목록. 배치 시작 시 한 번 적재해 재사용한다."""

    def __init__(self) -> None:
        self._known: dict[str, dict[str, int]] = {}
        self._loaded = False

    def load(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(_LOAD)
            for edge_type, subtype, count in cur.fetchall():
                self._known.setdefault(edge_type, {})[subtype] = count
        self._loaded = True

    def known(self, edge_type: str, *, limit: int = 25) -> list[str]:
        """빈도 순 기존 subtype — 추출 프롬프트에 주입해 새 표현 생성을 억제한다."""
        items = self._known.get(edge_type, {})
        return [s for s, _ in sorted(items.items(), key=lambda kv: -kv[1])[:limit]]

    def resolve(self, conn, edge_type: str, raw: Optional[str]) -> str:
        """새 subtype을 기존 것에 붙이거나(유사) 신규 등록한다(신규).

        반환값이 곧 저장할 subtype이다.
        """
        value = (raw or "").strip().strip(".·-")
        if len(_compact(value)) < _MIN_LEN:
            return value

        pool = self._known.setdefault(edge_type, {})
        if value in pool:
            self._touch(conn, edge_type, value)
            return value

        # 가장 비슷한 기존 표현 찾기 — 임계값을 넘으면 그쪽으로 병합.
        # 동점이면 **자주 쓰인 것**을 고른다(꼬리 표현으로 빨려들지 않게).
        best, best_score = None, 0.0
        for candidate, count in pool.items():
            score = similarity(value, candidate)
            if score > best_score or (
                score == best_score and best is not None and count > pool[best]
            ):
                best, best_score = candidate, score
        if best is not None and best_score >= _MERGE_THRESHOLD:
            self._touch(conn, edge_type, best)
            return best

        # 정말 새로운 표현 — 개방형이므로 등록한다
        self._touch(conn, edge_type, value)
        return value

    def _touch(self, conn, edge_type: str, subtype: str) -> None:
        self._known.setdefault(edge_type, {})
        self._known[edge_type][subtype] = self._known[edge_type].get(subtype, 0) + 1
        with conn.cursor() as cur:
            cur.execute(_UPSERT, (edge_type, subtype))


_registry: Optional[SubtypeRegistry] = None


def get_registry(conn) -> SubtypeRegistry:
    """배치 단위 싱글턴 — 매번 DB를 읽지 않는다."""
    global _registry
    if _registry is None:
        _registry = SubtypeRegistry()
        _registry.load(conn)
    return _registry


def consolidate(conn) -> list[tuple[str, str, str]]:
    """레지스트리 **내부**를 정리한다 — 꼬리 표현을 몸통에 붙인다.

    그래프에서 시드하면 이미 갈려 있던 표현이 전부 등록된다. 그 상태로는
    `resolve()`가 「전략적 협력」을 이미 있는 값으로 보고 그대로 통과시킨다.
    그래서 등록분끼리 한 번 정리해야 한다.

    규칙: 빈도 높은 표현을 **몸통**으로 두고, 유사한 저빈도 표현을 붙인다.
    (빈도가 곧 그 도메인에서 통용되는 표현이라는 신호다.)

    반환: [(엣지타입, 없앨표현, 남길표현), ...]
    """
    with conn.cursor() as cur:
        cur.execute(_CREATE)
        cur.execute(_LOAD)
        rows = cur.fetchall()

    by_edge: dict[str, list[tuple[str, int]]] = {}
    for edge_type, subtype, count in rows:
        by_edge.setdefault(edge_type, []).append((subtype, count))

    from pipeline.normalizer.relations import canonical_forms

    mapping: list[tuple[str, str, str]] = []
    for edge_type, items in by_edge.items():
        if edge_type in EXCLUDED_EDGE_TYPES:
            continue                               # 직위 등 — 서열 체계라 제외
        # ★사전에 명시 등재된 대표형은 서로 합치지 않는다.
        #   「OEM공급」·「ODM공급」·「공급」은 **일부러 나눈** 것이다
        #   (위탁생산과 자사공급은 공급망 분석에서 의미가 다르다).
        #   빈도 기반 정리가 그 의도를 덮으면 안 된다.
        protected = canonical_forms(edge_type)
        items.sort(key=lambda kv: -kv[1])          # 빈도 높은 순 = 몸통 후보
        kept: list[str] = []
        for subtype, _count in items:
            if subtype in protected:
                kept.append(subtype)
                continue
            target = next(
                (k for k in kept if similarity(subtype, k) >= _MERGE_THRESHOLD), None
            )
            if target:
                mapping.append((edge_type, subtype, target))
            else:
                kept.append(subtype)
    return mapping


def apply_consolidation(conn, mapping: list[tuple[str, str, str]]) -> None:
    """정리 결과를 레지스트리에 반영한다(없앨 표현 삭제 + 빈도 이관)."""
    with conn.cursor() as cur:
        for edge_type, drop, keep in mapping:
            cur.execute(
                "UPDATE edge_subtypes SET seen_count = seen_count + "
                "COALESCE((SELECT seen_count FROM edge_subtypes "
                "          WHERE edge_type=%s AND subtype=%s), 0) "
                "WHERE edge_type=%s AND subtype=%s",
                (edge_type, drop, edge_type, keep),
            )
            cur.execute("DELETE FROM edge_subtypes WHERE edge_type=%s AND subtype=%s",
                        (edge_type, drop))
    global _registry
    _registry = None       # 캐시 무효화


def seed_from_graph(conn, session) -> int:
    """현재 Neo4j에 있는 subtype으로 레지스트리를 초기화한다(1회).

    레지스트리가 비어 있으면 첫 표현이 무조건 신규 등록되어, 이미 쓰던 표현과
    갈릴 수 있다. 그래프의 현행 분포를 출발점으로 삼는다.
    """
    rows = session.run(
        "MATCH ()-[r]->() WHERE r.subtype IS NOT NULL AND r.subtype <> '' "
        "RETURN type(r) AS edge_type, r.subtype AS subtype, count(*) AS n"
    )
    n = 0
    with conn.cursor() as cur:
        cur.execute(_CREATE)
        for row in rows:
            cur.execute(
                "INSERT INTO edge_subtypes (edge_type, subtype, seen_count) "
                "VALUES (%s, %s, %s) ON CONFLICT (edge_type, subtype) "
                "DO UPDATE SET seen_count = GREATEST(edge_subtypes.seen_count, EXCLUDED.seen_count)",
                (row["edge_type"], row["subtype"], row["n"]),
            )
            n += 1
    return n
