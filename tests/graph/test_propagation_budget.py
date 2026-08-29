"""파급 예산의 **단위 계약** — 자르는 단위와 세는 단위가 같은가.

★이 테스트가 있는 이유(2026-08-29). `fetch_propagation` 은 `risky`(**사건 수**)
  를 예산으로 잘라 놓고 `len(propagation)`(**파급 행 수**)을 카운터에 더했다.
  사건 하나가 수십 행을 내므로 상한 12 에 실측 **92** 가 찍혔고(이전 측정 303),
  Agent 루프가 잘리지도 않았는데 `budget_exhausted` 가 켜졌다. 「막는다」고 적힌
  예산이 자기 카운터로는 상한을 넘긴 상태였다.

★**한 카운터에 두 단위가 섞이면 관측값을 읽을 수가 없다.** 상한을 올려야 하는지
  단위가 틀린 것인지 수치만 봐서는 안 갈린다 — `tests/agent/eval/report.py` 가
  「상한을 넘긴 카운터」를 결함 신호로 따로 뽑는 것도 그래서다.

★**DB 도 LLM 도 쓰지 않는다.** `graph_tools.get_propagation` 자리에 대역을 세워
  「사건 몇 개를 넘겼는데 행이 몇 개 나왔나」만 만든다. 실제 파급 계산은 이
  테스트의 관심이 아니다.
"""

from __future__ import annotations

import pytest

from app.graph import budget
from app.graph.nodes import material
from app.tools.dto import ROLE_NOTE, STATED_NOTE, EventDTO, PropagationDTO


def _risky(count: int) -> list[EventDTO]:
    return [EventDTO(event_id=f"evt_{i}", name=f"사건{i}", event_type="규제수사",
                     is_risk=True, evidence_ids=[f"ev_{i}"], role="subject",
                     role_note=ROLE_NOTE["subject"])
            for i in range(count)]


def _rows(event_ids, per_event: int) -> list[PropagationDTO]:
    """사건 하나가 여러 행을 낸다 — **이것이 단위가 갈리는 지점이다.**"""
    return [PropagationDTO(event_id=event_id, target=f"기업{n}", key=None,
                           score=0.3, hops=2, stated=False,
                           stated_note=STATED_NOTE[False], path=["a", "b"])
            for event_id in event_ids for n in range(per_event)]


@pytest.fixture
def fake_propagation(monkeypatch):
    """넘어온 사건마다 행 `per_event` 개를 낸다. 넘어온 사건 목록도 기록한다."""
    seen: dict[str, list[str]] = {}

    def _install(per_event: int):
        def _get(event_ids):
            seen["passed"] = list(event_ids)
            return _rows(event_ids, per_event)
        monkeypatch.setattr(material.graph_tools, "get_propagation", _get)
        return seen

    return _install


def _state(events):
    return {**budget.initial(), "events": events}


def test_the_counter_counts_events_not_propagation_rows(fake_propagation):
    """★사건 2건이 행 50개를 내도 **쓴 것은 2**다.

    행 수로 세면 「사건을 몇 개까지 넘길 수 있나」라는 상한의 뜻이 사라진다.
    """
    fake_propagation(per_event=25)
    out = material.fetch_propagation(_state(_risky(2)))

    assert len(out["propagation"]) == 50, "대역이 행을 많이 내야 의미가 있는 시험이다"
    assert out["propagations_used"] == 2


def test_the_counter_never_exceeds_its_own_cap(fake_propagation):
    """★상한을 **넘길 수 없다.** 넘겼다면 자르는 단위와 세는 단위가 갈린 것이다.

    사건을 상한보다 많이 주고, 행은 사건당 여럿을 내게 한다 — 옛 계산이라면
    `MAX_PROPAGATIONS` 의 몇 배가 찍힌다.
    """
    fake_propagation(per_event=30)
    out = material.fetch_propagation(_state(_risky(budget.MAX_PROPAGATIONS + 5)))

    assert out["propagations_used"] <= budget.MAX_PROPAGATIONS


def test_what_the_budget_cut_is_what_the_budget_counted(fake_propagation):
    """★**자른 목록의 길이와 센 값이 같은 수**여야 한다.

    둘을 따로 재면 「예산이 막았다」를 카운터로 되짚을 수 없다.
    """
    seen = fake_propagation(per_event=7)
    out = material.fetch_propagation(_state(_risky(budget.MAX_PROPAGATIONS + 3)))

    assert len(seen["passed"]) == out["propagations_used"]


def test_the_flag_does_not_turn_on_when_nothing_was_actually_cut(fake_propagation):
    """★평범한 규모에서 `budget_exhausted` 가 **켜지지 않는다.**

    옛 계산에서는 루프가 잘리지도 않았는데 켜졌고, 그 탓에 평가셋 보고서의
    「최종 플래그」 줄이 「Agent 루프가 예산으로 잘린」 줄과 뜻이 달라졌다.
    """
    fake_propagation(per_event=40)
    out = material.fetch_propagation(_state(_risky(3)))

    assert out["propagations_used"] == 3
    assert not out["budget_exhausted"]


def test_events_that_are_not_risky_are_not_counted(fake_propagation):
    """★`is_risk` 가 아닌 사건은 계산도 계수도 하지 않는다 — 기존 계약."""
    fake_propagation(per_event=2)
    events = _risky(2) + [EventDTO(event_id="evt_calm", name="평범", event_type="계약",
                                   is_risk=False, evidence_ids=[], role="subject",
                                   role_note=ROLE_NOTE["subject"])]
    out = material.fetch_propagation(_state(events))

    assert out["propagations_used"] == 2


# ══════════════════════════════════════════════════════════════════
#  ★파급은 **세기만 한다** — 소진 판정 대상이 아니다 (2026-08-29 · Phase 12)
# ══════════════════════════════════════════════════════════════════

def test_propagation_is_counted_but_never_triggers_exhaustion():
    """★계약 4 의 근거는 「인자 길이만 막으면 **반복 호출**로 우회된다」인데,
    `fetch_propagation` 은 Agent 도구가 아니라 결정론 노드이고 `_AFTER_LOOP` 에
    **한 번만** 배선된다 — 우회할 반복이 없으므로 막을 것도 없다.

    게다가 도구가 목록 전체에 자기 상한 3 을 먼저 걸어 이 상한은 **한 번도 문 적이
    없다.** 그런데도 소진 판정에 들어가 「루프가 잘리지도 않았는데 켜지는」 플래그를
    만들었다.
    """
    state = budget.initial()
    state = {**state,
             **budget.spend(state, propagations_used=budget.MAX_PROPAGATIONS * 3)}

    assert state["propagations_used"] == budget.MAX_PROPAGATIONS * 3, "세기는 센다"
    assert not budget.is_exhausted(state)
    assert not state["budget_exhausted"], "★막지는 않는다"


def test_the_caps_that_do_bite_still_bite():
    """★뺀 것은 파급 **하나**다 — 도구 호출·사건 상한은 그대로 문다."""
    for field, cap in (("tool_calls_used", budget.MAX_TOOL_CALLS),
                       ("events_used", budget.MAX_EVENTS)):
        state = budget.initial()
        state = {**state, **budget.spend(state, **{field: cap})}
        assert state["budget_exhausted"], f"{field} 는 여전히 상한이다"


def test_hops_stays_a_cap_so_it_cannot_die_silently():
    """★`hops_used` 는 소진 판정에 **남겨 뒀다.**

    지금은 아무도 안 늘려 `0 >= 6` 이 늘 거짓이라 무해하다. 빼 두면
    `explore_impact`(2-B)가 들어올 때 상한이 **조용히 죽고**, 「그때 되돌려 놓기」를
    누군가 기억해야 하는 함정이 된다.
    """
    state = budget.initial()
    state = {**state, **budget.spend(state, hops_used=budget.MAX_HOPS)}

    assert state["budget_exhausted"], "hops 는 여전히 상한이다"
