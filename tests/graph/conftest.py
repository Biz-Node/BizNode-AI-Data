"""`/ask` 그래프 테스트 공용 fixture.

★**DB 를 쓰지 않는다.** 노드가 부르는 것은 `RetrieveService` 의 메서드 몇 개와
  `workspace_service`·`query_understanding` 의 함수 둘이라, 그 자리에 대역을
  세우면 배선만 따로 볼 수 있다. 실 DB 대조는
  `batch/audit/ask_graph_parity.py` 의 몫이고 그쪽은 `needs_db` 마커가 붙는다.

★대역이 **진짜와 같은 타입**을 돌려주게 한다. dict 를 돌려주면 노드가 통과해도
  실제로는 pydantic 검증에서 터지는 배선을 「됐다」로 읽는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.api.schemas import (Anchor, AnchorSource, AskRequest, Event, Evidence,
                             Propagation, Relation, RelationEndpoint)
from app.services.query_understanding import AnchorDecision
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import SearchMode

_SAMSUNG = "00126380"
_HYNIX = "00164779"


@pytest.fixture
def endpoint():
    return RelationEndpoint(key=_SAMSUNG, name="삼성전자")


@pytest.fixture
def relation(endpoint):
    return Relation(edge_id="e1", type="SUPPLIES_TO", subtype="공급",
                    source=endpoint,
                    target=RelationEndpoint(key=_HYNIX, name="SK하이닉스"),
                    evidence_id="ev_rel", source_type="news")


@pytest.fixture
def event():
    return Event(event_id="evt_1", name="압수수색", event_type="규제수사",
                 is_risk=True, role="subject", occurred_at="2026-06-11",
                 evidence_ids=["ev_evt"])


@pytest.fixture
def evidence():
    return Evidence(evidence_id="ev_rel", text="원문", source_doc="20260101000001",
                    source_type="dart")


@pytest.fixture
def query():
    return SearchQuery(raw_query="q", normalized_query="q", mode=SearchMode.NAME,
                       today=date(2026, 8, 28))


@pytest.fixture
def result():
    return SearchResult(query="q", mode=SearchMode.NAME, hits=[], total=0,
                        took_ms=1, cache_hit=False, used_semantic_fallback=False)


@pytest.fixture
def decision(endpoint):
    return AnchorDecision(
        source=AnchorSource.QUERY,
        anchors=[Anchor(key=_SAMSUNG, name="삼성전자", source=AnchorSource.QUERY)],
        workspace_names={_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"})


@pytest.fixture
def request_():
    return AskRequest(question="삼성전자 압수수색", workspace_keys=[_SAMSUNG, _HYNIX])


class FakeRetrieveService:
    """노드가 부르는 메서드만 가진 대역. **무엇이 불렸는지 기록한다.**"""

    def __init__(self, *, query, result, events=(), propagation=(),
                 relations=(), evidence=()):
        self.calls: list[str] = []
        self._query, self._result = query, result
        self._events, self._propagation = list(events), list(propagation)
        self._relations, self._evidence = list(relations), list(evidence)

        outer = self

        class _Orchestrator:
            def search(self, request):
                outer.calls.append("search")
                return outer._query, outer._result

        self._orchestrator = _Orchestrator()

    def _events_of(self, companies, question, query, decision):
        self.calls.append("_events_of")
        return list(self._events)

    def _propagation_of(self, events):
        self.calls.append("_propagation_of")
        return list(self._propagation)

    def _relations_of(self, companies, workspace_keys, query, decision):
        self.calls.append("_relations_of")
        return list(self._relations)

    def _evidence_of(self, events, relations, result):
        self.calls.append("_evidence_of")
        return list(self._evidence)


@pytest.fixture
def fake_service(query, result, event, relation, evidence):
    return FakeRetrieveService(
        query=query, result=result, events=[event], relations=[relation],
        propagation=[Propagation(target="현대오토에버", key=None, score=0.3, hops=2,
                                 stated=False, path=[])],
        evidence=[evidence])


@pytest.fixture
def wired(monkeypatch, fake_service, decision):
    """그래프가 대역만 보게 묶는다. `(그래프, 대역)` 을 돌려준다."""
    from app.graph import ask_graph as ask_graph_module
    from app.graph.nodes import material

    monkeypatch.setattr(material, "_service", fake_service)
    monkeypatch.setattr(material.workspace_service, "names_of",
                        lambda keys: dict(decision.workspace_names))
    monkeypatch.setattr(material.query_understanding, "decide_anchor",
                        lambda question, resolved, names: decision)
    return ask_graph_module.build_ask_graph(), fake_service


class FakeLLM:
    """어댑터 자리. **예외를 던지지 않는다** — 진짜 어댑터와 같은 계약이다."""

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {
            "answer": "답변", "evidence_ids": ["ev_rel"], "claims": []}
        self.system = self.user = None
        self.calls = 0

    def structured(self, system, user, **kwargs):
        self.system, self.user = system, user
        self.calls += 1
        return dict(self.payload)


@pytest.fixture
def fake_llm(monkeypatch):
    from app.graph.nodes import answer

    llm = FakeLLM()
    monkeypatch.setattr(answer, "_llm", llm)
    return llm
