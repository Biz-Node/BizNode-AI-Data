"""Agent 평가셋 결과를 문서로 굽는다.

    python -m tests.agent.eval.report -o docs/BizNode_Agent_평가셋.md

★**문서는 생성물이다.** 손으로 고치면 다음 실행에 지워진다 —
  `tests/search/eval/report.py` 와 같은 규약이다.

★**판정과 같은 실행 경로를 쓴다**(`runner.run_all`). 보고서가 따로 돌면
  「테스트는 통과하는데 문서는 다르다」가 생기고, 그때 어느 쪽이 참인지 가릴
  방법이 없다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Sequence

from app.graph import budget
from app.tools import agent_tools, citation
from tests.agent.eval.cases import CASES
from tests.agent.eval import variance
from tests.agent.eval.runner import CaseRun, run_all

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_RING_LABEL = {
    0: "0 양끝 모두 워크스페이스 안",
    1: "1 워크스페이스 ↔ 바깥 기업",
    2: "2 워크스페이스 ↔ 비-Company",
    3: "3 워크스페이스와 안 닿음",
}


def _verdict(run: CaseRun) -> str:
    """이 케이스가 기대대로 돌았는가. **판정 로직을 복제하지 않는다** — 여기서
    보는 것은 서버가 정하는 값 셋뿐이고, 전체 판정은 pytest 가 한다."""
    if run.error:
        return "ERROR"
    if run.anchor_source is not run.case.expected_anchor_source:
        return "FAIL(anchor)"
    if run.agent_called is not run.case.expects_agent:
        return "FAIL(agent)"
    if run.case.expects_answer and run.failed:
        return "FAIL(answer)"
    return "PASS"


def _rings(counter: Counter) -> str:
    if not counter:
        return "없음"
    return " · ".join(f"R{ring}:{n}" for ring, n in sorted(counter.items()))


def _tools(used: dict[str, int]) -> str:
    if not used:
        return "없음"
    return " · ".join(f"`{k}`×{v}" for k, v in sorted(used.items()))


def build(runs: dict[str, CaseRun],
          passes: Optional[Sequence[Mapping[str, CaseRun]]] = None) -> str:
    """`runs` 는 **판정이 본 1회차**다. `passes` 는 반복 실행 전체(1회차 포함).

    ★둘을 갈라 받는다. 케이스별 표는 **판정과 같은 실행**을 보여야 하고
      (안 그러면 「테스트는 통과인데 문서 수치는 다르다」가 되살아난다),
      변동폭은 여러 패스가 있어야 나온다.
    """
    passes = list(passes) if passes else [runs]
    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    add("# BizNode Agent 루프 — 평가셋")
    add()
    add("> **이 파일은 생성물입니다.** 손으로 고치지 말고 아래로 다시 만드세요.")
    add("> ```bash")
    add("> .venv-wsl/bin/python -m tests.agent.eval.report \\")
    add(">     -o docs/BizNode_Agent_평가셋.md")
    add("> ```")
    add()
    add(f"마지막 실행 **{date.today().isoformat()}** · 케이스 **{len(CASES)}개**")
    add()
    add("케이스 정의는 `tests/agent/eval/cases.py`, 판정은 "
        "`tests/agent/eval/test_agent_eval.py` 에 있습니다.")
    add()
    add("```bash")
    add("pytest -m needs_llm tests/agent/eval -q     # Agent 평가셋 (LLM 실호출)")
    add("pytest tests/search/eval -q                 # 검색 회귀 기준선 (별개)")
    add("```")
    add()
    add("★**검색 평가셋과 섞지 않습니다.** `tests/search/eval/` 20 케이스는 검색 계층의 "
        "회귀 기준선으로 **그대로 보존**되어 있고, 이 문서는 그 위에서 Agent 가 도구를 "
        "골라 재료를 모으는 루프를 잽니다.")
    add()
    add("★**ranking 은 이 단계에서 바꾸지 않았습니다.** 링 분포·kept/cut·인용된 링은 "
        "**관측만** 합니다. 링 랭킹을 바꿀지는 이 수치를 보고 정합니다.")
    add()

    # ── 1. 한눈에 보기 ────────────────────────────────────────
    verdicts = Counter(_verdict(run) for run in runs.values())
    add("## 1. 한눈에 보기")
    add()
    add("| 판정 | 케이스 |")
    add("|---|---|")
    for name, count in sorted(verdicts.items()):
        add(f"| {name} | {count} |")
    add()

    add("| # | 케이스 | 질문 | anchor | Agent | 도구 호출 | 판정 |")
    add("|---:|---|---|---|---|---:|---|")
    for index, case in enumerate(CASES, start=1):
        run = runs[case.id]
        source = run.anchor_source.value if run.anchor_source else "—"
        add(f"| {index} | `{case.id}` | {case.question} | {source} | "
            f"{'호출' if run.agent_called else '미호출'} | {run.tool_calls} | "
            f"{_verdict(run)} |")
    add()

    # ── 반복 실행 — ★계획이 재려는 값이 여기 있다 ────────────
    add("### 반복 실행 — 변동폭")
    add()
    if len(passes) < 2:
        add(f"이 실행은 **1회**입니다. 변동폭은 잴 수 없습니다 — "
            f"`--agent-eval-repeat=N` 으로 N 번 돌리세요 "
            f"(**비용이 N 배**입니다).")
        add()
    else:
        calls = variance.total_tool_calls(passes)
        tokens = variance.total_tokens(passes)
        ratio = variance.uncited_ratio(passes)
        stability = variance.overall_stability(passes)
        add(f"패스 **{len(passes)}회** · 케이스 {len(CASES)}개")
        add()
        add("| 지표 | 값 |")
        add("|---|---|")
        add(f"| 도구 호출 총계 | {calls.describe()} |")
        add(f"| 입력+출력 토큰 | {tokens.describe()} |")
        add(f"| uncited 비율 | {ratio.describe(percent=True)} |")
        add(f"| 임베딩 캐시 빗나감 | {variance.total_embed_misses(passes).describe()} |")
        add(f"| ★**도구 조합 안정성** | {stability:.2f} "
            f"(1.00 = 매번 같은 조합) |")
        add()
        add("★**빗나감을 나란히 보세요.** 0 이 아니면 그 패스는 임베딩을 직접 "
            "계산했고 그 값은 실행마다 흔들립니다(현황서 §8-13) — 위 변동폭 중 "
            "얼마가 Agent 때문인지 그만큼 못 가릅니다. `EMBED_CACHE_STRICT=1` 은 "
            "이걸 0 으로 만드는 장치가 **아닙니다**(Evaluation §10-7).")
        add()
        add("★**두 줄을 갈라 읽으세요.** 총계의 폭만 보면 안 됩니다 — 도구 "
            "**조합**이 통째로 바뀌었는데 합계는 같을 수 있습니다"
            "(`get_relations×2 + get_events×1` 과 "
            "`get_relations×1 + search_news×2` 는 둘 다 3입니다). "
            "「모델을 바꿔 도구 선택이 안정됐나」에 답하는 것은 **안정성** 쪽입니다.")
        add()

        unstable = sorted((v, k) for k, v in variance.case_stability(passes).items())
        counts = variance.case_choice_counts(passes)
        add("| 케이스 | 조합 안정성 | 서로 다른 조합 |")
        add("|---|---:|---:|")
        for value, case_id in unstable:
            add(f"| `{case_id}` | {value:.2f} | {counts.get(case_id, 0)} |")
        add()
        add("★**이 표는 판정이 아닙니다.** 낮은 안정성이 곧 결함이 아니라, "
            "도구 선택이 LLM 몫이라 구조적으로 결정론이 아니라는 사실의 "
            "크기입니다. 모델을 바꿀 때 **이 값과 비교**하라고 둡니다.")
        add()

    # ── 2. 비용 — Phase 8 이 재려던 것 ────────────────────────
    add("## 2. 비용 — 도구·예산·임베딩")
    add()
    total_calls = sum(run.tool_calls for run in runs.values())
    agent_runs = [run for run in runs.values() if run.agent_called]
    stopped = [run for run in runs.values() if run.observed.agent_stopped_by_budget]
    flagged = [run for run in runs.values() if run.budget_exhausted]
    embed_calls = sum(run.observed.embed_calls for run in runs.values())
    embed_hits = sum(run.observed.embed_cache_hits for run in runs.values())
    embed_misses = sum(run.observed.embed_cache_misses for run in runs.values())

    add("| 항목 | 값 |")
    add("|---|---|")
    add(f"| Agent 가 불린 케이스 | {len(agent_runs)} / {len(CASES)} |")
    add(f"| 도구 호출 총계 | {total_calls} |")
    if agent_runs:
        per_case = [run.tool_calls for run in agent_runs]
        add(f"| 케이스당 도구 호출 | 최소 {min(per_case)} · "
            f"중앙 {sorted(per_case)[len(per_case) // 2]} · 최대 {max(per_case)} "
            f"(상한 {budget.MAX_TOOL_CALLS}) |")
    add(f"| ★**Agent 루프가 예산으로 잘린** 케이스 | {len(stopped)} / {len(CASES)} |")
    add(f"| 최종 `budget_exhausted` 플래그 | {len(flagged)} / {len(CASES)} |")
    add(f"| 임베딩 호출 | {embed_calls}회 · 캐시 적중 {embed_hits} · "
        f"빗나감 {embed_misses} |")
    add()
    add("★**두 줄을 갈라 읽으세요.** 플래그는 `fetch_propagation` 이 Agent 루프 "
        "**뒤에** 파급 예산을 채워도 켜집니다. 「상한을 올려야 하나」의 답이 갈립니다 — "
        "루프가 잘렸으면 **도구** 예산 얘기고, 뒤에서 찬 것이면 **파급** 예산 얘기입니다.")
    add()

    # ── LLM 토큰 — ★모델별로 가른다 ──────────────────────────
    add("### LLM 토큰 — 모델별")
    add()
    models = sorted({m for run in runs.values() for m in run.observed.llm_calls})
    if not models:
        add("이 실행에서 LLM 사용량이 잡히지 않았습니다.")
        add()
    else:
        add("| 모델 | 호출 | 입력 | 출력 | (그중 추론) |")
        add("|---|---:|---:|---:|---:|")
        for model in models:
            calls = sum(r.observed.llm_calls.get(model, 0) for r in runs.values())
            got_in = sum(r.observed.llm_input_tokens.get(model, 0)
                         for r in runs.values())
            got_out = sum(r.observed.llm_output_tokens.get(model, 0)
                          for r in runs.values())
            reasoning = sum(r.observed.llm_reasoning_tokens.get(model, 0)
                            for r in runs.values())
            add(f"| `{model}` | {calls} | {got_in:,} | {got_out:,} | {reasoning:,} |")
        add()
        missing = sum(r.observed.llm_calls_without_usage for r in runs.values())
        if missing:
            add(f"★**사용량이 안 실려 온 호출이 {missing}건** 있습니다 — 위 "
                f"토큰 수는 그만큼 **적게** 잡힌 값입니다. 0 토큰이 아닙니다.")
            add()
        add("★**모델명은 응답이 말한 것**입니다(`gpt-4o-mini-2024-07-18`). 설정에 "
            "적은 별칭이 아니라 **실제로 답한 스냅샷**이라, 별칭이 다른 모델을 "
            "가리키게 돼도 여기서 드러납니다.")
        add()
        add("★**추론 토큰은 출력 안에 이미 포함돼 있습니다** — 따로 더하지 "
            "마세요. gpt-5 계열로 바꾸면 이 열이 커지고, 그게 비용 증가의 "
            "출처입니다.")
        add()
        add("★**비용(달러)은 여기 적지 않습니다.** 단가는 코드 밖에서 바뀌므로 "
            "박아 두면 조용히 틀린 값이 됩니다. `토큰 × 그날의 단가` 로 "
            "계산하세요.")
        add()

    # ── 카운터별 소진 — ★어느 상한이 실제로 무는가 ────────────
    caps = {"tool_calls_used": budget.MAX_TOOL_CALLS,
            "events_used": budget.MAX_EVENTS,
            "propagations_used": budget.MAX_PROPAGATIONS,
            "hops_used": budget.MAX_HOPS}
    add("### 카운터별 소진 — 어느 상한이 실제로 무는가")
    add()
    add("| 카운터 | 상한 | 최대 사용 | 상한에 닿은 케이스 |")
    add("|---|---:|---:|---:|")
    overrun: list[tuple[str, int, int]] = []
    for name, cap in caps.items():
        used = [int(run.state.get(name) or 0) for run in runs.values()]
        peak = max(used) if used else 0
        at_cap = sum(1 for value in used if value >= cap)
        add(f"| `{name}` | {cap} | {peak} | {at_cap} |")
        if peak > cap:
            overrun.append((name, cap, peak))
    add()
    add("★상한값 4개는 아직 **실측 근거가 없는 잠정치**입니다(현황서 §9). 이 표가 "
        "그 근거입니다 — 한 번도 안 무는 상한과, 늘 무는 상한을 갈라 봅니다.")
    add()
    add("★**`propagations_used` 는 소진 판정 대상이 아닙니다**(2026-08-29 · `budget._CAPS`). "
        "`fetch_propagation` 은 Agent 도구가 아니라 결정론 노드라 반복 호출로 우회할 수 "
        "없고, 도구가 자기 상한 3 을 먼저 걸어 이 상한은 한 번도 문 적이 없습니다. "
        "위 표의 「상한에 닿은 케이스」는 이 줄에 한해 **소진 신호가 아니라 관측치**입니다.")
    add()

    # ★상한을 **넘긴** 카운터는 그 자체가 결함 신호다 — 「막는다」고 적혀 있는데
    #   넘었다면 자르는 단위와 세는 단위가 다르다는 뜻이다.
    if overrun:
        add("#### ⚠ 상한을 **넘긴** 카운터")
        add()
        for name, cap, peak in overrun:
            add(f"- `{name}` — 상한 **{cap}** 인데 최대 **{peak}** 을 썼습니다"
                f" (**{peak // cap}배**).")
        add()
        add("이건 상한이 낮다는 뜻이 **아닙니다.** 자르는 단위와 세는 단위가 "
            "다르다는 뜻입니다 — `fetch_propagation` 은 **입력(파급을 계산할 사건 수)** "
            "을 잘라 놓고 **출력(파급 행 수)** 을 씁니다. 사건 하나가 수십 행을 내므로 "
            "잘라도 카운터는 상한을 훌쩍 넘습니다. 그래서 「막는다」고 적힌 예산이 "
            "실제로는 막지 못하고, 루프가 끝난 뒤 플래그만 켭니다.")
        add()
        add("★**알려진 사례 하나는 2026-08-29 에 고쳤습니다**(Phase 10) — "
            "`propagations_used` 가 사건 수로 자르고 파급 행 수로 세던 것. "
            "`tests/graph/test_propagation_budget.py` 가 그 계약을 묶고 있으므로, "
            "이 표가 **다시 뜬다면 그건 새로운 단위 불일치**입니다. 어느 카운터가 "
            "떴는지부터 보세요.")
        add()
    if embed_misses:
        add(f"★캐시가 **{embed_misses}건 빗나갔습니다** — 그만큼 이 실행에서 직접 "
            "계산했고, 그 값은 실행마다 흔들립니다(현황서 §8-13). 기준선으로 쓰려면 "
            "`EMBED_CACHE_STRICT=1` 로 다시 재세요.")
        add()

    # ── 도구별 호출 빈도 ──────────────────────────────────────
    tool_totals: Counter = Counter()
    tool_cases: Counter = Counter()
    for run in runs.values():
        for tool, count in run.tools_used.items():
            tool_totals[tool] += count
            tool_cases[tool] += 1
    errors: Counter = Counter()
    for run in runs.values():
        errors.update(run.observed.tool_errors)

    add("### 도구별 호출 빈도")
    add()
    add("| 도구 | 호출 | 쓴 케이스 | 인용 가능 | 거부 |")
    add("|---|---:|---:|---|---:|")
    for tool in agent_tools.TOOL_NAMES:
        citable = "가능" if tool in citation.CITABLE_TOOLS else "불가"
        add(f"| `{tool}` | {tool_totals.get(tool, 0)} | {tool_cases.get(tool, 0)} | "
            f"{citable} | {errors.get(tool, 0)} |")
    add()
    never = [t for t in agent_tools.TOOL_NAMES if not tool_totals.get(t)]
    if never:
        add(f"★**한 번도 안 불린 도구: {', '.join('`' + t + '`' for t in never)}** — "
            "질문이 그 도구를 못 끌어오고 있습니다. 평가셋의 결함입니다.")
        add()

    # ── 3. 링(ring) — ★관측만 한다 ───────────────────────────
    add("## 3. 링(ring) — 관측만 합니다")
    add()
    add("링은 워크스페이스에서 몇 걸음 떨어진 관계인가입니다. **작을수록 안쪽**이고, "
        "관계는 링 순서로 줄을 세운 뒤에 자릅니다(설계서 §3).")
    add()
    for ring, label in _RING_LABEL.items():
        add(f"- **Ring {ring}** — {label[2:]}")
    add()
    seen_all: Counter = Counter()
    kept_all: Counter = Counter()
    cited_all: Counter = Counter()
    for run in runs.values():
        seen_all.update(run.observed.ring_seen)
        kept_all.update(run.observed.ring_kept)
        cited_all.update(run.observed.cited_rings)
    kept_total = sum(run.observed.relations_kept for run in runs.values())
    cut_total = sum(run.observed.relations_cut for run in runs.values())
    without_ring = sum(run.observed.cited_without_ring for run in runs.values())
    # ★**관계인데 링을 못 찾은 인용은 따로 센다.** 위와 섞으면 「인용이 전부
    #   사건·뉴스 근거였다」(정상)와 「되짚기가 끊겼다」(결함)가 같은 값이 된다.
    lost_ring = sum(run.observed.cited_relation_without_ring
                    for run in runs.values())

    add("| 링 | 도구가 본 관계 | 상한에 남은 것 | **최종 인용** |")
    add("|---|---:|---:|---:|")
    for ring in sorted(set(seen_all) | set(kept_all) | set(cited_all)):
        add(f"| R{ring} | {seen_all.get(ring, 0)} | {kept_all.get(ring, 0)} | "
            f"{cited_all.get(ring, 0)} |")
    add(f"| **합계** | {sum(seen_all.values())} | {kept_total} | "
        f"{sum(cited_all.values())} |")
    add()
    add(f"관계 kept **{kept_total}** · cut **{cut_total}** · "
        f"링 없는 근거 인용 **{without_ring}**건(사건·검색히트·뉴스 근거에는 링이 "
        f"없습니다 — **정상**) · 관계인데 링을 못 찾은 인용 **{lost_ring}**건"
        f"(**0 이어야 정상**).")
    add()

    # ★읽는 사람이 표를 보고 스스로 물어야 할 것을 대신 짚어 준다. **판정이
    #   아니다** — 링 랭킹을 바꿀지는 이 수치를 보고 사람이 정한다.
    notes: list[str] = []
    starved = [ring for ring in sorted(seen_all)
               if seen_all[ring] and not kept_all.get(ring)]
    if starved:
        notes.append(
            f"**Ring {', '.join(f'R{r}' for r in starved)} 는 본 것이 있는데 "
            f"상한에 하나도 못 남았습니다** — 안쪽 링이 먼저 자리를 채우고 끝났다는 "
            f"뜻입니다. 링 순서가 의도대로 동작한 결과일 수도, 상한이 너무 낮은 "
            f"것일 수도 있습니다.")
    uncited = [ring for ring in sorted(kept_all)
               if kept_all[ring] and not cited_all.get(ring)]
    if uncited:
        notes.append(
            f"**Ring {', '.join(f'R{r}' for r in uncited)} 는 재료로 남았지만 "
            f"한 번도 인용되지 않았습니다** — 링 순서가 프롬프트까지는 갔는데 "
            f"답변까지는 안 갔다는 뜻입니다.")
    if lost_ring:
        notes.append(
            f"★**관계를 인용했는데 링을 못 찾은 것이 {lost_ring}건 있습니다.** "
            f"`get_relations` 가 돌려준 관계는 전부 `ring_by_edge` 에 담기므로 "
            f"여기 오면 위쪽 규칙이 바뀐 것입니다 — 0 이 아니면 결함 신호입니다.")
    if without_ring > sum(cited_all.values()):
        notes.append(
            f"**최종 인용 {sum(cited_all.values()) + without_ring}건 중 "
            f"{without_ring}건이 관계가 아닙니다**(사건·뉴스 근거). 링 랭킹을 "
            f"손대도 인용의 대부분은 안 움직인다는 뜻입니다.")
    if notes:
        add("### 이 표에서 읽히는 것 — ★판정이 아니라 관측입니다")
        add()
        for note in notes:
            add(f"- {note}")
        add()

    # ── 4. 케이스 ─────────────────────────────────────────────
    # ── 4. 주장(claim) — ★관측만 한다 ────────────────────────
    add("## 4. 주장(claim) — 관측만 합니다")
    add()
    checked = [run for run in runs.values() if run.observed.claims_checked]
    claims_total = sum(run.observed.claims_total for run in runs.values())
    uncited = sum(run.observed.claims_uncited for run in runs.values())
    no_text = sum(run.observed.claims_no_text for run in runs.values())
    unlinked = sum(run.observed.claims_unlinked for run in runs.values())

    add("| 항목 | 값 |")
    add("|---|---|")
    add(f"| `check_claims` 를 지난 케이스 | {len(checked)} / {len(CASES)} |")
    add(f"| 주장 총계 | {claims_total} |")
    add(f"| ★**uncited**(근거를 안 단 주장) | {uncited}"
        + (f" · **{uncited / claims_total:.1%}**" if claims_total else "") + " |")
    add(f"| no_text(근거 원문을 못 찾음) | {no_text} |")
    add(f"| unlinked(질문 의도와 연결 없음) | {unlinked} |")
    add()
    add("★**`check_claims` 를 지난 케이스 수를 먼저 보세요.** 주장 0건과 "
        "「그 노드를 안 지났다」는 다른 사실인데, 총계만 보면 같은 0 입니다.")
    add()
    add("★**이 값으로 답변 품질을 판정하지 않습니다.** `claim_check` 는 검증기가 "
        "아니라 **의심 탐지기**라 낮은 점수가 곧 거짓이 아닙니다. 답변 모델을 "
        "바꿀 때 **바꾸기 전 값과 비교**하라고 둔 자리입니다.")
    add()

    add("## 5. 케이스")
    add()
    for index, case in enumerate(CASES, start=1):
        run = runs[case.id]
        add(f"### {index}. `{case.id}` — **{_verdict(run)}**")
        add()
        add("| | |")
        add("|---|---|")
        add(f"| 질문 | `{case.question}` |")
        add(f"| 기대 anchor_source | {case.expected_anchor_source.value} |")
        add(f"| 기대 Agent 호출 | {'예' if case.expects_agent else '**아니오**'} |")
        add(f"| 끌어오려는 도구 | "
            f"{', '.join('`' + t + '`' for t in case.expects_tools) or '없음'} |")
        add(f"| 무엇을 검증하나 | {case.verifies} |")
        add(f"| 커버 분기 | {', '.join(case.coverage)} |")
        source = run.anchor_source.value if run.anchor_source else "—"
        add(f"| 실제 anchor | {source} · {run.anchor_names or '없음'} |")
        add(f"| 실제 도구 | {_tools(run.tools_used)} (호출 {run.tool_calls}) |")
        add(f"| 재료 | 관계 {len(run.relations)} · 사건 {len(run.events)} · "
            f"근거 {len(run.evidence)} · 최종 인용 {len(run.sources)} |")
        add(f"| 링 | 본 것 {_rings(run.observed.ring_seen)} / "
            f"인용 {_rings(run.observed.cited_rings)} |")
        add(f"| 임베딩 | {run.observed.embed_calls}회 "
            f"(적중 {run.observed.embed_cache_hits} · "
            f"빗나감 {run.observed.embed_cache_misses}) |")
        add(f"| 예산 | 루프가 잘림 "
            f"{'**예**' if run.observed.agent_stopped_by_budget else '아니오'}"
            f" · 최종 플래그 {'예' if run.budget_exhausted else '아니오'}"
            f" (호출 {run.tool_calls}/{budget.MAX_TOOL_CALLS} ·"
            f" 사건 {int(run.state.get('events_used') or 0)}/{budget.MAX_EVENTS} ·"
            f" 파급 {int(run.state.get('propagations_used') or 0)}"
            f"/{budget.MAX_PROPAGATIONS}) |")
        add(f"| 소요 | {run.took_ms}ms |")
        if case.known_issue:
            add(f"| ⚠ known issue | {case.known_issue} |")
        if run.error:
            add(f"| ★오류 | `{run.error}` |")
        add()

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--out", type=Path,
                        help="쓸 파일. 없으면 표준출력")
    args = parser.parse_args()

    runs = run_all()
    text = build(runs)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out} — {len(CASES)} 케이스")
    else:
        print(text)


if __name__ == "__main__":
    main()
