"""노드 사이의 State 전달 — **끊긴 자리를 잡는다.**

★이 파일이 있는 이유(현황서 §5-28). 노드는 자기가 채운 키만 돌려주고 다음
  노드가 그걸 읽는다. 키 이름 하나가 어긋나면 `KeyError` 로 죽는 게 아니라
  — `AskState` 가 `total=False` 라 — **없는 채로 흘러가** 엉뚱한 곳에서
  터지거나, 더 나쁘게는 빈 값으로 조용히 지나간다.

바이트 대조는 1차에서만 쓸 수 있었다. 표기가 붙으면 프롬프트가 달라지므로
1.5차부터는 **여기가 배선의 유일한 자동 그물**이다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource
from app.graph.ask_graph import _AFTER_LOOP
from app.graph.state import AskState

# 각 노드가 **채워야 하는** 키. 노드를 지난 뒤 이게 없으면 배선이 끊긴 것이다.
_PRODUCES = {
    # ★`match_type` 은 **`search` 가 만든다**(1.5차 정리). `result.mode` 만 보고
    #   정해지는 값이라 검색이 끝난 자리에서 확정된다 — 전에는 `fetch_evidence`
    #   가 만들었는데 그 노드는 근거를 모으는 자리다.
    "search": ("query", "result", "match_type"),
    # ★`plan_material` 이 **탐색 예산을 연다**(2차) — 카운터가 0 인 시점이
    #   Agent 앞 마지막 결정론 노드 하나로 고정된다.
    "plan_material": ("companies", "anchor_names", "intent",
                      "tool_calls_used", "events_used", "propagations_used",
                      "hops_used", "budget_exhausted"),
    # ── Agent 루프 (2차) — `fetch_events`·`fetch_relations`·`fetch_evidence`
    #    자리를 대신한다
    "agent": ("messages",),
    "run_tools": ("tool_results",),
    "evidence_validation": ("relations", "events", "evidence"),
    "fetch_propagation": ("propagation",),
    "build_prompt": ("user_prompt",),
    "generate": ("llm_result",),
    "verify_sources": ("answer", "failed", "sources"),
    # ★`check_claims` 는 **관측 전용**이라 아무것도 안 채운다. 여기 없는 것이 계약이다.
    "respond": ("response",),
}


def test_every_declared_key_is_reachable(wired, fake_llm, request_):
    """정상 경로를 한 번 돌면 `AskState` 의 키가 **전부** 채워진다.

    ★선언만 해 두고 아무도 안 채우는 키가 있으면 그건 죽은 계약이다.
    """
    graph, _ = wired
    state = graph.invoke({"request": request_})

    missing = [k for k in AskState.__annotations__ if k not in state]
    assert missing == [], f"아무도 채우지 않는 State 키: {missing}"


@pytest.mark.parametrize("node", sorted(_PRODUCES))
def test_node_fills_the_keys_the_next_node_reads(wired, fake_llm, request_, node):
    """노드마다 자기가 채워야 할 키를 실제로 채우는지 본다."""
    graph, _ = wired
    state = graph.invoke({"request": request_})

    for key in _PRODUCES[node]:
        assert key in state, f"{node} 가 {key} 를 안 채웠다"


def test_check_claims_changes_nothing(monkeypatch, wired, fake_llm, request_):
    """★`check_claims` 는 **State 를 바꾸지 않는다.** `_STRIP_UNLINKED_CLAIMS` 가
    `False` 라 답변을 건드리면 안 된다 — 관측만 한다."""
    from app.graph.nodes import answer

    fake_llm.payload = {"answer": "답변 문장", "evidence_ids": ["ev_rel"],
                        "claims": [{"text": "답변 문장", "evidence_ids": ["ev_rel"]}]}

    graph, _ = wired
    state = graph.invoke({"request": request_})

    got = answer.check_claims(state)
    assert got == {}, "관측 전용 노드가 State 조각을 돌려주면 안 된다"
    assert state["response"].answer == "답변 문장"


def test_sequence_matches_the_wired_nodes(wired):
    """`_SEQUENCE` 와 실제 배선이 어긋나지 않는지 — 순서가 곧 계약이다.

    ★파급은 **사건 뒤**여야 한다(설계서 §13). 2차에서는 사건을 `evidence_validation`
      이 합쳐 놓으므로 `fetch_propagation` 이 그 뒤에 온다 — 순서가 뒤집히면
      빈 입력으로 조회가 돌아 조용히 0건이 된다.
    """
    graph, _ = wired
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    for before, after in zip(_AFTER_LOOP, _AFTER_LOOP[1:]):
        assert (before, after) in edges, f"{before} → {after} 배선이 없다"
    assert (_AFTER_LOOP.index("evidence_validation")
            < _AFTER_LOOP.index("fetch_propagation")), "파급은 사건이 합쳐진 뒤다"


def test_the_agent_loop_is_wired_as_a_loop(wired):
    """★`agent ⇄ run_tools` 가 **양방향**이어야 한다. 한쪽만 있으면 도구를 한 번
    부르고 끝나거나(→ 재료 부족) 영영 안 끝난다."""
    graph, _ = wired
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    assert ("plan_material", "agent") in edges
    assert ("agent", "run_tools") in edges
    assert ("run_tools", "agent") in edges
    assert ("agent", "evidence_validation") in edges


def test_the_agent_is_never_reached_without_an_anchor(wired):
    """★`resolve_anchor` 는 **Agent 앞**에 있고 `UNRESOLVED` 면 `halt_no_material`
    로 빠진다. 이 순서가 「TSMC 를 물었는데 삼성전자로 답하는」 오답을 막는다."""
    graph, _ = wired
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    assert ("resolve_anchor", "halt_no_material") in edges
    assert ("resolve_anchor", "agent") not in edges, "앵커를 건너뛰고 Agent 로 갈 수 없다"


def test_anchor_source_survives_to_the_response(wired, fake_llm, request_):
    """★`anchor_source` 는 LLM 과 무관한 **서버가 아는 결정론적 값**이라
    노드를 다 지나서도 그대로여야 한다(설계서 §14-3)."""
    graph, _ = wired
    state = graph.invoke({"request": request_})

    assert state["response"].anchor_source is AnchorSource.QUERY
    assert state["response"].anchor_source is state["decision"].source


def test_llm_failure_still_produces_a_response(wired, fake_llm, request_):
    """★어댑터가 `failed` 를 붙여 오면 200 + 고정 문구다(설계서 §13-3).
    예외로 터지면 500 이 나가는데 그건 계약 위반이다."""
    fake_llm.payload = {"answer": "", "evidence_ids": [], "claims": [],
                        "failed": True, "reason": "LLM 호출 실패"}

    graph, _ = wired
    state = graph.invoke({"request": request_})

    assert state["failed"] is True
    assert state["response"].failed is True
    assert state["response"].answer.startswith("죄송합니다")
    # 실패해도 **근거는 보여준다** — missing 만 뺀 원본 전부.
    assert [s.evidence_id for s in state["response"].sources] == ["ev_rel"]


def test_hallucinated_evidence_id_is_dropped(wired, fake_llm, request_):
    """화이트리스트 검증(설계서 §13-2) — 재료 밖의 id 는 버린다."""
    fake_llm.payload = {"answer": "답변", "evidence_ids": ["ev_지어낸것"], "claims": []}

    graph, _ = wired
    state = graph.invoke({"request": request_})

    assert state["response"].sources == []
    assert state["response"].failed is False, "지어낸 id 는 LLM 실패가 아니다"
