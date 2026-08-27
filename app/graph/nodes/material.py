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

from app.api.schemas import AnchorSource, Evidence
from app.core.trace import trace_logger
from app.graph.state import AskState
from app.services import (evidence_selector, query_understanding, relation_service,
                          workspace_service)
from app.services.retrieve_service import _MAX_LOGGED_EVIDENCE
from app.tools import graph_tools
from app.tools.scope import anchor_scope
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


def _scope_keys(state: AskState) -> list[str]:
    """도구가 만질 수 있는 key — **서버가 정한 재료 범위**다.

    ★`companies` 와 앵커를 **합친다.** 앵커만 두면 `use_hits=True` 경로가
      막힌다 — 그때 `companies` 는 검색 히트의 관계 상대이지 앵커가 아니다
      (「삼성전자에 납품하는 기업」의 재료는 공급사들이다). 반대로 `companies`
      만 두면 백스톱 이전 상태의 앵커를 못 쓴다.

    ★**요청이 준 값이 아니다.** `workspace_keys` 를 그대로 넣지 않는다 —
      범위는 「서버가 이 질문의 재료로 고른 것」이지 「사용자가 담아 둔 것」이
      아니다. 넓히면 도구가 재료 밖 기업을 조회할 수 있게 된다.
    """
    keys = [c.key for c in state["companies"]]
    keys += [a.key for a in state["decision"].anchors]
    return list(dict.fromkeys(k for k in keys if k))


def _scope(state: AskState):
    """도구가 읽을 **서버 쪽 문맥**을 세운다 — 범위 + 랭킹 문맥.

    ★`workspace_keys`·`anchor_keys` 를 도구 인자로 넘기지 않는다. 링(ring)
      순서와 방향 판정이 그 값을 쓰는데, 인자면 2차의 Agent 가 「워크스페이스는
      필터가 아니라 랭킹 문맥」(설계서 §3)이라는 정책을 스스로 바꿀 수 있다.
    """
    return anchor_scope(
        _scope_keys(state),
        workspace_keys=state["request"].workspace_keys,
        anchor_keys=[a.key for a in state["decision"].anchors],
        anchor_names=state["anchor_names"])


def fetch_events(state: AskState) -> AskState:
    """사건. **도구가 만든다**(Phase 1.5).

    ★`role=None` 을 넘긴다. 도구 기본값은 `"subject"` 지만(Agent 가 붙었을 때의
      안전한 기본 — 「이 기업에 난 일」은 `subject` 만이다), 1차의 `_events_of()`
      는 role 을 거르지 않았다. 여기서 거르면 **재료 집합이 달라져** 대조가
      성립하지 않는다. 거를지는 사람이 정할 일이다.
    """
    with _scope(state):
        return {"events": graph_tools.get_events(
            [c.key for c in state["companies"]], state["intent"], role=None)}


def fetch_propagation(state: AskState) -> AskState:
    """파급. ★**사건이 있어야 계산된다** — 사건 노드 뒤에만 온다(설계서 §13).

    ★`is_risk` 가 아닌 사건은 계산하지 않는다. 상한은 도구 안에 있다(원칙 ③).
    """
    risky = [e.event_id for e in state["events"] if e.is_risk]
    return {"propagation": graph_tools.get_propagation(risky)}


def fetch_relations(state: AskState) -> AskState:
    """관계. **도구가 만든다**(Phase 1.5).

    ★`edge_types` 는 **거르지 않고 순서만** 정한다 — 워크스페이스가 hard filter
      가 아닌 것과 같은 이유다(설계서 §3).
    """
    query = state["query"]
    with _scope(state):
        return {"relations": graph_tools.get_relations(
            [c.key for c in state["companies"]],
            edge_types=query.edge_types,
            direction=getattr(query.direction, "value", None))}


def fetch_evidence(state: AskState) -> AskState:
    """관계·사건·검색히트의 근거 id 를 **합집합으로 모아 한 번에** 조회한다.

    셋을 다 모으는 이유는 출처가 셋이기 때문이다 — 관계에 달린 근거, 사건에
    달린 근거, 검색이 짚어 준 근거. 어느 하나만 보면 답변이 인용할 수 있는
    문장이 줄어든다.

    ★히트를 재료로 **안 써도 그 근거는 그대로 모은다.** 한 번 걸러 봤다가
      실측으로 되돌렸다(현황서 §8-6) — 여기 든 근거의 절반가량이 워크스페이스에
      닿아, 거르면 질문이 물은 사례를 버린다.

    ★못 꺼낸 근거를 **조용히 빼지 않는다.** `missing=True` 로 남긴다 —
      빼면 「근거가 없는 관계」로 읽힌다.
    """
    from_relations = [r.evidence_id for r in state["relations"] if r.evidence_id]
    from_events = [eid for event in state["events"] for eid in event.evidence_ids]
    from_hits = [ref["evidence_id"] for hit in state["result"].hits
                 for ref in hit.evidence if ref.get("evidence_id")]
    ids = from_relations + from_events + from_hits

    evidence = [Evidence(**row) for row in relation_service.evidence_for_ids(ids)]

    # 출처별로 갈라 남긴다 — 합계만 있으면 「근거가 왜 이것뿐인가」를 못 따진다.
    log.info("evidence.collect from_relations=%d from_events=%d from_hits=%d "
             "unique=%d -> fetched=%d missing=%d ids=%s",
             len(from_relations), len(from_events), len(from_hits), len(set(ids)),
             len(evidence), sum(1 for e in evidence if e.missing),
             [e.evidence_id for e in evidence[:_MAX_LOGGED_EVIDENCE]])
    return {"evidence": evidence, "match_type": _match_type_of(state["result"])}
