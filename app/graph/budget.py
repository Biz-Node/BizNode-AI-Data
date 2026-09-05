"""탐색 총량 예산 — **인자 리스트 길이가 아니라 누적치로 센다.**

★계약 4번이 「탐색 상한은 총량 기준」인 이유가 이것이다. 도구마다 상한을 두면
  Agent 가 `get_events(keys=[A])` 를 **열 번 부르는 것**으로 상한을 열 배로
  만든다. 인자 리스트 길이만 제한하면 반복 호출로 우회된다.

      막는다   호출할 때마다 누적치를 더하고, 넘으면 더 못 부른다
      안 막는다 한 번에 몇 개를 넣었나 (그건 도구 내부 상수가 이미 본다)

★**상한은 모듈 상수이고 카운터는 State 다.** State 는 「이번 요청에서 흐르는
  값」만 담는다는 규칙(`app/graph/state.py`)을 그대로 따른다 — 상한을 State 에
  두면 「누가 언제 바꿨나」를 노드마다 따져야 하고, Agent 가 닿을 수 있는
  자리에 상한을 두는 셈이 된다.

★**소진되면 예외가 아니라 전이다.** `recursion_limit` 에 기대면 안 된다 —
  그건 예외를 던지고 끝나서 답변이 아예 안 나간다. 도구를 덜 불렀어도
  **있는 재료로 답하게** 하는 것이 옳다. 소진 여부는 State 플래그로 남기고
  로그에도 남긴다 — 「왜 재료가 적나」를 나중에 되짚을 수 있어야 한다.

★값에 **실측 근거가 없다**(현황서 §9). 평가셋으로 재고 나서 정한다.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.core.trace import trace_logger

log = trace_logger(__name__)

# ── 상한 — ★잠정치다 ──────────────────────────────────────────────
# 도구가 7종이라 한 바퀴에 7번이면 두 바퀴가 안 돈다. 「한 바퀴 돌고 부족한
# 것을 한 번 더」를 허용하는 선으로 잡았다.
MAX_TOOL_CALLS = 12
# 기업당 상한(`MAX_EVENTS_PER_COMPANY`=10)의 4배. 워크스페이스가 4곳까지
# 흔하다는 관찰에서 나온 값이고, 실측은 아니다.
MAX_EVENTS = 40
# `MAX_RISK_EVENTS_FOR_PROPAGATION`(=3)의 4배. 위와 같은 근거.
# ★**이 상한은 한 번도 문 적이 없다**(실측 2026-08-29). `graph_tools.get_propagation`
#   이 사건 목록을 자기 상한 3 으로 **먼저** 자르기 때문이다(원칙 ③ — 상한은 도구
#   안에 있다). 12 는 「기업 4곳 × 3」을 가정했는데 도구는 기업별이 아니라 **목록
#   전체**에 3 을 건다. 그래서 예산이 자른다고 적힌 `risky[:room]` 도 실제로는
#   자른 적이 없다. 값을 고칠지는 실측 뒤 판단이고, 여기서는 사실만 남긴다.
MAX_PROPAGATIONS = 12
# ★지금은 **아무도 쓰지 않는다.** 그래프를 걸어 다니는 도구가 없기 때문이다
#   (`explore_impact` 는 2-B). 자리를 미리 두는 이유는 그때 State·로그·테스트를
#   한꺼번에 고치지 않기 위해서다. 값이 0 으로 남아 있으면 「아직 안 쓴다」다.
MAX_HOPS = 6

# ★**소진 판정 대상** — 「넘으면 더 못 부른다」가 실제로 성립하는 항목만.
#
#   `propagations_used` 를 여기서 뺀 이유(2026-08-29): 계약 4 의 근거는 「인자
#   리스트 길이만 제한하면 **반복 호출**로 우회된다」인데, `fetch_propagation` 은
#   Agent 도구가 아니라 결정론 노드이고 `_AFTER_LOOP` 에 **한 번만** 배선된다 —
#   우회할 반복이 없다. 게다가 도구가 목록 전체에 자기 상한 3 을 먼저 걸어
#   (`MAX_RISK_EVENTS_FOR_PROPAGATION`) 이 상한은 **한 번도 문 적이 없다.**
#   그런데 카운터는 계속 소진 판정에 들어가 「루프가 잘리지도 않았는데 켜지는」
#   플래그를 만들었다. 세는 것과 **막는 것**을 가르는 편이 정직하다.
#
# ★`hops_used` 는 **남긴다.** 지금은 아무도 안 늘려 `0 >= 6` 이 늘 거짓이라
#   무해하고, 빼 두면 `explore_impact`(2-B)가 들어올 때 상한이 **조용히 죽는다.**
#   자리를 미리 두는 이유(위 주석)가 여기서도 그대로다.
_CAPS = {
    "tool_calls_used": MAX_TOOL_CALLS,
    "events_used": MAX_EVENTS,
    "hops_used": MAX_HOPS,
}

# 세는 항목 **전부**. 상한값은 읽는 사람을 위해 함께 둔다 — `remaining()` 이
# 이걸 쓰므로 `fetch_propagation` 의 backstop 절단은 그대로 남는다.
_FIELDS = {
    **_CAPS,
    "propagations_used": MAX_PROPAGATIONS,
}


def initial() -> dict[str, Any]:
    """카운터 0 과 소진 플래그. `plan_material` 이 State 에 심는다."""
    return {**{name: 0 for name in _FIELDS}, "budget_exhausted": False}


def used(state: Mapping[str, Any], field: str) -> int:
    return int(state.get(field) or 0)


def remaining(state: Mapping[str, Any]) -> dict[str, int]:
    """항목별 남은 양. **음수는 0 으로 접는다** — 넘겼어도 「덜 남았다」는 아니다."""
    return {name: max(0, cap - used(state, name)) for name, cap in _FIELDS.items()}


def is_exhausted(state: Mapping[str, Any]) -> bool:
    """하나라도 상한에 닿았나. ★**`_CAPS` 만 본다** — `propagations_used` 는
    세기만 하는 항목이라 여기 안 들어온다(위 주석).

    ★`hops_used` 는 아무도 안 늘리므로 0 이고, 그래서 지금은 늘 거짓이다.
    """
    return any(used(state, name) >= cap for name, cap in _CAPS.items())


def spend(state: Mapping[str, Any], **amounts: int) -> dict[str, Any]:
    """쓴 만큼 더한 **State 조각**을 돌려준다. 소진되면 플래그도 함께 켠다.

    ★State 를 직접 고치지 않는다 — 노드가 조각을 돌려주는 LangGraph 규약을
      지킨다. 그래야 어느 노드가 얼마를 썼는지가 그 노드의 반환값에 드러난다.
    """
    delta: dict[str, Any] = {}
    for name, amount in amounts.items():
        if name not in _FIELDS:
            raise KeyError(f"예산 항목이 아니다: {name}")
        delta[name] = used(state, name) + int(amount or 0)

    merged = {**state, **delta}
    exhausted = is_exhausted(merged)
    delta["budget_exhausted"] = exhausted
    if exhausted and not state.get("budget_exhausted"):
        # ★한 번만 찍는다. 「왜 재료가 적나」를 되짚는 자리다
        log.info("budget.exhausted %s", {n: used(merged, n) for n in _FIELDS})
    return delta
