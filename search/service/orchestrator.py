# 전체 검색 흐름을 지휘하는 최종 진입점
"""SearchOrchestrator — EntityResolver → QueryRouter → GraphSearcher/VectorSearcher →
ResultRanker를 지휘해 SearchResult를 만든다. CacheService/API/Agent Tool 연동은 범위
밖(Task8 지침 §1).

**분기는 edge_types 유무로만 결정한다 — GraphSearcher의 결과 유무로 분기하지
않는다(Task8 지침 §3).** 기존에 "GraphSearcher가 빈 결과면 VectorSearcher로 자동
폴백"하기로 했던 정책을 철회한 결과다: 실측("삼성전자에 납품하는 기업") 결과
VectorSearcher가 실제 공급사를 0건 맞히고 전부 삼성전자 계열사·지사만 반환했다 —
관계 질의에 의미검색 결과를 섞으면 없는 관계를 있는 것처럼 보여주는 사고가 난다.
edge_types가 있으면 GraphSearcher 결과가 0건이어도 VectorSearcher를 시도하지 않고
정직하게 빈 결과를 반환한다.

QueryRouter를 먼저 실행한다(순수 함수, EntityResolver와 독립 — 설계문서 "실행 순서
원칙"). 분기 결과에 따라 필요한 EntityResolver 메서드만 부른다 — GraphSearcher
분기는 resolve_candidates(), NAME/SEMANTIC 분기는 resolve() — 같은 질의로 Postgres를
두 번 조회하지 않기 위해서다.

GraphSearcher-only/VectorSearcher-only 분기도 ResultRanker를 거친다(스킵하지
않는다) — GraphSearcher의 anchorless 검색은 source/target 슬롯 간 dedup을 의도적으로
안 하고 ResultRanker로 넘기므로(Task5/7), 여기서 건너뛰면 중복 entity_id가 그대로
샌다. 대가로 이 두 분기의 SearchHit.score는 실제 Neo4j 점수/Chroma 유사도가 아니라
RRF 순위값(1/(60+rank))으로 바뀐다 — score의 의미가 달라진다는 점에 유의. NAME
분기만 ResultRanker를 건너뛴다(단일 확정 hit, 병합 대상 없음, §5).

mode/used_semantic_fallback은 사전에 정하지 않고 분기 실행 후 사후에 채운다(§5) —
SearchQuery.mode는 기본값이 없어 분기 결과를 알아야 생성할 수 있다.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional

from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode
from search.service.anchor_extractor import AnchorExtractor
from search.service.entity_resolver import EntityResolver
from search.service.graph_searcher import GraphSearcher
from search.service.query_router import QueryRouter
from search.service.result_ranker import ResultRanker
from search.service.vector_searcher import VectorSearcher


def _normalize_for_routing(raw_query: str) -> str:
    """QueryRouter 전용 정규화(공백 전부 제거 + 소문자화) — tests/search/service/
    test_query_router.py·tests/search/dto/test_search_query.py 컨벤션의 최초
    구현체. EntityResolver/VectorSearcher는 이 결과를 받지 않는다 — 원문
    request.query를 그대로 받는다(기업명 정규화·의미 임베딩 모두 공백 있는
    자연어를 전제로 하기 때문, Task5/6/7 실제 호출 관례)."""
    return "".join(raw_query.split()).lower()


def _hit_from_resolution(resolution: Resolution) -> SearchHit:
    """NAME 모드 최소 구현(Task8 지침 §4, [설계 결정 필요]로 남김) — 해소된 엔티티
    정보만 SearchHit 1건으로 반환한다. GraphSearcher는 이 경로에서 호출하지 않는다."""
    return SearchHit(
        entity_type=EntityType.COMPANY,
        entity_id=resolution.corp_code,
        name=resolution.corp_name,
        source_score=resolution.score,
        sources=["postgres"],
    )


class SearchOrchestrator:
    def __init__(
        self,
        entity_resolver: EntityResolver,
        query_router: QueryRouter,
        graph_searcher: GraphSearcher,
        vector_searcher: VectorSearcher,
        result_ranker: ResultRanker,
        anchor_extractor: AnchorExtractor,
    ) -> None:
        self._entity_resolver = entity_resolver
        self._query_router = query_router
        self._graph_searcher = graph_searcher
        self._vector_searcher = vector_searcher
        self._result_ranker = result_ranker
        self._anchor_extractor = anchor_extractor

    def search(
        self, request: SearchRequest, *, today: Optional[date] = None,
    ) -> tuple[SearchQuery, SearchResult]:
        start = time.monotonic()
        effective_today = today if today is not None else date.today()
        normalized_query = _normalize_for_routing(request.query)
        routing = self._query_router.route(normalized_query)

        # 요청이 엣지를 명시하면 그 값이 이긴다(D3) — 챗봇 탐색 프로파일이
        # 「생산 차질 → SUPPLIES_TO/DEPENDS_ON」처럼 엣지를 직접 지정하는데,
        # QueryRouter 키워드 추론에 밀리면 프로파일이 무력해진다.
        edge_types = request.edge_types or routing.edge_types

        # AnchorExtractor는 분기와 무관하게 먼저 돈다 — 확신이 없으면 None을 주고
        # 원문이 그대로 내려간다. 전에는 edge_types가 있는 분기에서만 불러서,
        # "삼성전자 관련 뉴스"처럼 관계 키워드 없이 기업명이 문장에 파묻힌 질의가
        # 원문 전체로 resolve()에 넘어가 해소에 실패하고 SEMANTIC으로 빠졌다
        # (현황서 §5-2). 대가는 NAME/SEMANTIC 분기의 Postgres 왕복 1회 증가다.
        anchor = self._anchor_extractor.extract(request.query)
        resolve_query = anchor if anchor is not None else request.query

        # 워크스페이스는 **랭킹 문맥**이다(2026-08-20 정책 변경). 후보를 지우는 데
        # 쓰지 않는다 — GraphSearcher에는 「후보를 넉넉히 뽑아라」는 신호로,
        # ResultRanker에는 「이 기준으로 줄 세워라」는 문맥으로 넘어간다.
        workspace_keys = request.workspace_keys

        if edge_types:
            resolved_entities = self._entity_resolver.resolve_candidates(resolve_query)
            graph_hits = self._graph_searcher.search(
                resolved_entities, edge_types, routing.direction,
                top_k=request.top_k, workspace_keys=workspace_keys,
            )
            hits = self._result_ranker.rank(graph_hits, [], top_k=request.top_k,
                                            workspace_keys=workspace_keys)
            mode = SearchMode.RELATIONSHIP
            used_semantic_fallback = False
        else:
            # ★워크스페이스 밖이라고 해서 이름 해소 결과를 버리지 않는다
            #   (2026-08-20 정책 변경) — 「삼성전자」를 물었으면 워크스페이스에
            #   없어도 삼성전자가 답이다. 워크스페이스는 순서를 정할 뿐이다.
            resolution = self._entity_resolver.resolve(resolve_query)
            if resolution is not None:
                resolved_entities = [resolution]
                hits = [_hit_from_resolution(resolution)]
                mode = SearchMode.NAME
                used_semantic_fallback = False
            else:
                resolved_entities = []
                vector_hits = self._vector_searcher.search(
                    request.query, top_k=request.top_k)
                hits = self._result_ranker.rank([], vector_hits, top_k=request.top_k,
                                                workspace_keys=workspace_keys)
                mode = SearchMode.SEMANTIC
                used_semantic_fallback = True

        query = SearchQuery(
            raw_query=request.query,
            normalized_query=normalized_query,
            mode=mode,
            today=effective_today,
            resolved_entities=resolved_entities,
            workspace_keys=workspace_keys,
            edge_types=edge_types,
            direction=routing.direction,
            top_k=request.top_k,
            include_evidence=request.include_evidence,
        )
        # rank는 최종 순서가 정해진 뒤에 붙인다 — NAME 분기는 ResultRanker를
        # 거치지 않으므로 여기서 채워야 모든 분기가 같은 계약을 지킨다.
        # include_evidence=False면 여기서 비운다 — 전에는 값과 무관하게 항상
        # 채워 나갔다(GraphSearcher가 relation.props에서 무조건 옮김).
        for position, hit in enumerate(hits, 1):
            hit.rank = position
            if not request.include_evidence:
                hit.evidence = []

        took_ms = int((time.monotonic() - start) * 1000)
        result = SearchResult(
            query=request.query,
            mode=mode,
            hits=hits,
            total=len(hits),
            took_ms=took_ms,
            cache_hit=False,
            used_semantic_fallback=used_semantic_fallback,
        )
        return query, result
