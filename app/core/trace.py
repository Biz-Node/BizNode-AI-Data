"""요청 하나를 여러 계층의 로그 줄로 잇는 상관관계 키.

검색 한 번은 SearchOrchestrator → GraphSearcher → ResultRanker → 근거 조립 →
LLM 까지 다섯 경계를 지난다. 요청이 둘만 겹쳐도 로그 줄이 섞여 「이 evidence_id
가 저 query 에서 나온 것인가」를 못 가린다. 그래서 요청마다 짧은 id 를 발급하고
모든 줄 앞에 붙인다.

★**함수 시그니처로 넘기지 않는다.** 위 다섯 경계에 파라미터를 하나 추가하면
  그 시그니처를 쓰는 테스트까지 전부 바뀐다. `contextvars` 는 같은 컨텍스트
  안에서 저절로 따라가고, `run_in_threadpool`(anyio)도 컨텍스트를 복사해
  워커 스레드까지 이어진다.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any, MutableMapping

# 요청 경계를 안 거친 호출(배치·단위테스트)은 이 값으로 찍힌다 — 죽지 않는다.
_NO_TRACE = "-"

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default=_NO_TRACE)


def new_trace_id() -> str:
    """요청 경계에서 한 번 부른다. 이후 이 컨텍스트의 모든 로그에 붙는다."""
    trace_id = uuid.uuid4().hex[:8]
    _TRACE_ID.set(trace_id)
    return trace_id


def current_trace_id() -> str:
    return _TRACE_ID.get()


def reset_trace_id() -> None:
    """경계 밖 상태로 되돌린다 — 테스트가 서로 오염되지 않게."""
    _TRACE_ID.set(_NO_TRACE)


class _TracePrefix(logging.LoggerAdapter):
    """★어댑터로 붙인다 — 로그 **포맷**을 바꾸지 않는다. 포맷에 필드를 추가하면
    trace id 를 모르는 uvicorn 자신의 로그 줄까지 영향을 받는다."""

    def process(self, msg: Any,
                kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        return f"[{current_trace_id()}] {msg}", kwargs


def trace_logger(name: str) -> logging.LoggerAdapter:
    return _TracePrefix(logging.getLogger(name), {})


_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# root 를 INFO 로 열면 이 라이브러리들이 요청마다 자기 로그를 쏟아낸다. 실측
# (「삼성전자에 납품하는 기업」 1회)에서 우리 trace 줄 5개에 라이브러리 줄 10개가
# 섞여 정작 추적할 줄이 파묻혔다. 경고 이상만 남긴다.
_NOISY_LIBRARIES = ("httpx", "httpcore", "chromadb", "openai", "urllib3", "neo4j")


def configure_logging(level: str) -> None:
    """앱 기동 때 한 번 부른다. 이게 없으면 root 로거가 WARNING 이라 아래 계층의
    trace 로그가 **통째로 사라진다** — 이 저장소의 기존 `log.info` 들도 그동안
    같은 이유로 안 보였다.

    ★`basicConfig` 만으로 끝내지 않는다. root 에 핸들러가 이미 붙어 있으면
      `basicConfig` 는 **아무것도 하지 않고 돌아간다.** uvicorn 이 자기 핸들러를
      먼저 붙여 둔 상태가 정확히 그 경우라, 레벨이 조용히 안 먹는다.
    """
    logging.basicConfig(level=level, format=_FORMAT)
    logging.getLogger().setLevel(level)
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
