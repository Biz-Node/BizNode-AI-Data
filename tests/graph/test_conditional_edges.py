"""조건부 엣지 둘 — **재료 없이 끝내는 두 길.**

★현황서 §5-28 이 적은 그물이다. 1차에서는 노드가 전부 위임 껍데기라 바이트
  대조가 배선까지 붙잡아 줬지만, 1.5차부터는 노드 안이 실제로 바뀌고 프롬프트도
  달라져 바이트 대조를 못 쓴다. **배선이 깨졌는지는 여기서 잡아야 한다.**

이 파일이 지키는 것은 「어떤 답을 하는가」가 아니라 **「무엇을 하지 않는가」**다.
빈 워크스페이스면 검색조차 안 하고, 대상을 못 찾으면 재료를 만들지 않는다.
그 「안 함」이 조용히 사라지면 비용과 조용한 오답이 같이 늘어난다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource, AskRequest
from app.graph.nodes.material import has_workspace, is_resolved
from app.services.query_understanding import AnchorDecision

_HALT = "halt_no_material"


# ══════════════════════════════════════════════════════════════════
#  판정 함수 자체
# ══════════════════════════════════════════════════════════════════

def test_empty_workspace_routes_to_halt(request_):
    empty = AskRequest(question=request_.question, workspace_keys=[])
    assert has_workspace({"request": empty}) == _HALT


def test_workspace_present_routes_to_search(request_):
    assert has_workspace({"request": request_}) == "search"


@pytest.mark.parametrize("source", [AnchorSource.QUERY, AnchorSource.WORKSPACE])
def test_resolved_anchor_routes_to_plan_material(source):
    assert is_resolved({"decision": AnchorDecision(source=source)}) == "plan_material"


def test_unresolved_anchor_routes_to_halt():
    decision = AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC")
    assert is_resolved({"decision": decision}) == _HALT


# ══════════════════════════════════════════════════════════════════
#  그래프를 실제로 돌려 — **안 한 일**을 확인한다
# ══════════════════════════════════════════════════════════════════

def test_empty_workspace_does_not_even_search(wired, fake_llm, request_):
    """★검색조차 하지 않는다(설계서 §16-2). 재료를 모을 출발점이 없다."""
    graph, service = wired
    state = graph.invoke({"request": AskRequest(question="q", workspace_keys=[])})

    assert service.calls == [], "검색·조회가 하나도 일어나면 안 된다"
    assert fake_llm.calls == 0, "LLM 을 부르면 안 된다"
    assert state["response"].answer == "이 워크스페이스에 담긴 기업이 없어 답변할 수 없습니다."
    # ★`failed` 는 「LLM 호출이 실패했다」는 뜻이다. 안 불렀으므로 실패가 아니다.
    assert state["response"].failed is False
    assert state["response"].anchor_source is AnchorSource.UNRESOLVED


def test_unresolved_searches_but_builds_no_material(monkeypatch, wired, fake_llm,
                                                    request_):
    """★검색은 한다 — 앵커 판정에 `resolved_entities` 가 필요하기 때문이다(①b 는
    ② 뒤다). 하지만 **조립은 하지 않는다.** 워크스페이스로 갈아타면 그게 곧
    「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이다(설계서 §14-4)."""
    from app.graph.nodes import material

    graph, service = wired
    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda q, r, n: AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names={"00126380": "삼성전자"}))

    state = graph.invoke({"request": request_})

    assert service.calls == ["search"], "검색만 하고 조립 넷은 돌지 않아야 한다"
    assert fake_llm.calls == 0
    assert "TSMC" in state["response"].answer
    # 대안은 **제안까지만** — 그 기업들에 대해 답하지 않는다.
    assert "삼성전자" in state["response"].answer
    assert state["response"].sources == []


def test_resolved_path_runs_every_fetch(wired, fake_llm, request_):
    """정상 경로는 조립 넷을 **순서대로** 다 돈다 — 파급은 사건 뒤여야 한다."""
    graph, service = wired
    graph.invoke({"request": request_})

    assert service.calls == ["search", "_events_of", "_propagation_of",
                             "_relations_of", "_evidence_of"]
    assert fake_llm.calls == 1
