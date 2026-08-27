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
from app.graph.ask_graph import _SEQUENCE
from app.graph.state import AskState

# 각 노드가 **채워야 하는** 키. 노드를 지난 뒤 이게 없으면 배선이 끊긴 것이다.
_PRODUCES = {
    "plan_material": ("use_hits", "companies", "backstop", "anchor_names", "intent"),
    "fetch_events": ("events",),
    "fetch_propagation": ("propagation",),
    "fetch_relations": ("relations",),
    "fetch_evidence": ("evidence", "match_type"),
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

    ★파급은 **사건 뒤**여야 하고(설계서 §13) 근거는 관계·사건 뒤여야 한다.
      순서가 뒤집히면 빈 입력으로 조회가 돌아 조용히 0건이 된다.
    """
    graph, _ = wired
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    for before, after in zip(_SEQUENCE, _SEQUENCE[1:]):
        assert (before, after) in edges, f"{before} → {after} 배선이 없다"
    assert _SEQUENCE.index("fetch_events") < _SEQUENCE.index("fetch_propagation")
    assert _SEQUENCE.index("fetch_relations") < _SEQUENCE.index("fetch_evidence")


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
