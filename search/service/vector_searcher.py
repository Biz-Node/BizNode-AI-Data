"""VectorSearcher — company 컬렉션 의미 검색 결과를 SearchHit으로 변환한다.

기술설계서 §6-6, Task6 지침. `ChromaRepository.search_company()`를 그대로 호출한다 —
새 임베딩 로직·컬렉션은 만들지 않는다. company 컬렉션만 검색한다(evidence·Person·
Event는 범위 밖 — Task6 지침 §5, §8). `corp_codes` 필터도 이번 Task에서는 쓰지
않는다(§8) — 항상 전체 컬렉션을 검색한다.

Score 정규화(실측, 2026-08-12): company 컬렉션은 생성 시 `hnsw:space`를 지정하지
않아 ChromaDB 기본값 `l2`(제곱 유클리드 거리)를 쓴다(collection.metadata=None 확인,
chromadb.segment.impl.vector.hnsw_params.py 기본값). OpenAI 임베딩은 단위벡터로
정규화돼 있다(임베딩 norm 실측 ≈1.0) — 단위벡터에서 d² = 2(1-cos)이므로
cos = 1 - d²/2, score = (cos+1)/2 = 1 - d²/4로 환산한다. 기술설계서 §6-1의
"Chroma=1-distance"는 metric을 확인하지 않은 잠정 가정이라 이번 실측으로 대체한다
(Task6 지침 §3 명시 요구사항). 최소 유사도 컷오프는 적용하지 않는다(§6) — 낮은
점수도 그대로 반환한다.

entity_id: metadata.corp_code를 우선 쓰되(§7), 해외 stub 기업은 corp_code가 빈
문자열이라(실측: company 컬렉션 2219건 중 1400건, 63%) `normalize_company_name(name)`
으로 대체한다 — GraphSearcher가 stub 기업(corp_code 없음)에 쓰는 norm_name 식별자와
같은 함수라 소스 간 entity_id가 일치한다(graph_loader.py·batch/repair/first_seen.py의
coalesce(corp_code, norm_name, ...) 우선순위와 동일, patch 2026-08-15).
"""

from __future__ import annotations

from typing import Optional

from pipeline.normalizer.base import normalize_company_name
from search.dto.search_hit import SearchHit
from search.model.enums import EntityType
from search.repository.chroma_repository import ChromaRepository

_DEFAULT_TOP_K = 10  # 실측 근거 없는 잠정치
_HARD_LIMIT_TOP_K = 50  # 실측 근거 없는 잠정치


def _resolve_top_k(top_k: Optional[int]) -> int:
    if top_k is None or top_k <= 0:
        return _DEFAULT_TOP_K
    return min(top_k, _HARD_LIMIT_TOP_K)


def _score_from_l2_distance(distance: float) -> float:
    """단위 정규화 임베딩의 L2 제곱거리 -> 0~1 유사도 점수(d²=2(1-cos) 역산)."""
    cosine = 1.0 - distance / 2.0
    score = (cosine + 1.0) / 2.0
    return max(0.0, min(1.0, score))


def _entity_id(name: str, corp_code: str) -> str:
    if corp_code:
        return corp_code
    return normalize_company_name(name)


class VectorSearcher:
    def __init__(self, repo: Optional[ChromaRepository] = None) -> None:
        self._repo = repo or ChromaRepository()

    def search(self, normalized_query: str, *, top_k: Optional[int] = None) -> list[SearchHit]:
        n_results = _resolve_top_k(top_k)
        result = self._repo.search_company(normalized_query, n_results=n_results)

        distances = result["distances"][0] if result.get("distances") else []
        metadatas = result["metadatas"][0] if result.get("metadatas") else []

        return [
            SearchHit(
                entity_type=EntityType.COMPANY,
                entity_id=_entity_id(meta.get("name") or "", meta.get("corp_code") or ""),
                name=meta.get("name") or "",
                score=_score_from_l2_distance(distance),
                sources=["chroma"],
                kind=meta.get("kind"),
            )
            for distance, meta in zip(distances, metadatas)
        ]
