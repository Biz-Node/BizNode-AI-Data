"""ResultRanker — GraphSearcher·VectorSearcher 결과를 RRF로 병합한다.

기술설계서 §6-6, Task7 지침. GraphSearcher 점수(Neo4j 관계 점수)와 VectorSearcher
점수(ChromaDB L2 거리 기반 유사도)는 스케일도 의미도 달라 가중합하면 실제보다
정밀해 보이는 착시가 생긴다(Task7 지침 §4) — 가중합 대신 Reciprocal Rank
Fusion(RRF)만 쓴다.

score(entity) = Σ 1 / (k + rank_s(entity)), 소스 s에 없으면 그 항은 생략한다.
k=60(RRF 원 논문·TREC 관행값 — 실측 튜닝값 아님, 조정 여지로 남긴다).

병합된 SearchHit.score는 이 RRF 값이지 확률이나 confidence가 아니다 — 순위
신호일 뿐이며, 그래프 엣지의 confidence/grounding_suspect 같은 필드와 혼동하면
안 된다(Task7 지침 §6).
"""

from __future__ import annotations

from typing import Optional

from search.dto.search_hit import SearchHit

_RRF_K = 60


def _sorted_deduped(hits: list[SearchHit]) -> list[SearchHit]:
    """score 내림차순 정렬 후 entity_id 중복 제거(순위 더 높은 쪽만 유지).

    GraphSearcher는 anchor 없는 조회에서 source/target 슬롯을 따로 채우므로
    전체 리스트가 score 내림차순임을 보장하지 않는다(Task5 실측) — RRF는 순위를
    근거로 계산하므로 여기서 먼저 정렬한다(Task7 지침 §3). 같은 entity_id가
    source/target 양쪽 슬롯에 중복으로 나올 수 있는 것(Task5 §8, 의도적으로
    ResultRanker로 넘겨둔 것)도 여기서 dedup해야 RRF에 이중으로 기여하지 않는다
    (Task7 지침 §5).
    """
    ordered = sorted(hits, key=lambda h: -h.score)
    seen: set[str] = set()
    deduped: list[SearchHit] = []
    for hit in ordered:
        if hit.entity_id in seen:
            continue
        seen.add(hit.entity_id)
        deduped.append(hit)
    return deduped


def _rrf_contribution(rank: int) -> float:
    return 1.0 / (_RRF_K + rank)


def _merge_hit(
    entity_id: str, score: float,
    graph_hit: Optional[SearchHit], vector_hit: Optional[SearchHit],
) -> SearchHit:
    """freshness/verdict/relations(그래프 전용), kind(벡터 전용)는 재계산하지 않고
    원본 SearchHit에서 그대로 보존한다(Task7 지침 §6)."""
    base = graph_hit or vector_hit
    sources = []
    if graph_hit is not None:
        sources += graph_hit.sources
    if vector_hit is not None:
        sources += vector_hit.sources

    return SearchHit(
        entity_type=base.entity_type,
        entity_id=entity_id,
        name=base.name,
        score=score,
        sources=sources,
        kind=vector_hit.kind if vector_hit is not None else None,
        freshness=graph_hit.freshness if graph_hit is not None else None,
        verdict=graph_hit.verdict if graph_hit is not None else None,
        relations=graph_hit.relations if graph_hit is not None else None,
        evidence=graph_hit.evidence if graph_hit is not None else [],
    )


class ResultRanker:
    def rank(
        self,
        graph_hits: list[SearchHit],
        vector_hits: list[SearchHit],
        *,
        top_k: Optional[int] = None,
    ) -> list[SearchHit]:
        graph_ranked = _sorted_deduped(graph_hits)
        vector_ranked = _sorted_deduped(vector_hits)

        graph_rank = {hit.entity_id: i + 1 for i, hit in enumerate(graph_ranked)}
        vector_rank = {hit.entity_id: i + 1 for i, hit in enumerate(vector_ranked)}
        graph_by_id = {hit.entity_id: hit for hit in graph_ranked}
        vector_by_id = {hit.entity_id: hit for hit in vector_ranked}

        entity_ids = list(graph_by_id.keys())
        entity_ids += [eid for eid in vector_by_id if eid not in graph_by_id]

        merged = []
        for entity_id in entity_ids:
            score = 0.0
            if entity_id in graph_rank:
                score += _rrf_contribution(graph_rank[entity_id])
            if entity_id in vector_rank:
                score += _rrf_contribution(vector_rank[entity_id])
            merged.append(_merge_hit(
                entity_id, score, graph_by_id.get(entity_id), vector_by_id.get(entity_id)))

        merged.sort(key=lambda h: -h.score)
        return merged[:top_k] if top_k is not None else merged
