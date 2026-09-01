"""앵커 Company 백스톱 — 현황서 §5-16.

관계 상대가 Company 가 아닌 질의에서 재료가 통째로 0 이 됐다.

    「삼성전자 임원」        IS_EXECUTIVE_OF  히트 = Person ×10        → companies = []
    「삼성전자를 규제한 기관」  REGULATES        히트 = Organization ×10  → companies = []
    「삼성전자 기술 유출 사건」 HAS_EVENT        히트 = Event ×10         → companies = []

`companies` 가 비면 `events_of`·`relations_of` 가 돌 대상이 없어 **사건·관계·근거가
전부 0** 이 된다. 41건 코퍼스에서 **5건(12%)** 이 이 경로였다.

★**`companies` 는 여전히 Company 만 담는다.** Person·Organization·Event 를 거기
  넣지 않는다 — 그건 조용히 빈 결과가 되어 「사건이 없다」로 잘못 읽힌다(설계서 §9).
  넣는 것은 **앵커 기업 자신**뿐이고, 상대 노드는 `relations`·`events` 로 나간다.

★**그래프에 있는 앵커만 넣는다.** `corp_code_master` 118,535건 대 그래프 Company
  3,432건이라 「해소됐다 ≠ 그래프에 있다」다. 없는 key 를 넣으면 `companies` 에
  **팬텀 항목**만 남고 재료는 안 생긴다.

★**의도 분류는 이번에 안 넣는다.** 「관계 상대가 답인가, 앵커 자신이 답인가」를
  가르는 규칙은 별도 `[TODO]` 다 — 여기서는 앵커를 **잃지 않는 것**까지만 한다.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.api.schemas import Anchor, AnchorSource, AskRequest
from app.services import retrieve_service as rs_module
from app.services.query_understanding import AnchorDecision
from app.services.retrieve_service import RetrieveService
from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_WS = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def _hit(entity_id, name, entity_type=EntityType.COMPANY):
    return SearchHit(entity_type=entity_type, entity_id=entity_id, name=name,
                     source_score=1.0, sources=["neo4j"])


def _orchestrator(hits, *, mode=SearchMode.RELATIONSHIP, resolved=True):
    entities = [Resolution(corp_code=_SAMSUNG, corp_name="삼성전자", stock_code=None,
                           method="exact", score=1.0)] if resolved else []
    query = SearchQuery(raw_query="q", normalized_query="q", mode=mode,
                        today=date(2026, 8, 26), resolved_entities=entities)
    result = SearchResult(query="q", mode=mode, hits=list(hits), total=len(hits),
                          took_ms=1, cache_hit=False, used_semantic_fallback=False)
    orchestrator = MagicMock()
    orchestrator.search.return_value = (query, result)
    return orchestrator


def _query_decision(key=_SAMSUNG, name="삼성전자"):
    return AnchorDecision(source=AnchorSource.QUERY, workspace_names=_WS,
                          anchors=[Anchor(key=key, name=name, source=AnchorSource.QUERY)])


@pytest.fixture
def wired(monkeypatch):
    state = {"decision": _query_decision(), "graph_companies": {_SAMSUNG: "삼성전자",
                                                               _HYNIX: "SK하이닉스"},
             "events": {}, "relations": {}}
    monkeypatch.setattr(rs_module.workspace_service, "names_of", lambda keys: _WS)
    monkeypatch.setattr(rs_module.query_understanding, "decide_anchor",
                        lambda *a, **k: state["decision"])
    monkeypatch.setattr(rs_module.company_service, "names_by_keys",
                        lambda keys: {k: state["graph_companies"][k] for k in keys
                                      if k in state["graph_companies"]})
    monkeypatch.setattr(rs_module.company_service, "events_of",
                        lambda key: state["events"].get(key, []))
    monkeypatch.setattr(rs_module.company_service, "relations_of",
                        lambda key, **kw: state["relations"].get(key, []))
    monkeypatch.setattr(rs_module.relation_service, "evidence_for_ids", lambda ids: [])
    return state


def _request():
    return AskRequest(question="삼성전자 임원", workspace_keys=[_SAMSUNG, _HYNIX])


# ══════════════════════════════════════════════════════════════════════
#  §5-16 — 상대가 Company 가 아니어도 앵커는 남는다
# ══════════════════════════════════════════════════════════════════════

def test_person_counterparties_no_longer_empty_the_material(wired):
    """「삼성전자 임원」 — 히트가 전부 Person 이라 companies 가 비었었다."""
    hits = [_hit("p_1", "이재용", EntityType.PERSON),
            _hit("p_2", "한종희", EntityType.PERSON)]
    _, retrieved = RetrieveService(_orchestrator(hits)).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG]


def test_organization_counterparties_no_longer_empty_the_material(wired):
    """「삼성전자를 규제한 기관」 — 히트가 전부 Organization."""
    hits = [_hit("o_1", "공정거래위원회", EntityType.ORGANIZATION)]
    _, retrieved = RetrieveService(_orchestrator(hits)).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG]


def test_event_counterparties_no_longer_empty_the_material(wired):
    """「삼성전자 기술 유출 사건」 — 히트가 전부 Event."""
    hits = [_hit("evt_1", "기술 유출", EntityType.EVENT)]
    _, retrieved = RetrieveService(_orchestrator(hits)).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG]


def test_backstop_makes_the_anchor_produce_material(wired):
    """★백스톱의 목적은 목록에 이름을 넣는 것이 아니라 **재료를 되살리는 것**이다."""
    wired["events"] = {_SAMSUNG: [{"event_id": "evt_1", "name": "기술 유출",
                                   "event_type": "정보유출", "is_risk": True,
                                   "role": "subject", "occurred_at": "2026-01-01",
                                   "evidence_ids": ["ev_a"]}]}
    _, retrieved = RetrieveService(
        _orchestrator([_hit("evt_1", "기술 유출", EntityType.EVENT)])).retrieve_for_ask(
        _request())
    assert [e.event_id for e in retrieved.events] == ["evt_1"]


# ══════════════════════════════════════════════════════════════════════
#  경계 — 넣지 말아야 할 것은 안 넣는다
# ══════════════════════════════════════════════════════════════════════

def test_companies_still_holds_only_company_nodes(wired):
    """★Person·Organization·Event 는 **여전히 안 들어간다**(설계서 §9)."""
    hits = [_hit("p_1", "이재용", EntityType.PERSON),
            _hit("o_1", "공정거래위원회", EntityType.ORGANIZATION)]
    _, retrieved = RetrieveService(_orchestrator(hits)).retrieve_for_ask(_request())
    assert [c.name for c in retrieved.companies] == ["삼성전자"]


def test_anchor_absent_from_the_graph_is_not_added(wired):
    """★해소됐다 ≠ 그래프에 있다. 없는 앵커를 넣으면 `companies` 에 **팬텀 항목**만
    남고 재료는 안 생긴다 — 「재료가 된 기업」이라는 뜻이 거짓이 된다."""
    wired["decision"] = _query_decision(key="00999999", name="그래프에없는회사")
    _, retrieved = RetrieveService(
        _orchestrator([_hit("p_1", "누군가", EntityType.PERSON)])).retrieve_for_ask(
        _request())
    assert retrieved.companies == []


def test_backstop_does_not_fire_when_material_already_exists(wired):
    """★**재료가 있으면 끼어들지 않는다.** 백스톱은 「아무것도 안 남는 것」을 막는
    안전망이지 앵커를 늘 앞에 세우는 규칙이 아니다.

    실측(2026-08-26 · 41건)으로 늘 넣게 하면 **13건**의 재료 구성이 바뀐다.
    「삼성전자에 납품하는 기업」에서 앵커를 앞에 넣으면 `_MAX_COMPANIES=5` 때문에
    **공급사 한 곳이 밀려난다** — 질문이 물은 것이 바로 그 공급사인데.
    그 교환을 정하려면 「관계 상대가 답인가, 앵커가 답인가」를 갈라야 하고
    그건 별도 `[TODO]` 다(현황서 §5-16).
    """
    hits = [_hit("00301246", "SFA반도체"), _hit(_HYNIX, "SK하이닉스")]
    _, retrieved = RetrieveService(_orchestrator(hits)).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == ["00301246", _HYNIX]


def test_backstop_never_exceeds_the_company_cap(wired):
    """★상한을 넘기지 않는다. 백스톱은 `companies` 가 비었을 때만 도므로 넣는
    것도 `_MAX_COMPANIES` 안에서다."""
    many = {f"0000000{i}": f"기업{i}" for i in range(8)}
    wired["graph_companies"] = {**wired["graph_companies"], **many}
    wired["decision"] = AnchorDecision(
        source=AnchorSource.QUERY, workspace_names=_WS,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.QUERY)
                 for k, n in many.items()])
    _, retrieved = RetrieveService(
        _orchestrator([_hit("p_1", "누군가", EntityType.PERSON)])).retrieve_for_ask(
        _request())
    assert len(retrieved.companies) <= rs_module._MAX_COMPANIES


def test_context_anchor_path_is_untouched(wired):
    """★워크스페이스 경로는 이미 앵커가 곧 companies 다 — 백스톱이 끼어들 자리가 없다."""
    wired["decision"] = AnchorDecision(
        source=AnchorSource.CONTEXT, workspace_names=_WS,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.CONTEXT)
                 for k, n in _WS.items()])
    _, retrieved = RetrieveService(
        _orchestrator([_hit("x", "무관")], resolved=False)).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG, _HYNIX]


def test_unresolved_gets_no_backstop(wired):
    """★못 찾은 대상에는 앵커가 없다 — 넣을 것도 없고, 애초에 재료를 안 만든다."""
    wired["decision"] = AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names=_WS)
    decision, retrieved = RetrieveService(
        _orchestrator([_hit("p_1", "누군가", EntityType.PERSON)])).retrieve_for_ask(
        _request())
    assert retrieved is None


def test_backstop_is_logged(wired, caplog):
    """★**조용히 넣지 않는다** — 재료 구성이 바뀐 것은 로그에 남아야 한다."""
    with caplog.at_level("INFO"):
        RetrieveService(
            _orchestrator([_hit("p_1", "누군가", EntityType.PERSON)])).retrieve_for_ask(
            _request())
    assert "anchor.backstop" in caplog.text
