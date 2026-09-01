"""재료를 모으는 노드 넷 — `RetrieveService` 에 위임한다.

    search ─▶ resolve_anchor ─▶ plan_material ─(Agent 루프)─▶ fetch_propagation

★**로직을 옮기지 않았다.** `RetrieveService._search()`·`_assemble()` 이 한
  덩어리로 하던 일을 노드 경계로 갈랐을 뿐이고, 각 단계가 부르는 함수는
  그 전과 같은 것이다.

★`_search()` 만은 **둘로 갈라 다시 썼다.** 노드 목록이 `search` 와
  `resolve_anchor` 를 나눠 놓았는데 저 메서드는 검색과 앵커 판정을 한 몸으로
  하고 있어서다. 가르면서 부르는 함수(`orchestrator.search`·
  `workspace_service.names_of`·`decide_anchor`)와 순서는 그대로 뒀다.

★**`guard_workspace` 가 사라졌다**(이번 개정 · 최종 설계 §17-1). 「담아 둔
  기업도 보고 있는 기업도 없으면 검색조차 하지 않는다」는 게이트였는데,
  워크스페이스를 검색 경계로 보는 정책이 폐기되면서 함께 나갔다. 이 파일의
  첫 노드는 이제 **검색**이다.
"""

from __future__ import annotations

from app.api.schemas import AnchorSource
from app.core.trace import trace_logger
from app.graph.state import AskState
from app.services import (evidence_selector, query_understanding,
                          workspace_service)
from app.tools import graph_tools
from app.graph import budget
from app.services.retrieve_service import (RetrieveService, _anchor_companies,
                                           _anchor_names_for, _companies_from,
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
#  ① search — flow ② (설계서 §10)
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

    ★`match_type` 도 **여기서 정한다.** 저건 `result.mode` 만 보고 정해지는
      값이라(`_match_type_of`) 검색이 끝난 순간 확정된다. 전에는
      `fetch_evidence` 가 정했는데, 그 노드는 근거를 조회·조립하는 자리라
      「검색이 어느 경로로 답했나」를 거기서 되짚을 이유가 없었다 — 재료를
      다 모을 때까지 미뤄 둔 것뿐이다. 값을 만드는 노드와 값이 정해지는
      시점을 맞춘다.
    """
    request = state["request"]
    query, result = _svc()._orchestrator.search(SearchRequest(
        query=request.question,
        workspace_keys=request.workspace_keys,
        # 인용이 목적이라 항상 켠다.
        include_evidence=True,
    ))
    return {"query": query, "result": result,
            "match_type": _match_type_of(result)}


# ══════════════════════════════════════════════════════════════════
#  ② resolve_anchor — flow ①b (설계서 §10)
# ══════════════════════════════════════════════════════════════════


def resolve_anchor(state: AskState) -> AskState:
    """★**①b 는 ② 뒤다.** 판정에 필요한 `resolved_entities` 가 ② 의 산출물이라
    질의 파싱 시점에는 확정할 수 없다.

    ★이름 조회는 **경계에서 한 번**이다(설계서 §16-3). 여기서는 그 결과를
      메모리에서 대조만 한다 — 「새 검색을 하지 않는다」(§10 ①b).

    ★`context_keys` 도 **같은 함수로** 이름을 붙인다. `names_of()` 가 하는
      일은 「key 목록 → 표시용 이름, 못 찾은 key 는 그대로 둔다」뿐이라
      목록의 출처를 안 따진다. 두 벌을 두면 못 찾은 key 의 처리가 갈린다.
      ★비면 조회하지 않는다 — `names_of([])` 는 Neo4j 왕복이라 공짜가 아니다.
    """
    request = state["request"]
    workspace_names = workspace_service.names_of(request.workspace_keys)
    context_names = (workspace_service.names_of(request.context_keys)
                     if request.context_keys else {})
    decision = query_understanding.decide_anchor(
        request.question, state["query"].resolved_entities, workspace_names,
        context_names)
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
#  ③ plan_material — 무엇을 재료로 삼을지 확정한다
# ══════════════════════════════════════════════════════════════════


def plan_material(state: AskState) -> AskState:
    """`companies`·`anchor_names`·`intent` 를 **여기서 전부** 정한다.

    ★`use_hits`·`backstop` 은 **State 에 싣지 않는다.** 둘 다 이 노드 안에서만
      쓰이는 중간 판정이었고, 뒤 노드 중 아무도 읽지 않았다(write-only). State 는
      「노드 사이를 흐르는 값」만 담는다 — 관측용 값을 얹어 두면 다음 노드가
      그걸 읽어도 되는 값으로 오해한다. 두 판정이 실제로 한 일은 `companies`
      한 곳에 전부 드러나므로, 검증도 그쪽을 본다.

    ★`companies` 의 `key` 형태를 **바꾸지 않는다.** `_companies_from()` 이
      `hit.entity_id` 를 그대로 싣는데 그게 `corp_code` 일 수도 `norm_name` 일
      수도 있다(실측: 「원익아이피에스」·「램리서치」는 `corp_code` 가 없다).
      `company_service.events_of()` 는 둘 다 받지만 **틀린 값을 주면 예외가
      아니라 조용히 0건**이라, 정규화하거나 변환하면 「사건이 없다」로 잘못
      읽힌다. 넘어온 형태 그대로 State 에 싣는다.

    ★`anchor_names`·`intent` 는 **retrieve 쪽 계산식**이다(`_anchor_names_for`).
      두 경로가 같은 함수를 부른다 — 사본을 두면 「무엇으로 골랐나」와 「무엇으로
      검사하나」가 갈린다(`state.py` 의 `anchor_names` 주석).
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
    #   앵커 경로에서는 이미 앵커가 `companies` 라 무동작이고, `anchorless` 는
    #   앵커가 없어 역시 무동작이다 — 그쪽은 히트가 유일한 재료다.
    companies = _with_anchor_backstop(companies, decision)

    anchor_names = _anchor_names_for(query, decision, companies)
    intent = evidence_selector.intent_of(request.question, anchor_names)

    # ★탐색 예산을 **여기서 연다.** Agent 가 도구를 부르기 전 마지막 결정론
    #   노드라, 카운터가 0 인 시점이 여기 하나로 고정된다.
    return {"companies": companies,
            "anchor_names": anchor_names, "intent": intent,
            **budget.initial()}


# ══════════════════════════════════════════════════════════════════
#  ④ fetch_propagation — Agent 뒤에 남은 결정론 조회 하나
# ══════════════════════════════════════════════════════════════════
#
# ★`fetch_events`·`fetch_relations`·`fetch_evidence` 는 **지웠다**(이번 개정).
#   배선이 끊긴 지 오래고(`agent ⇄ run_tools` 와 `evidence_validation` 이 대신),
#   그 셋만 쓰던 `_scope`·`_scope_keys` 도 함께 나갔다 — 살아 있는 범위 설정은
#   `agent_loop._scope_of` 다. 죽은 경로를 남겨 두면 「어느 쪽이 진짜인가」를
#   매번 되짚어야 한다.


def fetch_propagation(state: AskState) -> AskState:
    """파급. ★**사건이 있어야 계산된다** — 사건 노드 뒤에만 온다(설계서 §13).

    ★`is_risk` 가 아닌 사건은 계산하지 않는다. 상한은 도구 안에 있다(원칙 ③).
    """
    risky = [e.event_id for e in state["events"] if e.is_risk]
    # ★**backstop 절단이다 — 지금은 한 번도 물지 않는다**(2026-08-29 실측).
    #   `get_propagation` 이 목록 전체에 자기 상한 3 을 먼저 걸어
    #   (`_MAX_RISK_EVENTS_FOR_PROPAGATION`) 늘 그쪽이 더 빡빡하다. 그래도 남기는
    #   이유는 도구 상한이 올라가면 이 줄이 그때 무는 자리이기 때문이다.
    #
    # ★`propagations_used` 는 **소진 판정 대상이 아니다**(`budget._CAPS`). 여기는
    #   Agent 루프 밖이라 「더 못 부르게 막는다」가 성립하지 않는다 — 세기만 한다.
    room = budget.remaining(state)["propagations_used"]
    if len(risky) > room:
        log.info("fetch_propagation 예산으로 자른다 %d -> %d", len(risky), room)
        risky = risky[:room]
    propagation = graph_tools.get_propagation(risky)
    # ★**넘긴 사건 수로 센다 — 자른 것과 같은 단위여야 한다.**
    #
    #   전에는 `len(propagation)`(파급 **행** 수)을 썼다. 자르는 쪽은 `risky`
    #   (사건 수)를 자르는데 세는 쪽만 행 수라, 사건 하나가 수십 행을 내는 만큼
    #   카운터가 상한을 훌쩍 넘었다 — 실측 상한 12 에 **92**(이전 측정 303).
    #   「막는다」고 적힌 예산이 자기 카운터로는 넘긴 셈이라, `budget_exhausted`
    #   가 루프가 잘리지도 않았는데 켜졌다.
    #
    #   상한 12 의 근거부터 사건 수다 — `_MAX_RISK_EVENTS_FOR_PROPAGATION`(=3)의
    #   4배(`budget.py`). 즉 틀린 쪽은 상한이 아니라 **세는 단위**였다.
    #
    # ★출력에서 되짚지 않는 이유 — `len({p.event_id for p in propagation})` 는
    #   「파급이 **나온**」 사건만 세어 파급 0행인 사건을 놓친다. 예산은 입력을
    #   막는 장치이므로(`budget.py` 의 「호출할 때마다 누적치를 더하고, 넘으면
    #   더 못 부른다」) 자른 값과 같은 값을 세는 것이 계약에 맞다.
    return {"propagation": propagation,
            **budget.spend(state, propagations_used=len(risky))}
