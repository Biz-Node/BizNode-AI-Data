"""검색·답변 경계의 trace 로그.

두 가지를 본다.

1. **경계마다 추적에 필요한 값이 실제로 찍히는가** — query·mode·edge_id·
   source/target·evidence_id. 로그를 읽고 "이 근거가 어느 검색 경로에서
   나왔는가"를 되짚을 수 있어야 한다.
2. **민감정보가 찍히지 않는가** — 전체 프롬프트·근거 원문·API 키.
   2번이 회귀 방지의 핵심이다. 1번은 안 찍히면 불편할 뿐이지만, 2번은
   한 번 새면 로그 파일에 영구히 남는다.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

from app.api.schemas import AskRequest, Evidence, MatchType, RetrieveResponse
from app.core import trace as trace_module
from app.services import answer_service as as_module
from app.services import retrieve_service as rs_module
from app.services.graph_service import Relation
from app.services.retrieve_service import RetrieveService
from pipeline.freshness import Freshness
from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit
from search.dto.search_request import SearchRequest
from search.model.enums import Direction, EntityType
from search.service import graph_searcher as gs_module
from search.service.result_ranker import ResultRanker
from search.service.orchestrator import SearchOrchestrator
from search.service.query_router import RoutingResult

_SAMSUNG = Resolution(corp_code="00126380", corp_name="삼성전자",
                      stock_code="005930", method="exact", score=1.0)


def _hit(entity_id, *, entity_type=EntityType.COMPANY, **kwargs):
    return SearchHit(entity_type=entity_type, entity_id=entity_id, name=entity_id,
                     source_score=0.8, sources=["neo4j"], **kwargs)


# ══════════════════════════════════════════════════════════════════════
#  trace id — 요청 하나를 여러 계층의 로그 줄로 잇는 키
# ══════════════════════════════════════════════════════════════════════

def test_trace_logger_prefixes_the_current_trace_id(caplog):
    trace_id = trace_module.new_trace_id()
    log = trace_module.trace_logger("test.prefix")

    with caplog.at_level("INFO"):
        log.info("어떤 일이 일어났다")

    assert f"[{trace_id}] 어떤 일이 일어났다" in caplog.text


def test_new_trace_id_differs_per_call():
    """★같은 id가 나오면 두 요청의 로그를 못 가른다 — 추적의 전제다."""
    assert trace_module.new_trace_id() != trace_module.new_trace_id()


def test_logging_works_before_any_trace_id_is_issued(caplog):
    """배치·테스트처럼 요청 경계를 안 거친 호출도 죽지 않아야 한다."""
    trace_module.reset_trace_id()
    log = trace_module.trace_logger("test.no_trace")

    with caplog.at_level("INFO"):
        log.info("경계 밖 호출")

    assert "경계 밖 호출" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  경계 1 — SearchOrchestrator: 어떤 질의가 어느 경로로 갔는가
# ══════════════════════════════════════════════════════════════════════

def _orchestrator(*, route, resolve=None, resolve_candidates=None,
                  graph_search=None, vector_search=None, rank=None):
    """협력자 6개를 전부 통제한다 — 여기서 보는 것은 로그뿐이라 검색 자체는
    결정론적이어야 한다(tests/search/service/test_orchestrator.py 컨벤션)."""
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
    anchor_extractor.extract.return_value = None

    return SearchOrchestrator(entity_resolver, query_router, graph_searcher,
                              vector_searcher, result_ranker, anchor_extractor)


def test_orchestrator_logs_the_query_and_routed_edge_types_on_entry(caplog):
    orch = _orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=Direction.INCOMING),
        resolve_candidates=[_SAMSUNG])

    with caplog.at_level("INFO"):
        orch.search(SearchRequest(query="삼성전자에 납품하는 기업"))

    assert "search.start" in caplog.text
    assert "삼성전자에 납품하는 기업" in caplog.text
    assert "SUPPLIES_TO" in caplog.text


def test_orchestrator_logs_the_resolved_mode_and_hit_count_on_exit(caplog):
    """★`mode` 가 없으면 「이 결과가 그래프에서 온 건가 벡터에서 온 건가」를
    로그만 보고는 못 가른다 — 추적의 출발점이다."""
    orch = _orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG], rank=[_hit("00164742")])

    with caplog.at_level("INFO"):
        orch.search(SearchRequest(query="q"))

    assert "search.done" in caplog.text
    assert "mode=RELATIONSHIP" in caplog.text
    assert "hits=1" in caplog.text


def test_orchestrator_truncates_a_very_long_query_in_the_log(caplog):
    """★질의는 사용자 입력이라 길이 상한이 없다 — 그대로 찍으면 로그 한 줄이
    화면을 덮는다."""
    orch = _orchestrator(route=RoutingResult(edge_types=[], direction=None))

    with caplog.at_level("INFO"):
        orch.search(SearchRequest(query="가" * 500))

    assert "가" * 500 not in caplog.text
    assert "가" * 50 in caplog.text     # 앞부분은 남아 있어야 알아본다


# ══════════════════════════════════════════════════════════════════════
#  경계 2 — GraphSearcher: 어느 엣지를 짚었는가
# ══════════════════════════════════════════════════════════════════════

def _relation(source, target, *, edge_id, edge_type="SUPPLIES_TO"):
    return Relation(
        source=source, target=target, edge_type=edge_type, subtype="", source_type="",
        confidence=0.9, corroboration=1,
        freshness=Freshness(status="current", reason="test", days_since=0,
                            confidence_factor=1.0),
        props={}, source_id=source, source_entity_type="Company",
        target_id=target, target_entity_type="Company", edge_id=edge_id)


def test_graph_searcher_logs_the_edge_ids_and_endpoints_it_found(caplog, monkeypatch):
    """★`edge_id` 와 양끝이 없으면 「어느 관계를 근거로 골랐는가」를 되짚을 수 없다."""
    monkeypatch.setattr(gs_module, "relations_of",
                        lambda **kwargs: [_relation("SFA반도체", "삼성전자", edge_id="5:abc:1")])

    with caplog.at_level("INFO"):
        gs_module.GraphSearcher().search([_SAMSUNG], ["SUPPLIES_TO"], Direction.INCOMING)

    assert "graph.search" in caplog.text
    assert "5:abc:1" in caplog.text
    assert "SFA반도체" in caplog.text
    assert "삼성전자" in caplog.text


def test_graph_searcher_logs_which_branch_it_took(caplog, monkeypatch):
    """★anchor 유무로 쿼리가 완전히 갈린다 — 결과가 이상할 때 제일 먼저 볼 값이다."""
    monkeypatch.setattr(gs_module, "relations_of", lambda **kwargs: [])

    with caplog.at_level("INFO"):
        gs_module.GraphSearcher().search([], ["SUPPLIES_TO"])

    assert "anchor=None" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  경계 3 — ResultRanker: 무엇이 합쳐져 무엇이 위로 올라갔는가
# ══════════════════════════════════════════════════════════════════════

def test_ranker_logs_how_many_came_from_each_source(caplog):
    """★그래프 0건 + 벡터 N건인지, 그 반대인지가 답변 신뢰도를 가른다."""
    with caplog.at_level("INFO"):
        ResultRanker().rank([_hit("00126380")], [_hit("00164779")], top_k=5)

    assert "rank.merge" in caplog.text
    assert "graph=1" in caplog.text
    assert "vector=1" in caplog.text


def test_ranker_logs_the_winning_entity_ids(caplog):
    """★최종 상위 결과가 곧 답변의 재료다 — 어느 entity가 이겼는지 남긴다."""
    with caplog.at_level("INFO"):
        ResultRanker().rank([_hit("00126380")], [], top_k=5)

    assert "00126380" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  경계 4 — 근거 조립: 최종 근거가 어디서 왔는가
# ══════════════════════════════════════════════════════════════════════

def _event(event_id, *, evidence_ids=()):
    return {"event_id": event_id, "name": "화재", "event_type": "사고재해",
            "is_risk": False, "role": "subject", "occurred_at": "2026-06-11",
            "article_count": 1, "timeline": [], "evidence_ids": list(evidence_ids)}


def _relation_row(edge_id, *, evidence_id=None):
    return {"edge_id": edge_id, "evidence_id": evidence_id, "type": "SUPPLIES_TO",
            "subtype": None,
            "source": {"key": "00301246", "name": "SFA반도체", "label": "Company"},
            "target": {"key": "00126380", "name": "삼성전자", "label": "Company"},
            "symmetric": False, "amount": None, "ratio": None,
            "freshness": "current", "last_seen": "2026-03-23", "valid_from": None,
            "valid_until": None, "score": 0.9, "corroboration": 1,
            "source_type": "dart", "refresh_cycle_days": 365, "days_since": 10,
            "days_until_refresh": 355, "exclusive": None, "other_counterparties": []}


def _stub_repositories(monkeypatch, *, events=(), relations=(), evidence=()):
    company = MagicMock()
    company.events_of.return_value = list(events)
    company.relations_of.return_value = list(relations)
    relation = MagicMock()
    relation.evidence_for_ids.return_value = list(evidence)
    relation.event_impact.return_value = []
    monkeypatch.setattr(rs_module, "company_service", company)
    monkeypatch.setattr(rs_module, "relation_service", relation)


def _real_orchestrator(hits):
    """진짜 SearchOrchestrator에 mock 협력자를 끼운다 — trace id가 검색 계층과
    근거 계층을 실제로 잇는지 보려면 두 계층이 모두 진짜여야 한다."""
    return _orchestrator(
        route=RoutingResult(edge_types=["SUPPLIES_TO"], direction=None),
        resolve_candidates=[_SAMSUNG], rank=list(hits))


def test_evidence_log_separates_the_three_sources_of_ids(caplog, monkeypatch):
    """★근거 id의 출처가 셋이다(관계·사건·검색히트). 어디서 몇 개가 왔는지
    갈라 놓지 않으면 「근거가 왜 이것뿐인가」를 못 따진다."""
    _stub_repositories(
        monkeypatch,
        events=[_event("evt_1", evidence_ids=["ev_evt"])],
        relations=[_relation_row("5:a:1", evidence_id="ev_rel")])
    orch = _real_orchestrator([_hit("00126380", evidence=[{"evidence_id": "ev_hit"}])])

    with caplog.at_level("INFO"):
        RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert "evidence.collect" in caplog.text
    assert "from_relations=1" in caplog.text
    assert "from_events=1" in caplog.text
    assert "from_hits=1" in caplog.text


def test_evidence_log_names_the_ids_and_counts_the_missing(caplog, monkeypatch):
    """★`missing` 은 id는 있는데 원문을 못 찾은 것 — 인용에 못 쓴다. 개수를
    남기지 않으면 「근거가 있는데 답변이 인용을 안 했다」를 설명할 수 없다."""
    _stub_repositories(
        monkeypatch,
        relations=[_relation_row("5:a:1", evidence_id="ev_rel")],
        evidence=[{"evidence_id": "ev_rel", "text": "", "source_doc": "",
                   "source_type": "news", "press": None, "published_at": None,
                   "missing": True}])
    orch = _real_orchestrator([_hit("00126380")])

    with caplog.at_level("INFO"):
        RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert "ev_rel" in caplog.text
    assert "missing=1" in caplog.text


def test_one_request_shares_one_trace_id_across_search_and_evidence(caplog, monkeypatch):
    """★이 테스트가 trace 설계의 전부다 — 검색 계층 로그와 근거 계층 로그가
    같은 id를 달지 않으면, 요청이 둘만 겹쳐도 「이 근거가 저 질의에서 나왔다」를
    못 잇는다."""
    _stub_repositories(monkeypatch)
    orch = _real_orchestrator([_hit("00126380")])
    trace_module.reset_trace_id()

    with caplog.at_level("INFO"):
        RetrieveService(orch).retrieve(AskRequest(question="q"))

    prefixes = re.findall(r"\[([0-9a-f]{8}|-)\] (?:search|evidence)\.", caplog.text)
    assert prefixes, "검색·근거 로그가 아예 안 찍혔다"
    assert "-" not in prefixes, "retrieve()가 trace id를 발급하지 않았다"
    assert len(set(prefixes)) == 1, f"한 요청인데 id가 갈렸다: {set(prefixes)}"


# ══════════════════════════════════════════════════════════════════════
#  경계 5 — LLM: 무엇을 재료로 줬고 무엇이 인용으로 살아남았는가
# ══════════════════════════════════════════════════════════════════════

_SECRET_TEXT = "이 문장이 로그에 새면 안 된다"


def _evidence(evidence_id, *, missing=False, text=_SECRET_TEXT):
    return Evidence(evidence_id=evidence_id, text=text, source_doc="doc",
                    source_type="news", missing=missing)


def _retrieved(*, evidence=(), match_type=MatchType.EXACT):
    return RetrieveResponse(question="q", evidence=list(evidence), match_type=match_type)


def _answer_service(retrieved, monkeypatch, *, llm_result):
    service = MagicMock()
    service.retrieve.return_value = retrieved
    monkeypatch.setattr(as_module, "ask_json", lambda *a, **k: llm_result)
    return as_module.AnswerService(service)


def test_llm_request_log_carries_material_counts_and_match_type(caplog, monkeypatch):
    """★`match_type` 이 있어야 「SEMANTIC 재료로 쓴 답변」을 나중에 골라낼 수 있다."""
    retrieved = _retrieved(evidence=[_evidence("ev_a")], match_type=MatchType.SEMANTIC)
    service = _answer_service(retrieved, monkeypatch,
                              llm_result={"answer": "답", "evidence_ids": []})

    with caplog.at_level("INFO"):
        service.ask(AskRequest(question="q"))

    assert "llm.request" in caplog.text
    assert "match_type=SEMANTIC" in caplog.text
    assert "evidence=1" in caplog.text
    assert "prompt_chars=" in caplog.text


def test_llm_response_log_separates_cited_accepted_and_dropped(caplog, monkeypatch):
    """★이 한 줄이 「최종 근거가 어디서 만들어졌는가」에 답한다 — LLM 이 든 id,
    화이트리스트를 통과한 id, 버려진 id(환각이거나 원문 없음)를 갈라 놓는다."""
    retrieved = _retrieved(evidence=[_evidence("ev_a"), _evidence("ev_gone", missing=True)])
    service = _answer_service(
        retrieved, monkeypatch,
        llm_result={"answer": "답", "evidence_ids": ["ev_a", "ev_gone", "ev_ghost"]})

    with caplog.at_level("INFO"):
        service.ask(AskRequest(question="q"))

    assert "llm.response" in caplog.text
    assert "accepted=['ev_a']" in caplog.text
    assert "ev_ghost" in caplog.text     # 환각 id 가 버려졌다는 사실이 남아야 한다
    assert "ev_gone" in caplog.text      # missing 이라 버려진 것도 마찬가지


def test_llm_failure_is_visible_in_the_log(caplog, monkeypatch):
    retrieved = _retrieved(evidence=[_evidence("ev_a")])
    service = _answer_service(retrieved, monkeypatch, llm_result={
        "answer": "", "evidence_ids": [], "failed": True, "reason": "LLM 호출 실패"})

    with caplog.at_level("INFO"):
        service.ask(AskRequest(question="q"))

    assert "failed=True" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  민감정보 배제 — 한 번 새면 로그 파일에 영구히 남는다
# ══════════════════════════════════════════════════════════════════════

def test_evidence_text_never_reaches_the_log(caplog, monkeypatch):
    """★근거 원문은 뉴스·공시 본문이다. id 만 남기고 본문은 절대 찍지 않는다."""
    retrieved = _retrieved(evidence=[_evidence("ev_a", text=_SECRET_TEXT)])
    service = _answer_service(retrieved, monkeypatch,
                              llm_result={"answer": "답", "evidence_ids": ["ev_a"]})

    with caplog.at_level("DEBUG"):
        service.ask(AskRequest(question="q"))

    assert _SECRET_TEXT not in caplog.text
    assert "ev_a" in caplog.text, "본문을 뺐다고 id 까지 빠지면 추적이 안 된다"


def test_the_prompt_itself_never_reaches_the_log(caplog, monkeypatch):
    """★전체 프롬프트에는 시스템 지시문과 근거 본문이 통째로 들어 있다 —
    길이만 남긴다."""
    retrieved = _retrieved(evidence=[_evidence("ev_a")])
    service = _answer_service(retrieved, monkeypatch,
                              llm_result={"answer": "답", "evidence_ids": []})

    with caplog.at_level("DEBUG"):
        service.ask(AskRequest(question="q"))

    assert "당신은 BizNode" not in caplog.text     # 시스템 프롬프트 첫 구절
    assert "<evidence id=" not in caplog.text      # 근거 블록 델리미터


def test_the_generated_answer_body_never_reaches_the_log(caplog, monkeypatch):
    """★답변 본문은 근거 원문을 되풀이한 것이라 같은 위험을 진다."""
    retrieved = _retrieved(evidence=[_evidence("ev_a")])
    service = _answer_service(retrieved, monkeypatch, llm_result={
        "answer": f"근거에 따르면 {_SECRET_TEXT}", "evidence_ids": ["ev_a"]})

    with caplog.at_level("DEBUG"):
        service.ask(AskRequest(question="q"))

    assert _SECRET_TEXT not in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  로깅 설정 — 안 켜지면 위의 모든 로그가 통째로 사라진다
# ══════════════════════════════════════════════════════════════════════

def test_configure_logging_applies_the_requested_level():
    root = logging.getLogger()
    original = root.level
    try:
        trace_module.configure_logging("DEBUG")
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(original)


def test_configure_logging_quiets_noisy_third_party_loggers():
    """★root 를 INFO 로 열면 chromadb 텔레메트리와 httpx 요청 로그가 같이
    쏟아진다 — 실측(「삼성전자에 납품하는 기업」 1회)에서 우리 trace 줄 5개에
    라이브러리 줄 10개가 섞여 정작 추적할 줄이 파묻혔다."""
    root = logging.getLogger()
    original = root.level
    try:
        trace_module.configure_logging("INFO")

        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
        assert not logging.getLogger("chromadb").isEnabledFor(logging.INFO)
        # 우리 로거는 그대로 열려 있어야 한다 — 조용하게 만드는 게 목적이 아니다
        assert logging.getLogger("search.service.orchestrator").isEnabledFor(logging.INFO)
    finally:
        root.setLevel(original)


def test_configure_logging_still_applies_when_a_handler_already_exists():
    """★`logging.basicConfig` 는 root 에 핸들러가 이미 있으면 **아무것도 하지
    않고 돌아간다.** uvicorn 이 자기 핸들러를 붙여 둔 상태가 정확히 그 경우라,
    basicConfig 만 부르면 레벨이 조용히 안 먹고 trace 로그가 전부 사라진다."""
    root = logging.getLogger()
    original_level = root.level
    placeholder = logging.NullHandler()
    root.addHandler(placeholder)
    try:
        trace_module.configure_logging("DEBUG")
        assert root.level == logging.DEBUG
    finally:
        root.removeHandler(placeholder)
        root.setLevel(original_level)
