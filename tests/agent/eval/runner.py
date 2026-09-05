"""평가 케이스 1건을 **실제 그래프로 끝까지** 돌린다.

판정(`test_agent_eval.py`)과 보고서 생성기(`report.py`)가 같은 실행 경로를 쓰도록
여기 한 곳에만 둔다 — `tests/search/eval/runner.py` 와 같은 규약이다.

★**`run_ask()` 를 쓰지 않는다.** 저건 `AskResponse` 만 돌려주는데, 평가는 그 안을
  봐야 한다 — 어떤 도구가 불렸나 · 재료가 몇 건이나 들어왔나 · 링이 어떻게 갈렸나.
  그래서 `ask_graph().invoke()` 를 직접 불러 **최종 State 를 통째로** 받는다.
  `run_ask()` 가 하는 일(trace id 발급 → invoke → 응답 꺼내기)은 여기서 그대로
  한다. 다른 경로를 타는 것이 아니라 **같은 경로를 더 많이 보는 것**이다.

★**관측 버킷은 요청 하나를 통째로 감싼다.** `observe.observing()` 을 노드 안에서
  열면 LangGraph 가 노드마다 컨텍스트를 복사해 다음 노드의 관측이 안 들어온다.
  여기는 `invoke()` **바깥**이라 그 문제가 없다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from app.api.schemas import AnchorSource, AskRequest, AskResponse
from app.core import observe
from app.core.trace import new_trace_id
from app.graph.ask_graph import ask_graph
from app.graph.state import initial_state
from tests.agent.eval.cases import CASES, AgentEvalCase


@dataclass(frozen=True)
class CaseRun:
    case: AgentEvalCase
    state: dict[str, Any]
    observed: observe.Observation
    took_ms: int
    error: Optional[str] = None

    # ── 서버가 정한 값 ────────────────────────────────────────
    @property
    def anchor_source(self) -> Optional[AnchorSource]:
        decision = self.state.get("decision")
        return decision.source if decision is not None else None

    @property
    def anchor_names(self) -> list[str]:
        decision = self.state.get("decision")
        return [a.name for a in decision.anchors] if decision is not None else []

    @property
    def agent_called(self) -> bool:
        """Agent 가 불렸나. **메시지가 하나라도 있으면 불린 것이다** —
        `halt_no_material` 로 빠지면 `agent` 노드를 안 지나 messages 가 비어 있다."""
        return bool(self.state.get("messages"))

    # ── 재료 ──────────────────────────────────────────────────
    @property
    def relations(self) -> list:
        return list(self.state.get("relations") or [])

    @property
    def events(self) -> list:
        return list(self.state.get("events") or [])

    @property
    def evidence(self) -> list:
        return list(self.state.get("evidence") or [])

    @property
    def response(self) -> Optional[AskResponse]:
        return self.state.get("response")

    @property
    def failed(self) -> bool:
        response = self.response
        return bool(response.failed) if response is not None else True

    @property
    def sources(self) -> list:
        response = self.response
        return list(response.sources) if response is not None else []

    # ── 비용 ──────────────────────────────────────────────────
    @property
    def tool_calls(self) -> int:
        return int(self.state.get("tool_calls_used") or 0)

    @property
    def budget_exhausted(self) -> bool:
        return bool(self.state.get("budget_exhausted"))

    @property
    def tools_used(self) -> dict[str, int]:
        return dict(self.observed.tools_used)

    def describe(self) -> str:
        """실패 메시지에 붙일 한 줄. **재료가 왜 그런지 되짚을 수 있어야 한다.**"""
        source = self.anchor_source.value if self.anchor_source else "없음"
        tools = ", ".join(f"{k}×{v}" for k, v in sorted(self.tools_used.items()))
        return (f"\n  question={self.case.question!r}"
                f"\n  anchor_source={source} anchors={self.anchor_names}"
                f" agent_called={self.agent_called}"
                f"\n  tool_calls={self.tool_calls} tools=[{tools or '없음'}]"
                f" budget_exhausted={self.budget_exhausted}"
                f"\n  relations={len(self.relations)} events={len(self.events)}"
                f" evidence={len(self.evidence)} sources={len(self.sources)}"
                f" failed={self.failed}"
                f"\n  ring_seen={dict(sorted(self.observed.ring_seen.items()))}"
                f" kept={self.observed.relations_kept} cut={self.observed.relations_cut}"
                f"\n  cited_rings={dict(sorted(self.observed.cited_rings.items()))}"
                f" cited_without_ring={self.observed.cited_without_ring}"
                f"\n  embed_calls={self.observed.embed_calls}"
                f" hit={self.observed.embed_cache_hits}"
                f" miss={self.observed.embed_cache_misses}"
                f"\n  took={self.took_ms}ms"
                + (f"\n  ★error={self.error}" if self.error else ""))


def build_request(case: AgentEvalCase) -> AskRequest:
    return AskRequest(question=case.question,
                      workspace_keys=list(case.workspace_keys),
                      # ★「담은 것」과 **갈라서** 넘긴다. 한 필드로 합치면
                      #   `anchor_source` 가 `context` 와 `workspace` 를 못 가르고,
                      #   그러면 이 평가셋이 새 갈래를 덮지 못한다.
                      context_keys=list(case.context_keys))


def run_case(case: AgentEvalCase) -> CaseRun:
    """한 케이스를 돌린다. **예외를 삼키지 않되 평가를 멈추지도 않는다** —
    한 질문이 죽어도 나머지 분포는 모아야 하므로 `error` 에 담아 넘긴다."""
    new_trace_id()
    start = time.monotonic()
    error: Optional[str] = None
    state: dict[str, Any] = {}
    with observe.observing() as seen:
        try:
            state = ask_graph().invoke(initial_state(build_request(case)))
        except Exception as exc:                      # noqa: BLE001
            error = repr(exc)
    took_ms = int((time.monotonic() - start) * 1000)
    return CaseRun(case=case, state=state, observed=seen,
                   took_ms=took_ms, error=error)


def run_all() -> dict[str, CaseRun]:
    """★케이스마다 **한 번만** 돌린다. LLM 왕복이 들어 있어 두 번 돌리면
    비용이 두 배가 되고, 두 실행이 다른 도구를 골라 판정이 갈릴 수 있다."""
    return {case.id: run_case(case) for case in CASES}


def run_all_n(times: int) -> list[dict[str, CaseRun]]:
    """평가셋 **전체를 `times` 번** 돌린다. 한 원소가 한 패스(20 케이스)다.

    ★**케이스가 아니라 패스를 반복한다.** 같은 케이스를 연달아 n 번 부르면
      임베딩 캐시가 그 케이스에만 데워져, 「뒤 실행이 앞 실행보다 싸다」가
      케이스마다 다르게 섞인다. 패스 단위로 돌면 그 편향이 전 케이스에 고르게
      걸려, 패스끼리 비교할 수 있는 값이 된다.

    ★**판정은 1회차만 쓴다**(`conftest.runs`). 여기서 나온 변동폭은 보고서에만
      싣는다 — 링과 같은 규약이다. 변동폭에 임계값을 걸면 재는 도구가 판정기가
      되고, 「상한이 맞나」를 이 수치로 정하겠다는 결정을 미리 해버린다.

    ★**비용이 `times` 배다.** 부르는 쪽이 그걸 알고 부르라고 이름에 수를 받는다.
    """
    return [run_all() for _ in range(max(1, times))]
