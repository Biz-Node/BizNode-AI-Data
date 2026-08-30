"""「오늘」의 **단일 출처**.

★왜 이 파일이 있나 (2026-08-30)

  `app/` 안에 오늘을 부르는 코드가 **한 곳도 없었다**(`date.today()` 는
  `batch/audit/graph.py` 에만 있었다). 그래서 두 가지가 동시에 막혀 있었다.

      「최근」을 못 자른다   `evidence_selector` 가 기준일을 모르니 시간 창을
                            만들 수 없다.
      「최근」을 못 읽는다   프롬프트에 오늘이 안 실려 모델이 재료의 날짜를
                            무엇과 견줄지 모른다(`prompt.assemble`).

  둘이 **같은 값을 봐야 한다.** 창을 자른 기준일과 프롬프트가 말하는 오늘이
  다르면, 「최근 12개월을 우선했다」고 적어 놓고 다른 12개월을 실은 것이 된다.
  그래서 각자 `date.today()` 를 부르지 않고 여기를 지난다.

★**테스트가 고정할 수 있어야 한다.** 시간에 기대는 코드는 오늘이 바뀌면 조용히
  깨진다. 함수 하나로 좁혀 두면 `monkeypatch.setattr(clock, "today", ...)` 로
  한 자리만 잡으면 된다.
"""

from __future__ import annotations

from datetime import date


def today() -> date:
    """오늘. **여기 말고 다른 데서 `date.today()` 를 부르지 않는다.**"""
    return date.today()
