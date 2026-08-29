"""Agent 루프 — **Agent 가 못 고르는 것이 정말 못 고르게 되어 있는가.**

    고른다      어떤 도구를 · 어떤 순서로 · 몇 번
    못 고른다   대상 기업(앵커) · 자르는 기준 · 표기 · 무엇을 인용 가능한지 · 탐색 총량
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource, Evidence
from app.graph import budget
from app.graph.nodes import agent_loop
from app.tools import agent_tools, citation
from app.tools.dto import EventDTO, ROLE_NOTE

_SAMSUNG = "00126380"


def _call(name, **args):
    return {"name": name, "args": args, "id": f"c_{name}", "type": "tool_call"}


# ══════════════════════════════════════════════════════════════════
#  ★노출 경계 — 안 주는 것이 주는 것보다 중요하다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("forbidden", agent_tools.FORBIDDEN_TOOL_NAMES)
def test_contract_forbidden_tools_are_not_exposed(forbidden):
    """계약 1·2·3번 — 파급 계산 · 근거 수집 · 기업 검색은 Agent 도구가 아니다."""
    assert forbidden not in agent_tools.TOOL_NAMES
    assert forbidden not in {t.name for t in agent_tools.agent_tools()}


def test_exposed_tools_are_exactly_the_declared_seven():
    assert set(agent_tools.TOOL_NAMES) == {
        "get_relations", "get_events", "search_news", "search_dart",
        "get_business_overview", "get_market", "get_filings"}


@pytest.mark.parametrize("tool,banned", [
    ("get_events", "intent"),
    ("get_relations", "edge_types"),
    ("get_relations", "direction"),
    ("get_business_overview", "year"),
])
def test_scope_shaped_arguments_are_not_offered_to_the_agent(tool, banned):
    """★「무엇을 중요하게 볼지」를 LLM 이 정하면 그건 재료 범위를 고르는 것이다.
    서버가 `ToolContext` 에 실어 보낸다(4원칙 ①)."""
    bound = {t.name: t for t in agent_tools.agent_tools()}[tool]
    assert banned not in bound.args


# ══════════════════════════════════════════════════════════════════
#  ★총량 예산 — 반복 호출로 우회되지 않는가 (계약 4번)
# ══════════════════════════════════════════════════════════════════

def test_budget_counts_calls_cumulatively_not_per_argument_list():
    """★`get_events(keys=[A])` 를 열 번 부르면 상한이 열 배가 되면 안 된다."""
    state = budget.initial()
    for _ in range(budget.MAX_TOOL_CALLS):
        state = {**state, **budget.spend(state, tool_calls_used=1)}
    assert budget.is_exhausted(state)
    assert budget.remaining(state)["tool_calls_used"] == 0


def test_budget_flag_turns_on_only_when_a_cap_is_reached():
    state = budget.initial()
    state = {**state, **budget.spend(state, tool_calls_used=budget.MAX_TOOL_CALLS - 1)}
    assert not state["budget_exhausted"]
    state = {**state, **budget.spend(state, tool_calls_used=1)}
    assert state["budget_exhausted"]


def test_hops_budget_exists_but_nothing_spends_it_yet():
    """★자리를 미리 둔다. 그래프를 걸어 다니는 도구가 없어서(2-B) 0 으로 남는다 —
    0 이면 「아직 안 쓴다」이지 「상한이 없다」가 아니다."""
    assert budget.MAX_HOPS > 0
    assert budget.initial()["hops_used"] == 0


def test_exhausted_budget_sends_the_loop_to_the_finish_even_with_pending_calls():
    """★`recursion_limit` 에 기대면 예외로 끝나 답변이 아예 안 나간다.
    도구를 덜 불렀어도 **있는 재료로 답하게** 하는 것이 옳다."""
    class _Msg:
        tool_calls = [_call("get_events", keys=[_SAMSUNG])]

    state = {**budget.initial(), "messages": [_Msg()]}
    assert agent_loop.should_continue(state) == "run_tools"

    spent = budget.spend(state, tool_calls_used=budget.MAX_TOOL_CALLS)
    assert agent_loop.should_continue({**state, **spent}) == "evidence_validation"


def test_no_pending_calls_finishes_the_loop():
    class _Msg:
        tool_calls = []

    assert agent_loop.should_continue(
        {**budget.initial(), "messages": [_Msg()]}) == "evidence_validation"


# ══════════════════════════════════════════════════════════════════
#  ★마감 단계 — Agent 가 아니라 여기가 근거를 모은다 (계약 2번)
# ══════════════════════════════════════════════════════════════════

def _event(event_id, evidence_ids):
    return EventDTO(event_id=event_id, name="압수수색", event_type="규제수사",
                    is_risk=True, evidence_ids=list(evidence_ids),
                    role="subject", role_note=ROLE_NOTE["subject"])


def test_shared_events_keep_every_companys_evidence(monkeypatch, result):
    """★같은 Event 를 여러 기업이 공유한다(938건 중 85건). 건너뛰기만 하면 먼저
    온 기업의 근거만 남고 나머지가 조용히 사라진다 — `_merge_evidence_ids` 가
    이미 고쳐 둔 것과 **같은 규칙**이다."""
    seen: dict = {}

    def _fetch(ids):
        seen["ids"] = list(ids)
        return []

    monkeypatch.setattr(agent_loop.relation_service, "evidence_for_ids", _fetch)
    state = {"result": result,
             "tool_results": {"get_events": [_event("evt_1", ["ev_a"]),
                                             _event("evt_1", ["ev_b"])]}}
    got = agent_loop.evidence_validation(state)

    assert len(got["events"]) == 1, "같은 사건은 하나로 접힌다"
    assert got["events"][0].evidence_ids == ["ev_a", "ev_b"], "근거는 합친다"
    assert seen["ids"] == ["ev_a", "ev_b"]


def test_duplicate_evidence_ids_are_folded_once(monkeypatch, result):
    """★과거에 `fetch_texts` 가 중복 id 로 `DuplicateIDError` 를 내고 그걸 삼켜
    전건 판단불가가 된 사고가 있었다(2026-07-30). 지금은 `fetch_texts` 가 스스로
    접지만 **그 위에서 상한을 세는 코드는 여전히 중복을 두 건으로 센다.**"""
    seen: dict = {}
    monkeypatch.setattr(agent_loop.relation_service, "evidence_for_ids",
                        lambda ids: seen.setdefault("ids", list(ids)) and [])
    state = {"result": result,
             "tool_results": {"get_events": [_event("evt_1", ["ev_a", "ev_a"]),
                                             _event("evt_2", ["ev_a"])]}}
    agent_loop.evidence_validation(state)
    assert seen["ids"] == ["ev_a"]


def test_only_citable_tool_results_join_the_evidence_set(monkeypatch, result):
    """★인용 규칙은 `app/tools/citation.py` 가 정한다 — 마감 단계는 읽기만 한다."""
    class _Hit:
        def __init__(self, evidence_id):
            self.evidence_id = evidence_id

    seen: dict = {}
    monkeypatch.setattr(agent_loop.relation_service, "evidence_for_ids",
                        lambda ids: seen.setdefault("ids", list(ids)) and [])
    state = {"result": result, "tool_results": {
        "search_news": [_Hit("ev_news")],
        "search_dart": [_Hit("ev_dart")],          # 아직 인용 대상이 아니다
    }}
    agent_loop.evidence_validation(state)

    assert "ev_news" in seen["ids"]
    assert "ev_dart" not in seen["ids"], "인용 불가 도구의 근거는 안 들어간다"
    assert not citation.is_citable("search_dart")


def test_missing_evidence_is_kept_not_counted_as_a_lookup_failure(monkeypatch, result):
    """★엣지 11,060 대비 청크 10,510 — `evidence_id` 는 있는데 청크가 없는 엣지가
    약 5% 있다. **정상 상태이지 조회 실패가 아니다.**"""
    monkeypatch.setattr(
        agent_loop.relation_service, "evidence_for_ids",
        lambda ids: [Evidence(evidence_id="ev_a", text="", source_doc="",
                              source_type="news", missing=True).model_dump()])
    got = agent_loop.evidence_validation(
        {"result": result, "tool_results": {"get_events": [_event("e", ["ev_a"])]}})

    assert len(got["evidence"]) == 1, "숨기지 않는다"
    assert got["evidence"][0].missing is True


# ══════════════════════════════════════════════════════════════════
#  ★범위 — 거부는 재료를 늘리지 않는다
# ══════════════════════════════════════════════════════════════════

def test_out_of_scope_key_is_reported_to_the_agent_without_leaking_material():
    """★범위 밖 호출로 그래프가 죽으면 답변이 아예 안 나간다. Agent 가 읽고
    고칠 수 있게 문자열로 돌려주되, **재료로는 새지 않아야** 한다."""
    from app.tools import scope

    with scope.anchor_scope([_SAMSUNG]), agent_tools.collecting() as bucket:
        out = agent_tools.get_relations(["00164779"])      # 범위 밖

    assert "error" in out
    assert bucket == {}, "거부된 호출은 아무것도 남기지 않는다"


def test_unresolved_never_reaches_the_agent(wired, fake_llm, fake_chat, request_,
                                            monkeypatch, decision):
    """★`AskResponse.anchor_source` 는 「LLM 과 무관한 서버가 아는 결정론적 값」
    이라는 계약이다. `UNRESOLVED` 면 Agent 를 아예 부르지 않는다."""
    from app.graph.nodes import material
    from app.services.query_understanding import AnchorDecision

    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda question, resolved, names, context=None: AnchorDecision(
            source=AnchorSource.UNRESOLVED, anchors=[], named="TSMC",
            workspace_names=dict(decision.workspace_names)))

    graph, _ = wired
    state = graph.invoke({"request": request_})

    assert state["response"].anchor_source is AnchorSource.UNRESOLVED
    # ★`messages` **키**로는 못 본다 — `add_messages` 리듀서가 붙은 채널이라
    #   LangGraph 가 빈 리스트로 열어 둔다. 실제로 불렸는지는 호출 횟수다.
    assert fake_chat.calls == 0, "앵커를 못 찾았는데 Agent 가 불렸다"
    assert not state.get("messages"), "대화가 시작된 흔적이 없어야 한다"
