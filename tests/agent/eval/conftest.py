"""Agent 평가셋 fixture — ★**한 번의 실행으로 판정과 문서를 함께** 낸다.

★왜 여기 있나. 전에는 `runs` 가 테스트 모듈에 있었고 `report.py` 가 `run_all()` 을
  **따로** 불렀다. 그래서 판정 한 번 · 문서 한 번, **LLM 왕복이 두 배**로 나갔다.
  20 케이스 × (Agent 턴 + 도구 루프 + 생성)이라 그 두 배가 그대로 비용이다.

  게다가 두 실행은 **같은 값을 안 준다** — 도구 선택이 LLM 몫이라 실행마다 총
  호출이 흔들린다(실측 2026-08-28, 같은 코드로 3회: 36 · 33 · 37). 그러면
  「테스트는 통과했는데 문서 수치는 다르다」가 생기고, 그때 어느 쪽이 참인지
  가릴 방법이 없다. `report.py` 독스트링이 「판정과 같은 실행 경로를 쓴다」고
  적어 놓고도 **경로만 같고 실행은 달랐다.**

    pytest tests/agent -m needs_llm -q \\
        --agent-eval-report=docs/BizNode_Agent_평가셋.md

  플래그를 주면 **그 실행의 결과**로 문서를 쓴다. 안 주면 판정만 한다.
  `python -m tests.agent.eval.report` 도 그대로 둔다 — 판정 없이 문서만 필요할
  때가 있다.

★**반복은 그 위에 얹는다**(2026-08-29). `--agent-eval-repeat=N` 은 평가셋 전체를
  N 번 돌려 **변동폭**을 재는 장치다. 여기서 갈라 둔 것이 두 가지다:

      판정      **1회차만** 본다. 링과 같은 규약이다 — 변동폭은 관측이지
                판정이 아니고, 여기에 임계값을 걸면 재는 도구가 판정기가 된다
      문서      **패스 전체**를 받는다. 변동폭은 여러 패스가 있어야 나온다

  ★비용이 그대로 N 배다. 기본값이 1 인 이유가 그것이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from tests.agent.eval.runner import CaseRun, run_all_n

from conftest import AGENT_EVAL_REPEAT as _REPEAT
from conftest import AGENT_EVAL_REPORT as _OPTION


@pytest.fixture(scope="session")
def repeats(request) -> list[Mapping[str, CaseRun]]:
    """평가셋 전체를 `--agent-eval-repeat` 번 돌린다. **세션 스코프인 이유가 비용이다.**"""
    times = int(request.config.getoption(_REPEAT) or 1)
    got = run_all_n(times)

    out = request.config.getoption(_OPTION)
    if out:
        # ★판정이 다 끝난 **뒤에** 쓴다(finalizer). 먼저 쓰면 판정이 실패해도
        #   문서가 남아 「통과한 결과」로 읽힌다.
        def _write() -> None:
            from tests.agent.eval.report import build

            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(build(got[0], passes=got), encoding="utf-8")
            print(f"\n[agent-eval] 결과 문서 → {path} "
                  f"({len(got[0])} 케이스 × {len(got)} 회)")

        request.addfinalizer(_write)
    return got


@pytest.fixture(scope="session")
def runs(repeats) -> Mapping[str, CaseRun]:
    """★판정이 보는 것은 **1회차**다.

    반복을 켜도 판정 기준은 안 움직인다 — N 회 중 한 번이라도 실패하면 깨지는
    식으로 만들면, 평가셋이 「도구 선택은 결정론이 아니다」라는 **이미 아는
    사실** 때문에 상시 빨간불이 된다. 그 흔들림은 보고서에서 수치로 본다.
    """
    return repeats[0]
