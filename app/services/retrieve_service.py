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
from app.core import querylog
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
MAX_RELATIONS_PER_COMPANY = 10
MAX_RISK_EVENTS_FOR_PROPAGATION = 3
# ★사건에는 상한이 **없었다**(Step2, 2026-08-23). 실측으로 「삼성전자와
#   SK하이닉스의 담합 소송」이 사건 155건 → 근거 205건 → 34,430자를 프롬프트에
#   실었다. 기업마다 따로 적용한다 — 전체 상한 하나로 두면 사건 많은 기업이
#   다 먹는다. 역시 실측 근거 없는 잠정치다.
MAX_EVENTS_PER_COMPANY = 10
# ★앵커 없는 질문의 사건 상한 — **한 줄로 세운 전체**에 건다(2026-09-02).
#   기업별 상한과 **일부러 같은 값**이다. 앵커 없는 목록도 「한 화면치 재료」라는
#   같은 역할이고, 값을 갈라 두려면 그 근거가 먼저다.
#   ★기업별 상한처럼 **기업마다** 걸면 안 된다 — 후보가 933행 · 234기업이라
#     기업당 10 이면 상한이 사실상 없는 것과 같다.
_MAX_GLOBAL_EVENTS = 10


def default_embed(texts: list[str]):
    """지연 로딩 — 임포트 시점에 OpenAI 클라이언트를 만들지 않는다. 테스트는
    이 이름을 monkeypatch 해서 끈다(`None` 이면 유사도 없이 규칙만 쓴다).

    ★**영속 캐시를 거친다**(2026-08-28). OpenAI 임베딩은 같은 입력에 같은 벡터를
      보장하지 않고, 편차가 배치 크기에 붙어 있다(150건에서 2.1e-03). 그러면
      가까이 붙은 두 사건의 순위가 실행마다 뒤집혀 **기준선이 흔들린다** —
      평가셋 점수 차이를 무엇에 귀속시킬지 못 정하게 된다.
      까닭과 실측은 `app/services/embedding_cache.py` 에 적어 뒀다.
    """
    from app.services.embedding_cache import embed_with_cache
    from pipeline.vectorstore.chroma_store import get_store

    return embed_with_cache(texts, lambda missing: get_store().embed(missing))


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


def is_anchorless(decision: AnchorDecision) -> bool:
    """전역 사건 검색으로 가는가. **판정을 한 곳에만 둔다** — `/ask` 와
    `/retrieve` 가 각자 `decision.source is ...` 를 쓰면 조건이 갈린다."""
    return decision.source is AnchorSource.ANCHORLESS


def match_type_of(result: SearchResult,
                   decision: Optional[AnchorDecision] = None) -> MatchType:
    """★**앵커가 없으면 `EXACT` 가 아니다**(2026-09-02).

    실측 F1: 「최근 주요 투자 이벤트가 뭐야?」가 앵커를 하나도 못 잡았는데
    `match_type=EXACT` 로 나갔다. `SearchMode.RELATIONSHIP` 이 `EXACT` 로 매핑돼
    있어서인데, 그 매핑은 **앵커를 잡은 관계 검색**을 전제한 것이다.

    추론 계층이 이 값에서 읽는 것은 「그래프에서 정확히 찾았나, 의미 유사도로
    찾았나」 하나뿐이다(§11). 앵커 없는 전역 사건 검색은 규칙 티어와 임베딩
    유사도가 순위를 만드므로 **`SEMANTIC` 쪽**이다 — 같은 무게로 말하면 안 된다는
    그 규약이 걸려야 하는 자리다.

    ★새 enum 값을 만들지 않는다. `MatchType` 은 API 계약이고, 추론 계층에
      필요한 이분법은 지금 둘로 충분하다.
    """
    if decision is not None and is_anchorless(decision):
        return MatchType.SEMANTIC
    return _MATCH_TYPE_BY_MODE[result.mode]


def companies_from(result: SearchResult) -> list[RelationEndpoint]:
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


def ring_of(row: dict, workspace_keys: set[str]) -> int:
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


def anchor_companies(decision: AnchorDecision) -> list[RelationEndpoint]:
    """앵커 자신을 재료 기업으로 삼는다(설계서 §3 material anchor).

    ★앵커 기업 수 상한은 **기존 `_MAX_COMPANIES` 를 그대로** 쓴다 — 새 숫자를
      만들지 않는다. 「몇 곳까지 쓰는가」의 확정값은 아직 `[DECIDE]` 다
      (현황서 §7-4). **조용히 자르지 않는다.**
    """
    companies = [RelationEndpoint(key=a.key, name=a.name) for a in decision.anchors]
    if len(companies) > _MAX_COMPANIES:
        log.info("anchors truncated %d -> %d", len(companies), _MAX_COMPANIES)
    return companies[:_MAX_COMPANIES]


def with_anchor_backstop(companies: list[RelationEndpoint],
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


def hits_reflect_the_anchor(decision: AnchorDecision, query: SearchQuery) -> bool:
    """검색 히트를 재료로 써도 되나.

    ★**두 경우다.**

        ① `source=query` 이고 ② Search 가 **실제로 앵커를 잡았다**
           (`resolved_entities` 가 있음). 그때 히트는 「그 기업의 관계 상대」이고
           그게 곧 답이다(「삼성전자에 납품하는 기업」).

        ② `source=anchorless` — **앵커가 아예 없다.** 그러면 히트 말고 재료가
           없다. 전에는 이 자리가 `workspace` 였고 워크스페이스 기업을 앵커로
           승격시켜 재료를 만들었는데, 그것이 §17-3 이 폐기한 구조다. 앵커를
           지어내지 않으므로 **Global Search 가 찾아 준 것이 재료**다
           (최종 설계 §8: Global Search → Candidate → Contextual Ranking).

    ★쓰면 안 되는 경우 둘 — 히트가 **정해진 앵커와 어긋난다.**

        `source=context`       화면이 대상을 알고 있다 — 그 기업이 재료다
        `source=query` + norm_name fallback
                               ② 는 못 찾았고 우리가 뒤늦게 찾은 앵커라
                               히트는 그 앵커를 반영하지 않는다 (§16-1)

    ★★`anchorless` 히트의 **질은 보장되지 않는다.** SEMANTIC 폴백은 의미가
      비슷한 아무 기업이고, anchorless 관계 조회는 source 5 + target 5 를
      점수순으로 채운다(§14-7 ⓑ). 그래서 답변에 「대상 지정 없음」 표기가
      함께 나간다(`llm/prompt.TARGET_NOTE_BY_SOURCE`) — 재료가 약하다는 사실이
      사용자에게 가는 것이 조용히 서술하는 것보다 낫다(설계서 §14-6).
    """
    if decision.source is AnchorSource.ANCHORLESS:
        return True
    return decision.source is AnchorSource.QUERY and bool(query.resolved_entities)


def material_companies(decision: AnchorDecision, query: SearchQuery,
                       result: SearchResult, question: str,
                       *, embed=None) -> tuple[list[RelationEndpoint], Optional[list]]:
    """재료 기업을 정한다 — ★**두 입구가 이 함수 하나를 쓴다.**

    `/retrieve`(`_assemble`)와 `/ask`(그래프 `plan_material`)가 같은 질문에 같은
    재료를 내야 한다. 전에는 분기가 **두 벌**이었고 한 줄이 갈려 있었다:

        /retrieve   use_hits=True 고정
        /ask        use_hits=hits_reflect_the_anchor(...)

    판정 함수는 이미 공유하고 있었는데 **`/retrieve` 만 그 답을 안 썼다.** 그래서
    「이 회사 리스크 어때?」(기업 상세 화면)가 입구에 따라 갈렸다 — `/ask` 는
    삼성전자를, `/retrieve` 는 삼성에스디에스·원익홀딩스·리노공업을 재료로 냈다
    (현황서 §6-0 A-6 · 2026-09-05).

    ★돌려주는 둘째 값은 **앵커리스에서 이미 고른 사건**이다. 그 경로는 사건을
      먼저 고르고 기업을 역산하므로, 부르는 쪽이 사건을 다시 고르면 두 입구가
      **다른 사건**을 보게 된다. 앵커 경로에서는 `None` 이다 — 사건은 기업이
      정해진 뒤에 고른다.
    """
    if is_anchorless(decision):
        # ★**사건을 먼저 고르고 기업을 역산한다**(설계 Q3 · 2026-09-02).
        #   전에는 검색 히트에서 Company 만 추려 재료 기업을 정했는데, 앵커
        #   없는 질문에서 그 기업 5곳은 사실상 임의로 정해진 것이었다(F1) —
        #   「최근 주요 투자 이벤트」에 (주)DB Inc.·IMANTOAG·유진로봇이 나왔다.
        #   히트에 실려 오던 Event 노드는 `companies_from()` 이 통째로 버려
        #   **살아 있는 유일한 Event 경로가 재료 조립 직전에 끊겨** 있었다(F4).
        events = select_global_events(question, embed=embed or default_embed)
        return companies_of_events(events), events

    if hits_reflect_the_anchor(decision, query):
        companies = companies_from(result)
    else:
        # 히트가 앵커를 반영하지 않는다 — 앵커 자신이 재료의 출발점이다.
        companies = anchor_companies(decision)
        log.info("material.anchored companies=%s (검색 히트 %d건은 쓰지 않는다)",
                 [c.key for c in companies], len(result.hits))
    # ★재료 기업이 하나도 안 남았으면 앵커로 메운다(현황서 §5-16). 앵커 경로에서는
    #   이미 앵커가 `companies` 라 무동작이다.
    return with_anchor_backstop(companies, decision), None


def anchor_names_for(query: SearchQuery, decision: AnchorDecision,
                      companies: list[RelationEndpoint]) -> list[str]:
    """사건 랭킹에서 **라벨과 질문 양쪽에서 떼어낼 기업명**(`evidence_selector`).

    ★**한 벌만 둔다.** `/ask` 그래프(`plan_material`)와 `/retrieve`(`_events_of`)가
      같은 값을 써야 한다 — 갈리면 두 입구가 같은 질문에 다른 순위를 낸다.

    ★셋째 갈래가 `anchorless` 때문에 필요해졌다(이번 개정). 전에는

          resolved_entities  →  없으면 decision.anchors

      뿐이었는데, `anchorless` 는 **둘 다 비어 있다.** 그대로 두면 목록이 `[]` 가
      되어 `evidence_selector` 가 실험 3회로 정한 규칙 —「질문과 라벨 **양쪽에서**
      앵커 기업명 제거」— 가 라벨 쪽에서 안 걸리고, 모듈이 **실패로 기록한 실험
      ②**(「기업명이 든 라벨이 상위를 먹는다」)로 되돌아간다(현황서 §5-23 이
      워크스페이스 경로에서 이미 한 번 고친 퇴행이다).

      `workspace` 경로에서 그 목록은 「재료가 된 기업들의 이름」이었다.
      `anchorless` 에서 같은 것은 **히트가 준 재료 기업들의 이름**이다.
    """
    names = [r.corp_name for r in query.resolved_entities if r.corp_name]
    if not names:
        names = [a.name for a in decision.anchors if a.name]
    if not names:
        names = [c.name for c in companies if c.name]
    return names


# ── 전역 사건 검색 (2026-09-02) ──────────────────────────────────────────
# 최종 설계 §5 시나리오 3 · §17-2. 「최근 주요 투자 이벤트가 뭐야?」처럼 **앵커도
# 워크스페이스도 없는 질문**의 재료다.
#
# ★**한 벌만 둔다.** `/retrieve` 는 이 결과를 그대로 쓰고, `/ask` 는
#   `plan_material` 이 여기서 고른 쌍을 `scope` 에 실어 도구가 **조회만** 하게
#   한다(`company_service.events_by_pairs`). 두 입구가 같은 함수를 부르는 것이
#   아니라 **한 번 고른 것을 나눠 쓰는** 구조라, 갈릴 자리가 없다.
#
# ★**랭킹을 새로 만들지 않는다.** `evidence_selector.select()` 를 그대로 부른다 —
#   규칙 티어·위험·최근창·유사도 덩어리가 전부 그대로 적용된다. 사본을 두면
#   앵커 경로와 앵커 없는 경로의 순위 규칙이 조용히 갈린다.


def select_global_events(question: str, *, embed,
                         limit: int = _MAX_GLOBAL_EVENTS) -> list[Event]:
    """전역 사건 후보에서 질문이 부른 것을 고른다. **(기업, 사건) 쌍 단위.**

    ★`anchor_names` 가 **빈 목록인 것이 맞다.** 앵커가 없으니 질문에서도 라벨에서도
      떼어낼 기업명이 없다. `evidence_selector` 가 실험 3회로 정한 규칙(「질문과
      라벨 양쪽에서 앵커 기업명 제거」)이 풀려는 문제는 **질문에 든 기업명이
      유사도를 지배하는 것**인데, 앵커 없는 질문에는 그 기업명이 애초에 없다.

      ★라벨 쪽 기업명(「**삼성전자** 본사 압수수색」)은 여전히 남는다. 행마다
        기업이 달라 한 벌의 `anchor_names` 로는 못 떼는데, 떼는 편이 나은지는
        **아직 안 쟀다** — `similarities()` 에 행별 제거를 넣는 것은 시그니처
        변경이라 근거가 먼저다.

    ★`event_id` 로 **접지 않는다**(설계 Q2). 같은 사건에 기업이 둘 이상 붙은 것이
      5.7%(실측)이고, 그때 `role`·`occurred_at`·`evidence_ids` 가 기업마다 다르다.
      앵커 없는 질문에서는 **누구에게 난 일인가**가 곧 답의 일부다.
    """
    rows = company_service.global_events()
    candidates = [Event(**row) for row in rows]
    intent = evidence_selector.intent_of(question, [])
    matched = evidence_selector.matched_event_types(intent)
    risk_wanted = evidence_selector.risk_intent(intent)
    recent_since = (evidence_selector.recent_window()
                    if evidence_selector.recent_intent(intent) else None)
    sims = evidence_selector.similarities(candidates, intent=intent, embed=embed,
                                          anchor_names=[])
    kept, cut = evidence_selector.select(
        candidates, matched=matched, sims=sims, limit=limit,
        risk_wanted=risk_wanted, recent_since=recent_since)
    firms = {e.company.key for e in kept if e.company}
    log.info("global.events 후보=%d 선택=%d 버림=%d intent=%r matched=%s "
             "risk=%s recent=%s 기업=%d",
             len(candidates), len(kept), len(cut), intent, sorted(matched),
             risk_wanted, recent_since, len(firms))
    # ★Phase 6 의 재료 — 라벨 없이 정답/누락/오탐을 세려면 이 넷이 남아야 한다.
    querylog.record(question=question, intent=intent, matched=matched,
                    selected_types=[e.event_type for e in kept],
                    anchor_source="anchorless", n_events=len(kept),
                    n_companies=len(firms), risk_wanted=risk_wanted,
                    recent_since=recent_since, path="global")
    return kept


def companies_of_events(events: list[Event]) -> list[RelationEndpoint]:
    """선택된 사건에서 **기업을 역산한다**(설계 Q3). 순서 보존 · key 중복 제거.

    ★**자르지 않는다.** `_MAX_COMPANIES` 를 여기 걸면 안 된다 — 이 목록이 곧
      Agent 의 `scope.allowed` 라, 5 로 자르면 나머지 사건의 기업이 범위 밖이
      되어 도구가 `OutOfScopeKey` 로 거부한다. 실측(2026-09-02): 상위 10건이
      **6~10개 기업**에 걸쳐 질의 5건 전부가 5를 넘었다.

      상한은 이미 **사건 쪽에** 걸려 있다(`_MAX_GLOBAL_EVENTS`). 사건을 먼저
      자르고 기업은 그 결과에서 나오므로, 기업 수는 사건 수를 넘지 못한다.
    """
    out: list[RelationEndpoint] = []
    seen: set[str] = set()
    for event in events:
        company = event.company
        if company is None or company.key in seen:
            continue
        seen.add(company.key)
        out.append(RelationEndpoint(key=company.key, name=company.name))
    return out


class RetrieveService:
    def __init__(self, orchestrator: Optional[SearchOrchestrator] = None,
                 *, embed=None) -> None:
        self._orchestrator = orchestrator or build_orchestrator()
        # 주입용 — 테스트가 OpenAI 없이 돌 수 있어야 한다. None 이면 호출 시점에
        # 모듈 전역 `default_embed` 를 쓴다(monkeypatch 가 먹도록 늦게 읽는다).
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
        # ★`context_keys` 도 같은 함수로 붙인다. **그래프 경로와 같아야 한다** —
        #   `material.resolve_anchor` 와 이 메서드가 갈리면 `/ask` 와 `/retrieve`
        #   가 같은 요청에 다른 앵커를 낸다(계약 6 parity).
        workspace_names = workspace_service.names_of(request.workspace_keys)
        context_names = (workspace_service.names_of(request.context_keys)
                         if request.context_keys else {})
        decision = query_understanding.decide_anchor(
            request.question, query.resolved_entities, workspace_names,
            context_names)
        return query, result, decision

    def retrieve(self, request: AskRequest) -> RetrieveResponse:
        """질문 하나 → 챗봇이 인용할 재료. **여기서 문장을 만들지 않는다.**

        ★재료 기업 선정은 `material_companies()` 한 곳이다 — 그래프의
          `plan_material` 과 **같은 함수**를 쓴다. 전에는 여기가 `use_hits=True`
          로 고정돼 있어서 같은 질문이 입구에 따라 다른 재료를 냈다(§6-0 A-6).

        ★`SEMANTIC` 은 여전히 살아 있는 경로다(설계서 §14-5) — `match_type` 은
          그대로 나간다. 바뀐 것은 「의미 유사 기업을 **재료로도 쓰나**」뿐이다.

        ★`unresolved` 조기 반환은 여기 없다. `/ask` 의 규약이고, 그래프의
          조건부 엣지(`is_resolved`)가 그 자리에서 갈라 준다.
        """
        query, result, decision = self._search(request)
        return self._assemble(request, query, result, decision)

    # ── ③~⑥ — 재료를 조립한다 ──────────────────────────────────────────
    def _assemble(self, request: AskRequest, query: SearchQuery,
                  result: SearchResult, decision: AnchorDecision) -> RetrieveResponse:
        companies, global_events = material_companies(
            decision, query, result, request.question, embed=self._embed)
        # 앵커리스는 사건을 이미 골랐다 — 다시 고르면 두 입구가 다른 사건을 본다.
        events = (global_events if global_events is not None
                  else self._events_of(companies, request.question, query, decision))
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
            match_type=match_type_of(result, decision),
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
        # ★**앵커 없는 경로에서 이 목록이 비면 순위가 퇴행한다** (현황서 §5-23).
        #   왜 셋째 갈래(재료 기업 이름)가 필요한지는 `anchor_names_for` 에 적었다.
        #   ★계산식은 **그 함수 한 곳**에만 둔다 — `/ask` 그래프의 `plan_material`
        #     이 같은 함수를 부른다.
        anchor_names = anchor_names_for(query, decision, companies)
        intent = evidence_selector.intent_of(question, anchor_names)
        matched = evidence_selector.matched_event_types(intent)
        # ★`event_type` 과 **다른 축**이다(ERD: 별개 축). 「리스크」에 걸리는
        #   event_type 패턴이 하나도 없어 위험 질의가 규칙 티어를 통째로 못 받았다.
        risk_wanted = evidence_selector.risk_intent(intent)
        # ★세 번째 축 — 시간. 「최근」은 지금까지 아무도 해석하지 않고 임베딩에
        #   잡음으로 들어가기만 했다. 물었을 때만 창을 연다(안 물으면 `None`).
        recent_since = (evidence_selector.recent_window()
                        if evidence_selector.recent_intent(intent) else None)

        by_company = [(c, [Event(**row) for row in company_service.events_of(c.key)])
                      for c in companies]
        # 유사도는 **한 번에** 구한다 — 기업마다 부르면 왕복이 기업 수만큼 는다.
        embed = self._embed if self._embed is not None else default_embed
        sims = evidence_selector.similarities(
            [e for _, events in by_company for e in events],
            intent=intent, embed=embed, anchor_names=anchor_names)

        out: list[Event] = []
        seen: dict[str, Event] = {}
        dropped = 0
        for _company, events in by_company:
            kept, cut = evidence_selector.select(
                events, matched=matched, sims=sims, limit=MAX_EVENTS_PER_COMPANY,
                risk_wanted=risk_wanted, recent_since=recent_since)
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
        querylog.record(question=question, intent=intent, matched=matched,
                        selected_types=[e.event_type for e in out],
                        anchor_source=decision.source.value, n_events=len(out),
                        n_companies=len(companies), risk_wanted=risk_wanted,
                        recent_since=recent_since, path="per_company")
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
        if len(risky) > MAX_RISK_EVENTS_FOR_PROPAGATION:
            log.info("risk events truncated %d -> %d",
                     len(risky), MAX_RISK_EVENTS_FOR_PROPAGATION)
        out: list[Propagation] = []
        for event in risky[:MAX_RISK_EVENTS_FOR_PROPAGATION]:
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
                by_ring.setdefault(ring_of(row, workspace_keys), []).append(row)

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
        # ★기업 수에 **천장을 씌운다**(2026-09-02). 앵커 없는 경로에서 `companies`
        #   가 선택된 사건에서 역산되면서 5곳 → 최대 10곳이 됐는데, 그건 도구
        #   범위와 사건을 위한 변경이지 **관계를 늘리려던 것이 아니다.** 씌우지
        #   않았더니 관계 90~100건 · 재료 94,921자로 앵커 경로(48,719자)의 두 배가
        #   됐다(실측). 상한은 예전 그대로 `_MAX_COMPANIES` 곱까지다.
        limit = MAX_RELATIONS_PER_COMPANY * max(min(len(companies), _MAX_COMPANIES), 1)
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
