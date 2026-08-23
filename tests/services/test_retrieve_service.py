"""RetrieveService — 챗봇 재료 조립.

Tier A는 협력자(Orchestrator·company_service·relation_service)를 통제해 "무엇을
어떤 순서로 부르고 무엇을 합치는가"라는 조립 계약만 본다. 실제 DB 데이터에 기대면
사건·관계 건수가 재적재마다 달라져 재현이 안 된다.

Tier B는 실 Docker PostgreSQL/Neo4j/ChromaDB로 한 바퀴를 돈다(mock 없음).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.api.schemas import AskRequest, MatchType
from app.services import retrieve_service as rs_module
from app.services.retrieve_service import RetrieveService
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode

_TODAY = date(2026, 8, 20)


def _hit(entity_id, name, *, entity_type=EntityType.COMPANY, evidence=()):
    return SearchHit(
        entity_type=entity_type, entity_id=entity_id, name=name,
        source_score=0.9, sources=["neo4j"], evidence=list(evidence),
    )


def _orchestrator(hits, *, mode=SearchMode.RELATIONSHIP):
    orch = MagicMock()
    query = SearchQuery(raw_query="q", normalized_query="q",
                        mode=mode, today=_TODAY)
    result = SearchResult(query="q", mode=mode, hits=list(hits),
                          total=len(hits), took_ms=1, cache_hit=False,
                          used_semantic_fallback=False)
    orch.search.return_value = (query, result)
    return orch


def _event(event_id, name, *, is_risk=False, evidence_ids=()):
    return {"event_id": event_id, "name": name, "event_type": "사고재해",
            "is_risk": is_risk, "role": "subject", "occurred_at": "2026-06-11",
            "article_count": 1, "timeline": [], "evidence_ids": list(evidence_ids)}


def _relation(edge_id, *, evidence_id=None):
    return {"edge_id": edge_id, "evidence_id": evidence_id, "type": "SUPPLIES_TO",
            "subtype": None,
            "source": {"key": "00301246", "name": "SFA반도체", "label": "Company"},
            "target": {"key": "00126380", "name": "삼성전자", "label": "Company"},
            "symmetric": False, "amount": None, "ratio": None,
            "freshness": "current", "last_seen": "2026-03-23", "valid_from": None,
            "valid_until": None, "score": 0.9, "corroboration": 1,
            "source_type": "dart", "refresh_cycle_days": 365, "days_since": 10,
            "days_until_refresh": 355, "exclusive": None, "other_counterparties": []}


@pytest.fixture
def stub_services(monkeypatch):
    """company_service·relation_service를 통제한다. 반환값은 테스트가 정한다."""
    company = MagicMock()
    company.events_of.return_value = []
    company.relations_of.return_value = []
    relation = MagicMock()
    relation.evidence_for_ids.return_value = []
    relation.event_impact.return_value = []
    monkeypatch.setattr(rs_module, "company_service", company)
    monkeypatch.setattr(rs_module, "relation_service", relation)
    return company, relation


# ── 검색 호출 계약 ──────────────────────────────────────────────────────

def test_question_becomes_the_search_query(stub_services):
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(AskRequest(question="삼성전자에 납품하는 기업"))

    sent = orch.search.call_args.args[0]
    assert sent.query == "삼성전자에 납품하는 기업"


def test_workspace_keys_reach_the_search_layer(stub_services):
    """★후필터가 아니다 — 범위를 검색 단계로 내려보낸다(설계서 §7)."""
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(
        AskRequest(question="q", workspace_keys=["00126380", "00164779"]))

    assert orch.search.call_args.args[0].workspace_keys == ["00126380", "00164779"]


def test_evidence_is_always_requested(stub_services):
    """인용이 목적이라 끄지 않는다."""
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert orch.search.call_args.args[0].include_evidence is True


def test_question_is_echoed_back(stub_services):
    got = RetrieveService(_orchestrator([])).retrieve(AskRequest(question="무슨 일?"))
    assert got.question == "무슨 일?"


# ── Company 만 추린다 (설계서 §9) ───────────────────────────────────────

def test_only_company_hits_become_companies(stub_services):
    orch = _orchestrator([
        _hit("00126380", "삼성전자"),
        _hit("김준성|1967-10", "김준성", entity_type=EntityType.PERSON),
        _hit("evt_abc", "삼성전자 압수수색", entity_type=EntityType.EVENT),
    ])
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [c.key for c in got.companies] == ["00126380"]


def test_non_company_hits_are_not_sent_to_company_lookups(stub_services):
    """★Person·Event를 기업 조회에 넣으면 **조용히 빈 결과**가 되어
    「사건이 없다」로 잘못 읽힌다."""
    company, _ = stub_services
    orch = _orchestrator([_hit("김준성|1967-10", "김준성", entity_type=EntityType.PERSON)])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    company.events_of.assert_not_called()
    company.relations_of.assert_not_called()


def test_duplicate_company_hits_are_collapsed(stub_services):
    orch = _orchestrator([_hit("00126380", "삼성전자"), _hit("00126380", "삼성전자")])
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert len(got.companies) == 1


def test_companies_are_capped_but_reported(stub_services, caplog):
    orch = _orchestrator([_hit(f"c{i}", f"기업{i}") for i in range(9)])
    with caplog.at_level("INFO"):
        got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.companies) == rs_module._MAX_COMPANIES
    assert "truncated" in caplog.text, "조용히 자르면 「그게 전부」로 읽힌다"


# ── 사건 ────────────────────────────────────────────────────────────────

def test_events_are_collected_per_company(stub_services):
    company, _ = stub_services
    company.events_of.return_value = [_event("evt_1", "화재")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    company.events_of.assert_called_once_with("00126380")
    assert [e.event_id for e in got.events] == ["evt_1"]


def test_same_event_from_two_companies_appears_once(stub_services):
    """★엣지가 아니라 **Event 노드 기준**으로 묶는다 — 같은 사건에 여러 기업이
    엮여 있으면 그대로 쌓을 경우 같은 사건을 두 번 말하게 된다."""
    company, _ = stub_services
    company.events_of.return_value = [_event("evt_1", "화재")]
    orch = _orchestrator([_hit("00126380", "삼성전자"), _hit("00164779", "SK하이닉스")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.events) == 1


# ── 파급 ────────────────────────────────────────────────────────────────

def test_propagation_runs_only_for_risk_events(stub_services):
    company, relation = stub_services
    company.events_of.return_value = [
        _event("evt_risk", "화재", is_risk=True), _event("evt_plain", "신제품")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.event_impact.assert_called_once_with("evt_risk")


def test_propagation_is_not_called_before_events_exist(stub_services):
    company, relation = stub_services
    company.events_of.return_value = []
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.event_impact.assert_not_called()


def test_missing_event_node_is_logged_not_silently_dropped(stub_services, caplog):
    company, relation = stub_services
    company.events_of.return_value = [_event("evt_risk", "화재", is_risk=True)]
    relation.event_impact.return_value = None
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    with caplog.at_level("WARNING"):
        got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert got.propagation == []
    assert "event_impact miss" in caplog.text


# ── 관계 ────────────────────────────────────────────────────────────────

def test_relations_keep_edge_id(stub_services):
    """★`edge_id`가 관계를 가리키는 유일한 id다. `evidence_id`로 대신할 수 없다."""
    company, _ = stub_services
    company.relations_of.return_value = [_relation("5:abc:1")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [r.edge_id for r in got.relations] == ["5:abc:1"]


def test_same_edge_from_two_companies_appears_once(stub_services):
    """관계의 양끝이 둘 다 결과에 있으면 같은 엣지를 두 번 읽게 된다."""
    company, _ = stub_services
    company.relations_of.return_value = [_relation("5:abc:1")]
    orch = _orchestrator([_hit("00301246", "SFA반도체"), _hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.relations) == 1


# ── 근거 ────────────────────────────────────────────────────────────────

def test_evidence_ids_are_fetched_in_one_batch(stub_services):
    """★관계마다 부르면 근거·언론사·공시제목 조회가 관계 수만큼 반복된다(§14·§26)."""
    company, relation = stub_services
    company.relations_of.return_value = [
        _relation("5:a:1", evidence_id="ev_1"), _relation("5:a:2", evidence_id="ev_2")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.evidence_for_ids.assert_called_once()


def test_evidence_ids_come_from_relations_events_and_hits(stub_services):
    """출처가 셋이다 — 어느 하나만 보면 인용할 문장이 줄어든다."""
    company, relation = stub_services
    company.relations_of.return_value = [_relation("5:a:1", evidence_id="ev_rel")]
    company.events_of.return_value = [_event("evt_1", "화재", evidence_ids=["ev_evt"])]
    orch = _orchestrator([_hit("00126380", "삼성전자",
                               evidence=[{"evidence_id": "ev_hit"}])])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    sent = relation.evidence_for_ids.call_args.args[0]
    assert {"ev_rel", "ev_evt", "ev_hit"} <= set(sent)


def test_duplicate_evidence_ids_are_collapsed(stub_services):
    """한 근거가 여러 관계를 뒷받침한다(엣지 11,060 : 근거 9,228)."""
    company, relation = stub_services
    company.relations_of.return_value = [
        _relation("5:a:1", evidence_id="ev_same"), _relation("5:a:2", evidence_id="ev_same")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    sent = relation.evidence_for_ids.call_args.args[0]
    assert len([i for i in sent if i == "ev_same"]) >= 1
    # 배치 함수가 중복을 제거한다 — 여기서 중복이 넘어가도 결과는 1건이다
    assert relation.evidence_for_ids.call_count == 1


def test_missing_evidence_is_kept_not_hidden(stub_services):
    """★못 꺼낸 근거를 **조용히 빼지 않는다.** 빼면 「근거가 없는 관계」로 읽힌다.
    다만 인용 가능한 source 로 쓰면 안 된다 — 그 판정은 추론 계층 몫이다."""
    company, relation = stub_services
    relation.evidence_for_ids.return_value = [
        {"evidence_id": "ev_gone", "text": "", "source_doc": "",
         "source_type": "news", "press": None, "published_at": None, "missing": True}]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [e.evidence_id for e in got.evidence] == ["ev_gone"]
    assert got.evidence[0].missing is True


# ── match_type ─────────────────────────────────────────────────────────

def test_match_type_is_exact_for_relationship_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.RELATIONSHIP)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_exact_for_name_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.NAME)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_semantic_for_semantic_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.SEMANTIC)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.SEMANTIC


# ══════════════════════════════════════════════════════════════════════
#  Tier B — 실제 저장소로 한 바퀴 (mock 없음)
# ══════════════════════════════════════════════════════════════════════

def test_real_retrieve_returns_material():
    got = RetrieveService().retrieve(AskRequest(question="삼성전자에 납품하는 기업"))

    assert got.companies
    assert got.relations
    assert all(r.edge_id for r in got.relations)
    assert got.evidence
    assert all(e.evidence_id for e in got.evidence)


def test_real_workspace_does_not_shrink_the_material():
    """★워크스페이스를 줘도 재료가 쪼그라들지 않는다(2026-08-20 정책 변경).

    워크스페이스는 순서를 정하는 문맥이지 필터가 아니다. 한때 양끝 모두를
    요구하는 hard filter 였을 때는 같은 질문이 재료를 크게 잃었다.
    """
    question = "삼성전자에 납품하는 기업"
    wide = RetrieveService().retrieve(AskRequest(question=question))
    scoped = RetrieveService().retrieve(
        AskRequest(question=question, workspace_keys=["00126380", "00301246"]))

    assert len(scoped.companies) == len(wide.companies)
    assert scoped.relations
