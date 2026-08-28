"""Agent 평가셋 fixture — ★**한 번의 실행으로 판정과 문서를 함께** 낸다.

★왜 여기 있나. 전에는 `runs` 가 테스트 모듈에 있었고 `report.py` 가 `run_all()` 을
  **따로** 불렀다. 그래서 판정 한 번 · 문서 한 번, **LLM 왕복이 두 배**로 나갔다.
  20 케이스 × (Agent 턴 + 도구 루프 + 생성)이라 그 두 배가 그대로 비용이다.

  게다가 두 실행은 **같은 값을 안 준다** — 도구 선택이 LLM 몫이라 실행마다 총
  호출이 흔들린다(실측 2026-08-28, 같은 코드로 3회: 36 · 33 · 37). 그러면
  「테스트는 통과했는데 문서 수치는 다르다」가 생기고, 그때 어느 쪽이 참인지
  가릴 방법이 없다. `report.py` 독스트링이 「판정과 같은 실행 경로를 쓴다」고
  적어 놓고도 **경로만 같고 실행은 달랐다.**

    pytest tests/agent -m needs_llm -q \
        --agent-eval-report=docs/BizNode_Agent_평가셋.md

  플래그를 주면 **그 실행의 결과**로 문서를 쓴다. 안 주면 판정만 한다.
  `python -m tests.agent.eval.report` 도 그대로 둔다 — 판정 없이 문서만 필요할
  때가 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent.eval.runner import CaseRun, run_all

from conftest import AGENT_EVAL_REPORT as _OPTION


@pytest.fixture(scope="session")
def runs(request) -> dict[str, CaseRun]:
    """★케이스마다 **한 번만** 돌린다 — 세션 스코프인 이유가 비용이다."""
    got = run_all()

    out = request.config.getoption(_OPTION)
    if out:
        # ★판정이 다 끝난 **뒤에** 쓴다(finalizer). 먼저 쓰면 판정이 실패해도
        #   문서가 남아 「통과한 결과」로 읽힌다.
        def _write() -> None:
            from tests.agent.eval.report import build

            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(build(got), encoding="utf-8")
            print(f"\n[agent-eval] 결과 문서 → {path} ({len(got)} 케이스)")

        request.addfinalizer(_write)
    return got
