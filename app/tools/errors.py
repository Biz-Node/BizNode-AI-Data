"""도구 실패 — **빈 결과와 구별한다.**

★왜 예외인가. `company_service.events_of()` 는 못 찾은 key 에 **예외가 아니라
  빈 목록**을 준다. 그래서 잘못된 `corp_code` 를 넘기면 「이 기업에 사건이
  없다」와 구별이 안 된다. 도구 계층이 그 둘을 가르는 자리다.

    ToolError        입력이 틀렸다 — 범위 밖 key · 해소 실패
    빈 list          입력은 맞고 **정말로 없다**

★`relation_service.event_impact()` 가 이미 같은 규약을 쓴다 —
  `None`(사건 노드를 못 찾음) vs `[]`(파급이 없음).
"""

from __future__ import annotations


class ToolError(RuntimeError):
    """도구가 **답을 만들 수 없는** 상태. 빈 결과가 아니다."""


class OutOfScopeKey(ToolError):
    """서버가 정한 재료 범위 밖의 key 를 요구했다.

    ★Agent 가 붙으면 이게 방어선이 된다. 범위를 **인자로 받지 않는** 이유가
      여기 있다 — Agent 가 넘기면 Agent 가 넓힐 수 있다.
    """


class KeyNotResolved(ToolError):
    """그래프에서 그 key 에 해당하는 Company 를 못 찾았다.

    ★0건으로 돌려주면 안 된다. 「해소됐다 ≠ 그래프에 있다」이고
      (`corp_code_master` 118,535건 대 그래프 Company 3,432건), 조용히 0건이면
      「사건이 없는 기업」으로 읽힌다.
    """
