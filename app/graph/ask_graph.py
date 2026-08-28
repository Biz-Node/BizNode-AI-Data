"""`/ask` 실행 그래프 — Agent 루프 + 조건부 엣지 셋.

    guard_workspace ─┬─▶ search ─▶ resolve_anchor ─┬─▶ plan_material
                     │                             │        ─▶ agent ⇄ run_tools
                     │                             │        ─▶ evidence_validation
                     │                             │        ─▶ fetch_propagation
                     │                             │        ─▶ build_prompt ─▶ generate
                     │                             │        ─▶ verify_sources
                     │                             │        ─▶ check_claims ─▶ respond ─▶ END
                     └─────────────┬───────────────┘
                                   ▼
                           halt_no_material ─▶ END

★**앵커 해소는 Agent 앞에 남는다.** `resolve_anchor` 가 `UNRESOLVED` 를 내면
  `halt_no_material` 로 빠져 **Agent 를 아예 부르지 않는다.** 이 순서가
  「TSMC 를 물었는데 삼성전자로 답하는 탐지 불가능한 오답」을 막는 장치다
  (설계서 §14-3) — 판정을 LLM 뒤로 옮기면 장치가 사라진다.

★**`fetch_propagation` 은 Agent 뒤에 남는다.** 계약 1번이 `get_propagation` 을
  「주어진 Event 의 파급을 계산하는 내부 primitive」로 못 박았기 때문에 도구로
  열지 않는다. 그렇다고 빼면 파급 재료가 통째로 사라지므로, **Agent 가 모은
  사건 위에서 결정론으로** 계산한다. 그래서 `evidence_validation` 뒤다 —
  거기서 사건이 합쳐진 뒤라야 파급이 중복 없이 계산된다.

★**`fetch_events`·`fetch_relations`·`fetch_evidence` 는 더 이상 배선되지 않는다.**
  앞의 둘은 `agent ⇄ run_tools` 가, 마지막은 `evidence_validation` 이 대신한다.
  함수는 `material.py` 에 남겨 뒀다 — 지우는 diff 를 이 변경에 섞으면 정작
  바뀐 것(실행 구조)이 안 보인다. 정리는 따로 한다.

★게이트는 **둘**이고, 2차에서 **Agent 루프 분기**가 하나 더 붙었다. `AnswerService.ask()` 에는 게이트가 셋 있었다:

    ① 워크스페이스가 비었나            → 여기 남는다 (조건부 엣지)
    ② 앵커를 못 찾았나                 → 여기 남는다 (조건부 엣지)
    ③ `retrieve_for_ask()` 가 `None` 을 줬나  → **사라진다**

  ③ 은 ② 와 **같은 판정을 결과로 되짚은 것**이었다. `retrieve_for_ask()` 가
  `UNRESOLVED` 를 보고 `None` 을 돌려주면, `ask()` 가 그 `None` 을 보고 다시
  같은 결론을 내렸다. 그래프에서는 판정이 난 자리에서 바로 갈라지므로 되짚을
  일이 없다 — **그래프화의 핵심 이득이다.**

★`agent ⇄ run_tools` **말고는 전부 순차**다. 병렬화는 아직 하지 않는다 —
  루프가 도는 횟수부터 재고 나서 볼 일이다.

★체크포인터를 붙이지 않는다. 한 요청이 한 번에 끝나고 중단·재개가 없다.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.nodes.material import has_workspace, is_resolved
from app.graph.state import AskState

# ★노드 이름은 **함수 이름과 같게** 둔다. 로그·`get_graph()` 출력·테스트가 전부
#   이 문자열을 쓰는데, 이름이 갈리면 어느 노드 얘기인지 대조해야 한다.
# Agent 루프가 끝난 **뒤**의 순차 구간. 루프 자체는 조건부 엣지로 돈다.
_AFTER_LOOP = [
    "evidence_validation",
    "fetch_propagation",
    "build_prompt",
    "generate",
    "verify_sources",
    "check_claims",
    "respond",
]


def build_ask_graph():
    """컴파일된 그래프를 만든다. **부작용 없음** — 부를 때마다 새로 만든다."""
    builder = StateGraph(AskState)

    builder.add_node("guard_workspace", nodes.guard_workspace)
    builder.add_node("search", nodes.search)
    builder.add_node("resolve_anchor", nodes.resolve_anchor)
    builder.add_node("halt_no_material", nodes.halt_no_material)
    builder.add_node("plan_material", nodes.plan_material)
    builder.add_node("agent", nodes.agent)
    builder.add_node("run_tools", nodes.run_tools)
    for name in _AFTER_LOOP:
        builder.add_node(name, getattr(nodes, name))

    builder.add_edge(START, "guard_workspace")

    # ── 조건부 ① — 워크스페이스가 비었으면 **검색도 하지 않는다** ──
    builder.add_conditional_edges("guard_workspace", has_workspace,
                                  {"search": "search",
                                   "halt_no_material": "halt_no_material"})
    builder.add_edge("search", "resolve_anchor")

    # ── 조건부 ② — 대상을 못 찾았으면 재료를 만들지 않는다 ──────
    builder.add_conditional_edges("resolve_anchor", is_resolved,
                                  {"plan_material": "plan_material",
                                   "halt_no_material": "halt_no_material"})

    # ── Agent 루프 — ★조건부 ③ ────────────────────────────────
    #   예산이 소진되면 도구 호출을 요청했어도 마감으로 보낸다(계약 4번).
    #   `recursion_limit` 에 기대면 예외로 끝나 답변이 아예 안 나간다.
    builder.add_edge("plan_material", "agent")
    builder.add_conditional_edges(
        "agent", nodes.should_continue,
        {"run_tools": "run_tools", "evidence_validation": "evidence_validation"})
    builder.add_edge("run_tools", "agent")

    for before, after in zip(_AFTER_LOOP, _AFTER_LOOP[1:]):
        builder.add_edge(before, after)
    builder.add_edge("respond", END)
    builder.add_edge("halt_no_material", END)

    return builder.compile()


@lru_cache(maxsize=1)
def ask_graph():
    """앱이 쓰는 단일 인스턴스. 컴파일은 한 번이면 된다."""
    return build_ask_graph()


def run_ask(request) -> "AskResponse":
    """질문 하나 → `AskResponse`. **`AnswerService.ask()` 를 대신하는 입구다.**

    ★동기다. 노드가 전부 sync 이고 그 안이 Neo4j·PostgreSQL·OpenAI 왕복이라
      이벤트루프에서 직접 부르면 안 된다 — 라우트가 `run_in_threadpool` 로 감싼다
      (`RetrieveService.retrieve_async()` 와 같은 이유).
    """
    from app.core.trace import new_trace_id
    from app.graph.state import final_response, initial_state

    # ★**여기가 요청 경계다.** 발급을 노드 안에서 하면 안 된다 — LangGraph 가
    #   노드마다 컨텍스트를 복사하므로 노드 안의 `set()` 은 그 노드에서 끝난다.
    #   바깥에서 한 번 세워 두면 복사본이 값을 물고 들어가 **모든 노드가 같은
    #   id 로** 찍힌다(`app/graph/nodes/material.py::search` 주석 참조).
    new_trace_id()
    state = ask_graph().invoke(initial_state(request))
    response = final_response(state)
    if response is None:
        # ★출구가 둘(`respond`·`halt_no_material`)뿐이고 둘 다 `response` 를
        #   채우므로 여기 오면 **그래프 배선이 깨진 것**이다. 빈 답을 지어내지
        #   않는다 — 조용한 오답보다 죽는 편이 낫다.
        raise RuntimeError("ask graph 가 response 없이 끝났다 — 배선 확인 필요")
    return response
