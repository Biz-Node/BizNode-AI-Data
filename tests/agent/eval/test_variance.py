"""변동폭 집계 — **가짜 패스로** 계산이 맞는지 본다.

★LLM 을 부르지 않는다. `needs_llm` 도 아니다 — 여기서 재는 것은 산수이지
  Agent 의 행동이 아니다. 진짜 실행에서만 확인되면 「집계가 틀렸다」와
  「Agent 가 흔들렸다」를 못 가르는데, 그건 이 작업이 없애려는 바로 그
  종류의 혼동이다.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.core import observe
from tests.agent.eval import variance
from tests.agent.eval.runner import CaseRun


def _run(tools: dict, *, tool_calls=None, claims=0, uncited=0,
         input_tokens=0, output_tokens=0) -> CaseRun:
    """관측만 채운 `CaseRun`. 집계가 읽는 자리는 `observed` 와 `state` 뿐이다."""
    seen = observe.Observation()
    seen.tools_used = Counter(tools)
    seen.claims_total = claims
    seen.claims_uncited = uncited
    seen.llm_input_tokens = Counter({"모델": input_tokens})
    seen.llm_output_tokens = Counter({"모델": output_tokens})
    calls = sum(tools.values()) if tool_calls is None else tool_calls
    return CaseRun(case=None, state={"tool_calls_used": calls},
                   observed=seen, took_ms=0)


# ══════════════════════════════════════════════════════════════════
#  ① Spread — n=1 은 「안 흔들렸다」가 아니라 「못 쟀다」
# ══════════════════════════════════════════════════════════════════


def test_a_single_pass_reports_that_it_cannot_measure():
    got = variance.Spread([34.0])
    assert got.n == 1
    assert got.stdev == 0.0
    assert "잴 수 없음" in got.describe(), \
        "n=1 인데 편차 0 이 「안정적이다」로 읽히면 안 된다"


def test_span_matches_the_old_way_of_reading_it():
    """★옛 관측은 `33~37` 이었다. 같은 축으로 읽히는 값을 내야 이어 볼 수 있다."""
    got = variance.Spread([36.0, 33.0, 37.0])
    assert (got.low, got.high, got.span) == (33.0, 37.0, 4.0)


def test_total_tool_calls_sums_each_pass():
    passes = [
        {"a": _run({"get_relations": 2}), "b": _run({"get_events": 1})},
        {"a": _run({"get_relations": 3}), "b": _run({"get_events": 1})},
    ]
    got = variance.total_tool_calls(passes)
    assert got.values == [3.0, 4.0]


def test_total_tokens_adds_input_and_output():
    passes = [{"a": _run({}, input_tokens=100, output_tokens=10)}]
    assert variance.total_tokens(passes).values == [110.0]


# ══════════════════════════════════════════════════════════════════
#  ② ★조합 안정성 — 합계가 같아도 조합이 바뀔 수 있다
# ══════════════════════════════════════════════════════════════════


def test_the_same_total_can_hide_a_completely_different_choice():
    """★이 파일에서 가장 중요한 테스트다. 총계만 보면 「안 흔들렸다」로
    읽히는 실행이, 조합으로 보면 **매번 다른 도구를 골랐다**."""
    passes = [
        {"a": _run({"get_relations": 2, "get_events": 1})},
        {"a": _run({"get_relations": 1, "search_news": 2})},
    ]

    calls = variance.total_tool_calls(passes)
    assert calls.span == 0.0, "총 호출은 3 으로 같다"
    assert variance.case_stability(passes)["a"] == 0.5, \
        "조합은 두 번 다 달랐는데 안정으로 세어졌다"
    assert variance.case_choice_counts(passes)["a"] == 2


def test_a_deterministic_case_scores_one():
    passes = [{"a": _run({"get_relations": 2})} for _ in range(4)]
    assert variance.case_stability(passes)["a"] == 1.0
    assert variance.case_choice_counts(passes)["a"] == 1
    assert variance.overall_stability(passes) == 1.0


def test_stability_is_the_share_of_the_most_common_choice():
    passes = [
        {"a": _run({"get_relations": 1})},
        {"a": _run({"get_relations": 1})},
        {"a": _run({"get_relations": 1})},
        {"a": _run({"get_events": 1})},
    ]
    assert variance.case_stability(passes)["a"] == 0.75


def test_calling_the_same_tool_more_often_is_not_stable():
    """★집합이 아니라 **다중집합**으로 본다. 집합만 보면 「같은 도구를 두 배로
    불렀다」가 안정으로 세어진다 — 그건 비용이 두 배라는 뜻이다."""
    passes = [
        {"a": _run({"get_relations": 1})},
        {"a": _run({"get_relations": 5})},
    ]
    assert variance.case_stability(passes)["a"] == 0.5


# ══════════════════════════════════════════════════════════════════
#  ③ uncited 비율 — 분모가 0 인 패스를 0% 로 세지 않는다
# ══════════════════════════════════════════════════════════════════


def test_uncited_ratio_divides_by_claims():
    passes = [{"a": _run({}, claims=8, uncited=2)}]
    assert variance.uncited_ratio(passes).values == [0.25]


def test_a_pass_without_any_claim_is_dropped_not_scored_zero():
    """★0 으로 세면 **주장을 아예 안 단 실행**이 「근거를 잘 달았다」로 보인다."""
    passes = [
        {"a": _run({}, claims=4, uncited=1)},
        {"a": _run({}, claims=0, uncited=0)},
    ]
    got = variance.uncited_ratio(passes)
    assert got.values == [0.25]
    assert got.n == 1, "주장이 0건인 패스가 표본에 섞였다"


@pytest.mark.parametrize("passes", [[], ()])
def test_no_passes_does_not_explode(passes):
    assert variance.case_stability(passes) == {}
    assert variance.overall_stability(passes) == 0.0
