"""Agent 가 **부를 수 있는** 도구 — LangChain 바인딩과 노출 경계.

    Agent(LLM) → 여기(바인딩) → app/tools/*_tools.py → Service → 저장소

★**노출 경계가 이 파일의 본체다.** 무엇을 안 주느냐가 무엇을 주느냐보다 중요하다.

    안 준다  `get_propagation`   계약 1번 — 주어진 Event 의 파급을 계산하는
                                 **내부 primitive** 다. 도구로 열면 Agent 가
                                 파급을 재료로 끌어오는 통로가 된다
    안 준다  근거 수집            계약 2번 — `evidence_validation` 이 탐색 결과의
                                 합집합을 **결정론적으로** 모으는 마감 단계다.
                                 Agent 가 임의의 evidence 를 고르면 안 된다
    안 준다  `search_company`     계약 3번 — 요청의 초기 scope 는 서버가 정한다.
                                 Agent 가 기업을 찾아 넣으면 scope 가 뚫린다

★**인자도 좁힌다.** 아래 도구들은 원본보다 인자가 적다.

    get_events(keys)          `intent` 를 안 받는다 — 「무엇을 중요하게 볼지」를
                              LLM 이 정하면 그건 재료 범위를 고르는 것이다.
                              서버가 `ToolContext.intent` 에 실어 보낸다
    get_relations(keys)       `edge_types`·`direction` 을 안 받는다 — 같은 이유
    get_business_overview(key) `year` 를 안 받는다 — 최신 연도로 고정

  4원칙 ① 이 「범위를 인자로 받지 않는다」인 이유가 여기서 실현된다. 부르는
  쪽이 LLM 이라, 인자로 두면 방어가 아니라 장식이 된다.

★**결과는 두 갈래로 나간다.**

    Agent 에게   짧은 JSON 문자열 — 다음에 무엇을 부를지 고르는 데 필요한 만큼
    뒤 노드에게  DTO 원본 — `_COLLECTED` 에 쌓아 `evidence_validation` 이 읽는다

  문자열만 남기면 뒤 노드가 LLM 이 본 텍스트를 **다시 파싱**해야 한다. 그건
  같은 사실을 두 번 만드는 것이고, 두 벌은 반드시 갈린다.

★**`ToolError` 를 예외로 새우지 않는다.** 범위 밖 key 를 부른 것은 Agent 의
  실수이고, 거기서 그래프가 죽으면 답변이 아예 안 나간다. 대신 **Agent 가 읽는
  오류 문자열**로 돌려줘 스스로 고치게 한다 — 다만 `_COLLECTED` 에는 아무것도
  안 쌓이므로 **재료로는 새지 않는다.**
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional, Sequence

from app.core import observe
from app.core.trace import trace_logger
from app.tools import company_tools, graph_tools, scope, search_tools
from app.tools.errors import ToolError

log = trace_logger(__name__)

# 도구가 만든 DTO 를 노드가 거둬 가는 자리. **노드가 열고 노드가 닫는다.**
_COLLECTED: ContextVar[Optional[dict[str, list[Any]]]] = ContextVar(
    "agent_tool_results", default=None)


@contextmanager
def collecting():
    """이 블록 안에서 도구가 만든 DTO 를 모은다. `{도구이름: [DTO, ...]}`.

    ★`ContextVar` 를 **노드 안에서 열고 그 안에서 닫는다.** LangGraph 는 노드마다
      컨텍스트를 복사하므로 노드에서 세운 값은 다음 노드로 안 넘어간다 — 그래서
      노드가 블록을 나가기 전에 **State 로 옮겨 담아야** 한다.
    """
    bucket: dict[str, list[Any]] = {}
    token = _COLLECTED.set(bucket)
    try:
        yield bucket
    finally:
        _COLLECTED.reset(token)


def _record(tool: str, items: Sequence[Any]) -> None:
    bucket = _COLLECTED.get()
    if bucket is None:          # 수집 블록 밖 — 직접 호출·테스트. 조용히 넘어간다
        return
    bucket.setdefault(tool, []).extend(items)


def _dump(items: Sequence[Any]) -> str:
    """DTO 목록 → Agent 가 읽는 짧은 JSON."""
    return json.dumps([i.model_dump(exclude_none=True) for i in items],
                      ensure_ascii=False)


def _guard(tool: str, fn, *args, **kwargs) -> str:
    """도구 하나를 부르고 **결과를 두 갈래로** 내보낸다. 실패는 문자열로.

    ★관측도 여기서 한다 — 7종이 **전부 이 깔때기를 지나므로** 도구마다 세는
      코드를 붙이면 한 곳만 빠뜨려도 「그 도구는 안 불렸다」로 읽힌다.
      `observe` 는 버킷이 안 열려 있으면 no-op 이라 운영 경로에 비용이 없다.
    """
    try:
        got = fn(*args, **kwargs)
    except ToolError as exc:
        # ★Agent 가 읽고 고칠 수 있게 문자열로. 재료로는 새지 않는다
        observe.record_tool_error(tool)
        log.info("agent_tool.%s 거부 — %s", tool, exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    items = [got] if (got is not None and not isinstance(got, list)) else (got or [])
    _record(tool, items)
    observe.record_tool(tool, len(items))
    log.info("agent_tool.%s -> %d", tool, len(items))
    return _dump(items)


# ══════════════════════════════════════════════════════════════════
#  도구 본체 — 인자가 원본보다 좁다
# ══════════════════════════════════════════════════════════════════


def get_relations(keys: list[str]) -> str:
    """이 기업들의 관계(공급·협력·경쟁·소송·지분 등)를 가져온다."""
    return _guard("get_relations", graph_tools.get_relations, keys)


def get_events(keys: list[str]) -> str:
    """이 기업들에 일어난 사건(규제수사·분쟁소송·사고 등)을 가져온다."""
    ctx = scope.context()
    return _guard("get_events", graph_tools.get_events, keys,
                  (ctx.intent if ctx else ""))


def search_news(query: str, keys: list[str]) -> str:
    """이 기업들에 관한 보도 근거를 의미검색으로 찾는다. 기사 전문은 없다."""
    return _guard("search_news", search_tools.search_news, query, keys)


def search_dart(query: str, keys: list[str]) -> str:
    """이 기업들에 관한 공시 근거를 의미검색으로 찾는다."""
    return _guard("search_dart", search_tools.search_dart, query, keys)


def get_business_overview(key: str) -> str:
    """이 기업의 사업보고서 「사업의 내용」 원문. 참고 맥락이며 인용할 수 없다."""
    return _guard("get_business_overview", company_tools.get_business_overview, key)


def get_market(key: str) -> str:
    """이 기업의 시세와 지표(시총·PER·PBR·PSR). 계산값이라 근거 id 가 없다."""
    return _guard("get_market", company_tools.get_market, key)


def get_filings(key: str) -> str:
    """이 기업의 공시 목록. 제목까지이고 본문은 없다."""
    return _guard("get_filings", company_tools.get_filings, key)


# ★Agent 에게 보이는 **전부**. 여기 없는 것은 Agent 가 부를 수 없다.
_EXPOSED = (get_relations, get_events, search_news, search_dart,
            get_business_overview, get_market, get_filings)

TOOL_NAMES = tuple(fn.__name__ for fn in _EXPOSED)

# ★계약이 금지한 것들. 테스트가 이 목록과 `TOOL_NAMES` 를 마주 세운다.
FORBIDDEN_TOOL_NAMES = ("get_propagation", "search_company",
                        "fetch_evidence", "evidence_for_ids")


def agent_tools() -> list:
    """LangChain 도구 목록. **부를 때마다 새로 만들지 않는다**(바인딩 비용)."""
    global _BOUND
    if _BOUND is None:
        from langchain_core.tools import StructuredTool

        _BOUND = [StructuredTool.from_function(fn, name=fn.__name__,
                                               description=(fn.__doc__ or "").strip())
                  for fn in _EXPOSED]
    return _BOUND


_BOUND: Optional[list] = None
