"""GraphSearcher 테스트.

순수 로직(_primary_resolution/_resolve_limit/_resolve_anchorless_fetch_limit/
_relation_direction)은 in-memory 객체로 검증한다. entity_id/entity_type 부여,
unknown label 방어, anchor 없는 5+5 슬롯 로직(Task6)은 정확한 케이스를 통제하기
위해 monkeypatch로 `relations_of`를 대체한다 — DB 데이터를 대신하는 게 아니라
"이 Relation이 주어졌을 때 GraphSearcher가 정확히 무엇을 반환하는가"라는 코드
자체의 계약을 확인하는 것이라, 다른 테스트의 "실제 DB, mock 없음" 원칙과
배치되지 않는다(Task5 지침 §11, Task6도 동일 원칙 유지).

§6-6 3개 질의 재현은 그대로 실제 Docker Neo4j 대상(mock 없음)이다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.graph_service import Relation
from pipeline.freshness import Freshness
from pipeline.normalizer.resolver import Resolution
from search.model.enums import Direction, EntityType
from search.service import graph_searcher as gs_module
from search.service.graph_searcher import (
    GraphSearcher,
    _primary_resolution,
    _relation_direction,
    _resolve_anchorless_fetch_limit,
    _resolve_limit,
)


def _make_relation(
    source: str, target: str, edge_type: str = "SUPPLIES_TO",
    source_id: str | None = None, source_entity_type: str = "Company",
    target_id: str | None = None, target_entity_type: str = "Company",
    **props,
):
    return Relation(
        source=source, target=target, edge_type=edge_type, subtype="", source_type="",
        confidence=0.9, corroboration=1,
        freshness=Freshness(status="current", reason="test", days_since=0, confidence_factor=1.0),
        props=props,
        source_id=source_id or source, source_entity_type=source_entity_type,
        target_id=target_id or target, target_entity_type=target_entity_type,
    )


# ── 순수 로직 ────────────────────────────────────────────────────────────

def test_primary_resolution_picks_highest_score():
    low = Resolution("A", "가", None, "fuzzy", 0.6)
    high = Resolution("B", "나", None, "exact", 1.0)
    assert _primary_resolution([low, high]) == high


def test_primary_resolution_empty_returns_none():
    assert _primary_resolution([]) is None


def test_primary_resolution_tie_keeps_first():
    a = Resolution("A", "가", None, "fuzzy", 0.7)
    b = Resolution("B", "나", None, "fuzzy", 0.7)
    assert _primary_resolution([a, b]) == a


def test_resolve_limit_uses_top_k_when_under_hard_cap():
    assert _resolve_limit(10) == 10


def test_resolve_limit_caps_at_hard_limit():
    assert _resolve_limit(9999) == 100


def test_resolve_limit_defaults_to_hard_limit_when_none():
    assert _resolve_limit(None) == 100


def test_resolve_anchorless_fetch_limit_uses_floor_when_top_k_small():
    """top_k=3을 그대로 relations_of(limit=3)에 넘기면 5+5 슬롯을 채우기도 전에
    후보가 바닥난다 — 최소 확보량(50)이 우선한다(Task6 승인 사항)."""
    assert _resolve_anchorless_fetch_limit(3) == 50


def test_resolve_anchorless_fetch_limit_uses_top_k_when_above_floor():
    assert _resolve_anchorless_fetch_limit(80) == 80


def test_resolve_anchorless_fetch_limit_respects_hard_cap():
    assert _resolve_anchorless_fetch_limit(9999) == 100


def test_resolve_anchorless_fetch_limit_defaults_to_hard_limit_when_none():
    assert _resolve_anchorless_fetch_limit(None) == 100


def test_relation_direction_outgoing_when_anchor_is_source():
    r = _make_relation("삼성전자", "세메스")
    assert _relation_direction(r, "삼성전자") == Direction.OUTGOING


def test_relation_direction_incoming_when_anchor_is_target():
    r = _make_relation("세메스", "삼성전자")
    assert _relation_direction(r, "삼성전자") == Direction.INCOMING


def test_relation_direction_unmatched_returns_none():
    r = _make_relation("세메스", "하나머티리얼즈")
    assert _relation_direction(r, "삼성전자") is None


# ── limit 강제(§6, Task5) ────────────────────────────────────────────────

def test_limit_always_passed_when_no_resolved_entity(monkeypatch):
    mock_relations_of = MagicMock(return_value=[])
    monkeypatch.setattr(gs_module, "relations_of", mock_relations_of)

    GraphSearcher().search([], ["SUES"])

    assert mock_relations_of.call_args.kwargs["limit"] is not None
    assert mock_relations_of.call_args.kwargs["norm_name"] is None


def test_limit_always_passed_when_resolved_entity_present(monkeypatch):
    mock_relations_of = MagicMock(return_value=[])
    monkeypatch.setattr(gs_module, "relations_of", mock_relations_of)
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    GraphSearcher().search([resolution], ["SUPPLIES_TO"], top_k=5)

    assert mock_relations_of.call_args.kwargs["limit"] == 5
    assert mock_relations_of.call_args.kwargs["norm_name"] is not None


def test_limit_respects_hard_cap_even_with_large_top_k(monkeypatch):
    mock_relations_of = MagicMock(return_value=[])
    monkeypatch.setattr(gs_module, "relations_of", mock_relations_of)
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    GraphSearcher().search([resolution], ["SUPPLIES_TO"], top_k=99999)

    assert mock_relations_of.call_args.kwargs["limit"] == 100


def test_anchorless_fetch_uses_floor_not_bare_top_k(monkeypatch):
    """anchor 없을 때는 top_k=3이어도 5+5 슬롯을 위해 넉넉히(50) 조회한다."""
    mock_relations_of = MagicMock(return_value=[])
    monkeypatch.setattr(gs_module, "relations_of", mock_relations_of)

    GraphSearcher().search([], ["SUES"], top_k=3)

    assert mock_relations_of.call_args.kwargs["limit"] == 50


# ── entity_id/entity_type 전달(Task6 §11-1~4) ────────────────────────────

def test_anchored_search_uses_relation_source_target_ids(monkeypatch):
    """Company 상대측의 ID가 이름 추측이 아니라 Relation의 id를 그대로 쓰는가."""
    relation = _make_relation(
        "세메스", "삼성전자", "SUPPLIES_TO",
        source_id="9999999", source_entity_type="Company",
        target_id="00126380", target_entity_type="Company",
    )
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=[relation]))
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    hits = GraphSearcher().search([resolution], ["SUPPLIES_TO"], Direction.INCOMING)

    assert len(hits) == 1
    assert hits[0].entity_id == "9999999"
    assert hits[0].entity_type == EntityType.COMPANY
    assert hits[0].name == "세메스"


def test_anchored_search_reports_person_type_not_company(monkeypatch):
    """Person 상대측이 실제 PERSON으로 나오고 COMPANY로 고정되지 않는가."""
    relation = _make_relation(
        "이재용", "삼성전자", "OWNS_STAKE_IN",
        source_id="이재용|1968-06", source_entity_type="Person",
        target_id="00126380", target_entity_type="Company",
    )
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=[relation]))
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    hits = GraphSearcher().search([resolution], ["OWNS_STAKE_IN"], Direction.INCOMING)

    assert len(hits) == 1
    assert hits[0].entity_type == EntityType.PERSON
    assert hits[0].entity_type != EntityType.COMPANY
    assert hits[0].entity_id == "이재용|1968-06"


def test_anchored_search_skips_unknown_label_without_crashing(monkeypatch):
    """label이 EntityType 5종에 없으면(이론상 없어야 함) 검색 전체를 죽이지 않고
    그 relation만 건너뛴다 — UNKNOWN 멤버가 없어(2026-08-09 확인) 임의로 추가하지
    않았다(Task6 승인 사항)."""
    bad = _make_relation(
        "삼성전자", "미확인노드", "SUPPLIES_TO",
        source_id="00126380", source_entity_type="Company",
        target_id="x", target_entity_type="MysteryLabel",
    )
    good = _make_relation(
        "세메스", "삼성전자", "SUPPLIES_TO",
        source_id="s1", source_entity_type="Company",
        target_id="00126380", target_entity_type="Company",
    )
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=[bad, good]))
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    hits = GraphSearcher().search([resolution], ["SUPPLIES_TO"], None)

    assert len(hits) == 1
    assert hits[0].name == "세메스"


def test_relation_info_carries_entity_metadata(monkeypatch):
    """SearchHit.relations(SearchRelation)에 상대측 id/type이 그대로 노출되는가."""
    relation = _make_relation(
        "세메스", "삼성전자", "SUPPLIES_TO",
        source_id="9999999", source_entity_type="Company",
        target_id="00126380", target_entity_type="Company",
    )
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=[relation]))
    resolution = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)

    hits = GraphSearcher().search([resolution], ["SUPPLIES_TO"], Direction.INCOMING)

    info = hits[0].relations[0]
    assert info.source_id == "9999999"
    assert info.source_entity_type == "Company"
    assert info.target_id == "00126380"
    assert info.target_entity_type == "Company"


# ── anchor 없는 검색 — source 5 + target 5(§8, Task6) ─────────────────────

def test_anchorless_search_fills_five_and_five_deduped(monkeypatch):
    """source_id가 중복되면 한 번만 세고, 서로 다른 5개가 채워질 때까지 이어간다.
    반환 순서는 relations_of()가 준 순서(=score 내림차순)를 그대로 따른다."""
    relations = [
        _make_relation("반복공급사", "회사1", "SUES", source_id="dup", target_id="t1"),
        _make_relation("반복공급사", "회사2", "SUES", source_id="dup", target_id="t2"),
        _make_relation("공급사3", "회사3", "SUES", source_id="s3", target_id="t3"),
        _make_relation("공급사4", "회사4", "SUES", source_id="s4", target_id="t4"),
        _make_relation("공급사5", "회사5", "SUES", source_id="s5", target_id="t5"),
        _make_relation("공급사6", "회사6", "SUES", source_id="s6", target_id="t6"),
    ]
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=relations))

    hits = GraphSearcher()._search_anchorless(["SUES"], None)

    assert len(hits) == 10
    source_side, target_side = hits[:5], hits[5:]
    assert [h.entity_id for h in source_side] == ["dup", "s3", "s4", "s5", "s6"]
    assert [h.entity_id for h in target_side] == ["t1", "t2", "t3", "t4", "t5"]


def test_anchorless_search_skips_unknown_label_per_slot(monkeypatch):
    relations = [
        _make_relation(
            "미확인", "정상타겟", "SUES", source_id="bad", source_entity_type="MysteryLabel",
            target_id="t1",
        ),
        _make_relation("정상소스", "정상타겟2", "SUES", source_id="s1", target_id="t2"),
    ]
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=relations))

    hits = GraphSearcher()._search_anchorless(["SUES"], None)

    source_ids = [h.entity_id for h in hits if h.name in ("미확인", "정상소스")]
    assert source_ids == ["s1"]  # 미확인 label 쪽은 건너뛴다


def test_anchorless_search_truncates_to_top_k(monkeypatch):
    """5+5 슬롯을 채우더라도 top_k의 "최종 결과 개수 상한" 의미는 지킨다(Task6 승인 사항)."""
    relations = [
        _make_relation(f"S{i}", f"T{i}", "SUES", source_id=f"s{i}", target_id=f"t{i}")
        for i in range(6)
    ]
    monkeypatch.setattr(gs_module, "relations_of", MagicMock(return_value=relations))

    hits = GraphSearcher().search([], ["SUES"], top_k=4)

    assert len(hits) == 4


# ── §6-6 3개 질의 재현 — 실제 Docker Neo4j, Task3·Task4 실제 결과를 그대로 입력 ──

def test_supplies_to_query_direction_incoming(entity_resolver, query_router, graph_searcher):
    """"삼성전자에 납품하는 기업" — Task3 direction=INCOMING을 그대로 입력."""
    resolution = entity_resolver.resolve("삼성전자")
    routing = query_router.route("삼성전자에납품하는기업")
    assert routing.edge_types == ["SUPPLIES_TO"]
    assert routing.direction == Direction.INCOMING

    hits = graph_searcher.search([resolution], routing.edge_types, routing.direction, top_k=20)

    assert len(hits) > 0
    for hit in hits:
        assert hit.relations[0].direction == "incoming"
        assert hit.relations[0].target_id == "00126380"
        assert hit.entity_id == hit.relations[0].source_id
        assert hit.entity_type == EntityType.COMPANY
        assert hit.sources == ["neo4j"]
        assert hit.freshness is not None


def test_investment_query_direction_none_returns_both_sides(entity_resolver, query_router, graph_searcher):
    """"삼성전자 최근 투자 기업" — Task3 direction=None(양방향)을 그대로 입력.

    방향을 임의로 하나만 고르지 않았는지 확인한다(§5) — 실제 그래프에 outgoing(삼성전자가
    투자) 165건, incoming(삼성전자가 투자받음) 17건이 모두 있어(2026-08-09 실측)
    top_k=20이면 양쪽이 다 섞여 나온다.
    """
    resolution = entity_resolver.resolve("삼성전자")
    routing = query_router.route("삼성전자최근투자기업")
    assert routing.edge_types == ["OWNS_STAKE_IN"]
    assert routing.direction is None

    hits = graph_searcher.search([resolution], routing.edge_types, routing.direction, top_k=20)

    assert len(hits) > 0
    directions = {hit.relations[0].direction for hit in hits}
    assert directions == {"incoming", "outgoing"}


def test_investment_query_reports_person_counterparty_correctly(entity_resolver, graph_searcher):
    """OWNS_STAKE_IN에는 Person(이재용) 상대가 실제로 존재한다 —
    Task6 이전엔 COMPANY로 오분류됐던 케이스. 이제 PERSON으로 정확히 표현된다.

    ★top_k를 걸지 않는다. 전에는 top_k=20이었는데 2026-08-09 실측 당시엔 이재용이
      상위 20 안에 들었다. 이후 OWNS_STAKE_IN 관계가 늘면서(스킬드AI·미스트랄AI·
      주타코어·그록·앤트로픽 등) 30/49위로 밀려 테스트가 깨졌다. 이 테스트가
      확인하려는 건 **순위가 아니라 상대 엔티티의 타입 분류**이므로, 순위에
      의존하지 않게 전체(하드캡 100)를 받는다.
    """
    resolution = entity_resolver.resolve("삼성전자")
    hits = graph_searcher.search([resolution], ["OWNS_STAKE_IN"], None)
    person_hits = [h for h in hits if h.name == "이재용"]
    assert len(person_hits) == 1
    assert person_hits[0].entity_type == EntityType.PERSON


def test_lawsuit_query_without_resolved_entity(entity_resolver, query_router, graph_searcher):
    """"최근 소송 관련 기업" — 특정 기업이 해소되지 않는(§6-6) norm_name=None 케이스.
    source 최대 5 + target 최대 5 = 최대 10건(§8)."""
    resolution = entity_resolver.resolve("최근 소송 관련 기업")
    assert resolution is None
    routing = query_router.route("최근소송관련기업")
    assert routing.edge_types == ["SUES"]

    hits = graph_searcher.search([], routing.edge_types, routing.direction, top_k=20)

    assert 0 < len(hits) <= 10
    for hit in hits:
        # anchor가 없어 방향을 판별할 기준이 없다 — direction은 null이다(§5·§7).
        #   ★지어내지 않는다. 타입이 생기면서 「키가 없다」가 「값이 null이다」로 바뀌었다.
        assert hit.relations[0].direction is None
        assert hit.entity_type in set(EntityType)
