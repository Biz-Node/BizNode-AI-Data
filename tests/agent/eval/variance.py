"""반복 실행의 **변동폭**을 잰다. ★판정하지 않는다 — 관측만 한다.

왜 필요한가. 「모델을 바꿨더니 도구 선택이 안정됐다」를 말하려면 **바꾸기 전의
변동폭**이 있어야 한다. 그런데 저장소에 있던 것은 총 도구 호출 `33~37`, **관측
4회**가 전부였다(`docs/BizNode_Agent_Evaluation.md` §10-6 ⑥ — 「동일 실행 N회
반복으로 변동폭 확정」 **미착수**). 그건 표본이 아니라 일화다. 그 상태로 새
모델을 2~3회 돌려 비교하면 차이를 모델에 귀속시킬 수 없다 — 이 저장소가 임베딩
드리프트(§4-5)와 링 계측기(§4-9)에서 **두 번 겪은 바로 그 함정**이다.

★**지표를 둘 낸다.**

    총 도구 호출의 폭      옛 관측(33~37)과 이어 읽는다
    케이스별 조합 안정성    N 회 중 **최빈 조합**이 몇 번 나왔나 ÷ N

  앞의 것만 보면 안 된다. 도구 **조합**이 통째로 바뀌었는데 합계는 같을 수
  있기 때문이다 — `get_relations×2 + get_events×1` 과 `get_relations×1 +
  search_news×2` 는 둘 다 3이다. 계획이 실제로 재려는 것은 뒤의 값이다.

★**「조합」은 다중집합이다** — 어떤 도구를 **몇 번** 불렀나까지 본다. 집합만
  보면 「같은 도구를 두 배로 불렀다」가 안정으로 세어진다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from tests.agent.eval.runner import CaseRun

Passes = Sequence[Mapping[str, CaseRun]]


@dataclass(frozen=True)
class Spread:
    """한 지표의 패스별 값들. **폭을 읽는 것이 목적이라 평균만 두지 않는다.**"""

    values: list[float]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def low(self) -> float:
        return min(self.values) if self.values else 0.0

    @property
    def high(self) -> float:
        return max(self.values) if self.values else 0.0

    @property
    def span(self) -> float:
        """최대 − 최소. 옛 관측 `33~37` 과 **같은 방식으로** 읽는 값이다."""
        return self.high - self.low

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    @property
    def stdev(self) -> float:
        """표본 표준편차. ★**패스가 2개 미만이면 0** 이다 — 「흔들리지 않았다」가
        아니라 **「잴 수 없었다」**는 뜻이라, 읽는 쪽이 `n` 을 함께 봐야 한다."""
        return statistics.stdev(self.values) if len(self.values) > 1 else 0.0

    def describe(self, *, percent: bool = False) -> str:
        """★`n` 을 반드시 함께 낸다 — n=1 의 편차 0 은 「안정」이 아니라
        「못 쟀다」이고, 그 둘을 같은 문장으로 쓰면 안 된다."""
        if not self.values:
            return "표본 없음"
        fmt = (lambda v: f"{v:.1%}") if percent else (lambda v: f"{v:g}")
        if self.n < 2:
            return f"{fmt(self.mean)} (n=1 — 변동폭 잴 수 없음)"
        return (f"{fmt(self.low)}~{fmt(self.high)} · 평균 {fmt(self.mean)} · "
                f"편차 {fmt(self.stdev)} (n={self.n})")


# ══════════════════════════════════════════════════════════════════
#  ① 합계 지표 — 옛 관측과 이어 읽는다
# ══════════════════════════════════════════════════════════════════


def _per_pass(passes: Passes, total: Callable[[Mapping[str, CaseRun]], float]) -> Spread:
    return Spread([float(total(one)) for one in passes])


def total_tool_calls(passes: Passes) -> Spread:
    """패스마다 「20 케이스 도구 호출 합계」. ★`33~37` 이 이 값이었다."""
    return _per_pass(passes, lambda runs: sum(r.tool_calls for r in runs.values()))


def total_tokens(passes: Passes) -> Spread:
    """입력+출력 토큰 합계. **비용이 실제로 얼마나 흔들리나**를 본다."""
    return _per_pass(passes, lambda runs: sum(
        sum(r.observed.llm_input_tokens.values())
        + sum(r.observed.llm_output_tokens.values())
        for r in runs.values()))


def total_uncited(passes: Passes) -> Spread:
    return _per_pass(passes, lambda runs: sum(
        r.observed.claims_uncited for r in runs.values()))


def uncited_ratio(passes: Passes) -> Spread:
    """`uncited ÷ claims`. ★**주장이 0건인 패스는 0 이 아니라 뺀다** — 0 으로
    세면 답변이 주장을 아예 안 단 실행이 「근거를 잘 달았다」로 보인다."""
    values: list[float] = []
    for runs in passes:
        claims = sum(r.observed.claims_total for r in runs.values())
        if not claims:
            continue
        values.append(sum(r.observed.claims_uncited
                          for r in runs.values()) / claims)
    return Spread(values)


# ══════════════════════════════════════════════════════════════════
#  ② 조합 안정성 — ★계획이 실제로 재려는 값
# ══════════════════════════════════════════════════════════════════


def _choice(run: CaseRun) -> tuple:
    """이 케이스에서 고른 **도구 다중집합**. 정렬해 두어 비교 가능하게 만든다."""
    return tuple(sorted(run.tools_used.items()))


def case_stability(passes: Passes) -> dict[str, float]:
    """케이스별 「N 회 중 최빈 조합의 비율」. **1.0 이면 매번 같은 조합**이다.

    ★비율로 낸다 — 패스 수가 달라져도 같은 축에서 읽히게 하려는 것이다.
    """
    if not passes:
        return {}
    out: dict[str, float] = {}
    for case_id in passes[0]:
        seen = Counter(_choice(one[case_id]) for one in passes if case_id in one)
        if not seen:
            continue
        out[case_id] = seen.most_common(1)[0][1] / sum(seen.values())
    return out


def case_choice_counts(passes: Passes) -> dict[str, int]:
    """케이스별 **서로 다른 조합이 몇 가지** 나왔나. 1 이면 결정론이다."""
    if not passes:
        return {}
    return {case_id: len({_choice(one[case_id])
                          for one in passes if case_id in one})
            for case_id in passes[0]}


def overall_stability(passes: Passes) -> float:
    """전 케이스 평균 안정성. ★**한 줄로 비교할 때만** 쓴다 — 평균 하나로
    합치면 「어느 케이스가 흔들렸나」가 사라지므로 표를 함께 봐야 한다."""
    got = case_stability(passes)
    return statistics.fmean(got.values()) if got else 0.0
