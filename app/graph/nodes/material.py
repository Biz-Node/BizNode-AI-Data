"""재료를 모으는 노드 여덟 — `RetrieveService` 에 위임한다.

★**로직을 옮기지 않았다.** `RetrieveService._search()`·`_assemble()` 이 한
  덩어리로 하던 일을 노드 경계로 갈랐을 뿐이고, 각 단계가 부르는 함수는
  그 전과 같은 것이다. 로그도 같은 순서·같은 문구로 나온다.

★`_search()` 만은 **둘로 갈라 다시 썼다.** 노드 목록이 `search` 와
  `resolve_anchor` 를 나눠 놓았는데 저 메서드는 검색과 앵커 판정을 한 몸으로
  하고 있어서다. 가르면서 부르는 함수(`orchestrator.search`·
  `workspace_service.names_of`·`decide_anchor`)와 순서는 그대로 뒀다.
"""

from __future__ import annotations

from app.api.schemas import AnchorSource, RetrieveResponse
from app.core.trace import trace_logger
from app.graph.state import AskState
from app.services import evidence_selector, query_understanding, workspace_service
from app.services.retrieve_service import (RetrieveService, _anchor_companies,
                                           _companies_from,
                                           _hits_reflect_the_anchor,
                                           _match_type_of, _with_anchor_backstop)
from search.dto.search_request import SearchRequest

log = trace_logger(__name__)

# ★서비스 인스턴스는 **주입받는다.** 모듈 전역으로 두면 테스트가 갈아끼울 수
#   없고, `/ask` 라우트가 이미 만들어 둔 것과 두 벌이 된다.
_service: RetrieveService | None = None


def bind_service(service: RetrieveService) -> None:
    """그래프가 쓸 `RetrieveService` 를 정한다. 앱 기동 때 한 번 부른다."""
    global _service
    _service = service


def _svc() -> RetrieveService:
    """지연 생성 — 안 묶어 두고 부르면 기본 인스턴스를 만든다."""
    global _service
    if _service is None:
        _service = RetrieveService()
    return _service


# ══════════════════════════════════════════════════════════════════
#  ① guard_workspace — 워크스페이스가 비었나 (설계서 §16-2)
# ══════════════════════════════════════════════════════════════════


def guard_workspace(state: AskState) -> AskState:
    """★**검색조차 하지 않는다** — 재료를 모을 출발점이 없다.

    「무엇에 대한 인사이트인가」가 정해지지 않으면 답하지 않는 것이 맞다.
    실제 분기는 조건부 엣지가 하고, 이 노드는 **그 사실을 로그에 남긴다.**
    """
    if not state["request"].workspace_keys:
        log.info("ask.rejected reason=empty_workspace")
    return {}


def has_workspace(state: AskState) -> str:
    """조건부 엣지 — 워크스페이스가 비었으면 재료 없이 끝낸다."""
    return "search" if state["request"].workspace_keys else "halt_no_material"


# ══════════════════════════════════════════════════════════════════
#  ② search — flow ② (설계서 §10)
# ══════════════════════════════════════════════════════════════════


def search(state: AskState) -> AskState:
    """검색한다.

    ★`SearchQuery` 를 버리지 않는다 — 앵커 기업명이 여기 있고, 그게 있어야
      질문에서 「무엇을」만 떼어낼 수 있다(`evidence_selector.intent_of()`).

    ★**trace id 를 여기서 발급하지 않는다.** `RetrieveService._search()` 는
      여기서 발급했지만(그때는 요청 컨텍스트가 하나였다), 그래프에서는 그러면
      안 된다 — LangGraph 가 **노드마다 컨텍스트를 복사**해서 노드 안의
      `ContextVar.set()` 이 노드 밖으로 나가지 않는다. 실측(2026-08-27):
      이 노드가 발급하면 자기 로그 4줄만 id 를 달고 **나머지 9줄이 `-` 로**
      찍혔다. 발급은 진짜 요청 경계인 `run_ask()` 가 한다.
    """
    request = state["request"]
    query, result = _svc()._orchestrator.search(SearchRequest(
        query=request.question,
        workspace_keys=request.workspace_keys,
        # 인용이 목적이라 항상 켠다.
        include_evidence=True,
    ))
    return {"query": query, "result": result}


# ══════════════════════════════════════════════════════════════════
#  ③ resolve_anchor — flow ①b (설계서 §10)
# ══════════════════════════════════════════════════════════════════


def resolve_anchor(state: AskState) -> AskState:
    """★**①b 는 ② 뒤다.** 판정에 필요한 `resolved_entities` 가 ② 의 산출물이라
    질의 파싱 시점에는 확정할 수 없다.

    ★이름 조회는 **경계에서 한 번**이다(설계서 §16-3). 여기서는 그 결과를
      메모리에서 대조만 한다 — 「새 검색을 하지 않는다」(§10 ①b).
    """
    request = state["request"]
    workspace_names = workspace_service.names_of(request.workspace_keys)
    decision = query_understanding.decide_anchor(
        request.question, state["query"].resolved_entities, workspace_names)
    if decision.source is AnchorSource.UNRESOLVED:
        log.info("ask.unresolved named=%r — 재료를 만들지 않는다", decision.named)
    return {"decision": decision}


def is_resolved(state: AskState) -> str:
    """조건부 엣지 — 대상을 못 찾았으면 재료를 만들지 않는다(설계서 §14-4).

    ★**워크스페이스로 갈아타지 않는다.** 그러면 「TSMC 를 물었는데 삼성전자로
      답하는」 탐지 불가능한 오답이 된다. LLM 도 안 부른다.

    ★`AnswerService.ask()` 의 **세 번째 게이트(`retrieved is None`)가 여기로
      흡수된다.** 저쪽은 「재료가 없다」는 결과를 보고 되짚어 판단했는데,
      그래프에서는 **판정이 난 자리에서 바로 갈라진다.** 조립 노드 넷이 아예
      실행되지 않으므로 Neo4j 왕복도 나가지 않는다.
    """
    return ("halt_no_material"
            if state["decision"].source is AnchorSource.UNRESOLVED
            else "plan_material")


# ══════════════════════════════════════════════════════════════════
#  ④ plan_material — 무엇을 재료로 삼을지 확정한다
# ══════════════════════════════════════════════════════════════════


def plan_material(state: AskState) -> AskState:
    """`use_hits`·`companies`·`backstop`·`anchor_names`·`intent` 를 **여기서 전부** 정한다.

    ★`companies` 의 `key` 형태를 **바꾸지 않는다.** `_companies_from()` 이
      `hit.entity_id` 를 그대로 싣는데 그게 `corp_code` 일 수도 `norm_name` 일
      수도 있다(실측: 「원익아이피에스」·「램리서치」는 `corp_code` 가 없다).
      `company_service.events_of()` 는 둘 다 받지만 **틀린 값을 주면 예외가
      아니라 조용히 0건**이라, 정규화하거나 변환하면 「사건이 없다」로 잘못
      읽힌다. 넘어온 형태 그대로 State 에 싣는다.

    ★`anchor_names`·`intent` 는 **retrieve 쪽 계산식**이다(`_events_of`).
      `resolved_entities` 를 우선하고 비면 `decision.anchors` 로 내려간다 —
      `answer_service` 가 쓰던 「`decision.anchors` 만」과 다르다. 재료를 실제로
      고른 것이 이쪽이라 이쪽을 채택했다(`state.py` 의 `anchor_names` 주석).
    """
    request, query, result = state["request"], state["query"], state["result"]
    decision = state["decision"]

    use_hits = _hits_reflect_the_anchor(decision, query)
    if use_hits:
        companies = _companies_from(result)
    else:
        # 히트가 앵커를 반영하지 않는다 — 앵커 자신이 재료의 출발점이다.
        companies = _anchor_companies(decision)
        log.info("material.anchored companies=%s (검색 히트 %d건은 쓰지 않는다)",
                 [c.key for c in companies], len(result.hits))

    # ★재료 기업이 하나도 안 남았으면 앵커로 메운다(현황서 §5-16).
    #   앵커 경로에서는 이미 앵커가 `companies` 라 무동작이다.
    took_backstop = not companies and bool(decision.anchors)
    companies = _with_anchor_backstop(companies, decision)

    anchor_names = [r.corp_name for r in query.resolved_entities if r.corp_name]
    if not anchor_names:
        anchor_names = [a.name for a in decision.anchors if a.name]
    intent = evidence_selector.intent_of(request.question, anchor_names)

    return {"use_hits": use_hits, "companies": companies, "backstop": took_backstop,
            "anchor_names": anchor_names, "intent": intent}


# ══════════════════════════════════════════════════════════════════
#  ⑤~⑧ fetch_* — 조회 넷. 전부 RetrieveService 에 위임한다
# ══════════════════════════════════════════════════════════════════


def fetch_events(state: AskState) -> AskState:
    """사건. `RetrieveService._events_of()` 그대로다.

    ★저 메서드가 `anchor_names`·`intent` 를 **자기 안에서 다시 계산**하는데,
      `plan_material` 이 쓴 것과 **같은 계산식**이라 값이 같다(그래서 출력이
      안 바뀐다). 중복을 없애려면 `RetrieveService` 를 고쳐야 하는데 그건
      Phase 1 범위 밖이다 — `/retrieve` 를 건드리지 않는다.
    """
    return {"events": _svc()._events_of(
        state["companies"], state["request"].question,
        state["query"], state["decision"])}


def fetch_propagation(state: AskState) -> AskState:
    """파급. ★**사건이 있어야 계산된다** — 사건 노드 뒤에만 온다(설계서 §13)."""
    return {"propagation": _svc()._propagation_of(state["events"])}


def fetch_relations(state: AskState) -> AskState:
    """관계. 링(ring) 순서로 줄을 세운 뒤 자르는 규칙까지 그대로다(설계서 §3)."""
    return {"relations": _svc()._relations_of(
        state["companies"], set(state["request"].workspace_keys),
        state["query"], state["decision"])}


def fetch_evidence(state: AskState) -> AskState:
    """근거를 모으고 **`/retrieve` 와 같은 DTO 로 묶는다.**

    ★히트를 재료로 **안 써도 그 근거는 그대로 모은다.** 한 번 걸러 봤다가
      실측으로 되돌렸다(현황서 §8-6).
    """
    evidence = _svc()._evidence_of(
        state["events"], state["relations"], state["result"])
    retrieved = RetrieveResponse(
        question=state["request"].question,
        match_type=_match_type_of(state["result"]),
        anchors=state["decision"].anchors,
        companies=state["companies"],
        events=state["events"],
        relations=state["relations"],
        propagation=state["propagation"],
        evidence=evidence,
    )
    return {"evidence": evidence, "retrieved": retrieved}
