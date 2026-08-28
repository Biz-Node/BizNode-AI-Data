"""`/ask` 그래프의 노드들.

각 노드는 **sync 함수**이고 `AskState` 조각을 돌려준다. 판단 로직은 전부
`RetrieveService`·`AnswerService` 에 그대로 있다 — 노드는 위임하는 껍데기다.
그래야 그래프로 옮기면서 동작이 따라 바뀌지 않는다.
"""

from app.graph.nodes.agent_loop import (agent, evidence_validation, run_tools,
                                   should_continue)
from app.graph.nodes.answer import (build_prompt, check_claims, generate,
                                    halt_no_material, respond, verify_sources)
from app.graph.nodes.material import (fetch_evidence, fetch_events,
                                      fetch_propagation, fetch_relations,
                                      guard_workspace, plan_material,
                                      resolve_anchor, search)

__all__ = [
    "guard_workspace", "search", "resolve_anchor", "plan_material",
    # ── Agent 루프 (2차) ──
    "agent", "run_tools", "should_continue", "evidence_validation",
    "fetch_propagation",
    # ★더 이상 배선되지 않는다 — `agent ⇄ run_tools` 와 `evidence_validation` 이 대신한다
    "fetch_events", "fetch_relations", "fetch_evidence",
    "build_prompt", "generate", "verify_sources", "check_claims", "respond",
    "halt_no_material",
]
