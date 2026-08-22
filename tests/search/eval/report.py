"""평가셋을 전부 실행해 현황 문서(`docs/BizNode_Search_Layer_평가셋.md`)를 만든다.

    .venv-wsl/bin/python -m tests.search.eval.report   # 표준출력으로
    .venv-wsl/bin/python -m tests.search.eval.report \
        -o docs/BizNode_Search_Layer_평가셋.md         # 문서 갱신

관측값(`현재 실제 결과`)은 여기서 직접 검색을 돌려 얻고, PASS/FAIL은 **pytest를
그대로 한 번 더 돌려** 그 판정을 옮긴다 — 판정 로직을 두 벌 두면 문서와 테스트가
갈라지기 때문이다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from collections import Counter
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
_TEST_PATH = "tests/search/eval"
# 짧은 요약(-rA)의 판정 줄만 집는다. 라이브러리 로그에도 "ERROR ..."가 섞여
# 나오므로 nodeid 모양(경로::테스트)까지 맞춘 것만 받는다.
_OUTCOME_RE = re.compile(
    r"^(PASSED|FAILED|XFAIL|XPASS|ERROR|SKIPPED)\s+(tests/\S+::\S+)")


def _pytest_outcomes() -> dict[str, str]:
    """nodeid -> 판정. pytest를 서브프로세스로 돌려 `-rA` 요약을 읽는다."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", _TEST_PATH, "-q", "-rA", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=_ROOT, capture_output=True, text=True,
    )
    outcomes: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        match = _OUTCOME_RE.match(line.strip())
        if match:
            outcomes[match.group(2)] = match.group(1)
    return outcomes


def _verdict(outcome: Optional[str]) -> str:
    return {
        "PASSED": "PASS",
        "XFAIL": "FAIL (known issue)",
        "XPASS": "PASS — 결함이 고쳐졌다. 평가셋을 갱신하라",
        None: "?",
    }.get(outcome, outcome or "?")


def _observed(run) -> str:
    result, hits = run.result, run.result.hits
    parts = [
        f"mode={run.query.mode.value}",
        f"anchor={run.anchor!r}",
        f"direction={run.query.direction.value if run.query.direction else '없음'}",
        f"edge_types={list(run.query.edge_types or []) or '없음'}",
        f"{result.total}건",
    ]
    if hits:
        sources = sorted({s for h in hits for s in h.sources})
        types = Counter(h.entity_type.value for h in hits)
        parts.append("/".join(sources))
        parts.append(" ".join(f"{k} {v}" for k, v in types.most_common()))
        fresh = Counter((h.freshness or {}).get("status") for h in hits
                        if (h.freshness or {}).get("status"))
        if fresh:
            parts.append("신선도 " + " ".join(f"{k} {v}" for k, v in fresh.most_common()))
        parts.append("상위 " + " · ".join(h.name for h in hits[:3]))
    parts.append(f"{run.took_ms}ms")
    return " · ".join(parts)


def _expected_result_spec(case) -> str:
    bits = [f"source={sorted(case.expected_sources)}"]
    if case.exact_total is not None:
        bits.append(f"정확히 {case.exact_total}건")
    else:
        bits.append(f"{case.min_hits}건 이상")
    allowed = case.allowed_types()
    if allowed is not None:
        bits.append(f"엔티티 {sorted(allowed)} 안")
    if case.must_contain_entity_types:
        bits.append("반드시 포함: " + ", ".join(e.value for e in case.must_contain_entity_types))
    if case.must_include:
        bits.append("고정 기업: " + ", ".join(f"{n}({i})" for n, i in case.must_include))
    if case.workspace_keys:
        bits.append(f"workspace_keys={list(case.workspace_keys)}")
    if case.request_edge_types:
        bits.append(f"요청 edge_types={list(case.request_edge_types)}")
    return " · ".join(bits)


def build(runs, outcomes: dict[str, str], today: str) -> str:
    from tests.search.eval.cases import CASES

    lines: list[str] = []
    add = lines.append

    add("# BizNode Search Layer — 회귀 평가셋")
    add("")
    add("> **이 파일은 생성물입니다.** 손으로 고치지 말고 아래로 다시 만드세요.")
    add("> ```bash")
    add("> .venv-wsl/bin/python -m tests.search.eval.report \\")
    add(">     -o docs/BizNode_Search_Layer_평가셋.md")
    add("> ```")
    add("")
    add(f"마지막 실행 **{today}** · 케이스 **{len(CASES)}개**")
    add("")
    add("케이스 정의는 `tests/search/eval/cases.py`, 판정은 "
        "`tests/search/eval/test_search_eval.py`에 있습니다.")
    add("")
    add("```bash")
    add(".venv-wsl/bin/python -m pytest tests/search/eval -q       # 평가셋만")
    add(".venv-wsl/bin/python -m pytest tests/ -q                  # 전체")
    add("```")
    add("")

    # ── 요약 ──
    tally = Counter(_verdict(outcomes.get(f"{_TEST_PATH}/test_search_eval.py::test_case[{c.id}]"))
                    for c in CASES)
    add("## 1. 한눈에 보기")
    add("")
    add("| 판정 | 케이스 |")
    add("|---|---|")
    for verdict, count in tally.most_common():
        add(f"| {verdict} | {count} |")
    add("")
    add("| # | 케이스 | 질의 | 판정 방식 | 결과 |")
    add("|---:|---|---|---|---|")
    for i, case in enumerate(CASES, 1):
        nodeid = f"{_TEST_PATH}/test_search_eval.py::test_case[{case.id}]"
        kind = "고정값" if case.kind == "fixed" else "구조 조건"
        add(f"| {i} | `{case.id}` | {case.query} | {kind} | "
            f"{_verdict(outcomes.get(nodeid))} |")
    add("")

    # ── 커버리지 ──
    add("## 2. 검색 분기 커버리지")
    add("")
    add("| 분기 | 케이스 |")
    add("|---|---|")
    tags: dict[str, list[str]] = {}
    for case in CASES:
        for tag in case.coverage:
            tags.setdefault(tag, []).append(case.id)
    for tag in sorted(tags):
        add(f"| {tag} | {', '.join(f'`{i}`' for i in tags[tag])} |")
    add("")

    # ── 케이스별 상세 ──
    add("## 3. 케이스")
    add("")
    for i, case in enumerate(CASES, 1):
        nodeid = f"{_TEST_PATH}/test_search_eval.py::test_case[{case.id}]"
        run = runs[case.id]
        add(f"### {i}. `{case.id}` — **{_verdict(outcomes.get(nodeid))}**")
        add("")
        add("| | |")
        add("|---|---|")
        add(f"| query | `{case.query}` |")
        add(f"| expected_mode | {case.expected_mode.value} |")
        add(f"| expected_anchor | {case.expected_anchor!r} |")
        if case.expected_direction is not None:
            direction = case.expected_direction.value
        elif case.expected_edge_types:
            direction = "없음(양방향)"
        else:
            direction = "해당 없음(관계 질의가 아니다)"
        add(f"| expected_direction | {direction} |")
        add(f"| expected_edge_type | {list(case.expected_edge_types) or '없음'} |")
        add(f"| expected_result/source | {_expected_result_spec(case)} |")
        add(f"| 판정 방식 | {'고정값(기업명·corp_code를 못 박는다)' if case.kind == 'fixed' else '구조 조건만(기업명 미고정)'} |")
        add(f"| 무엇을 검증하나 | {case.verifies} |")
        add(f"| 커버 분기 | {', '.join(case.coverage)} |")
        add(f"| 현재 실제 결과 | {_observed(run)} |")
        if case.known_issue:
            add(f"| ⚠ known issue | {case.known_issue} |")
        add("")

    # ── 알려진 결함 ──
    known = [c for c in CASES if c.known_issue]
    add("## 4. 알려진 결함 (이번 작업에서 고치지 않았다)")
    add("")
    add("`xfail(strict=True)`로 돌아갑니다 — **지금은 실패로 집계되고**, 결함이 "
        "고쳐지면 XPASS로 뒤집혀 평가셋을 갱신하라고 알립니다.")
    add("")
    for case in known:
        run = runs[case.id]
        add(f"### `{case.id}` — {case.query}")
        add("")
        add(f"- **기대** {case.expected_mode.value} · anchor={case.expected_anchor!r}")
        add(f"- **실제** {_observed(run)}")
        add(f"- **왜** {case.known_issue}")
        add("")

    # ── 심층 판정 ──
    add("## 5. 분기별 심층 판정")
    add("")
    add("케이스 공통 판정으로는 못 보는 것들입니다. 케이스 하나에 묶이지 않아 "
        "따로 돌아갑니다.")
    add("")
    add("| 테스트 | 결과 |")
    add("|---|---|")
    for nodeid, outcome in sorted(outcomes.items()):
        if "test_case[" in nodeid:
            continue
        add(f"| `{nodeid.split('::')[-1]}` | {_verdict(outcome)} |")
    add("")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output")
    parser.add_argument("--today", default=date.today().isoformat(),
                        help="문서에 적을 실행일 (기본: 오늘)")
    args = parser.parse_args()

    load_dotenv()
    from search.service.anchor_extractor import AnchorExtractor
    from search.service.factory import build_orchestrator
    from search.repository.postgres_repository import PostgresRepository
    from tests.search.eval.runner import run_all

    runs = run_all(build_orchestrator(), AnchorExtractor(PostgresRepository()))
    text = build(runs, _pytest_outcomes(), args.today)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
