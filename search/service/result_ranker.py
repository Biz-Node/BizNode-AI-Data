"""ResultRanker — GraphSearcher·VectorSearcher 결과를 RRF로 병합한다.

기술설계서 §6-6, Task7 지침. GraphSearcher 점수(Neo4j 관계 점수)와 VectorSearcher
점수(ChromaDB L2 거리 기반 유사도)는 스케일도 의미도 달라 가중합하면 실제보다
정밀해 보이는 착시가 생긴다(Task7 지침 §4) — 가중합 대신 Reciprocal Rank
Fusion(RRF)만 쓴다.

score(entity) = Σ 1 / (k + rank_s(entity)), 소스 s에 없으면 그 항은 생략한다.
k=60(RRF 원 논문·TREC 관행값 — 실측 튜닝값 아님, 조정 여지로 남긴다).

병합 결과는 `SearchHit.rrf_score`에 담는다 — 이 값은 순위 신호일 뿐 확률이나
confidence가 아니며, 그래프 엣지의 confidence/grounding_suspect 같은 필드와
혼동하면 안 된다(Task7 지침 §6). 생산자 원점수는 `source_score`에 그대로 남겨
덮어쓰지 않는다(D2, 2026-08-19) — 정렬·dedup의 **입력**은 source_score,
**출력**은 rrf_score다.
"""

from __future__ import annotations

from typing import Optional

from search.dto.search_hit import SearchHit
from search.model.enums import EntityType

_RRF_K = 60

# ── 워크스페이스 관련도 (2026-08-20) ──────────────────────────────────────
# ★워크스페이스는 **필터가 아니라 랭킹 문맥**이다. 후보를 지우지 않고 순서만
#   정한다. 한때 Cypher와 Chroma에서 hard filter로 걸었는데, 그러면
#   「삼성전자 → SK하이닉스」같은 바깥 상대와의 관계가 통째로 사라지고
#   corp_code가 없는 Event·Person·Organization·Product 끝은 하나도 남지 않았다.
#
# 값이 작을수록 앞에 온다. 노출용이 아니라 **정렬 키**라서 DTO에 싣지 않는다.
_WS_BOTH_INSIDE = 0        # 워크스페이스 ↔ 워크스페이스
_WS_OUTSIDE_COMPANY = 1    # 워크스페이스 ↔ 바깥 기업
_WS_OUTSIDE_OTHER = 2      # 워크스페이스 기업 ↔ 사건·인물·기관·제품
_WS_UNRELATED = 3          # 워크스페이스와 닿지 않음


def _relation_priority(relation, keys: set[str]) -> int:
    source_in = relation.source_id in keys
    target_in = relation.target_id in keys
    if source_in and target_in:
        return _WS_BOTH_INSIDE
    if not source_in and not target_in:
        return _WS_UNRELATED
    # 한쪽만 안에 있다 — 바깥쪽이 무엇이냐로 갈린다.
    outside_type = relation.target_entity_type if source_in else relation.source_entity_type
    return (_WS_OUTSIDE_COMPANY if outside_type == EntityType.COMPANY.value
            else _WS_OUTSIDE_OTHER)


def workspace_priority(hit: SearchHit, keys: set[str]) -> int:
    """이 히트가 워크스페이스와 얼마나 가까운가. **작을수록 앞.**

    워크스페이스를 안 주면 전부 같은 값이라 기존 정렬(rrf_score 내림차순)이
    그대로 유지된다 — 이 기능이 기존 동작을 바꾸지 않는다는 뜻이다.

    관계를 든 히트(GraphSearcher)는 관계별로 따져 **가장 가까운 것**을 쓴다. 한
    엔티티가 워크스페이스 안팎 양쪽에 걸친 관계를 여러 개 들고 있을 수 있는데,
    가장 가까운 연결이 그 엔티티의 관련도다.

    관계가 없는 히트(VectorSearcher·이름 해소)는 자기 자신이 워크스페이스 안이냐만
    본다 — 바깥이면 「바깥 기업」으로 본다. 의미검색은 company 컬렉션만 보므로
    나오는 것이 전부 기업이다.
    """
    if not keys:
        return _WS_BOTH_INSIDE
    if hit.relations:
        return min(_relation_priority(r, keys) for r in hit.relations)
    return _WS_BOTH_INSIDE if hit.entity_id in keys else _WS_OUTSIDE_COMPANY


def _sorted_deduped(hits: list[SearchHit]) -> list[SearchHit]:
    """source_score 내림차순 정렬 후 entity_id 중복 제거(순위 더 높은 쪽만 유지).

    GraphSearcher는 anchor 없는 조회에서 source/target 슬롯을 따로 채우므로
    전체 리스트가 score 내림차순임을 보장하지 않는다(Task5 실측) — RRF는 순위를
    근거로 계산하므로 여기서 먼저 정렬한다(Task7 지침 §3). 같은 entity_id가
    source/target 양쪽 슬롯에 중복으로 나올 수 있는 것(Task5 §8, 의도적으로
    ResultRanker로 넘겨둔 것)도 여기서 dedup해야 RRF에 이중으로 기여하지 않는다
    (Task7 지침 §5).
    """
    ordered = sorted(hits, key=lambda h: -h.source_score)
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
    entity_id: str, rrf_score: float,
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
        source_score=base.source_score,
        rrf_score=rrf_score,
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
        workspace_keys: Optional[list[str]] = None,
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
            rrf_score = 0.0
            if entity_id in graph_rank:
                rrf_score += _rrf_contribution(graph_rank[entity_id])
            if entity_id in vector_rank:
                rrf_score += _rrf_contribution(vector_rank[entity_id])
            merged.append(_merge_hit(
                entity_id, rrf_score, graph_by_id.get(entity_id),
                vector_by_id.get(entity_id)))

        # ★워크스페이스 관련도가 **먼저**, 같은 관련도 안에서 RRF 순위다.
        #   그리고 `top_k`는 이 정렬 **뒤에** 자른다 — 순서가 바뀌면
        #   「점수순 상위 10건을 고른 다음 워크스페이스로 거르기」가 되어
        #   결과가 이유 없이 쪼그라든다.
        keys = set(workspace_keys or ())
        merged.sort(key=lambda h: (workspace_priority(h, keys), -h.rrf_score))
        return merged[:top_k] if top_k is not None else merged
