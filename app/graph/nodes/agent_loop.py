"""Agent 루프 — **무엇을 고르고 무엇을 못 고르는가.**

★파일 이름이 `agent_loop` 인 이유 — `nodes/__init__` 이 `agent` **함수**를
  다시 내보내므로, 모듈을 `agent.py` 로 두면 `from app.graph.nodes import agent`
  가 **함수를 집어 온다.** 테스트가 모듈을 patch 하려다 함수를 붙잡는다.

    plan_material ─▶ agent ⇄ run_tools ─▶ evidence_validation ─▶ …

Agent 가 고르는 것: 어떤 도구를 · 어떤 순서로 · 몇 번.
Agent 가 못 고르는 것:

    대상 기업(앵커)     `resolve_anchor` 가 **Agent 앞에서** 결정론으로 정한다.
                        `decision.source == UNRESOLVED` 면 **Agent 를 안 부른다**
    자르는 기준          도구 내부 상수(4원칙 ③)
    표기                 DTO 가 붙인다(4원칙 ②)
    무엇을 인용 가능한지  `app/tools/citation.py` 가 정한다
    탐색 총량            `app/graph/budget.py` 가 **누적치로** 센다(계약 4)

★**앵커 해소를 Agent 안으로 넣지 않는다.** `AskResponse.anchor_source` 는
  「LLM 과 무관한 서버가 아는 결정론적 값」이라고 스키마가 못 박았고, `unresolved`
  일 때 워크스페이스로 갈아타지 않는 규칙이 **「TSMC 를 물었는데 삼성전자로
  답하는 탐지 불가능한 오답」**을 막는 핵심 장치다. 그 판정을 LLM 이 하면
  장치가 사라진다.

★**근거는 Agent 가 고르지 않는다**(계약 2). `evidence_validation` 이 탐색 결과의
  `evidence_id` 합집합을 결정론적으로 모은다. Agent 는 어떤 도구를 부를지만
  정하고, 그 결과에서 무엇이 근거가 되는지는 서버가 정한다.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from app.api.schemas import Evidence
from app.core import observe
from app.core.trace import trace_logger
from app.graph import budget
from app.graph.state import AskState
from app.services import relation_service
from app.tools import agent_tools, citation, scope
from app.tools.dto import EventDTO, RelationDTO

log = trace_logger(__name__)

_MAX_LOGGED_EVIDENCE = 12

# 예산이 막아 실행하지 않은 호출에 돌려주는 문구. ★도구가 거부할 때와 **같은
# 모양**(`{"error": ...}`)이라, Agent 가 이미 아는 형식으로 읽는다.
_BUDGET_DENIED = "탐색 총량 상한에 닿았다 — 이번 요청에서 도구를 더 부를 수 없다"

_AGENT_SYSTEM = """
당신은 기업 리스크 질문에 답할 **재료를 모으는** 단계입니다.
답변을 쓰지 않습니다. 필요한 도구를 골라 부르기만 합니다.

주어진 기업 key 목록 밖의 key 를 넘기지 않습니다.
범위 밖 key 는 거부되고, 거부는 재료를 늘리지 않습니다.

같은 도구를 같은 인자로 다시 부르지 않습니다.
호출 횟수에는 총량 상한이 있고, 소진되면 남은 재료로 답하게 됩니다.

충분한 재료를 모았다고 판단하면 도구를 더 부르지 않고 끝냅니다.
""".strip()


# ★모듈 전역 — **테스트가 갈아끼우는 이음매**다(`answer.bind_llm` 과 같은 규약).
_chat: Optional[Any] = None


def bind_chat(chat: Any) -> None:
    """도구를 물린 chat 모델을 갈아끼운다. 테스트·운영이 같은 이음매를 쓴다."""
    global _chat
    _chat = chat


def _model():
    """도구를 물린 chat 모델. **지연 생성** — import 시점에 키가 없어도 뜬다.

    ★**모델을 `config.AGENT_MODEL` 에서 읽는다**(2026-08-29). 전에는
      `adapter.DEFAULT_MODEL` 을 집어 왔는데, 그건 답변 생성이 쓰는 것과 **같은
      상수**였다 — 「Agent 만 바꿔서 도구 선택 분산을 본다」가 구조적으로 안
      됐다는 뜻이다. 노브가 하나면 점수 차이를 Agent 에 귀속시킬지 답변에
      귀속시킬지 가를 수가 없다.

    ★`temperature` 도 config 가 정한다. 0 이 규약이지만 **0 을 거부하는 모델이
      있어**(gpt-5.6 계열) 비울 수 있어야 한다 — 까닭은 `config` 에 적어 뒀다.
    """
    global _chat
    if _chat is None:
        from langchain_openai import ChatOpenAI

        from app.core import config

        _chat = ChatOpenAI(
            model=config.AGENT_MODEL, api_key=config.OPENAI_API_KEY,
            **config.temperature_kwargs(config.AGENT_TEMPERATURE),
            # ★추론 세기와 전송 경로를 **함께** 받는다 — chat.completions 는
            #   function tools 와 추론을 같이 못 쓴다(까닭은 `config` 에).
            **config.reasoning_kwargs(config.AGENT_REASONING_EFFORT),
        ).bind_tools(agent_tools.agent_tools())
    return _chat


_TOOL_NODE: Optional[Any] = None


def _tool_node():
    global _TOOL_NODE
    if _TOOL_NODE is None:
        from langgraph.prebuilt import ToolNode

        _TOOL_NODE = ToolNode(agent_tools.agent_tools())
    return _TOOL_NODE


def _scope_of(state: AskState):
    """도구가 만질 범위 — **서버가 정하고 도구가 강제한다.**

    ★`edge_types`·`direction` 은 `intent` 와 **같은 자리에 같은 방식**으로 싣는다.
      1.5차 `fetch_relations` 가 인자로 넘기던 값인데 Agent 배선에서 인자를 빼며
      옮기지 않아, `relation_selector.order()` 가 `if not matched: return ordered`
      로 빠져 **링 안의 의도 정렬이 죽어 있었다**(현황서 §8-18).

    ★`direction` 은 **`.value` 문자열**로 넘긴다 — 1.5차 `fetch_relations` 가
      `getattr(query.direction, "value", None)` 로 넘기던 형태 그대로다.
    """
    query = state["query"]
    return scope.anchor_scope(
        [c.key for c in state["companies"]],
        workspace_keys=state["request"].workspace_keys,
        anchor_keys=[a.key for a in state["decision"].anchors],
        anchor_names=state["anchor_names"],
        intent=state.get("intent") or "",
        edge_types=query.edge_types or (),
        direction=getattr(query.direction, "value", None))


# ══════════════════════════════════════════════════════════════════
#  agent — 무엇을 부를지 고른다
# ══════════════════════════════════════════════════════════════════


def agent(state: AskState) -> AskState:
    """도구를 고른다. **답변을 쓰지 않는다.**

    ★첫 진입에 사람 메시지를 하나 넣는다 — 질문과 **부를 수 있는 key 목록**이다.
      key 를 프롬프트에 실어야 Agent 가 범위 안에서 고른다. 그래도 밖을 부르면
      도구가 거부하고, 그 거부는 재료를 늘리지 않는다(이중 방어).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = list(state.get("messages") or [])
    if not messages:
        keys = [c.key for c in state["companies"]]
        names = ", ".join(f"{c.name}({c.key})" for c in state["companies"])
        messages = [
            SystemMessage(content=_AGENT_SYSTEM),
            HumanMessage(content=(
                f"질문: {state['request'].question}\n"
                f"부를 수 있는 기업 key: {keys}\n"
                f"(참고 — {names})")),
        ]

    reply = _model().invoke(messages)
    # ★**Agent 턴의 사용량을 여기서 센다.** 답변 생성과 갈라 담아야 「Agent 만
    #   모델을 올렸을 때 얼마가 더 나가나」를 잴 수 있다(모델명은 응답이 말한
    #   것을 쓰므로 두 노드가 다른 모델이면 키가 저절로 갈린다).
    observe.record_llm_message(reply)
    calls = getattr(reply, "tool_calls", None) or []
    log.info("agent.turn messages=%d -> tool_calls=%d %s",
             len(messages), len(calls), [c.get("name") for c in calls])
    return {"messages": messages[len(state.get("messages") or []):] + [reply]}


def should_continue(state: AskState) -> str:
    """도구를 더 부를까, 마감할까. **예산이 이긴다.**

    ★예산이 소진되면 도구 호출을 요청했어도 `evidence_validation` 으로 보낸다.
      `recursion_limit` 에 기대면 예외로 끝나 답변이 아예 안 나간다 — 도구를
      덜 불렀어도 **있는 재료로 답하게** 하는 것이 옳다(계약 4).
    """
    if budget.is_exhausted(state):
        # ★관측 — **루프가 잘린 것**과 「끝난 뒤 파급 예산이 찼다」를 가른다.
        #   State 의 `budget_exhausted` 만 보면 둘이 같은 값으로 보인다.
        observe.record_agent_stopped_by_budget()
        log.info("agent.stop 예산 소진 — 남은 재료로 마감한다")
        return "evidence_validation"
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is not None and getattr(last, "tool_calls", None):
        return "run_tools"
    return "evidence_validation"


# ══════════════════════════════════════════════════════════════════
#  run_tools — 부르고, 거둬 담고, 예산을 센다
# ══════════════════════════════════════════════════════════════════


def run_tools(state: AskState) -> AskState:
    """도구를 실제로 부른다. **범위와 수집 블록을 이 노드가 연다.**

    ★`ContextVar` 를 노드 안에서 열고 **나가기 전에 State 로 옮겨 담는다.**
      LangGraph 는 노드마다 컨텍스트를 복사하므로 여기서 세운 값은 다음 노드로
      넘어가지 않는다(1차에서 `new_trace_id()` 로 실측 확인).

    ★**호출 시점마다 누적치를 더한다**(계약 4). 인자 리스트 길이만 제한하면
      `get_events(keys=[A])` 를 열 번 불러 상한을 열 배로 만들 수 있다.

    ★**한 턴 안에서도 상한을 지킨다**(2026-08-29 · Evaluation §10-9-1). 전에는
      `should_continue` 가 턴과 턴 **사이**에서만 검사하는데 여기서 한 턴을
      통째로 썼다. `gpt-4o-mini` 는 턴당 1~2개만 요청해 한 번도 안 걸렸지만,
      **한 턴에 여러 개를 병렬로 요청하는 모델**(실측: `gpt-5.6-sol`)에서는
      카운터가 11일 때 3개짜리 턴이 14가 됐다 — 「막는다」고 적힌 예산이 실제로는
      막지 못했다. `propagations_used` 와 **같은 종류**의 「자르는 단위 ≠ 세는
      단위」다(§4-4).

    ★**거부한 호출에도 답을 채워 넣는다.** 남는 것을 그냥 버리면 AIMessage 에
      있는 `tool_call_id` 중 짝 없는 것이 생기고, OpenAI 는 그 대화의 **다음
      요청을 거부한다.** 상한은 지켰는데 대화가 깨지는 상태가 되므로, 자르는
      것과 짝을 맞추는 것은 **함께** 해야 한다.
    """
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    calls = getattr(last, "tool_calls", None) or []

    room = budget.remaining(state)["tool_calls_used"]
    allowed, denied = calls[:room], calls[room:]

    collected: dict[str, list] = {}
    out_messages: list = []
    if allowed:
        if denied:
            # ★자른 목록만 실은 **새 AIMessage** 로 부른다. 원본을 고치지
            #   않는다 — State 의 대화는 Agent 가 실제로 **요청한** 것 그대로
            #   남아야 「무엇을 부르려 했는데 못 불렀나」를 되짚을 수 있다.
            from langchain_core.messages import AIMessage

            invoke_from = [AIMessage(content="", tool_calls=list(allowed))]
        else:
            invoke_from = messages
        with _scope_of(state), agent_tools.collecting() as bucket:
            out = _tool_node().invoke({"messages": invoke_from})
            collected = {tool: list(items) for tool, items in bucket.items()}
        out_messages = list(out.get("messages") or [])

    if denied:
        from langchain_core.messages import ToolMessage

        out_messages += [
            ToolMessage(content=json.dumps({"error": _BUDGET_DENIED},
                                           ensure_ascii=False),
                        tool_call_id=call.get("id") or "",
                        name=call.get("name") or "")
            for call in denied]
        # ★`record_tool_error` 에 담지 않는다 — 저건 **도구가** 거부한 것이고
        #   이건 **예산이** 막은 것이다. 섞으면 고칠 곳이 갈린다.
        observe.record_tool_calls_denied_by_budget(len(denied))

    events = sum(len(v) for k, v in collected.items() if k == "get_events")
    merged = {tool: list(state.get("tool_results", {}).get(tool, [])) + items
              for tool, items in collected.items()}
    tool_results = {**(state.get("tool_results") or {}), **merged}

    # ★**실행한 것만** 센다. 거부한 것까지 더하면 카운터가 상한을 넘어, 「막는다」고
    #   적힌 값이 다시 못 막는 값이 된다.
    spent = budget.spend(state, tool_calls_used=len(allowed), events_used=events)
    # ★관측 — 예산이 세는 것과 **같은 값**을 센다. 따로 세면 두 벌이 갈린다.
    observe.record_tool_calls(len(allowed))
    log.info("run_tools calls=%d ran=%d denied=%d collected=%s",
             len(calls), len(allowed), len(denied),
             {k: len(v) for k, v in collected.items()})
    return {"messages": out_messages, "tool_results": tool_results, **spent}


# ══════════════════════════════════════════════════════════════════
#  evidence_validation — ★마감 단계. Agent 가 아니라 여기가 근거를 모은다
# ══════════════════════════════════════════════════════════════════


def _dedup_relations(items: Sequence[RelationDTO]) -> list[RelationDTO]:
    seen: dict[str, RelationDTO] = {}
    for item in items:
        seen.setdefault(item.edge_id, item)
    return list(seen.values())


def _dedup_events(items: Sequence[EventDTO]) -> list[EventDTO]:
    """사건은 **근거를 합치며** 접는다.

    ★같은 Event 를 여러 기업이 공유한다(938건 중 85건). 건너뛰기만 하면 먼저 온
      기업의 근거만 남고 나머지가 조용히 사라진다 — `graph_tools._merge_evidence_ids`
      와 `retrieve_service._merge_evidence_ids` 가 이미 고쳐 둔 것과 **같은 규칙**이다.
    """
    seen: dict[str, EventDTO] = {}
    for item in items:
        previous = seen.get(item.event_id)
        if previous is None:
            seen[item.event_id] = item.model_copy(
                update={"evidence_ids": list(item.evidence_ids)})
            continue
        for evidence_id in item.evidence_ids:
            if evidence_id not in previous.evidence_ids:
                previous.evidence_ids.append(evidence_id)
    return list(seen.values())


def evidence_validation(state: AskState) -> AskState:
    """탐색 결과를 **결정론적으로** 재료와 근거로 마감한다 (계약 2번).

    ★**Agent 가 근거를 고르지 않는다.** 여기가 `evidence_id` 합집합을 모으고
      화이트리스트를 만든다. Agent 는 어떤 도구를 부를지만 정했다.

    ★**dedup 을 여기가 책임진다.** 1차까지는 `_evidence_of` 가 3출처 합집합을
      만들며 중복을 접었는데 Agent 루프에서는 그 합류점이 사라진다. 과거에
      `fetch_texts` 가 중복 id 로 `DuplicateIDError` 를 내고 그걸 삼켜 **전건
      판단불가**가 된 사고가 있었다(2026-07-30). `fetch_texts` 는 이제 스스로
      중복을 접지만, **그 위에서 상한을 세는 코드는 여전히 중복을 두 건으로 센다.**

    ★**없는 근거를 한 건으로 세지 않는다.** 엣지 11,060건 대비 Chroma 청크가
      10,510건이라 `evidence_id` 는 있는데 청크가 없는 엣지가 약 5% 있다. 이건
      **정상 상태이지 조회 실패가 아니다** — `missing=True` 로 남기되 「근거를
      못 꺼냈다」로 세지 않는다.
    """
    results = state.get("tool_results") or {}
    relations = _dedup_relations(
        [r for r in results.get("get_relations", []) if isinstance(r, RelationDTO)])
    events = _dedup_events(
        [e for e in results.get("get_events", []) if isinstance(e, EventDTO)])

    # ── 근거 id 합집합 — **순서를 지키며 중복을 없앤다** ────────────
    ids: list[str] = []

    def _add(candidates: Sequence[str]) -> None:
        for candidate in candidates:
            if candidate and candidate not in ids:
                ids.append(str(candidate))

    _add([r.evidence_id for r in relations if r.evidence_id])
    for event in events:
        _add(event.evidence_ids)
    _add([ref["evidence_id"] for hit in state["result"].hits
          for ref in hit.evidence if ref.get("evidence_id")])
    # ★인용 가능한 도구의 결과만 여기서 더한다 — 규칙은 `app/tools/citation.py`
    for tool, items in results.items():
        _add(citation.citable_evidence_ids(tool, items))

    evidence = [Evidence(**row) for row in relation_service.evidence_for_ids(ids)]
    missing = sum(1 for e in evidence if e.missing)
    log.info("evidence_validation relations=%d events=%d unique_ids=%d "
             "-> fetched=%d missing=%d ids=%s",
             len(relations), len(events), len(ids), len(evidence), missing,
             [e.evidence_id for e in evidence[:_MAX_LOGGED_EVIDENCE]])
    return {"relations": relations, "events": events, "evidence": evidence}
