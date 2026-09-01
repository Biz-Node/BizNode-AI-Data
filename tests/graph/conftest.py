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

from app.api.schemas import (Anchor, AnchorSource, AskRequest, Evidence,
                             RelationEndpoint)
from app.services.query_understanding import AnchorDecision
from app.tools.dto import (DIRECTION_NOTE, ROLE_NOTE, SOURCE_NOTE, STATED_NOTE,
                           EventDTO, PropagationDTO, RelationDTO)
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import SearchMode

_SAMSUNG = "00126380"
_HYNIX = "00164779"


@pytest.fixture
def endpoint():
    return RelationEndpoint(key=_SAMSUNG, name="삼성전자")


@pytest.fixture
def relation():
    """★1.5차부터 **도구가 만든 DTO** 다 — API 스키마 `Relation` 이 아니다."""
    return RelationDTO(
        edge_id="e1", source="삼성전자", target="SK하이닉스",
        source_key=_SAMSUNG, target_key=_HYNIX,
        edge_type="SUPPLIES_TO", subtype="공급", evidence_id="ev_rel",
        source_type="news", source_note=SOURCE_NOTE["news"],
        direction="directed", direction_note=DIRECTION_NOTE["directed"],
        freshness="current", effective_confidence=0.9)


@pytest.fixture
def event():
    return EventDTO(event_id="evt_1", name="압수수색", event_type="규제수사",
                    is_risk=True, evidence_ids=["ev_evt"], role="subject",
                    role_note=ROLE_NOTE["subject"], occurred_at="2026-06-11")


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


# ══════════════════════════════════════════════════════════════════
#  그래프 Company 대장 — 이 파일이 대역을 안 세웠던 마지막 한 자리
# ══════════════════════════════════════════════════════════════════

_GRAPH_COMPANIES = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


@pytest.fixture
def graph_companies():
    """**그래프에 실제로 있는** Company 대장. 여기 없는 key 는 그래프에 없다.

    ★`company_service.names_by_keys()` 와 **같은 계약**이다 — 못 찾은 key 는
      돌려주지 않는다. 그래야 「해소됐다 ≠ 그래프에 있다」를 가리는
      `_with_anchor_backstop()` 의 존재 확인이 대역에서도 그대로 돈다.
    """
    return dict(_GRAPH_COMPANIES)


@pytest.fixture(autouse=True)
def no_graph_db(request, monkeypatch, graph_companies):
    """★맨 위 선언(**DB 를 쓰지 않는다**)을 **실제로 지키게** 한다.

    `plan_material` 은 `companies` 가 비면 `_with_anchor_backstop()` 을 타고,
    거기서 `company_service.names_by_keys()` → Neo4j 가 열린다. 대역이 없어
    **DB 가 떠 있는 환경에서만 조용히 통과**하던 자리다 — 재료가 빈 경로는
    드물지 않다(검색 히트가 0 이거나 상대가 Person·Organization·Event 일 때
    늘 여기로 온다).

    ★`needs_db` 는 건드리지 않는다 — `test_parity.py` 는 **진짜 그래프**와
      대조하는 것이 목적이라 대역을 세우면 대조 자체가 성립하지 않는다.
    """
    if request.node.get_closest_marker("needs_db"):
        return
    from app.services import company_service

    monkeypatch.setattr(
        company_service, "names_by_keys",
        lambda keys: {k: graph_companies[k] for k in keys if k in graph_companies})


class FakeRetrieveService:
    """검색만 담당하는 대역 — 조회는 이제 **도구**가 한다(1.5차)."""

    def __init__(self, *, query, result, calls):
        self._query, self._result = query, result
        outer = self

        class _Orchestrator:
            def search(self, request):
                calls.append("search")
                return outer._query, outer._result

        self._orchestrator = _Orchestrator()


class FakeTools:
    """도구 자리의 대역. **무엇이 불렸는지 기록한다.**

    ★1.5차에서 이음매가 `RetrieveService._events_of` 등에서 **도구**로 옮겨졌다.
      이 파일이 보는 것은 예나 지금이나 「노드가 순서대로 위임하는가」 하나다.
    """

    def __init__(self, *, events, relations, propagation, evidence, calls):
        self.events, self.relations = list(events), list(relations)
        self.propagation, self.evidence = list(propagation), list(evidence)
        self.calls = calls
        self.scopes: list[frozenset] = []

    def _note_scope(self):
        from app.tools import scope

        self.scopes.append(scope.allowed_keys())

    def get_events(self, keys, intent):
        self.calls.append("get_events")
        self._note_scope()
        return list(self.events)

    def get_relations(self, keys, edge_types=None, direction=None):
        self.calls.append("get_relations")
        self._note_scope()
        return list(self.relations)

    def get_propagation(self, event_ids):
        self.calls.append("get_propagation")
        return list(self.propagation)


class FakeChat:
    """Agent 자리의 대역 — **무엇을 부를지 미리 정해 둔 각본**대로 답한다.

    ★진짜 모델을 쓰면 테스트가 LLM 의 기분에 걸린다. 여기서 보려는 것은
      「루프가 도는가 · 예산이 자르는가 · 결과가 State 로 옮겨지는가」다.
    """

    def __init__(self, plans=None):
        # 각 턴에 낼 tool_calls 목록. 다 쓰면 빈 손으로 답해 루프가 끝난다.
        self.plans = [list(p) for p in (plans if plans is not None else [[
            {"name": "get_relations", "args": {"keys": [_SAMSUNG]},
             "id": "c1", "type": "tool_call"},
            {"name": "get_events", "args": {"keys": [_SAMSUNG]},
             "id": "c2", "type": "tool_call"},
        ]])]
        self.calls = 0
        self.seen: list = []

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        self.calls += 1
        self.seen.append(list(messages))
        calls = self.plans.pop(0) if self.plans else []
        return AIMessage(content="" if calls else "재료를 다 모았습니다",
                         tool_calls=calls)


@pytest.fixture
def fake_chat(monkeypatch):
    """기본 각본 — 한 바퀴 돌고 끝낸다."""
    from app.graph.nodes import agent_loop as agent_node

    chat = FakeChat()
    monkeypatch.setattr(agent_node, "_chat", chat)
    # ToolNode 는 진짜를 쓴다 — 도구 바인딩·스코프 전달까지 함께 보려는 것이다
    monkeypatch.setattr(agent_node, "_TOOL_NODE", None)
    return chat


@pytest.fixture
def wired(monkeypatch, query, result, event, relation, evidence, decision, fake_chat):
    """그래프가 대역만 보게 묶는다. `(그래프, 대역)` 을 돌려준다."""
    from app.graph import ask_graph as ask_graph_module
    from app.graph.nodes import material

    calls: list[str] = []
    tools = FakeTools(
        events=[event], relations=[relation],
        propagation=[PropagationDTO(event_id="evt_1", target="현대오토에버", key=None,
                                    score=0.3, hops=2, stated=False,
                                    stated_note=STATED_NOTE[False], path=["a", "b"])],
        evidence=[evidence], calls=calls)
    tools.calls = calls

    monkeypatch.setattr(material, "_service",
                        FakeRetrieveService(query=query, result=result, calls=calls))
    monkeypatch.setattr(material.workspace_service, "names_of",
                        lambda keys: dict(decision.workspace_names))
    monkeypatch.setattr(material.query_understanding, "decide_anchor",
                        lambda question, resolved, names, context=None: decision)
    for name in ("get_events", "get_relations", "get_propagation"):
        monkeypatch.setattr(material.graph_tools, name, getattr(tools, name))
    # ★근거 조회 대역은 **`agent_loop` 쪽 하나뿐**이다. `material.fetch_evidence`
    #   가 지워지면서(최종 설계 §17-1 정리) `material` 은 `relation_service` 를
    #   더 이상 import 하지 않는다 — 근거를 모으는 것은 `evidence_validation` 이다.
    from app.graph.nodes import agent_loop as agent_node

    monkeypatch.setattr(agent_node.relation_service, "evidence_for_ids",
                        lambda ids: [e.model_dump() for e in tools.evidence])
    return ask_graph_module.build_ask_graph(), tools


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
