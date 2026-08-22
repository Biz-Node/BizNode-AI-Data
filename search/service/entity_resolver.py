# 검색어에서 진짜 "기업명" 찾기 (삼성전자 → 코드 변환)
"""EntityResolver — 검색어를 corp_code로 해소하는 Search Layer 서비스.

기술설계서 4-3절, 8절. `PostgresRepository.resolve_candidates()`가 반환하는 다중
후보(exact/fuzzy)를 받아 가장 신뢰할 수 있는 1건을 선택한다. `pipeline/normalizer/
resolver.py`를 wrapper로 감싸지 않는다 — 그쪽은 단일 후보로 이미 축약된 결과만
내놓아 "후보 여러 개 중 최적 선택"이라는 이번 책임과 맞지 않는다(8절 근거). 다만
`Resolution` dataclass는 그대로 재사용한다.

정규화(예: "Samsung Electronics" -> "삼성전자")는 여기서 다시 하지 않는다 —
`PostgresRepository.resolve_candidates()`가 fuzzy 질의에 이미
`normalize_company_name()`을 적용한다(postgres_repository.py 참고).

companies(64개 핵심 기업) 존재 여부는 확인하지 않는다 — corp_code_master 기준으로
resolve 성공/실패만 판정한다(기술설계서 §3: 식별 가능 여부 ≠ 상세 데이터 보유 여부).
"""

from __future__ import annotations

from typing import Optional

from pipeline.normalizer.resolver import Resolution
from search.repository.postgres_repository import PostgresRepository


def _tier(resolution: Resolution) -> int:
    """후보 우선순위 계층. 0(exact) < 1(사실상 완전 일치, 정규화/별칭 경유 fuzzy) < 2(순수 fuzzy)."""
    if resolution.method == "exact":
        return 0
    if resolution.score == 1.0:
        return 1
    return 2


def _pick_best(candidates: list[Resolution]) -> Optional[Resolution]:
    """계층 우선, 동일 계층 내에서는 score 우선. 최상위가 동점이면 임의로 확정하지 않는다(§9)."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    best_tier = min(_tier(c) for c in candidates)
    tier_candidates = [c for c in candidates if _tier(c) == best_tier]
    if len(tier_candidates) == 1:
        return tier_candidates[0]

    best_score = max(c.score for c in tier_candidates)
    top = [c for c in tier_candidates if c.score == best_score]
    return top[0] if len(top) == 1 else None


class EntityResolver:
    def __init__(self, repo: Optional[PostgresRepository] = None):
        self._repo = repo or PostgresRepository()

    def resolve_candidates(self, query: str) -> list[Resolution]:
        """`PostgresRepository.resolve_candidates()`로 위임 — SQL은 여기서 직접 작성하지 않는다."""
        return self._repo.resolve_candidates(query)

    def resolve(self, query: str) -> Optional[Resolution]:
        """단일 최적 후보. 후보가 없거나 동점으로 확정 불가하면 None(§9, §10)."""
        return _pick_best(self.resolve_candidates(query))

    def resolve_many(self, queries: list[str]) -> dict[str, Optional[Resolution]]:
        """이미 나뉜 질의 리스트만 받는다 — 콤마 구분 문자열 파싱은 이 책임이 아니다(§12)."""
        return {query: self.resolve(query) for query in queries}
