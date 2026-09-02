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

★리듀서는 **`messages` 하나에만** 쓴다(2차). 나머지는 전 노드 순차라 같은 키에
  두 노드가 동시에 쓰는 일이 없다 — 리듀서는 그 경합을 푸는 도구다. `messages`
  는 `agent` 와 `run_tools` 가 **번갈아 덧붙이는** 값이라 예외다. `add_messages`
  가 id 로 중복을 접어 주므로 직접 이어 붙이지 않는다.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages

from app.api.schemas import (AskRequest, AskResponse, Evidence, MatchType,
                             RelationEndpoint, Source)
from app.services.query_understanding import AnchorDecision
from app.tools.dto import EventDTO, PropagationDTO, RelationDTO
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
    # 검색이 어느 경로로 답했나. `[사실]` 첫 줄이 이걸 쓴다.
    # ★`result.mode` 만 보고 정해지므로 **검색이 끝난 자리**에서 확정된다.
    #   전에는 `fetch_evidence` 가 만들었는데, 그 노드는 근거를 모으는 자리라
    #   여기까지 미룰 이유가 없었다.
    match_type: MatchType

    # ── resolve_anchor 노드 ───────────────────────────────────
    decision: AnchorDecision

    # ── plan_material 노드 ────────────────────────────────────
    # ★`use_hits`(히트를 믿어도 되나)·`backstop`(앵커로 메웠나)은 **여기 없다.**
    #   둘 다 `plan_material` 안에서만 쓰이고 아무 노드도 안 읽던 값이라(1.5차
    #   정리에서 제거) State 에 둘 이유가 없었다. State 는 **노드 사이를 흐르는
    #   값**만 담는다 — 관측용을 얹으면 다음 노드가 읽어도 되는 값으로 읽는다.
    #   두 판정의 결과는 `companies` 에 전부 드러난다.
    # ★`key` 형태를 **바꾸지 않는다.** 정규화도 `corp_code` 변환도 하지 않는다.
    #   `company_service.events_of()` 는 `corp_code` 든 `norm_name` 이든 받지만,
    #   **잘못된 값을 주면 예외가 아니라 조용히 0건**이 나온다 — 「사건이 없다」로
    #   잘못 읽힌다. `_events_of` 가 `events_of(c.key)` 로 넘기던 그 형태 그대로 싣는다.
    companies: list[RelationEndpoint]

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

    # ★**앵커 없는 질문에서 서버가 고른 사건**(2026-09-02).
    #   `(event_id, company_key)` 쌍의 순서 있는 목록. 앵커 경로에서는 비어 있다.
    #
    #   `plan_material` 이 전역 사건 검색을 **한 번** 돌려 여기 싣고,
    #   `_scope_of` 가 `scope.event_pairs` 로 넘기면 `get_events` 가 **고르지 않고
    #   조회만** 한다. 도구가 다시 고르면 `/retrieve` 의 전역 10건과 재료가
    #   갈린다 — 앵커 없는 경로에서 `companies` 는 이미 그 사건에서 역산된
    #   것이라, 기업별로 다시 조회하면 기업당 10건 × 최대 10곳이 된다.
    #
    #   ★`companies` 와 달리 이건 **사건 쪽 순서**다. 둘의 순서가 어긋나도
    #     문제가 없다 — 기업은 사건에서 나온 부산물이고 범위 설정에만 쓰인다.
    event_pairs: list[tuple[str, str]]

    # ── agent ⇄ run_tools 노드 (2차) ──────────────────────────
    # ★Agent 대화. `agent` 가 고르고 `run_tools` 가 결과를 붙인다.
    messages: Annotated[list, add_messages]

    # ★도구가 만든 DTO 를 **도구 이름별로** 쌓아 둔다. `evidence_validation`
    #   이 이걸 읽어 재료와 근거로 마감한다.
    #
    #   ★문자열(Agent 가 본 것)을 다시 파싱하지 않는다 — 같은 사실을 두 번
    #     만드는 것이고, 두 벌은 반드시 갈린다.
    tool_results: dict[str, list[Any]]

    # ── 탐색 총량 예산 (계약 4번) ─────────────────────────────
    # ★상한은 `app/graph/budget.py` 의 **모듈 상수**다. 여기 있는 것은
    #   「이번 요청에서 얼마나 썼나」 — 흐르는 값이라 State 가 맞다.
    tool_calls_used: int
    events_used: int
    propagations_used: int
    hops_used: int
    # 소진돼서 도구를 덜 부른 채 마감했나. ★예외가 아니라 **표시**다
    budget_exhausted: bool

    # ── fetch_* 노드 ──────────────────────────────────────────
    # ★1.5차부터 **도구가 만든 DTO** 다(`app/tools/dto.py`). API 스키마의
    #   `Event`·`Relation`·`Propagation` 이 아니다 — 그것들은 표기가 없어서
    #   LLM 이 `score=0.9`·`stated=False`·`role=mentioned` 를 제 뜻대로 읽는다.
    #   재료 자체는 1차와 **같다**(`ask_graph_parity.py --materials`).
    events: list[EventDTO]
    propagation: list[PropagationDTO]
    relations: list[RelationDTO]
    # ★근거만은 API 스키마 그대로다 — `Source` 로 그대로 옮겨 담기고
    #   `claim_check`·`material_consistency` 가 이 모양을 읽는다.
    evidence: list[Evidence]

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
