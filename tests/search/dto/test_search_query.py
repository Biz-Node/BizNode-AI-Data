"""search/dto/search_query.py 테스트.

SearchQuery는 SearchOrchestrator가 조립하는 내부 실행 컨텍스트다(기술설계서 6-1, 6-3절).
SearchRequest(API 계약)와 달리 Pydantic이 아니며, resolved_entities/mode/today처럼
검색 실행 중에만 존재하는 상태를 담는다.
"""

from dataclasses import is_dataclass
from datetime import date

import pytest

from pipeline.normalizer.resolver import Resolution
from search.dto.search_query import SearchQuery
from search.model.enums import Direction, SearchMode


def test_is_plain_dataclass_not_pydantic():
    """API 계약(SearchRequest)과 섞이지 않았는지 확인 — Pydantic BaseModel이 아니어야 한다."""
    assert is_dataclass(SearchQuery)


def test_minimal_construction_defaults_resolved_entities_to_empty_list():
    q = SearchQuery(
        raw_query="HBM을 만드는 기업",
        normalized_query="hbm을만드는기업",
        mode=SearchMode.SEMANTIC,
        today=date(2026, 8, 8),
    )
    assert q.resolved_entities == []
    assert q.entity_types is None
    assert q.edge_types is None
    assert q.direction is None
    assert q.filters is None


def test_direction_accepts_incoming_and_outgoing():
    q = SearchQuery(
        raw_query="삼성전자에 납품하는 기업",
        normalized_query="삼성전자에납품하는기업",
        mode=SearchMode.RELATIONSHIP,
        today=date(2026, 8, 8),
        edge_types=["SUPPLIES_TO"],
        direction=Direction.INCOMING,
    )
    assert q.direction == Direction.INCOMING


def test_construction_with_resolved_entities():
    resolution = Resolution(
        corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", method="exact", score=1.0,
    )
    q = SearchQuery(
        raw_query="삼성전자",
        normalized_query="삼성전자",
        mode=SearchMode.NAME,
        today=date(2026, 8, 8),
        resolved_entities=[resolution],
    )
    assert q.resolved_entities == [resolution]
    assert q.resolved_entities[0].corp_code == "00126380"


def test_missing_required_field_raises():
    with pytest.raises(TypeError):
        SearchQuery(raw_query="삼성전자")  # normalized_query/mode/today 누락


def test_top_k_default_matches_search_request_default():
    from search.dto.search_request import SearchRequest

    q = SearchQuery(
        raw_query="삼성전자", normalized_query="삼성전자",
        mode=SearchMode.NAME, today=date(2026, 8, 8),
    )
    assert q.top_k == SearchRequest(query="삼성전자").top_k
