"""`/ask` 그래프의 State.

★**기존 DTO 를 해체하지 않는다.** `SearchQuery`·`SearchResult`·`AnchorDecision`·
  `Event`·`Relation`·`Evidence` 를 그대로 필드로 품는다. 이 계약은 이미 백엔드에
  나가 있어서, 여기서 모양을 바꾸면 그래프가 「같은 값을 다르게 부르는 두 번째
  진실」이 된다.

★`app/tools/dto.py` 는 **쓰지 않는다.** 저건 표기(`source_note`·`caution` 등)를
  붙이는 계층이라, 넣는 순간 프롬프트가 바뀌고 출력 대조가 깨진다. Phase 1.5 다.

★상한 상수(`_MAX_*`)를 State 에 올리지 않는다. State 는 **이번 요청에서 흐르는
  값**만 담는다. 상한은 모듈 상수라 요청마다 달라질 이유가 없고, State 에 두면
  「누가 언제 바꿨나」를 노드마다 따져야 한다.

★리듀서(`Annotated` + `operator.add`)를 쓰지 않는다. Phase 1 은 전 노드 순차라
  같은 키에 두 노드가 동시에 쓰는 일이 없다 — 리듀서는 그 경합을 푸는 도구다.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from app.api.schemas import (AskRequest, AskResponse, Event, Evidence, Propagation,
                             Relation, RelationEndpoint, RetrieveResponse, Source)
from app.services.query_understanding import AnchorDecision
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult


class AskState(TypedDict, total=False):
    """한 요청이 노드를 지나며 쌓는 값.

    `total=False` 라 **모든 키가 선택적**이다. 노드가 자기 차례에 채우고, 앞
    노드가 안 채운 키는 아예 없다 — `None` 과 「아직 안 왔다」를 섞지 않는다.
    """

    # ── 입력 ──────────────────────────────────────────────────
    request: AskRequest

    # ── search 노드 ───────────────────────────────────────────
    query: SearchQuery
    result: SearchResult

    # ── resolve_anchor 노드 ───────────────────────────────────
    decision: AnchorDecision

    # ── plan_material 노드 ────────────────────────────────────
    # 검색 히트를 재료로 믿어도 되나(`_hits_reflect_the_anchor`).
    use_hits: bool
    # ★`key` 형태를 **바꾸지 않는다.** 정규화도 `corp_code` 변환도 하지 않는다.
    #   `company_service.events_of()` 는 `corp_code` 든 `norm_name` 이든 받지만,
    #   **잘못된 값을 주면 예외가 아니라 조용히 0건**이 나온다 — 「사건이 없다」로
    #   잘못 읽힌다. `_events_of` 가 `events_of(c.key)` 로 넘기던 그 형태 그대로 싣는다.
    companies: list[RelationEndpoint]
    # 백스톱이 실제로 끼어들었나 — 로그·검증용 관측값이다.
    backstop: bool

    # ★`anchor_names` 와 `intent` 를 **여기 한 번만 올린다.**
    #
    #   Phase 0 이전에는 두 곳에서 따로 계산됐고 값이 갈릴 수 있었다:
    #
    #       retrieve_service._events_of()   resolved_entities 우선, 비면 decision.anchors
    #       answer_service.ask()            decision.anchors 만
    #
    #   `source=query` 일 때 `decision.anchors` 는 **최고점 1개**인데
    #   (`query_understanding._primary`) `resolved_entities` 는 **복수 후보**라
    #   두 리스트가 다를 수 있다. `answer_service` 주석이 「의도는 재료를 고를 때
    #   쓴 것과 같아야 한다」고 못 박아 놓고도 코드가 못 지키고 있었다.
    #
    #   ★**retrieve 쪽 계산식을 채택한다** — 재료를 실제로 고른 것이 그쪽이다.
    #     `check_claims` 가 이 값을 읽으므로 「무엇으로 골랐나」와 「무엇으로
    #     검사하나」가 처음으로 같아진다.
    anchor_names: list[str]
    intent: str

    # ── fetch_* 노드 ──────────────────────────────────────────
    events: list[Event]
    propagation: list[Propagation]
    relations: list[Relation]
    evidence: list[Evidence]
    # 위 넷을 담은 `/retrieve` 와 **같은 DTO**. `_build_user_prompt` 가 이걸 받는다.
    retrieved: RetrieveResponse

    # ── build_prompt · generate 노드 ──────────────────────────
    user_prompt: str
    # 어댑터가 돌려준 dict 그대로. `failed` 표시가 여기 붙어 온다.
    llm_result: dict[str, Any]

    # ── verify_sources 노드 ───────────────────────────────────
    answer: str
    failed: bool
    sources: list[Source]

    # ── respond · halt_no_material 노드 ───────────────────────
    response: AskResponse


def initial_state(request: AskRequest) -> AskState:
    """입력만 담은 State. 그래프의 출발점이다."""
    return {"request": request}


def final_response(state: AskState) -> Optional[AskResponse]:
    """그래프가 끝난 State 에서 응답을 꺼낸다. 없으면 `None` — **지어내지 않는다.**"""
    return state.get("response")
