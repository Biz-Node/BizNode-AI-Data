"""저장소 루트 conftest — **명령줄 옵션 등록 전용**이다.

★여기 있는 이유는 pytest 규칙 하나 때문이다: `pytest_addoption` 은 **rootdir 의
  conftest.py 또는 플러그인에서만** 읽힌다. 하위 디렉터리 conftest 에 두면
  조용히 무시되는 것이 아니라 `unrecognized arguments` 로 **실행이 죽는다.**

  그래서 옵션 **등록**만 여기 두고, 그 옵션을 실제로 쓰는 fixture 는 쓰는 자리
  옆에 둔다(`tests/agent/eval/conftest.py`). 루트에 로직을 모으면 어느 테스트가
  무엇을 쓰는지 여기서부터 되짚어야 한다.

★**fixture 를 여기 추가하지 마라.** 루트 conftest 의 fixture 는 전 테스트에
  걸리므로, 한 계층에만 필요한 것을 여기 두면 나머지가 그 존재를 모른 채
  영향을 받는다.
"""

from __future__ import annotations

# Agent 평가셋 결과 문서를 **판정과 같은 실행**에서 쓰게 하는 옵션.
# 쓰는 자리는 `tests/agent/eval/conftest.py::runs`.
AGENT_EVAL_REPORT = "--agent-eval-report"


def pytest_addoption(parser) -> None:
    parser.addoption(
        AGENT_EVAL_REPORT, action="store", default=None, metavar="PATH",
        help="Agent 평가셋 결과 문서를 이 경로에 쓴다. ★판정과 **같은 실행**의 "
             "결과를 쓰므로 LLM 왕복이 한 번으로 끝난다 "
             "(예: --agent-eval-report=docs/BizNode_Agent_평가셋.md)")
