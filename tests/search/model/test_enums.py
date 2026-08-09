"""search/model/enums.py 테스트.

EntityType은 pipeline/validators/matrix.py의 NODE_TYPES를,
SearchMode는 기술설계서 6-3절 정의를 따른다.
"""

from pipeline.validators.matrix import NODE_TYPES
from search.model.enums import Direction, EntityType, SearchMode


def test_entity_type_values_match_node_types():
    assert {e.value for e in EntityType} == set(NODE_TYPES)


def test_entity_type_is_str_enum():
    assert EntityType.COMPANY == "Company"
    assert isinstance(EntityType.COMPANY, str)


def test_search_mode_has_four_modes():
    assert {m.value for m in SearchMode} == {
        "NAME", "RELATIONSHIP", "SEMANTIC", "HYBRID",
    }


def test_direction_has_two_values():
    assert {d.value for d in Direction} == {"incoming", "outgoing"}
