"""search/dto/search_hit.py 테스트. 기술설계서 6-4절 정의."""

import pytest
from pydantic import ValidationError

from search.dto.search_hit import SearchHit
from search.model.enums import EntityType


def _minimal_kwargs(**overrides):
    kwargs = dict(
        entity_type=EntityType.COMPANY,
        entity_id="00126380",
        name="삼성전자",
        score=0.87,
        sources=["neo4j"],
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_valid_hit_defaults_evidence_to_empty_list():
    hit = SearchHit(**_minimal_kwargs())
    assert hit.entity_type == EntityType.COMPANY
    assert hit.entity_id == "00126380"
    assert hit.score == 0.87
    assert hit.sources == ["neo4j"]
    assert hit.evidence == []
    assert hit.freshness is None
    assert hit.verdict is None
    assert hit.relations is None


def test_full_hit_with_optional_fields():
    hit = SearchHit(**_minimal_kwargs(
        sources=["neo4j", "chroma"],
        freshness={"status": "current", "reason": "180일 경과"},
        verdict="supported",
        relations=[{"edge_type": "SUPPLIES_TO", "target": "삼성전자"}],
        evidence=[{"evidence_id": "ev_599ae4f46bf15b7c", "snippet": "..."}],
    ))
    assert hit.freshness == {"status": "current", "reason": "180일 경과"}
    assert hit.verdict == "supported"
    assert hit.relations == [{"edge_type": "SUPPLIES_TO", "target": "삼성전자"}]
    assert len(hit.evidence) == 1


def test_unknown_source_is_rejected():
    """sources는 postgres/neo4j/chroma 셋 중 하나만 허용한다(6-4절)."""
    with pytest.raises(ValidationError):
        SearchHit(**_minimal_kwargs(sources=["mongodb"]))


def test_invalid_entity_type_is_rejected():
    with pytest.raises(ValidationError):
        SearchHit(**_minimal_kwargs(entity_type="NotAnEntityType"))


def test_missing_required_field_is_rejected():
    kwargs = _minimal_kwargs()
    del kwargs["entity_id"]
    with pytest.raises(ValidationError):
        SearchHit(**kwargs)
