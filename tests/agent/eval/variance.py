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

★**흔들려야 하는 값과 흔들리면 안 되는 값을 갈라 낸다**(2026-08-29 · 지적을
  받아 고침). 같은 재료를 같은 규칙으로 자르면 `ring_seen`·`ring_kept` 는
  결정론이고, `cited_rings`·도구 조합·토큰은 LLM 몫이라 흔들리는 것이 정상이다.
  한 표에 섞어 두면 **「무엇이 흔들려야 정상인가」가 안 보인다** — 재료가
  움직인 실행을 「모델이 불안정하다」로 읽게 된다. `material_is_stable()` 이
  그 전제를 한 줄로 답한다.
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


def total_embed_misses(passes: Passes) -> Spread:
    """패스마다 임베딩 캐시가 몇 건 빗나갔나 — **남은 노이즈원의 크기**다.

    ★변동폭과 **나란히 읽어야** 하는 값이다. 빗나간 만큼 그 패스는 임베딩을
      직접 계산했고, 그 값은 실행마다 흔들린다(현황서 §8-13). 즉 여기 수가
      크면 위의 변동폭 중 **얼마가 Agent 때문인지 가를 수 없다.**

    ★`EMBED_CACHE_STRICT=1` 은 이걸 0 으로 만드는 장치가 **아니다.**
      `default_embed` 를 타는 경로만 걸리고, `search_news`·`search_dart` 의
      질의는 `ChromaStore.query()` 로 캐시를 우회한다 — 그 질의문은 LLM 이
      매번 새로 쓴다(`docs/BizNode_Agent_Evaluation.md` §10-7).
    """
    return _per_pass(passes, lambda runs: sum(
        r.observed.embed_cache_misses for r in runs.values()))


# ── ①-2 ★**흔들리면 안 되는 값** — 재료 파이프라인의 불변량 ────────
#
#   같은 재료를 같은 규칙으로 자르면 `ring_seen`·`ring_kept` 는 **결정론**이다.
#   도구를 몇 번 부르든 안 움직인다(`observe.record_rings` 가 `edge_id` 로 중복을
#   접는다 — §4-9 가 그걸 고친 자리다). 그러니 여기가 흔들리면 그건 **모델 탓이
#   아니라** 랭킹이 바뀌었거나 계측이 깨진 것이다.
#
#   ★반대로 `cited_rings` 는 **LLM 이 무엇을 인용했나**에 달려 있어 흔들리는 것이
#     정상이다. 셋을 한 표에 섞어 두면 「무엇이 흔들려야 정상인가」가 안 보인다 —
#     실제로 같은 재료(seen 1008 · kept 110)에서 인용만 R0 이 1 대 2 로 갈린
#     실측이 있고, 모수가 9뿐이라 그 1건이 11% 로 보인다.


def total_ring_seen(passes: Passes) -> Spread:
    """도구가 본 관계 수. ★**span 0 이어야 정상**이다."""
    return _per_pass(passes, lambda runs: sum(
        sum(r.observed.ring_seen.values()) for r in runs.values()))


def total_ring_kept(passes: Passes) -> Spread:
    """상한에 남은 관계 수. ★**span 0 이어야 정상**이다."""
    return _per_pass(passes, lambda runs: sum(
        sum(r.observed.ring_kept.values()) for r in runs.values()))


def total_cited_relations(passes: Passes) -> Spread:
    """최종 인용된 **관계** 수. ★위 둘과 달리 **흔들리는 것이 정상**이다."""
    return _per_pass(passes, lambda runs: sum(
        sum(r.observed.cited_rings.values()) for r in runs.values()))


def material_is_stable(passes: Passes) -> bool:
    """재료 파이프라인이 불변이었나. ★거짓이면 **모델 비교가 성립하지 않는다** —
    재료가 달랐다는 뜻이라, 인용이나 도구 선택의 차이를 모델에 귀속시킬 수 없다."""
    return (total_ring_seen(passes).span == 0
            and total_ring_kept(passes).span == 0)


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
