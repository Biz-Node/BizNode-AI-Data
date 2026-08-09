"""QueryRouter 테스트.

DB 접근 없는 순수 함수 테스트. 입력은 실제 SearchQuery.normalized_query 컨벤션
(공백 제거 + 소문자화, tests/search/dto/test_search_query.py 참고)을 따른다.
"""

import pytest

from search.model.enums import Direction
from search.service.query_router import QueryRouter


@pytest.fixture
def router():
    return QueryRouter()


# ── 기술설계서 §6-6 대표 질의 5개 ────────────────────────────────────────

def test_name_query_has_no_edge_types(router):
    """"삼성전자" — 관계 키워드 없음."""
    result = router.route("삼성전자")
    assert result.edge_types == []
    assert result.direction is None


def test_ambiguous_investment_query_detects_edge_but_no_direction(router):
    """"삼성전자 최근 투자 기업" — 조사가 없어 방향은 판단하지 않는다(§4-1)."""
    result = router.route("삼성전자최근투자기업")
    assert result.edge_types == ["OWNS_STAKE_IN"]
    assert result.direction is None


def test_semantic_query_has_no_edge_types(router):
    """"HBM을 만드는 기업" — DEVELOPS 대표 키워드("개발")가 없어 SEMANTIC 폴백으로 남는다."""
    result = router.route("hbm을만드는기업")
    assert result.edge_types == []
    assert result.direction is None


def test_supplies_to_incoming_direction(router):
    """"삼성전자에 납품하는 기업" — 대상 조사("에")로 incoming이 명확히 판단된다."""
    result = router.route("삼성전자에납품하는기업")
    assert result.edge_types == ["SUPPLIES_TO"]
    assert result.direction == Direction.INCOMING


def test_lawsuit_query_without_named_entity_detects_edge_no_direction(router):
    """"최근 소송 관련 기업" — 특정 기업/조사가 없어 SUES는 감지하되 방향은 없다."""
    result = router.route("최근소송관련기업")
    assert result.edge_types == ["SUES"]
    assert result.direction is None


# ── 방향 판단 — 3종 모두 outgoing/incoming 왕복 확인 ─────────────────────

def test_supplies_to_outgoing_direction(router):
    """"삼성전자가 납품하는 기업" — 주체 조사("가")로 outgoing."""
    result = router.route("삼성전자가납품하는기업")
    assert result.edge_types == ["SUPPLIES_TO"]
    assert result.direction == Direction.OUTGOING


def test_owns_stake_in_outgoing_direction(router):
    result = router.route("삼성전자가투자한기업")
    assert result.edge_types == ["OWNS_STAKE_IN"]
    assert result.direction == Direction.OUTGOING


def test_owns_stake_in_incoming_direction(router):
    result = router.route("삼성전자에투자한기업")
    assert result.edge_types == ["OWNS_STAKE_IN"]
    assert result.direction == Direction.INCOMING


def test_sues_outgoing_direction(router):
    """"삼성전자가 제소한 기업" — 삼성전자가 원고(source)."""
    result = router.route("삼성전자가제소한기업")
    assert result.edge_types == ["SUES"]
    assert result.direction == Direction.OUTGOING


def test_sues_incoming_direction(router):
    """"삼성전자를 제소한 기업" — 삼성전자가 피고(target)."""
    result = router.route("삼성전자를제소한기업")
    assert result.edge_types == ["SUES"]
    assert result.direction == Direction.INCOMING


# ── 중복/결정성 ──────────────────────────────────────────────────────────

def test_duplicate_keyword_mentions_do_not_duplicate_edge_type(router):
    result = router.route("삼성전자에납품하는기업과다른기업에도납품하는곳")
    assert result.edge_types == ["SUPPLIES_TO"]


def test_multiple_edge_types_are_returned_in_deterministic_order(router):
    result = router.route("삼성전자에납품하면서투자도받는기업")
    assert result.edge_types == ["SUPPLIES_TO", "OWNS_STAKE_IN"]


# ── 나머지 9종 — 대표 키워드 최소 1개 매핑 확인 ──────────────────────────

@pytest.mark.parametrize("keyword,edge_type", [
    ("협력", "PARTNERS_WITH"),
    ("경쟁", "COMPETES_WITH"),
    ("인수", "ACQUIRES"),
    ("규제", "REGULATES"),
    ("개발", "DEVELOPS"),
    ("의존", "DEPENDS_ON"),
    ("임원", "IS_EXECUTIVE_OF"),
    ("사건", "HAS_EVENT"),
    ("영향", "IMPACTS"),
])
def test_shallow_edge_types_have_at_least_one_keyword(router, keyword, edge_type):
    result = router.route(f"{keyword}관련기업")
    assert edge_type in result.edge_types
