"""`/ask` 그래프의 노드들.

각 노드는 **sync 함수**이고 `AskState` 조각을 돌려준다. 판단 로직은 전부
`RetrieveService`·`AnswerService` 에 그대로 있다 — 노드는 위임하는 껍데기다.
그래야 그래프로 옮기면서 동작이 따라 바뀌지 않는다.
"""

from app.graph.nodes.agent_loop import (agent, evidence_validation, run_tools,
                                   should_continue)
from app.graph.nodes.answer import (build_prompt, check_claims, generate,
                                    halt_no_material, respond, verify_sources)
from app.graph.nodes.material import (fetch_propagation, plan_material,
                                      resolve_anchor, search)

# ★`guard_workspace` 가 빠졌다(최종 설계 §17-1) — 워크스페이스는 검색 게이트가
#   아니다. `fetch_events`·`fetch_relations`·`fetch_evidence` 도 빠졌다 —
#   배선이 끊긴 지 오래고 `agent ⇄ run_tools`·`evidence_validation` 이 대신한다.
__all__ = [
    "search", "resolve_anchor", "plan_material",
    # ── Agent 루프 (2차) ──
    "agent", "run_tools", "should_continue", "evidence_validation",
    "fetch_propagation",
    "build_prompt", "generate", "verify_sources", "check_claims", "respond",
    "halt_no_material",
]
