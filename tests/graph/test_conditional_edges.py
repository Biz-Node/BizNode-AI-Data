"""조건부 엣지 — **재료 없이 끝내는 길은 하나뿐이다.**

★현황서 §5-28 이 적은 그물이다. 1차에서는 노드가 전부 위임 껍데기라 바이트
  대조가 배선까지 붙잡아 줬지만, 1.5차부터는 노드 안이 실제로 바뀌고 프롬프트도
  달라져 바이트 대조를 못 쓴다. **배선이 깨졌는지는 여기서 잡아야 한다.**

★**게이트가 둘에서 하나로 줄었다**(최종 설계 §17-1). 「빈 워크스페이스면 검색조차
  하지 않는다」가 사라졌다 — 워크스페이스는 검색 경계가 아니라 랭킹 문맥이다.
  그래서 이 파일이 지키는 것도 뒤집혔다:

      전  빈 워크스페이스면 **아무것도 안 한다**
      후  빈 워크스페이스여도 **검색하고 답한다**   ← 되돌아가면 여기서 걸린다

  남은 「안 함」은 하나다 — 대상을 못 찾으면(`unresolved`) 재료를 만들지 않는다.
  그것이 조용히 사라지면 「TSMC 를 물었는데 삼성전자로 답하는」 오답이 돌아온다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource, AskRequest
from app.graph.nodes.material import is_resolved
from app.services.query_understanding import AnchorDecision

_HALT = "halt_no_material"


# ══════════════════════════════════════════════════════════════════
#  판정 함수 자체
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", [AnchorSource.QUERY, AnchorSource.CONTEXT,
                                    AnchorSource.ANCHORLESS])
def test_anchor_states_that_still_build_material(source):
    """★`ANCHORLESS` 도 재료를 만든다 — **정상 질의**다(최종 설계 §8).

    앵커가 없다는 것은 「질문이 대상을 지정하지 않았다」이지 「실패」가 아니다.
    여기가 `_HALT` 로 가면 「최근 주요 투자 이벤트가 뭐야?」가 답을 못 받는다.
    """
    assert is_resolved({"decision": AnchorDecision(source=source)}) == "plan_material"


def test_unresolved_anchor_routes_to_halt():
    decision = AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC")
    assert is_resolved({"decision": decision}) == _HALT


def test_workspace_is_no_longer_a_gate():
    """★게이트 함수 자체가 없어졌다는 것을 **이름으로** 못 박는다.

    되살리려는 사람이 이 테스트를 먼저 만난다 — 지우고 되살리려면 최종 설계
    §17-1 을 다시 열어야 한다.
    """
    from app.graph import nodes
    from app.graph.nodes import material

    assert not hasattr(material, "has_workspace")
    assert not hasattr(material, "guard_workspace")
    assert "guard_workspace" not in nodes.__all__


# ══════════════════════════════════════════════════════════════════
#  그래프를 실제로 돌려 — **무엇을 하고 무엇을 안 하는가**
# ══════════════════════════════════════════════════════════════════

def test_empty_workspace_still_searches_and_answers(monkeypatch, wired, fake_llm):
    """★**뒤집힌 계약이다**(최종 설계 §6-1·§17-1).

    전에는 이 자리에서 「검색·조회가 하나도 일어나면 안 된다」를 지켰다. 지금은
    반대다 — 워크스페이스가 비어도 Global Search 를 하고 Global Ranking 으로
    답한다. Home 화면에서 아무것도 안 담고 던지는 질문이 이 경로다(시나리오 1).
    """
    from app.graph.nodes import material

    graph, service = wired
    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda q, r, n, c=None: AnchorDecision(source=AnchorSource.ANCHORLESS))

    state = graph.invoke({"request": AskRequest(question="최근 반도체 업계 이슈는?",
                                                workspace_keys=[])})

    assert service.calls[0] == "search", "워크스페이스가 비어도 검색은 한다"
    assert fake_llm.calls == 1, "답변까지 간다"
    assert state["response"].failed is False
    assert state["response"].anchor_source is AnchorSource.ANCHORLESS
    # ★옛 거절 문구가 되살아나면 여기서 걸린다.
    assert "담긴 기업이 없어" not in state["response"].answer


def test_anchorless_does_not_borrow_the_workspace_as_its_target(monkeypatch, wired,
                                                                fake_llm, request_):
    """★워크스페이스가 **있어도** 앵커로 승격되지 않는다(최종 설계 §17-3).

    담아 둔 기업이 있는 채로 대상을 지정하지 않은 질문을 던지면, 전에는
    `anchor_source=workspace` 로 그 기업들이 답변 대상이 됐다. 지금은
    `anchorless` 로 남고 워크스페이스는 **순서**에만 관여한다.
    """
    from app.graph.nodes import material

    graph, _service = wired
    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda q, r, n, c=None: AnchorDecision(
            source=AnchorSource.ANCHORLESS,
            workspace_names={"00126380": "삼성전자", "00164779": "SK하이닉스"}))

    state = graph.invoke({"request": request_})

    assert state["response"].anchor_source is AnchorSource.ANCHORLESS
    assert fake_llm.calls == 1
    # 「답변 대상」 줄이 워크스페이스를 대상으로 지목하지 않는다.
    assert "답변 대상: 지정 없음" in fake_llm.user
    assert "워크스페이스 기업들을 대상으로" not in fake_llm.user


def test_unresolved_searches_but_builds_no_material(monkeypatch, wired, fake_llm,
                                                    request_):
    """★검색은 한다 — 앵커 판정에 `resolved_entities` 가 필요하기 때문이다(①b 는
    ② 뒤다). 하지만 **조립은 하지 않는다.** 워크스페이스로 갈아타면 그게 곧
    「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이다(설계서 §14-4)."""
    from app.graph.nodes import material

    graph, service = wired
    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda q, r, n, c=None: AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names={"00126380": "삼성전자"}))

    state = graph.invoke({"request": request_})

    assert service.calls == ["search"], "검색만 하고 조립은 돌지 않아야 한다"
    assert fake_llm.calls == 0
    assert "TSMC" in state["response"].answer
    # 대안은 **제안까지만** — 그 기업들에 대해 답하지 않는다.
    assert "삼성전자" in state["response"].answer
    assert state["response"].sources == []
    # ★`failed=false` 다 — `failed` 는 「LLM 호출이 실패했다」는 뜻이고 여기서는
    #   애초에 안 불렀다(설계서 §14-4). 섞으면 화면이 「서버가 고장났다」와
    #   「그 기업을 못 찾았다」를 구별하지 못한다.
    assert state["response"].failed is False
    assert state["response"].anchor_source is AnchorSource.UNRESOLVED


def test_the_unresolved_message_is_deterministic(monkeypatch, wired, fake_llm,
                                                 request_):
    """★문구는 이름만으로 조립된다 — 같은 입력에 같은 문장이 나와야 한다."""
    from app.graph.nodes import material

    graph, _ = wired
    monkeypatch.setattr(
        material.query_understanding, "decide_anchor",
        lambda q, r, n, c=None: AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names={"00126380": "삼성전자"}))

    first = graph.invoke({"request": request_})["response"].answer
    second = graph.invoke({"request": request_})["response"].answer
    assert first == second


def test_resolved_path_runs_every_fetch(wired, fake_llm, request_):
    """정상 경로는 재료를 다 모은다 — **파급은 사건 뒤**여야 한다(설계서 §13).

    ★2차부터 **순서를 그래프가 정하지 않는다.** 어떤 도구를 어떤 순서로 부를지는
      Agent 가 고르므로(각본은 `FakeChat`), 여기서 볼 수 있는 계약은 「무엇이
      불렸나」와 「파급이 사건 뒤인가」뿐이다.
    """
    graph, service = wired
    graph.invoke({"request": request_})

    assert service.calls[0] == "search", "검색이 먼저다"
    assert set(service.calls) == {"search", "get_relations", "get_events",
                                  "get_propagation"}
    assert (service.calls.index("get_events")
            < service.calls.index("get_propagation")), "파급은 사건 뒤다"
    assert fake_llm.calls == 1
