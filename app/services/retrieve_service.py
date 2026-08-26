"""RetrieveService — 챗봇이 인용할 **사실과 근거**를 완성한다.

★**답변 문장을 만들지 않는다.** 생성은 추론 담당 몫이고, 경계를 섞으면
  「누가 지어냈나」를 못 가린다. 여기까지가 Retrieve Layer 다.

경계가 둘이다.

    백엔드   → HTTP `POST /retrieve` → 이 서비스
    추론담당 → 이 모듈을 **직접 import** → 이 서비스

같은 유스케이스를 두 입구가 공유한다. 그래서 핵심은 동기 함수 `retrieve()` 에 두고,
HTTP 쪽에서 필요한 타임아웃은 `retrieve_async()` 가 감싼다 — 추론 담당은 async
컨텍스트가 아닐 수 있으므로 동기 진입점을 없애지 않는다.

조립 순서에 이유가 있다.

    검색 → Company 만 추림 → 사건 → 파급 → 관계 → 근거
                                ↑                    ↑
              파급은 사건 이름이 있어야 계산된다   id 를 다 모아 **한 번**에 조회

`AskRequest` 를 그대로 입력으로 쓴다 — 백엔드에 이미 나간 계약이고 필드가
`RetrieveRequest` 설계안과 같다. 이름만 새로 만들면 OpenAPI 스키마 이름이 바뀐다.
"""

from __future__ import annotations

from typing import Optional

from app.api.schemas import (AnchorSource, AskRequest, Event, Evidence, MatchType,
                             Propagation, Relation, RelationEndpoint, RetrieveResponse)
from app.core.trace import new_trace_id, trace_logger
from app.services import (company_service, evidence_selector, query_understanding,
                          relation_selector, relation_service, workspace_service)
from app.services.query_understanding import AnchorDecision
from search.dto.search_query import SearchQuery
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode
from search.service.factory import build_orchestrator
from search.service.orchestrator import SearchOrchestrator

log = trace_logger(__name__)

# 로그 한 줄에 실을 근거 id 개수. 전량은 응답에 있고, 로그에는 「어떤 것이
# 걸렸나」의 앞머리만 있으면 된다.
_MAX_LOGGED_EVIDENCE = 10

# ── 상한 셋. 전부 **실측 근거 없는 잠정치**다 ────────────────────────────
# 챗봇이 한 답변에서 실제로 인용하는 양을 재 본 적이 없다. 상한을 걸지 않으면
# 검색 히트 수만큼 Neo4j 왕복이 생기므로(설계서 §26) 일단 막아 두되, **잘라낸
# 사실을 로그로 남긴다** — 조용히 자르면 「그게 전부」로 읽힌다.
_MAX_COMPANIES = 5
_MAX_RELATIONS_PER_COMPANY = 10
_MAX_RISK_EVENTS_FOR_PROPAGATION = 3
# ★사건에는 상한이 **없었다**(Step2, 2026-08-23). 실측으로 「삼성전자와
#   SK하이닉스의 담합 소송」이 사건 155건 → 근거 205건 → 34,430자를 프롬프트에
#   실었다. 기업마다 따로 적용한다 — 전체 상한 하나로 두면 사건 많은 기업이
#   다 먹는다. 역시 실측 근거 없는 잠정치다.
_MAX_EVENTS_PER_COMPANY = 10


def _default_embed(texts: list[str]):
    """지연 로딩 — 임포트 시점에 OpenAI 클라이언트를 만들지 않는다. 테스트는
    이 이름을 monkeypatch 해서 끈다(`None` 이면 유사도 없이 규칙만 쓴다)."""
    from pipeline.vectorstore.chroma_store import get_store

    return get_store().embed(texts)


def _merge_evidence_ids(target: Event, other: Event) -> None:
    """공유 사건의 근거를 합친다 — 순서 보존, 중복 제거."""
    for evidence_id in other.evidence_ids:
        if evidence_id not in target.evidence_ids:
            target.evidence_ids.append(evidence_id)


_MATCH_TYPE_BY_MODE: dict[SearchMode, MatchType] = {
    SearchMode.NAME: MatchType.EXACT,
    SearchMode.RELATIONSHIP: MatchType.EXACT,
    SearchMode.SEMANTIC: MatchType.SEMANTIC,
}

# ★`SearchMode`에 새 값이 추가됐는데 이 매핑을 안 고치면, 지금은 요청마다
#   `_MATCH_TYPE_BY_MODE[result.mode]`가 `KeyError` → `POST /retrieve` 500 을
#   낸다. import 시점 assert 로 옮기면 기동 즉시 잡힌다 — 같은 패턴을
#   `search/model/enums.py`의 `EntityType`/`NODE_TYPES` 전수 검사가 이미 쓴다.
assert set(_MATCH_TYPE_BY_MODE) == set(SearchMode), (
    "SearchMode에 새 값이 생겼다 — match_type 매핑을 갱신할 것")


def _match_type_of(result: SearchResult) -> MatchType:
    return _MATCH_TYPE_BY_MODE[result.mode]


def _companies_from(result: SearchResult) -> list[RelationEndpoint]:
    """검색 히트에서 **Company 만** 추린다(설계서 §9).

    ★`entity_id` 가 항상 `corp_code` 라고 가정하지 않는다. GraphSearcher 는
      `coalesce(corp_code, person_key, norm_name, event_id, name)` 를 싣고,
      실제로 corp_code 없는 기업이 있다(예: 「원익아이피에스」·「램리서치」).
      다행히 `company_service` 의 조회는 둘 다 받는다 —
      `WHERE c.corp_code = $k OR c.norm_name = $k` (확인 완료).
      **막아야 하는 것은 Person·Event 를 기업 조회에 넣는 것**이다. 그건 조용히
      빈 결과가 되어 「사건이 없다」로 잘못 읽힌다.
    """
    companies: list[RelationEndpoint] = []
    seen: set[str] = set()
    for hit in result.hits:
        if hit.entity_type != EntityType.COMPANY or hit.entity_id in seen:
            continue
        seen.add(hit.entity_id)
        companies.append(RelationEndpoint(key=hit.entity_id, name=hit.name))
    if len(companies) > _MAX_COMPANIES:
        log.info("companies truncated %d -> %d", len(companies), _MAX_COMPANIES)
    return companies[:_MAX_COMPANIES]


# ── 링(ring) — 설계서 §3 ────────────────────────────────────────────────
# ★**새 값을 만들지 않는다.** `result_ranker.workspace_priority()` 가 쓰는 관련도
#   0/1/2/3 을 그대로 쓴다 — 저쪽은 `SearchHit` 을, 여기는 `RetrieveResponse.
#   Relation` 을 보므로 DTO 모양이 달라 함수를 공유할 수 없을 뿐이다. 값이
#   갈라지면 순서가 조용히 어긋나므로 `test_ring_values_match_the_ranker` 가 묶어 둔다.
_RING_BOTH_INSIDE = 0      # 양끝이 둘 다 워크스페이스 안
_RING_OUTSIDE_COMPANY = 1  # 워크스페이스 ↔ 바깥 기업
_RING_OUTSIDE_OTHER = 2    # 워크스페이스 ↔ 비-Company (사건·인물·기관·제품)
_RING_UNRELATED = 3        # 워크스페이스와 닿지 않음 — ★버리지 않는다


def _ring_of(row: dict, workspace_keys: set[str]) -> int:
    """이 관계가 워크스페이스에서 몇 링 떨어져 있나. **작을수록 안쪽.**

    ★`Relation` 을 만들기 **전에** 원본 dict 로 판정한다 — 삼성전자 관계가 526건이라
      전부 pydantic 으로 만들면 버릴 것까지 만들게 된다.
    """
    source_in = row["source"]["key"] in workspace_keys
    target_in = row["target"]["key"] in workspace_keys
    if source_in and target_in:
        return _RING_BOTH_INSIDE
    if not source_in and not target_in:
        return _RING_UNRELATED
    outside = row["target"] if source_in else row["source"]
    return (_RING_OUTSIDE_COMPANY if outside.get("label") == "Company"
            else _RING_OUTSIDE_OTHER)


def _anchor_companies(decision: AnchorDecision) -> list[RelationEndpoint]:
    """앵커 자신을 재료 기업으로 삼는다(설계서 §3 material anchor).

    ★앵커 기업 수 상한은 **기존 `_MAX_COMPANIES` 를 그대로** 쓴다 — 새 숫자를
      만들지 않는다. 「몇 곳까지 쓰는가」의 확정값은 아직 `[DECIDE]` 다
      (현황서 §7-4). **조용히 자르지 않는다.**
    """
    companies = [RelationEndpoint(key=a.key, name=a.name) for a in decision.anchors]
    if len(companies) > _MAX_COMPANIES:
        log.info("anchors truncated %d -> %d", len(companies), _MAX_COMPANIES)
    return companies[:_MAX_COMPANIES]


def _with_anchor_backstop(companies: list[RelationEndpoint],
                          decision: AnchorDecision) -> list[RelationEndpoint]:
    """재료 기업이 **하나도 안 남았을 때** 앵커 기업으로 메운다(현황서 §5-16).

    ★왜 필요한가 — 관계 상대가 Company 가 아닌 질의에서 재료가 통째로 0 이 됐다.

        「삼성전자 임원」        IS_EXECUTIVE_OF  히트 = Person ×10       → companies = []
        「삼성전자를 규제한 기관」  REGULATES        히트 = Organization ×10 → companies = []
        「삼성전자 기술 유출 사건」 HAS_EVENT        히트 = Event ×10        → companies = []

      `companies` 가 비면 `events_of`·`relations_of` 가 돌 대상이 없어 사건·관계가
      전부 0 이 된다. 앵커(삼성전자)는 멀쩡히 잡혀 있는데도 그렇다.

    ★**`companies` 는 여전히 Company 만 담는다.** 상대 노드(Person·Organization·
      Event)는 넣지 않는다 — 그건 조용히 빈 결과가 되어 「사건이 없다」로 잘못
      읽힌다(설계서 §9). 상대는 `relations`·`events` 로 나간다.

    ★**재료가 이미 있으면 끼어들지 않는다.** 실측(2026-08-26 · 41건)으로 늘 넣게
      하면 **13건**의 재료 구성이 바뀐다 — 「삼성전자에 납품하는 기업」에서 앵커를
      앞에 세우면 `_MAX_COMPANIES` 때문에 **공급사 한 곳이 밀려난다.** 그 교환을
      정하려면 「관계 상대가 답인가, 앵커가 답인가」를 갈라야 하는데 그건 별도
      `[TODO]` 다(현황서 §5-16). 여기서는 **잃지 않는 것**까지만 한다.

    ★**그래프에 있는 앵커만 넣는다.** 「해소됐다 ≠ 그래프에 있다」다 — 실측에서
      「이 사건의 대상 기업은?」의 앵커(`00121941`)가 그래프에 없었다. 없는 key 를
      넣으면 `companies` 에 팬텀 항목만 남는다.
    """
    if companies or not decision.anchors:
        return companies
    keys = [a.key for a in decision.anchors][:_MAX_COMPANIES]
    found = company_service.names_by_keys(keys)
    kept = [RelationEndpoint(key=k, name=found[k]) for k in keys if k in found]
    log.info("anchor.backstop companies=[] -> %s (그래프에 없어 뺀 앵커: %s)",
             [c.key for c in kept], [k for k in keys if k not in found])
    return kept


def _hits_reflect_the_anchor(decision: AnchorDecision, query: SearchQuery) -> bool:
    """검색 히트를 재료로 믿어도 되나.

    ★믿어도 되는 경우는 하나다 — ② Search 가 **실제로 앵커를 잡고** 그래프를 돈
      경우다(`resolved_entities` 가 있음). 그때 히트는 「그 기업의 관계 상대」이고
      그게 곧 답이다(「삼성전자에 납품하는 기업」).

    ★믿으면 안 되는 경우 셋 — 전부 히트가 앵커와 무관하다.

        SEMANTIC 폴백        의미가 비슷한 아무 기업 (설계서 §14-5)
        anchorless 슬롯      source 5 + target 5 를 점수순으로 아무거나 (§14-7 ⓑ)
        norm_name fallback   ② 는 못 찾았고 우리가 뒤늦게 찾은 앵커 (§16-1)
    """
    return decision.source is AnchorSource.QUERY and bool(query.resolved_entities)


class RetrieveService:
    def __init__(self, orchestrator: Optional[SearchOrchestrator] = None,
                 *, embed=None) -> None:
        self._orchestrator = orchestrator or build_orchestrator()
        # 주입용 — 테스트가 OpenAI 없이 돌 수 있어야 한다. None 이면 호출 시점에
        # 모듈 전역 `_default_embed` 를 쓴다(monkeypatch 가 먹도록 늦게 읽는다).
        self._embed = embed

    # ── ②·①b — 검색하고 앵커를 정한다 ──────────────────────────────────
    def _search(self, request: AskRequest) -> tuple[SearchQuery, SearchResult,
                                                    AnchorDecision]:
        """flow ② Search → ①b Anchor Resolution (설계서 §10).

        ★**①b 는 ② 뒤다.** 판정에 필요한 `resolved_entities` 가 ② 의 산출물이라
          질의 파싱 시점에는 확정할 수 없다.
        """
        # 요청 경계다 — 여기서 발급한 id 가 검색·랭킹·근거·LLM 로그를 잇는다.
        new_trace_id()
        search_request = SearchRequest(
            query=request.question,
            workspace_keys=request.workspace_keys,
            # 인용이 목적이라 항상 켠다.
            include_evidence=True,
        )
        # ★`SearchQuery` 를 버리지 않는다(Step2). 앵커 기업명이 여기 있고,
        #   그게 있어야 질문에서 「무엇을」만 떼어낼 수 있다 —
        #   `evidence_selector.intent_of()` 참고.
        query, result = self._orchestrator.search(search_request)

        # ★이름 조회는 **경계에서 한 번**이다(설계서 §16-3). ①b 는 이 결과를
        #   메모리에서 대조만 한다 — 「새 검색을 하지 않는다」(§10 ①b).
        workspace_names = workspace_service.names_of(request.workspace_keys)
        decision = query_understanding.decide_anchor(
            request.question, query.resolved_entities, workspace_names)
        return query, result, decision

    def retrieve(self, request: AskRequest) -> RetrieveResponse:
        """질문 하나 → 챗봇이 인용할 재료. **여기서 문장을 만들지 않는다.**

        ★`/retrieve` 의 **동작은 안 바뀐다**(설계서 §14-5) — `anchors[]` 가 실릴
          뿐이고, SEMANTIC 은 여기서 여전히 살아 있는 경로다. `unresolved` 조기
          반환은 `/ask` 의 규약이라 `retrieve_for_ask()` 쪽에 있다.
        """
        query, result, decision = self._search(request)
        return self._assemble(request, query, result, decision, use_hits=True)

    def retrieve_for_ask(self, request: AskRequest) -> tuple[AnchorDecision,
                                                             Optional[RetrieveResponse]]:
        """`/ask` 전용 입구 — **`unresolved` 면 재료를 만들지 않는다**(설계서 §14-4).

        ★못 찾은 대상에 워크스페이스 재료를 붙이면 그게 곧 조용한 오답이다.
          조립을 건너뛰므로 Neo4j 왕복(사건·관계·파급·근거)도 나가지 않는다.
        """
        query, result, decision = self._search(request)
        if decision.source is AnchorSource.UNRESOLVED:
            log.info("ask.unresolved named=%r — 재료를 만들지 않는다", decision.named)
            return decision, None
        # ★`/ask` 에서만 앵커가 재료를 정한다(설계서 §14-5) — `/retrieve` 는 무변경.
        use_hits = _hits_reflect_the_anchor(decision, query)
        return decision, self._assemble(request, query, result, decision,
                                        use_hits=use_hits)

    # ── ③~⑥ — 재료를 조립한다 ──────────────────────────────────────────
    def _assemble(self, request: AskRequest, query: SearchQuery,
                  result: SearchResult, decision: AnchorDecision,
                  *, use_hits: bool) -> RetrieveResponse:
        if use_hits:
            companies = _companies_from(result)
        else:
            # 히트가 앵커를 반영하지 않는다 — 앵커 자신이 재료의 출발점이다.
            companies = _anchor_companies(decision)
            log.info("material.anchored companies=%s (검색 히트 %d건은 쓰지 않는다)",
                     [c.key for c in companies], len(result.hits))
        # ★재료 기업이 하나도 안 남았으면 앵커로 메운다(현황서 §5-16). 앵커 경로에서는
        #   이미 앵커가 `companies` 라 무동작이다.
        companies = _with_anchor_backstop(companies, decision)
        events = self._events_of(companies, request.question, query, decision)
        propagation = self._propagation_of(events)
        relations = self._relations_of(companies, set(request.workspace_keys),
                                       query, decision)
        # ★히트를 재료로 **안 써도 그 근거는 그대로 모은다.** 한 번 걸러 봤다가
        #   실측으로 되돌렸다 — 현황서 §8-6. 요지 둘:
        #     · SEMANTIC 히트는 애초에 근거를 안 들고 온다(실측 0건). 거를 게 없다.
        #     · anchorless 히트의 근거는 **절반가량이 워크스페이스에 닿는다**
        #       (「납품 단가 압박」 38건 중 18 · 「최근 인수 사례」 140건 중 78).
        #       거르면 질문이 물은 바로 그 사례를 버린다.
        evidence = self._evidence_of(events, relations, result)

        return RetrieveResponse(
            question=request.question,
            match_type=_match_type_of(result),
            anchors=decision.anchors,
            companies=companies,
            events=events,
            relations=relations,
            propagation=propagation,
            evidence=evidence,
        )

    # ── 사건 ────────────────────────────────────────────────────────────
    def _events_of(self, companies: list[RelationEndpoint],
                   question: str, query: SearchQuery,
                   decision: AnchorDecision) -> list[Event]:
        """**Event 노드 기준**으로 묶는다. 같은 사건에 여러 기업이 엮여 있으면
        기업마다 한 번씩 나오는데, 그걸 그대로 쌓으면 같은 사건을 여러 번 말한다.

        ★selection 은 **기업 scope 안에서, 기업마다 따로** 한다(Step2).
          전부 한 줄로 세워 자르면 사건이 많은 기업이 상한을 다 먹고 나머지
          기업이 통째로 사라진다 — 그건 「관련 없어서」가 아니라 「다른
          기업이라서」 버린 것이다.

        ★공유 사건의 근거는 **scope 안에 있는 기업들 것을 합친다.** Step1 이
          근거를 엣지로 좁힌 뒤로, 여기 dedup 이 먼저 온 기업 것만 남기고
          나머지를 조용히 버리고 있었다(실측: 「담합 소송」 질의에서 3건).
          질문이 부른 기업이 둘이면 둘 다 근거다. scope 밖 기업은 애초에
          `companies` 에 없으므로 섞이지 않는다.
        """
        # ★**workspace 앵커 경로에서 이 목록이 비어 순위가 퇴행했다** (2026-08-26).
        #
        #   `decide_anchor()` 는 `resolved_entities` 가 **있으면** `query` 로 가므로,
        #   `source=workspace` 는 **정의상 `resolved_entities` 가 0** 이다. 그런데
        #   `anchor_names` 를 거기서만 읽어서 workspace 질의는 늘 `[]` 였다
        #   (실측: 「납품 단가 압박」·「최근 인수 사례」·「생산 차질 위험」 셋 다).
        #
        #   그러면 `evidence_selector` 가 실험 3회로 정한 규칙 —「질문과 라벨
        #   **양쪽에서** 앵커 기업명 제거」— 가 라벨 쪽에서 안 걸린다. 질문에는
        #   기업명이 없고(그래서 workspace 로 떨어졌다) 라벨에는 있으니, 모듈이
        #   **실패로 기록한 실험 ②**(「기업명이 든 라벨이 상위를 먹는다」)로
        #   그대로 되돌아간다.
        #
        #   ★이름은 이미 손에 있다 — `decision.anchors` 가 그 워크스페이스 기업들이다.
        anchor_names = [r.corp_name for r in query.resolved_entities if r.corp_name]
        if not anchor_names:
            anchor_names = [a.name for a in decision.anchors if a.name]
        intent = evidence_selector.intent_of(question, anchor_names)
        matched = evidence_selector.matched_event_types(intent)

        by_company = [(c, [Event(**row) for row in company_service.events_of(c.key)])
                      for c in companies]
        # 유사도는 **한 번에** 구한다 — 기업마다 부르면 왕복이 기업 수만큼 는다.
        embed = self._embed if self._embed is not None else _default_embed
        sims = evidence_selector.similarities(
            [e for _, events in by_company for e in events],
            intent=intent, embed=embed, anchor_names=anchor_names)

        out: list[Event] = []
        seen: dict[str, Event] = {}
        dropped = 0
        for _company, events in by_company:
            kept, cut = evidence_selector.select(
                events, matched=matched, sims=sims, limit=_MAX_EVENTS_PER_COMPANY)
            dropped += len(cut)
            for event in kept:
                previous = seen.get(event.event_id)
                if previous is not None:
                    _merge_evidence_ids(previous, event)
                    continue
                seen[event.event_id] = event
                out.append(event)

        if dropped:
            log.info("events truncated dropped=%d kept=%d intent=%r matched=%s "
                     "sims=%d", dropped, len(out), intent, sorted(matched), len(sims))
        return out

    # ── 파급 ────────────────────────────────────────────────────────────
    def _propagation_of(self, events: list[Event]) -> list[Propagation]:
        """★사건이 있어야 계산된다 — 사건 조회 뒤에만 부른다(설계서 §13).

        `is_risk` 가 아닌 사건은 계산하지 않는다.

        ★파급 계산을 새로 만들지 않는다. `relation_service.event_impact()` 를
          쓰는데, 이게 `graph_service.propagate_risk()` 를 감싸면서 기업 `key` 까지
          붙여 준다 — `GET /events/{id}/impact` 가 쓰는 바로 그 경로다.
          `propagate_risk()` 를 직접 부르면 dataclass 가 나와 `key` 가 없고
          응답 스키마와 모양이 다르다.
        """
        risky = [e for e in events if e.is_risk]
        if len(risky) > _MAX_RISK_EVENTS_FOR_PROPAGATION:
            log.info("risk events truncated %d -> %d",
                     len(risky), _MAX_RISK_EVENTS_FOR_PROPAGATION)
        out: list[Propagation] = []
        for event in risky[:_MAX_RISK_EVENTS_FOR_PROPAGATION]:
            rows = relation_service.event_impact(event.event_id)
            if rows is None:      # 사건 노드를 못 찾음 — 조용히 0건으로 두지 않는다
                log.warning("event_impact miss: %s", event.event_id)
                continue
            out.extend(Propagation(**row) for row in rows)
        return out

    # ── 관계 ────────────────────────────────────────────────────────────
    def _relations_of(self, companies: list[RelationEndpoint],
                      workspace_keys: set[str], query: SearchQuery,
                      decision: AnchorDecision) -> list[Relation]:
        """`company_service.relations_of()` 로 채운다.

        ★검색이 이미 준 `SearchHit.relations`(=`SearchRelation`)를 그대로 쓰지
          않는 이유 — 거기엔 `edge_id`·양끝은 있지만 `freshness`·`score`·
          `corroboration`·`subtype` 이 없다. `RetrieveResponse.relations` 는 그걸
          전부 요구하므로, 없는 값을 기본값으로 채우면 **지어내는 것**이 된다.
          `company_service` 는 같은 `edge_id`(elementId)를 쓰면서 그 값들을 실제로
          들고 있다.
        ★NAME·SEMANTIC 분기는 애초에 `SearchHit.relations` 가 비어 있어(관계
          검색을 거치지 않는다) 어차피 조회가 필요하다.

        ★**링(ring) 순서로 줄을 세운 뒤에 자른다**(설계서 §3). 점수순으로 먼저
          자르면 Ring 0 이 통째로 사라진다 — 실측(2026-08-25) 삼성전자 관계
          526건에서 Ring 0 은 **137·225·414번째**이고 SK하이닉스도 68·126·166번째다.
          상위 10건만 받으면 워크스페이스 안쪽 관계가 하나도 안 남는다.

        ★**점수 상한을 걸지 않고 받아도 비용이 같다.** `relations_of()` 의 Cypher 에
          LIMIT 이 없어 조회량이 어차피 같다 — 실측 526건 155ms vs limit=10 127ms.
          `graph_searcher._fetch_limit()` 이 이미 같은 이유로 같은 선택을 했다.

        ★**hard filter 가 아니다.** 워크스페이스와 안 닿는 관계(Ring 3)도 남긴다 —
          순서만 뒤로 간다(설계서 §3).

        ★**④a 관계 의도 선택은 링 안에서만 한다**(현황서 §5-4 · 완료조건 ⓐ).
          `relation_selector` 가 질문이 물은 `edge_types`·`direction` 을 위로
          올리는데, **링을 가로지르지는 않는다** — 링별 quota 냐 의도별 우선순위
          냐는 아직 `[DECIDE]` 이고(현황서 §5-17·§7-3) 둘 다 재 본 적이 없다.
        """
        by_ring: dict[int, list[dict]] = {}
        seen: set[str] = set()
        for company in companies:
            for row in company_service.relations_of(company.key):
                if row["edge_id"] in seen:
                    continue
                seen.add(row["edge_id"])
                by_ring.setdefault(_ring_of(row, workspace_keys), []).append(row)

        # ★질문이 무슨 관계를 물었나 — 지금까지 `SearchQuery` 에 와 있는데도 한
        #   번도 참조되지 않던 신호다(현황서 §5-4).
        matched = relation_selector.matched_edge_types(query)
        anchor_keys = {a.key for a in decision.anchors}
        # 링 안에서는 의도 → 입력 순서(=점수순)가 남는다 — 같은 질문에 매번 다른
        # 순서가 나오면 안 된다(`evidence_selector.select` 와 같은 규약).
        ordered = [row
                   for ring in sorted(by_ring)
                   for row in relation_selector.order(
                       by_ring[ring], matched=matched, direction=query.direction,
                       anchor_keys=anchor_keys)]
        limit = _MAX_RELATIONS_PER_COMPANY * max(len(companies), 1)
        kept, cut = ordered[:limit], ordered[limit:]

        log.info("relations.rings %s -> kept=%d cut=%d matched=%s direction=%s",
                 {ring: len(rows) for ring, rows in sorted(by_ring.items())},
                 len(kept), len(cut), sorted(matched),
                 getattr(query.direction, "value", None))
        return [Relation(**row) for row in kept]

    # ── 근거 ────────────────────────────────────────────────────────────
    def _evidence_of(self, events: list[Event], relations: list[Relation],
                     result: SearchResult) -> list[Evidence]:
        """관계·사건·검색히트의 근거 id 를 **합집합으로 모아 한 번에** 조회한다.

        셋을 다 모으는 이유는 출처가 셋이기 때문이다 — 관계에 달린 근거, 사건에
        달린 근거, 그리고 검색이 짚어 준 근거. 어느 하나만
        보면 답변이 인용할 수 있는 문장이 줄어든다.

        ★사건 근거는 **HAS_EVENT 엣지**에서 온다(`company_service.events_of`).
          Event 노드의 `evidence_ids` 는 그 사건에 엮인 **모든 기업의 합집합**이라,
          쓰면 「SK하이닉스 노조」 질의에 현대오토에버 기사가 섞인다(2026-08-23).
        """
        from_relations = [r.evidence_id for r in relations if r.evidence_id]
        from_events = [eid for event in events for eid in event.evidence_ids]
        # ★히트의 근거는 **재료 기업을 무엇으로 정했든** 그대로 모은다. 한 번
        #   걸러 봤다가 실측으로 되돌렸다(현황서 §8-6) — 여기 든 근거의 절반가량이
        #   워크스페이스에 닿아, 거르면 질문이 물은 사례를 버린다.
        from_hits = [ref["evidence_id"] for hit in result.hits for ref in hit.evidence
                     if ref.get("evidence_id")]
        ids = from_relations + from_events + from_hits

        rows = relation_service.evidence_for_ids(ids)
        evidence = [Evidence(**row) for row in rows]

        # 출처별로 갈라 남긴다 — 합계만 있으면 「근거가 왜 이것뿐인가」를 못
        # 따진다. `missing` 은 id 는 있는데 원문을 못 찾은 것이라 인용에 못 쓴다.
        log.info("evidence.collect from_relations=%d from_events=%d from_hits=%d "
                 "unique=%d -> fetched=%d missing=%d ids=%s",
                 len(from_relations), len(from_events), len(from_hits), len(set(ids)),
                 len(evidence), sum(1 for e in evidence if e.missing),
                 [e.evidence_id for e in evidence[:_MAX_LOGGED_EVIDENCE]])
        return evidence

    # ── HTTP 경계용 ──────────────────────────────────────────────────────
    async def retrieve_async(self, request: AskRequest) -> RetrieveResponse:
        """`retrieve()` 를 threadpool 에서 돌린다.

        ★`SearchOrchestrator.search()` 도 그래프·근거 조회도 전부 **동기 블로킹**
          이다. async 라우트에서 그냥 부르면 이벤트루프가 멈춘다.
        ★타임아웃은 아직 붙이지 않았다 — 기존 10초는 `/search/nl` 컨트롤러가
          「실측 근거 없는 잠정치」라고 스스로 적어 둔 값이고, 이 서비스는 검색
          외에 그래프·근거 조회를 더 한다. 단계별 예산을 **실측 뒤에** 정한다.
          `asyncio.wait_for` 는 threadpool 스레드를 죽이지 못하므로, 진짜 취소가
          필요하면 드라이버 레벨(PG `statement_timeout`·Neo4j `tx.timeout`)로
          내려야 한다 — 현재 어느 저장소에도 설정돼 있지 않다.
        """
        from fastapi.concurrency import run_in_threadpool

        return await run_in_threadpool(self.retrieve, request)
