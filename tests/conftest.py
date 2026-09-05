"""`tests/` 공통 conftest — **관측 장치가 저장소 로그를 건드리지 않게 막는다.**

★왜 이 파일이 생겼나 (2026-09-05)

`logs/queries.jsonl` 은 B-1(실사용 kw 상태 분포)의 **측정 표본**인데, 테스트가
같은 파일에 쓰고 있었다. `trace_id` 는 요청 경계에서 발급되므로 `TestClient` 를
쓰는 테스트도 그것을 받는다 — 현황서 §6-0 B-1 이 전제한 「집계할 때
`trace_id != "-"` 로 배치를 뺀다」가 **성립하지 않았다.**

    실측 2026-09-05 — 4,192행 중 trace_id 보유 3,369행(80%)
                      상위 질문 "q" 1,727 · "x" 195 · 평가셋 질의

라벨 없이 정답/누락/오탐을 가르려고 만든 표본이 **내가 지은 질의로 채워지는** 것이라,
그대로 집계하면 B-1 이 재려던 것과 반대의 답이 나온다.

★루트 `conftest.py` 가 아니라 여기 있는 이유 — 저쪽은 「fixture 를 추가하지 마라」를
  못 박아 두었고(옵션 등록 전용), 이건 `tests/` 전체에 걸려야 하는 그물이다.

★환경변수(`BIZNODE_QUERY_LOG=off`)로 안 되는 이유 — `querylog` 가 `_DISABLED` 와
  `_PATH` 를 **import 시점에** 굳힌다. conftest 가 도는 시점엔 이미 늦을 수 있어,
  모듈 속성을 직접 덮는 쪽이 import 순서와 무관하게 확실하다.
"""

from __future__ import annotations

import pytest

from app.core import querylog


@pytest.fixture(autouse=True)
def _query_log_off(monkeypatch):
    """모든 테스트에서 질의 로깅을 끈다.

    ★`test_querylog.py` 는 자기 `sink` 픽스처로 다시 켠다 — autouse 가 먼저 돌고
      그 뒤에 덮으므로 그쪽 검사는 그대로 산다. 끄는 것을 검사하는 테스트도 있다.
    """
    monkeypatch.setattr(querylog, "_DISABLED", True)
