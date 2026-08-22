"""search/dto/search_request.py 테스트.

SearchRequest는 API 경계(HTTP body)의 입력 계약이다. 기술설계서 6-2절 필드 정의를 따른다.
"""

import pytest
from pydantic import ValidationError

from search.dto.search_request import SearchRequest


def test_minimal_valid_request():
    req = SearchRequest(query="삼성전자")
    assert req.query == "삼성전자"
    assert req.top_k > 0
    assert req.include_evidence is True
    assert req.edge_types is None


def test_full_valid_request():
    req = SearchRequest(
        query="삼성전자에 납품하는 기업",
        edge_types=["SUPPLIES_TO"],
        top_k=5,
        include_evidence=False,
    )
    assert req.edge_types == ["SUPPLIES_TO"]
    assert req.top_k == 5
    assert req.include_evidence is False


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
        SearchRequest(query="삼성전자", entity_types=["Company"])


# ── 계약에서 뺀 필드(D3, 2026-08-19) ─────────────────────────────────────────

def test_entity_types_is_rejected():
    """Company 외 엔티티는 pg_trgm·Neo4j·벡터 인덱스가 존재하지 않아 지원할 방법이
    없다. 담기기만 하고 아무 Searcher도 읽지 않는 필드를 계약에 남기면 백엔드가
    보낸 값이 조용히 무시된다 — 계약에서 뺀다."""
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", entity_types=["Company"])


def test_filters_is_rejected():
    """sector 2단계 조합(설계 §6)은 구현되지 않았고, 그 선필터가 읽던 companies
    표도 삭제됐다. 필요해지면 그때 되살린다."""
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", filters={"sector": ["반도체"]})


def test_unknown_field_is_rejected():
    """오타나 폐기된 필드가 조용히 무시되지 않게 한다."""
    with pytest.raises(ValidationError):
        SearchRequest(query="삼성전자", top_kk=5)
