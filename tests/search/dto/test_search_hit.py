"""search/dto/search_hit.py 테스트. 기술설계서 6-4절 정의."""

import pytest
from pydantic import ValidationError

from search.dto.search_hit import SearchHit, SearchRelation
from search.model.enums import EntityType



def _relation(edge_type: str = "SUPPLIES_TO") -> SearchRelation:
    """SearchRelation은 edge_id를 포함해 필드가 전부 필수다(설계서 Rule 7) —
    테스트가 부분 dict를 넘길 수 없으므로 한 곳에서 만든다."""
    return SearchRelation(
        edge_id="5:abc:0", edge_type=edge_type,
        source="세메스", source_id="00164742", source_entity_type="Company",
        target="삼성전자", target_id="00126380", target_entity_type="Company",
    )

def _minimal_kwargs(**overrides):
    kwargs = dict(
        entity_type=EntityType.COMPANY,
        entity_id="00126380",
        name="삼성전자",
        source_score=0.87,
        sources=["neo4j"],
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_valid_hit_defaults_evidence_to_empty_list():
    hit = SearchHit(**_minimal_kwargs())
    assert hit.entity_type == EntityType.COMPANY
    assert hit.entity_id == "00126380"
    assert hit.source_score == 0.87
    assert hit.sources == ["neo4j"]
    assert hit.evidence == []
    assert hit.freshness is None
    assert hit.verdict is None
    assert hit.relations is None
    assert hit.kind is None


def test_full_hit_with_optional_fields():
    hit = SearchHit(**_minimal_kwargs(
        sources=["neo4j", "chroma"],
        freshness={"status": "current", "reason": "180일 경과"},
        verdict="supported",
        relations=[_relation()],
        evidence=[{"evidence_id": "ev_599ae4f46bf15b7c", "snippet": "..."}],
        kind="기업",
    ))
    assert hit.freshness == {"status": "current", "reason": "180일 경과"}
    assert hit.verdict == "supported"
    assert hit.relations[0].edge_type == "SUPPLIES_TO"
    assert hit.relations[0].target == "삼성전자"
    assert hit.relations[0].edge_id == "5:abc:0"
    assert len(hit.evidence) == 1
    assert hit.kind == "기업"


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
