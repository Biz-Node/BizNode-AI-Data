"""search/dto/search_request.py 테스트.

SearchRequest는 API 경계(HTTP body)의 입력 계약이다. 기술설계서 6-2절 필드 정의를 따른다.
"""

import pytest
from pydantic import ValidationError

from search.dto.search_request import SearchRequest
from search.model.enums import EntityType


def test_minimal_valid_request():
    req = SearchRequest(query="삼성전자")
    assert req.query == "삼성전자"
    assert req.top_k > 0
    assert req.include_evidence is True
    assert req.entity_types is None
    assert req.edge_types is None
    assert req.filters is None


def test_full_valid_request():
    req = SearchRequest(
        query="삼성전자에 납품하는 기업",
        entity_types=[EntityType.COMPANY],
        edge_types=["SUPPLIES_TO"],
        top_k=5,
        include_evidence=False,
        filters={"sector": ["반도체"]},
    )
    assert req.entity_types == [EntityType.COMPANY]
    assert req.edge_types == ["SUPPLIES_TO"]
    assert req.top_k == 5
    assert req.include_evidence is False
    assert req.filters == {"sector": ["반도체"]}


def test_missing_query_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest()


def test_blank_query_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")


def test_unknown_edge_type_is_rejected():
    """edge_types는 pipeline/ontology.EDGE_TYPES(12종)에 있는 값만 허용한다."""
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", edge_types=["NOT_A_REAL_EDGE_TYPE"])


def test_known_edge_type_is_accepted():
    req = SearchRequest(query="삼성전자", edge_types=["SUES"])
    assert req.edge_types == ["SUES"]


def test_top_k_zero_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", top_k=0)


def test_top_k_negative_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", top_k=-1)


def test_top_k_over_max_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", top_k=10_000)


def test_invalid_entity_type_is_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", entity_types=["NotAnEntityType"])
