"""SearchOrchestrator 테스트.

Tier A(순수 호출 계약)는 5개 협력 객체(EntityResolver/QueryRouter/GraphSearcher/
VectorSearcher/ResultRanker)를 전부 MagicMock으로 통제해 "이 분기에서 정확히 무엇을
호출/미호출하는가"라는 Orchestrator 자체의 계약을 확인한다 — 특히 "edge_types가
있으면 GraphSearcher 결과가 0건이어도 VectorSearcher를 절대 호출하지 않는다"는
Task8의 핵심 정책(§3)은 실제 DB 데이터에 의존하면 재현이 불안정해지므로 mock으로
결정론적으로 고정한다(Task5~7과 동일 원칙, tests/search/service/test_graph_searcher.py
참고).

Tier B는 기술설계서 §6-6 대표 질의 5개를 실제 Docker PostgreSQL/Neo4j/ChromaDB
대상으로 재현한다(mock 없음) — query5(§7 핵심 회귀)는 실제 컴포넌트를 그대로 쓰되
VectorSearcher만 monkeypatch로 스파이 래핑해 "정말로 호출되지 않는가"를 검증한다.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit
from search.dto.search_request import SearchRequest
from search.model.enums import Direction, EntityType, SearchMode
from search.service.orchestrator import SearchOrchestrator
from search.service.query_router import RoutingResult

_TODAY = date(2026, 8, 15)

_SAMSUNG = Resolution(
    corp_code="00126380", corp_name="삼성전자",
    stock_code="005930", method="exact", score=1.0,
)


def _hit(entity_id, *, score=0.8, sources=("neo4j",)):
    return SearchHit(
        entity_type=EntityType.COMPANY, entity_id=entity_id, name=entity_id,
        source_score=score, sources=list(sources),
    )


def _make_orchestrator(*, route, resolve=None, resolve_candidates=None,
                        graph_search=None, vector_search=None, rank=None,
                        extract=None):
    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = resolve
    entity_resolver.resolve_candidates.return_value = resolve_candidates or []
    query_router = MagicMock()
    query_router.route.return_value = route
    graph_searcher = MagicMock()
    graph_searcher.search.return_value = graph_search or []
    vector_searcher = MagicMock()
    vector_searcher.search.return_value = vector_search or []
    result_ranker = MagicMock()
    result_ranker.rank.return_value = rank if rank is not None else []
    anchor_extractor = MagicMock()
    anchor_extractor.extract.return_value = extract

    orchestrator = SearchOrchestrator(
        entity_resolver, query_router, graph_searcher, vector_searcher,
        result_ranker, anchor_extractor)
    return (orchestrator, entity_resolver, query_router, graph_searcher,
            vector_searcher, result_ranker, anchor_extractor)


# ── Tier A: 순수 호출 계약(mock) ────────────────────────────────────────────

def test_edge_types_present_calls_graph_searcher_only():
    graph_hit = _hit("00164742")
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=Direction.INCOMING),
        resolve_candidates=[_SAMSUNG],
        graph_search=[graph_hit], rank=[graph_hit],
    )
    request = SearchRequest(query="삼성전자에 납품하는 기업")

    query, result = orch.search(request, today=_TODAY)

    vs.search.assert_not_called()
    er.resolve.assert_not_called()
    gs.search.assert_called_once_with(
        [_SAMSUNG], ["SUPPLIES_TO"], Direction.INCOMING, top_k=request.top_k,
        workspace_keys=[])
    rr.rank.assert_called_once_with([graph_hit], [], top_k=request.top_k,
                                    workspace_keys=[])
    assert query.mode == SearchMode.RELATIONSHIP
    assert result.used_semantic_fallback is False
    assert result.hits == [graph_hit]


def test_edge_types_present_and_graph_searcher_empty_never_calls_vector_searcher():
    """핵심 회귀 테스트(Task8 지침 §3·§7) — GraphSearcher가 0건이어도 edge_types가
    있으면 VectorSearcher로 폴백하지 않는다. 이전에 정했던 "빈 결과 시 자동 폴백"
    정책은 실측(§6-6 "삼성전자에 납품하는 기업")에서 VectorSearcher가 실제 공급사를
    0건 맞히고 계열사만 반환한 사고로 철회됐다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=Direction.INCOMING),
        resolve_candidates=[_SAMSUNG],
        graph_search=[], rank=[],
    )
    request = SearchRequest(query="삼성전자에 납품하는 기업")

    query, result = orch.search(request, today=_TODAY)

    vs.search.assert_not_called()
    assert result.hits == []
    assert result.total == 0
    assert query.mode == SearchMode.RELATIONSHIP
    assert result.used_semantic_fallback is False


def test_graph_searcher_zero_results_from_nonexistent_relation_combo_is_honest_empty_list():
    """§7 두 번째 회귀 케이스 — "존재하지 않는 관계 조합"도 정직하게 빈 리스트를
    반환하고 VectorSearcher 호출 흔적이 없어야 한다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUES"], direction=None),
        resolve_candidates=[],
        graph_search=[], rank=[],
    )
    request = SearchRequest(query="존재하지 않는 관계 조합 질의")

    query, result = orch.search(request, today=_TODAY)

    vs.search.assert_not_called()
    assert result.hits == []
    assert result.total == 0


def test_edge_types_empty_and_entity_resolved_returns_name_hit_only():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None),
        resolve=_SAMSUNG,
    )
    request = SearchRequest(query="삼성전자")

    query, result = orch.search(request, today=_TODAY)

    gs.search.assert_not_called()
    vs.search.assert_not_called()
    rr.rank.assert_not_called()
    er.resolve_candidates.assert_not_called()
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.entity_id == "00126380"
    assert hit.name == "삼성전자"
    assert hit.source_score == 1.0
    assert hit.sources == ["postgres"]
    assert query.mode == SearchMode.NAME
    assert result.used_semantic_fallback is False


def test_edge_types_empty_and_entity_unresolved_calls_vector_searcher_only():
    vector_hit = _hit("00164742", sources=["chroma"])
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None),
        resolve=None,
        vector_search=[vector_hit], rank=[vector_hit],
    )
    request = SearchRequest(query="HBM을 만드는 기업")

    query, result = orch.search(request, today=_TODAY)

    gs.search.assert_not_called()
    vs.search.assert_called_once_with(request.query, top_k=request.top_k)
    rr.rank.assert_called_once_with([], [vector_hit], top_k=request.top_k,
                                    workspace_keys=[])
    assert query.mode == SearchMode.SEMANTIC
    assert result.used_semantic_fallback is True
    assert result.hits == [vector_hit]


# ── top_k / today 전달 ──────────────────────────────────────────────────────

def test_top_k_propagates_through_graph_branch():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
    )
    request = SearchRequest(query="질의", top_k=5)

    orch.search(request, today=_TODAY)

    assert gs.search.call_args.kwargs["top_k"] == 5
    assert rr.rank.call_args.kwargs["top_k"] == 5


def test_top_k_propagates_through_vector_branch():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=None,
    )
    request = SearchRequest(query="질의", top_k=5)

    orch.search(request, today=_TODAY)

    assert vs.search.call_args.kwargs["top_k"] == 5
    assert rr.rank.call_args.kwargs["top_k"] == 5


def test_today_defaults_to_current_date_when_not_provided():
    orch, *_ = _make_orchestrator(route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)
    request = SearchRequest(query="삼성전자")

    query, _ = orch.search(request)

    assert query.today == date.today()


def test_today_uses_provided_value():
    orch, *_ = _make_orchestrator(route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)
    request = SearchRequest(query="삼성전자")

    query, _ = orch.search(request, today=_TODAY)

    assert query.today == _TODAY


# ── normalized_query / raw_query 분리 ───────────────────────────────────────

def test_normalized_query_strips_whitespace_and_lowercases():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=None,
    )
    request = SearchRequest(query="HBM을 만드는 기업")

    query, _ = orch.search(request, today=_TODAY)

    qr.route.assert_called_once_with("hbm을만드는기업")
    assert query.normalized_query == "hbm을만드는기업"


def test_raw_query_passed_untouched_to_entity_resolver_and_vector_searcher():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=None,
    )
    request = SearchRequest(query="HBM을 만드는 기업")

    orch.search(request, today=_TODAY)

    vs.search.assert_called_once_with("HBM을 만드는 기업", top_k=request.top_k)


def test_raw_query_passed_untouched_to_resolve_candidates():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
    )
    request = SearchRequest(query="Samsung 에 납품하는 기업")

    orch.search(request, today=_TODAY)

    er.resolve_candidates.assert_called_once_with("Samsung 에 납품하는 기업")


# ── SearchQuery 필드 소스(routing 우선, request 아님) ───────────────────────

def test_edge_types_and_direction_flow_from_router_not_request():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=Direction.INCOMING),
    )
    request = SearchRequest(query="질의", edge_types=["SUPPLIES_TO"])

    query, _ = orch.search(request, today=_TODAY)

    assert query.edge_types == ["SUPPLIES_TO"]
    assert query.direction == Direction.INCOMING


def test_result_query_field_is_raw_query():
    orch, *_ = _make_orchestrator(route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)
    request = SearchRequest(query="삼성전자")

    _, result = orch.search(request, today=_TODAY)

    assert result.query == "삼성전자"


def test_took_ms_is_nonnegative_int():
    orch, *_ = _make_orchestrator(route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)
    request = SearchRequest(query="삼성전자")

    _, result = orch.search(request, today=_TODAY)

    assert isinstance(result.took_ms, int)
    assert result.took_ms >= 0


# ── Tier B: §6-6 5개 질의 재현 — 실제 Docker PostgreSQL/Neo4j/ChromaDB, mock 없음 ──

def test_query1_samsung_name_mode(orchestrator):
    request = SearchRequest(query="삼성전자")

    query, result = orchestrator.search(request)

    assert query.mode == SearchMode.NAME
    assert result.used_semantic_fallback is False
    assert len(result.hits) == 1
    assert result.hits[0].entity_id == "00126380"
    assert result.hits[0].sources == ["postgres"]


def test_query2_investment_relationship_mode(orchestrator):
    """"삼성전자 최근 투자 기업" — 설계문서 §6-1 표는 mode=HYBRID로 적혀 있지만,
    Task8 지침 §5는 HYBRID를 만들지 않는 규칙(GraphSearcher만 돌면 무조건
    RELATIONSHIP)을 명시적으로 확정했다 — 이 테스트는 Task8 규칙이 우선임을
    검증한다(현황서 §4 이슈 기록 참고)."""
    request = SearchRequest(query="삼성전자 최근 투자 기업")

    query, result = orchestrator.search(request)

    assert query.edge_types == ["OWNS_STAKE_IN"]
    assert query.mode == SearchMode.RELATIONSHIP
    assert result.used_semantic_fallback is False


def test_query2_anchor_extraction_resolves_samsung(orchestrator):
    """"삼성전자 최근 투자 기업"(조사 없는 케이스)도 anchor가 삼성전자로
    정확히 잡히는지 확인한다."""
    request = SearchRequest(query="삼성전자 최근 투자 기업")

    query, result = orchestrator.search(request)

    assert query.resolved_entities
    assert query.resolved_entities[0].corp_code == "00126380"


def test_query3_hbm_semantic_mode(orchestrator, entity_resolver):
    assert entity_resolver.resolve("HBM을 만드는 기업") is None
    request = SearchRequest(query="HBM을 만드는 기업")

    query, result = orchestrator.search(request)

    assert query.mode == SearchMode.SEMANTIC
    assert result.used_semantic_fallback is True
    assert len(result.hits) > 0
    for hit in result.hits:
        assert hit.sources == ["chroma"]


def test_query4_supplies_to_relationship_mode(orchestrator):
    request = SearchRequest(query="삼성전자에 납품하는 기업")

    query, result = orchestrator.search(request)

    assert query.direction == Direction.INCOMING
    assert query.mode == SearchMode.RELATIONSHIP
    assert result.used_semantic_fallback is False
    assert len(result.hits) > 0


def test_query4_anchor_extraction_excludes_unrelated_suppliers(orchestrator):
    """§3-1/§6 완료 기준 정면 검증 — anchor 추출 전에는 "삼성전자에 납품하는
    기업"이 anchor 없는 전역 top-N 경로로 빠져 엔비디아·마이크론·포스코 같은
    무관 기업이 섞여 나왔다(현황서 §3-1 실측). 수정 후에는 anchor(삼성전자)로
    좁혀진 결과만 나와야 한다."""
    request = SearchRequest(query="삼성전자에 납품하는 기업")

    query, result = orchestrator.search(request)

    assert query.resolved_entities
    assert query.resolved_entities[0].corp_code == "00126380"
    hit_names = {hit.name for hit in result.hits}
    assert "엔비디아" not in hit_names
    assert "마이크론" not in hit_names
    assert "포스코" not in hit_names


def test_query4_never_calls_vector_searcher_regardless_of_graph_result_count(
    orchestrator, vector_searcher, monkeypatch,
):
    """§3 원 사고를 실제 컴포넌트로 재현: "삼성전자에 납품하는 기업"에서 GraphSearcher
    결과 수와 무관하게 VectorSearcher가 호출되지 않는지 확인한다."""
    spy = MagicMock(side_effect=vector_searcher.search)
    monkeypatch.setattr(vector_searcher, "search", spy)
    request = SearchRequest(query="삼성전자에 납품하는 기업")

    orchestrator.search(request)

    spy.assert_not_called()


def test_query5_lawsuit_relationship_mode_never_touches_vector_searcher(
    orchestrator, vector_searcher, monkeypatch,
):
    """§7 핵심 회귀 — "최근 소송 관련 기업"은 EntityResolver가 기업명을 못 찾아도
    edge_types=["SUES"]가 있으므로 RELATIONSHIP 분기이고, VectorSearcher는 절대
    호출되지 않는다(실제 컴포넌트 대상, mock은 VectorSearcher 스파이 하나뿐)."""
    spy = MagicMock(side_effect=vector_searcher.search)
    monkeypatch.setattr(vector_searcher, "search", spy)
    request = SearchRequest(query="최근 소송 관련 기업")

    query, result = orchestrator.search(request)

    assert query.edge_types == ["SUES"]
    assert query.mode == SearchMode.RELATIONSHIP
    assert result.used_semantic_fallback is False
    spy.assert_not_called()
    assert 0 < len(result.hits) <= request.top_k


# ── AnchorExtractor 연동(Task 9) ────────────────────────────────────────────

def test_anchor_extractor_result_used_instead_of_raw_query():
    """AnchorExtractor가 anchor를 찾으면 그 결과로 resolve_candidates를
    호출한다(원문이 아니라)."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG], extract="삼성전자",
    )
    request = SearchRequest(query="삼성전자에 납품하는 기업", edge_types=["SUPPLIES_TO"])

    orch.search(request)

    ae.extract.assert_called_once_with("삼성전자에 납품하는 기업")
    er.resolve_candidates.assert_called_once_with("삼성전자")


def test_falls_back_to_raw_query_when_anchor_extraction_returns_none():
    """AnchorExtractor가 확신하지 못하면(None) 지금까지와 동일하게 원문을
    그대로 resolve_candidates에 넘긴다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUES"], direction=None),
        resolve_candidates=[], extract=None,
    )
    request = SearchRequest(query="최근 소송 관련 기업", edge_types=["SUES"])

    orch.search(request)

    er.resolve_candidates.assert_called_once_with("최근 소송 관련 기업")


def test_anchor_feeds_resolve_when_edge_types_empty():
    """edge_types 없는 분기(NAME/SEMANTIC)도 AnchorExtractor를 거치고, 잘라낸
    기업명이 resolve()의 입력이 된다(A3).

    ★전에는 이 분기를 범위 밖으로 두고 호출조차 하지 않았다 — 그래서 "삼성전자
      관련 뉴스"가 원문 전체로 resolve()에 들어가 해소에 실패했다(현황서 §5-2).
    """
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG,
        extract="삼성전자",
    )

    orch.search(SearchRequest(query="삼성전자 관련 뉴스"))

    ae.extract.assert_called_once_with("삼성전자 관련 뉴스")
    er.resolve.assert_called_once_with("삼성전자")


def test_raw_query_reaches_resolve_when_anchor_extraction_fails():
    """AnchorExtractor가 확신하지 못하면(None) 원문이 그대로 resolve()로 간다 —
    잘못 잘라낸 이름으로 엉뚱한 기업에 매칭되는 것보다 낫다는 기존 원칙 유지."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=None,
        extract=None,
    )

    orch.search(SearchRequest(query="HBM을 만드는 기업"))

    er.resolve.assert_called_once_with("HBM을 만드는 기업")


# ── A3: AnchorExtractor 를 NAME/SEMANTIC 분기에도 적용(2026-08-19) ────────────
#   실제 Docker PostgreSQL/ChromaDB 대상, mock 없음

def test_entity_buried_in_sentence_resolves_to_name_mode(orchestrator, entity_resolver):
    """"삼성전자 관련 뉴스" — edge_types 가 비어 있으면서 문장 속에 기업명이
    파묻힌 질의. 전에는 원문 전체가 EntityResolver.resolve() 로 넘어가 해소에
    실패하고 SEMANTIC 으로 빠졌다(현황서 §5-2). AnchorExtractor 가 먼저
    기업명을 잘라내면 NAME 으로 해소된다.
    """
    assert entity_resolver.resolve("삼성전자 관련 뉴스") is None  # 전제: 원문은 해소 안 됨

    query, result = orchestrator.search(SearchRequest(query="삼성전자 관련 뉴스"))

    assert query.mode == SearchMode.NAME
    assert result.used_semantic_fallback is False
    assert [h.entity_id for h in result.hits] == ["00126380"]


def test_descriptive_query_without_entity_still_semantic(orchestrator, anchor_extractor):
    """anchor 를 못 찾는 질의는 그대로 SEMANTIC 이어야 한다 — A3 가 의미검색
    경로를 잡아먹으면 안 된다."""
    assert anchor_extractor.extract("HBM을 만드는 기업") is None  # 전제

    query, result = orchestrator.search(SearchRequest(query="HBM을 만드는 기업"))

    assert query.mode == SearchMode.SEMANTIC
    assert result.used_semantic_fallback is True


# ── 응답 계약(D2·D3, 2026-08-19) ─────────────────────────────────────────────

def test_rank_is_assigned_from_one_in_result_order():
    hits = [_hit("A"), _hit("B"), _hit("C")]
    orch, *_ = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG], graph_search=hits, rank=hits,
    )

    _, result = orch.search(SearchRequest(query="삼성전자에 납품하는 기업"), today=_TODAY)

    assert [h.rank for h in result.hits] == [1, 2, 3]


def test_name_mode_hit_also_gets_rank_but_no_rrf_score():
    """NAME 분기는 ResultRanker를 거치지 않으므로 rrf_score가 없다 — 그래도 rank는
    붙는다. 여기에 RRF 값을 지어내면 계약이 거짓말이 된다."""
    orch, *_ = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG,
    )

    _, result = orch.search(SearchRequest(query="삼성전자"), today=_TODAY)

    assert result.hits[0].rank == 1
    assert result.hits[0].rrf_score is None


def test_result_exposes_mode():
    """백엔드가 결과 성격을 알아야 화면·답변이 갈린다 — SEMANTIC 결과는 프로필
    보유 기업 안에서 고른 것이라 NAME·RELATIONSHIP과 같은 무게로 말하면 안 된다."""
    orch, *_ = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG,
    )

    _, result = orch.search(SearchRequest(query="삼성전자"), today=_TODAY)

    assert result.mode == SearchMode.NAME


def test_request_edge_types_override_router_inference():
    """명시 지정이 추론을 이긴다(D3). 챗봇 탐색 프로파일이 「생산 차질 →
    SUPPLIES_TO/DEPENDS_ON」처럼 엣지를 직접 지정하는데, QueryRouter 키워드 추론에
    무력화되면 프로파일이 의미가 없다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None),
        resolve_candidates=[_SAMSUNG], resolve=_SAMSUNG,
    )

    query, _ = orch.search(
        SearchRequest(query="삼성전자 관련 뉴스", edge_types=["SUPPLIES_TO"]), today=_TODAY)

    gs.search.assert_called_once()
    assert gs.search.call_args.args[1] == ["SUPPLIES_TO"]
    assert query.mode == SearchMode.RELATIONSHIP
    assert query.edge_types == ["SUPPLIES_TO"]


def test_include_evidence_false_clears_evidence():
    hit = _hit("A")
    hit.evidence = [{"evidence_id": "ev_1"}]
    orch, *_ = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG], graph_search=[hit], rank=[hit],
    )

    _, result = orch.search(
        SearchRequest(query="삼성전자에 납품하는 기업", include_evidence=False), today=_TODAY)

    assert result.hits[0].evidence == []


# ══════════════════════════════════════════════════════════════════════════
#  workspace_keys 전달 (작업계획 §2 · K2 · 설계서 §7)
# ══════════════════════════════════════════════════════════════════════════

_WORKSPACE = ["00126380", "00301246"]


def test_workspace_keys_reach_graph_searcher():
    """★어느 단계에서도 범위가 사라지면 안 된다(설계서 §7)."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG])

    orch.search(SearchRequest(query="삼성전자에 납품하는 기업",
                              workspace_keys=_WORKSPACE), today=_TODAY)

    # 필터가 아니라 「후보를 넉넉히 뽑아라」는 신호로 넘어간다.
    assert gs.search.call_args.kwargs["workspace_keys"] == _WORKSPACE
    assert rr.rank.call_args.kwargs["workspace_keys"] == _WORKSPACE


def test_workspace_keys_reach_the_ranker_not_the_vector_searcher():
    """★의미검색은 모집단을 좁히지 않는다 — 관련도는 랭킹에서 따진다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=None)

    orch.search(SearchRequest(query="HBM을 만드는 기업",
                              workspace_keys=_WORKSPACE), today=_TODAY)

    assert "workspace_keys" not in vs.search.call_args.kwargs
    assert rr.rank.call_args.kwargs["workspace_keys"] == _WORKSPACE


def test_workspace_keys_are_carried_into_search_query():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)

    query, _ = orch.search(SearchRequest(query="삼성전자", workspace_keys=_WORKSPACE),
                           today=_TODAY)

    assert query.workspace_keys == _WORKSPACE


def test_empty_workspace_keys_mean_no_scope():
    """빈 목록을 「전부 제외」로 읽으면 기존 검색이 통째로 죽는다."""
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG])

    orch.search(SearchRequest(query="삼성전자에 납품하는 기업"), today=_TODAY)

    assert gs.search.call_args.kwargs["workspace_keys"] == []


def test_name_resolution_inside_workspace_stays_name_mode():
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)

    query, result = orch.search(
        SearchRequest(query="삼성전자", workspace_keys=_WORKSPACE), today=_TODAY)

    assert query.mode == SearchMode.NAME
    assert result.hits[0].entity_id == "00126380"


def test_name_resolution_outside_workspace_is_still_returned():
    """★워크스페이스 밖이라고 이름 해소 결과를 버리지 않는다(2026-08-20 정책 변경).

    「삼성전자」를 물었으면 워크스페이스에 없어도 삼성전자가 답이다. 워크스페이스는
    **순서를 정하는 문맥**이지 답을 지우는 필터가 아니다.
    """
    orch, er, qr, gs, vs, rr, ae = _make_orchestrator(
        route=RoutingResult(edge_types=[], direction=None), resolve=_SAMSUNG)

    query, result = orch.search(
        SearchRequest(query="삼성전자", workspace_keys=["00164779"]), today=_TODAY)

    assert query.mode == SearchMode.NAME
    assert result.hits[0].entity_id == "00126380"
    vs.search.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
#  Tier B — 워크스페이스 랭킹 (실 저장소, mock 없음)
# ══════════════════════════════════════════════════════════════════════════

# 삼성전자 · 현대자동차. 그래프에 둘 사이 PARTNERS_WITH 가 있다.
_REAL_WORKSPACE = ["00126380", "00164742"]


def test_real_workspace_does_not_shrink_the_result(orchestrator):
    """★워크스페이스를 줘도 결과 수가 줄지 않는다 — 필터가 아니라 랭킹 문맥이다."""
    question = "삼성전자와 협력하는 곳"
    wide = orchestrator.search(SearchRequest(query=question, top_k=10))[1]
    scoped = orchestrator.search(
        SearchRequest(query=question, workspace_keys=_REAL_WORKSPACE, top_k=10))[1]

    assert scoped.total == wide.total


def test_real_workspace_member_is_promoted_to_the_front(orchestrator):
    """실측(2026-08-20) — 현대자동차는 삼성전자 관계 998건 중 **418번째**다.

    점수순으로 top_k=10 을 자르면 후보에 들지도 못한다. 워크스페이스를 주면
    1위로 올라온다. 「후보를 넉넉히 뽑고 관련도로 줄 세운 뒤 top_k」가 실제로
    동작한다는 증거다.
    """
    _, result = orchestrator.search(
        SearchRequest(query="삼성전자와 협력하는 곳",
                      workspace_keys=_REAL_WORKSPACE, top_k=10))

    assert result.hits[0].entity_id in _REAL_WORKSPACE


def test_real_external_companies_are_still_returned(orchestrator):
    """워크스페이스 밖 기업이 사라지지 않는다."""
    _, result = orchestrator.search(
        SearchRequest(query="삼성전자와 협력하는 곳",
                      workspace_keys=_REAL_WORKSPACE, top_k=10))

    outside = [h for h in result.hits if h.entity_id not in _REAL_WORKSPACE]
    assert outside, "바깥 기업이 전부 사라졌다면 hard filter 로 돌아간 것이다"


def test_real_non_company_entities_survive_a_workspace(orchestrator):
    """★Event·Person·Organization·Product 는 워크스페이스에 담기지 않는다.

    한때 양끝 모두를 요구하는 hard filter 였을 때 이 라벨들은 **하나도** 남지
    않았다(corp_code 가 없어 조건을 통과할 방법이 없었다).
    """
    _, result = orchestrator.search(
        SearchRequest(query="삼성전자 임원", workspace_keys=["00126380"], top_k=20))

    labels = {h.entity_type for h in result.hits}
    assert labels - {EntityType.COMPANY}, f"비-Company 가 전부 사라졌다: {labels}"
