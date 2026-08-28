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
from typing import Optional

from app.graph import budget
from app.tools import agent_tools, citation
from tests.agent.eval.cases import CASES
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


def build(runs: dict[str, CaseRun]) -> str:
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
        add("★**이번 단계에서 고치지 않았습니다.** Phase 8 은 현재 동작을 고정한 채 "
            "재는 단계이고, 이 표가 그 측정 결과입니다. 고치면 무엇을 쟀는지가 "
            "흐려집니다.")
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

    add("| 링 | 도구가 본 관계 | 상한에 남은 것 | **최종 인용** |")
    add("|---|---:|---:|---:|")
    for ring in sorted(set(seen_all) | set(kept_all) | set(cited_all)):
        add(f"| R{ring} | {seen_all.get(ring, 0)} | {kept_all.get(ring, 0)} | "
            f"{cited_all.get(ring, 0)} |")
    add(f"| **합계** | {sum(seen_all.values())} | {kept_total} | "
        f"{sum(cited_all.values())} |")
    add()
    add(f"관계 kept **{kept_total}** · cut **{cut_total}** · "
        f"링 없는 근거 인용 **{without_ring}**건(사건·뉴스 근거에는 링이 없습니다).")
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
    add("## 4. 케이스")
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
