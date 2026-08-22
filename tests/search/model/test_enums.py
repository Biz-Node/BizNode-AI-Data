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


def test_search_mode_has_three_modes():
    """분기 규칙(edge_types 유무)이 만들 수 있는 모드 전부. HYBRID는 어떤 경로로도
    생성되지 않아 제거했다(A4, 2026-08-19) — 계약에 죽은 값을 남기면 백엔드가
    처리해야 할 모드로 오해한다."""
    assert {m.value for m in SearchMode} == {"NAME", "RELATIONSHIP", "SEMANTIC"}


def test_direction_has_two_values():
    assert {d.value for d in Direction} == {"incoming", "outgoing"}
