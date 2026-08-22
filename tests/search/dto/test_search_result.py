"""search/dto/search_result.py 테스트.

기술설계서 6-5절 + 이번 Task 결정: used_semantic_fallback 필드 추가.
  False: VectorSearcher(company) fallback을 사용하지 않음
  True : 검색 과정에서 VectorSearcher(company) fallback을 사용함
"""

import pytest
from pydantic import ValidationError

from search.dto.search_hit import SearchHit
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode


def _hit(**overrides):
    kwargs = dict(
        entity_type=EntityType.COMPANY, entity_id="00126380",
        name="삼성전자", source_score=0.9, sources=["neo4j"],
    )
    kwargs.update(overrides)
    return SearchHit(**kwargs)


def test_minimal_valid_result_defaults_warnings_to_empty_list():
    result = SearchResult(
        query="삼성전자", mode=SearchMode.NAME, hits=[_hit()], total=1, took_ms=42,
        cache_hit=False, used_semantic_fallback=False,
    )
    assert result.total == 1
    assert result.warnings == []
    assert result.used_semantic_fallback is False


def test_used_semantic_fallback_true():
    """의미 검색 결과가 fallback으로 쓰였을 때 True로 설정 가능해야 한다."""
    result = SearchResult(
        query="HBM을 만드는 기업", mode=SearchMode.SEMANTIC, hits=[], total=0, took_ms=120,
        cache_hit=False, used_semantic_fallback=True,
    )
    assert result.used_semantic_fallback is True


def test_used_semantic_fallback_false():
    result = SearchResult(
        query="삼성전자에 납품하는 기업", mode=SearchMode.RELATIONSHIP, hits=[_hit()],
        total=1, took_ms=55,
        cache_hit=False, used_semantic_fallback=False,
    )
    assert result.used_semantic_fallback is False


def test_used_semantic_fallback_is_required():
    """기본값을 두지 않는다 — Orchestrator가 항상 명시적으로 판정해야 한다."""
    with pytest.raises(ValidationError):
        SearchResult(query="삼성전자", hits=[], total=0, took_ms=1, cache_hit=False)


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationError):
        SearchResult(hits=[], total=0, took_ms=1, cache_hit=False,
                     used_semantic_fallback=False)


def test_result_carries_warnings():
    result = SearchResult(
        query="삼성전자", mode=SearchMode.NAME, hits=[], total=0, took_ms=1,
        cache_hit=False, used_semantic_fallback=False,
        warnings=["58%가 갱신주기를 넘겨 재확인 필요"],
    )
    assert result.warnings == ["58%가 갱신주기를 넘겨 재확인 필요"]
