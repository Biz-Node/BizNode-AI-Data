"""기술설계서 6-6절 예시 질의 5개 기준 DTO 체인 생성 스모크 테스트.

HTTP → SearchRequest → SearchQuery → Searcher → SearchHit → ResultRanker → SearchResult
흐름(Task 1 지침)을 DTO 레벨에서 실제로 조립해 보고, 완료 기준을 확인한다:
  - 필드 누락이 없는가
  - Enum 값이 올바른가
  - SearchHit이 결과 출처를 표현할 수 있는가
  - SearchResult.used_semantic_fallback을 표현할 수 있는가
  - API 응답용 DTO(SearchRequest/SearchHit/SearchResult)와 내부 실행 DTO(SearchQuery)가
    섞이지 않았는가

Searcher/Orchestrator는 이번 Task 범위 밖이므로 실행하지 않는다 — 각 시나리오의
resolved_entities/hits/used_semantic_fallback 값은 기술설계서 6-6절이 서술한 예상 경로를
사람이 손으로 채워 DTO가 그 형태를 담을 수 있는지만 검증한다. 실제 판정 로직(EntityResolver가
언제 실패로 보는지, VectorSearcher가 언제 fallback으로 호출되는지)은 Orchestrator/Searcher
구현 시점에 결정한다.
"""

from dataclasses import is_dataclass
from datetime import date

import pytest
from pydantic import BaseModel

from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit, SearchRelation
from search.dto.search_query import SearchQuery
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode

_TODAY = date(2026, 8, 8)

_SAMSUNG = Resolution(
    corp_code="00126380", corp_name="삼성전자",
    stock_code="005930", method="exact", score=1.0,
)



def _relation(edge_type: str = "SUPPLIES_TO") -> SearchRelation:
    """SearchRelation은 edge_id를 포함해 필드가 전부 필수다(설계서 Rule 7) —
    테스트가 부분 dict를 넘길 수 없으므로 한 곳에서 만든다."""
    return SearchRelation(
        edge_id="5:abc:0", edge_type=edge_type,
        source="세메스", source_id="00164742", source_entity_type="Company",
        target="삼성전자", target_id="00126380", target_entity_type="Company",
    )

def _build(*, request: SearchRequest, mode: SearchMode,
           resolved_entities: list[Resolution], hits: list[SearchHit],
           used_semantic_fallback: bool) -> SearchResult:
    query = SearchQuery(
        raw_query=request.query,
        normalized_query=request.query.strip(),
        mode=mode,
        today=_TODAY,
        resolved_entities=resolved_entities,
        edge_types=request.edge_types,
        top_k=request.top_k,
        include_evidence=request.include_evidence,
    )
    return SearchResult(
        query=query.raw_query,
        mode=query.mode,
        hits=hits,
        total=len(hits),
        took_ms=10,
        cache_hit=False,
        used_semantic_fallback=used_semantic_fallback,
    )


def test_simple_name_query():
    """1. "삼성전자" — NAME 모드, EntityResolver만으로 hit 구성(6-6절)."""
    req = SearchRequest(query="삼성전자")
    hit = SearchHit(
        entity_type=EntityType.COMPANY, entity_id=_SAMSUNG.corp_code,
        name=_SAMSUNG.corp_name, source_score=1.0, sources=["postgres"],
    )
    result = _build(
        request=req, mode=SearchMode.NAME,
        resolved_entities=[_SAMSUNG], hits=[hit],
        used_semantic_fallback=False,
    )
    assert result.hits[0].entity_id == "00126380"
    assert result.used_semantic_fallback is False


def test_relationship_query_with_time_ordering():
    """2. "삼성전자 최근 투자 기업" — RELATIONSHIP, GraphSearcher 경로.

    ★설계서 6-6절 초안은 HYBRID(관계+시간 정렬)로 적었으나, 확정된 분기 규칙은
      edge_types 유무로만 갈라 HYBRID를 생성하지 않는다. 실측(2026-08-19):
      QueryRouter가 OWNS_STAKE_IN을 잡아 mode=RELATIONSHIP, sources=["neo4j"].
    """
    req = SearchRequest(query="삼성전자 최근 투자 기업", edge_types=["OWNS_STAKE_IN"])
    hit = SearchHit(
        entity_type=EntityType.COMPANY, entity_id="00164742",
        name="세메스", source_score=0.8, sources=["neo4j"],
        freshness={"status": "current", "reason": "180일 경과"},
        verdict="supported",
        relations=[_relation("OWNS_STAKE_IN")],
    )
    result = _build(
        request=req, mode=SearchMode.RELATIONSHIP,
        resolved_entities=[_SAMSUNG], hits=[hit],
        used_semantic_fallback=False,
    )
    assert result.hits[0].sources == ["neo4j"]
    assert result.hits[0].relations is not None
    assert result.used_semantic_fallback is False


def test_semantic_query_without_entity_resolution():
    """3. "HBM을 만드는 기업" — SEMANTIC, EntityResolver 매칭 실패 → VectorSearcher(company)."""
    req = SearchRequest(query="HBM을 만드는 기업")
    hit = SearchHit(
        entity_type=EntityType.COMPANY, entity_id="00164742",
        name="한미반도체", source_score=0.73, sources=["chroma"],
    )
    result = _build(
        request=req, mode=SearchMode.SEMANTIC,
        resolved_entities=[], hits=[hit],
        used_semantic_fallback=True,
    )
    assert result.hits[0].sources == ["chroma"]
    assert result.used_semantic_fallback is True


def test_relationship_query_supplies_to():
    """4. "삼성전자에 납품하는 기업" — RELATIONSHIP, GraphSearcher(SUPPLIES_TO)."""
    req = SearchRequest(query="삼성전자에 납품하는 기업", edge_types=["SUPPLIES_TO"])
    hit = SearchHit(
        entity_type=EntityType.COMPANY, entity_id="00164742",
        name="세메스", source_score=0.87, sources=["neo4j"],
        freshness={"status": "current"}, verdict="supported",
        evidence=[{"evidence_id": "ev_599ae4f46bf15b7c", "snippet": "세메스는 삼성전자에..."}],
    )
    result = _build(
        request=req, mode=SearchMode.RELATIONSHIP,
        resolved_entities=[_SAMSUNG], hits=[hit],
        used_semantic_fallback=False,
    )
    assert result.hits[0].evidence[0]["evidence_id"] == "ev_599ae4f46bf15b7c"
    assert result.used_semantic_fallback is False


def test_anchorless_relationship_query():
    """5. "최근 소송 관련 기업" — 기업명이 해소되지 않는 anchor 없는 관계 질의.

    ★초안은 SEMANTIC + sources=["neo4j","chroma"]로 적었는데, 이는 "결과가 없으면
      의미검색으로 폴백"하던 정책의 잔재다. 그 정책은 철회됐다 — 관계 질의에
      의미검색을 섞으면 없는 관계를 있는 것처럼 보여준다. edge_types가 있으면
      GraphSearcher만 돌고 결과가 0건이어도 VectorSearcher를 부르지 않는다.
      실측(2026-08-19): mode=RELATIONSHIP, resolved_entities=[], sources=["neo4j"].
    """
    req = SearchRequest(query="최근 소송 관련 기업", edge_types=["SUES"])
    hit = SearchHit(
        entity_type=EntityType.COMPANY, entity_id="00126380",
        name="삼성전자", source_score=0.6, sources=["neo4j"],
        verdict="supported",
    )
    result = _build(
        request=req, mode=SearchMode.RELATIONSHIP,
        resolved_entities=[], hits=[hit],
        used_semantic_fallback=False,
    )
    assert result.hits[0].sources == ["neo4j"]
    assert result.used_semantic_fallback is False


@pytest.mark.parametrize("query,edge_types", [
    ("삼성전자", None),
    ("삼성전자 최근 투자 기업", ["OWNS_STAKE_IN"]),
    ("HBM을 만드는 기업", None),
    ("삼성전자에 납품하는 기업", ["SUPPLIES_TO"]),
    ("최근 소송 관련 기업", ["SUES"]),
])
def test_no_field_loss_from_request_to_query(query, edge_types):
    """SearchRequest → SearchQuery 변환에서 필드가 유실되지 않는지 확인."""
    req = SearchRequest(query=query, edge_types=edge_types, top_k=7, include_evidence=False)
    q = SearchQuery(
        raw_query=req.query, normalized_query=req.query.strip(),
        mode=SearchMode.RELATIONSHIP, today=_TODAY,
        edge_types=req.edge_types,
        top_k=req.top_k, include_evidence=req.include_evidence,
    )
    assert q.raw_query == req.query
    assert q.edge_types == req.edge_types
    assert q.top_k == 7
    assert q.include_evidence is False


def test_api_dto_and_internal_dto_are_not_conflated():
    """API 응답용 DTO(Pydantic)와 내부 실행 DTO(dataclass)가 섞이지 않았는지 확인."""
    assert issubclass(SearchRequest, BaseModel)
    assert issubclass(SearchHit, BaseModel)
    assert issubclass(SearchResult, BaseModel)
    assert is_dataclass(SearchQuery)
    assert not issubclass(SearchQuery, BaseModel)
