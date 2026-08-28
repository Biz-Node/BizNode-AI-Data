"""도구가 만질 수 있는 key 범위 — **서버가 정하고 도구가 강제한다.**

★왜 인자가 아니라 스코프인가. 도구 4원칙 ① 은 「State 의 앵커 키 집합 밖의
  key 는 거부한다」인데, 범위를 **인자로 받으면 부르는 쪽이 넓힐 수 있다.**
  Agent 가 붙는 2차에는 부르는 쪽이 LLM 이다. 그러면 방어가 아니라 장식이 된다.

  그래서 범위는 **노드가 세우고**(`with anchor_scope(...)`) 도구는 읽기만 한다.
  도구 시그니처에 범위가 없는 것이 계약이다.

★`ContextVar` 를 **노드 안에서 세우고 그 안에서 다 쓴다.** LangGraph 는 노드마다
  컨텍스트를 복사하므로 노드에서 세운 값은 **다음 노드로 넘어가지 않는다**
  (1차에서 `new_trace_id()` 로 실측 확인). 스코프는 한 노드 안에서만 살면 되므로
  그 성질이 오히려 맞다 — 노드를 벗어나면 저절로 닫힌다.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from contextvars import ContextVar
from typing import Iterable, Optional

from app.tools.errors import OutOfScopeKey

@dataclass(frozen=True)
class ToolContext:
    """도구가 읽는 **서버 쪽 문맥.** 전부 노드가 정하고 도구는 읽기만 한다.

    ★`workspace_keys`·`anchor_keys` 도 여기 둔다. 관계 순서를 정하는 링(ring)
      계산과 방향 판정에 필요한데, **인자로 받으면 부르는 쪽이 바꿀 수 있다.**
      「워크스페이스는 필터가 아니라 랭킹 문맥」(설계서 §3)이라는 정책이
      Agent 의 재량이 되면 안 된다.
    """

    allowed: frozenset[str]
    workspace_keys: frozenset[str] = frozenset()
    anchor_keys: frozenset[str] = frozenset()
    # ★사건 라벨에서 **떼어낼 앵커 기업명**. `evidence_selector.event_label()` 이
    #   질문과 라벨 양쪽에서 앵커명을 빼야 순위가 제대로 선다(현황서 §5-23 이
    #   고친 퇴행). 이름이지만 **식별에 쓰는 값이 아니라** 문자열 제거용이고,
    #   서버가 정한 앵커에서만 온다 — 도구가 이름을 해소하는 것이 아니다.
    anchor_names: tuple[str, ...] = ()
    # ★사건 순위를 정하는 **질문 의도**. `get_events` 가 쓴다.
    #   Agent 인자로 두면 「무엇을 중요하게 볼지」를 LLM 이 정하게 되는데,
    #   그건 재료 범위를 고르는 것과 같다(4원칙 ①). 서버가 `plan_material`
    #   에서 정한 값을 여기 실어 보낸다.
    intent: str = ""
    # ★관계 순서를 정하는 **질문이 물은 엣지 타입·방향**. `get_relations` 가 쓴다.
    #   `intent` 와 **같은 이유로 같은 자리**에 있다 — 「무슨 관계를 물었나」를
    #   Agent 인자로 두면 LLM 이 순서를 정하게 되는데, 순서는 `ordered[:limit]` 의
    #   자르는 지점을 정하므로 곧 **재료를 고르는 일**이 된다(4원칙 ①).
    #
    #   ★**비면 정렬이 통째로 꺼진다** — `relation_selector.order()` 가
    #     `if not matched: return ordered` 로 그대로 돌려준다. 1.5차에는
    #     `fetch_relations` 가 인자로 넘기던 값인데, Agent 배선에서 인자를 빼며
    #     **여기로 옮기지 않아** 60% 질의에서 정렬이 죽어 있었다(현황서 §8-18).
    edge_types: tuple[str, ...] = ()
    #   ★`Direction` enum 이 아니라 **그 `.value` 문자열**이다 — 1.5차
    #     `fetch_relations` 가 `getattr(query.direction, "value", None)` 로
    #     넘기던 형태를 그대로 쓴다. `relation_selector._direction_matches` 는
    #     둘 다 받지만, 형태를 바꾸면 그게 대조에서 티가 안 나는 차이가 된다.
    direction: Optional[str] = None


_SCOPE: ContextVar[Optional[ToolContext]] = ContextVar("tool_scope", default=None)


@contextmanager
def anchor_scope(keys: Iterable[str], *, workspace_keys: Iterable[str] = (),
                 anchor_keys: Iterable[str] = (),
                 anchor_names: Iterable[str] = (),
                 intent: str = "",
                 edge_types: Iterable[str] = (),
                 direction: Optional[str] = None):
    """이 블록 안에서 도구가 만질 수 있는 key 와 랭킹 문맥을 정한다."""
    token = _SCOPE.set(ToolContext(
        allowed=frozenset(k for k in keys if k),
        workspace_keys=frozenset(k for k in workspace_keys if k),
        anchor_keys=frozenset(k for k in anchor_keys if k),
        anchor_names=tuple(n for n in anchor_names if n),
        intent=intent,
        edge_types=tuple(t for t in (edge_types or ()) if t),
        direction=direction))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def context() -> Optional[ToolContext]:
    """현재 문맥. `None` 이면 **범위가 안 세워진 것**이다 — 비어 있는 것과 다르다."""
    return _SCOPE.get()


def allowed_keys() -> Optional[frozenset[str]]:
    got = _SCOPE.get()
    return None if got is None else got.allowed


def check(keys: Iterable[str]) -> list[str]:
    """범위 안의 key 만 순서대로 돌려준다. 하나라도 밖이면 `OutOfScopeKey`.

    ★**조용히 거르지 않는다.** 거르면 「그 기업은 재료가 없었다」로 읽히는데,
      실제로는 물어본 적조차 없는 것이다.
    """
    wanted = [k for k in keys if k]
    got = _SCOPE.get()
    if got is None:
        raise OutOfScopeKey(
            "도구를 범위 밖에서 불렀다 — 노드가 `anchor_scope(...)` 를 세워야 한다")
    outside = [k for k in wanted if k not in got.allowed]
    if outside:
        raise OutOfScopeKey(
            f"재료 범위 밖의 key: {outside} (허용 {sorted(got.allowed)})")
    return wanted
