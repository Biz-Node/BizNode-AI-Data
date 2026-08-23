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

from app.api.schemas import (AskRequest, Event, Evidence, MatchType, Propagation, Relation,
                             RelationEndpoint, RetrieveResponse)
from app.core.trace import new_trace_id, trace_logger
from app.services import company_service, evidence_selector, relation_service
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


class RetrieveService:
    def __init__(self, orchestrator: Optional[SearchOrchestrator] = None,
                 *, embed=None) -> None:
        self._orchestrator = orchestrator or build_orchestrator()
        # 주입용 — 테스트가 OpenAI 없이 돌 수 있어야 한다. None 이면 호출 시점에
        # 모듈 전역 `_default_embed` 를 쓴다(monkeypatch 가 먹도록 늦게 읽는다).
        self._embed = embed

    def retrieve(self, request: AskRequest) -> RetrieveResponse:
        """질문 하나 → 챗봇이 인용할 재료. **여기서 문장을 만들지 않는다.**"""
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

        companies = _companies_from(result)
        events = self._events_of(companies, request.question, query)
        propagation = self._propagation_of(events)
        relations = self._relations_of(companies)
        evidence = self._evidence_of(events, relations, result)

        return RetrieveResponse(
            question=request.question,
            match_type=_match_type_of(result),
            companies=companies,
            events=events,
            relations=relations,
            propagation=propagation,
            evidence=evidence,
        )

    # ── 사건 ────────────────────────────────────────────────────────────
    def _events_of(self, companies: list[RelationEndpoint],
                   question: str, query: SearchQuery) -> list[Event]:
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
        anchor_names = [r.corp_name for r in query.resolved_entities if r.corp_name]
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
    def _relations_of(self, companies: list[RelationEndpoint]) -> list[Relation]:
        """`company_service.relations_of()` 로 채운다.

        ★검색이 이미 준 `SearchHit.relations`(=`SearchRelation`)를 그대로 쓰지
          않는 이유 — 거기엔 `edge_id`·양끝은 있지만 `freshness`·`score`·
          `corroboration`·`subtype` 이 없다. `RetrieveResponse.relations` 는 그걸
          전부 요구하므로, 없는 값을 기본값으로 채우면 **지어내는 것**이 된다.
          `company_service` 는 같은 `edge_id`(elementId)를 쓰면서 그 값들을 실제로
          들고 있다.
        ★NAME·SEMANTIC 분기는 애초에 `SearchHit.relations` 가 비어 있어(관계
          검색을 거치지 않는다) 어차피 조회가 필요하다.
        """
        out: list[Relation] = []
        seen: set[str] = set()
        for company in companies:
            rows = company_service.relations_of(
                company.key, limit=_MAX_RELATIONS_PER_COMPANY)
            for row in rows:
                if row["edge_id"] in seen:
                    continue
                seen.add(row["edge_id"])
                out.append(Relation(**row))
        return out

    # ── 근거 ────────────────────────────────────────────────────────────
    def _evidence_of(self, events: list[Event], relations: list[Relation],
                     result: SearchResult) -> list[Evidence]:
        """관계·사건·검색히트의 근거 id 를 **합집합으로 모아 한 번에** 조회한다.

        셋을 다 모으는 이유는 출처가 셋이기 때문이다 — 관계에 달린 근거, 사건에
        달린 근거(`Event.evidence_ids`), 그리고 검색이 짚어 준 근거. 어느 하나만
        보면 답변이 인용할 수 있는 문장이 줄어든다.
        """
        from_relations = [r.evidence_id for r in relations if r.evidence_id]
        from_events = [eid for event in events for eid in event.evidence_ids]
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
