# BizNode Search Layer — 현황서

> 이 문서는 **"실제로 어디까지 만들었는가"만** 다룹니다. 설계 근거·아키텍처는
> [`BizNode_Search_Layer_설계.md`](BizNode_Search_Layer_설계.md)를 보세요.
> 이전 개별 문서(`BizNode_AI에이전트_검색레이어_구현현황.md`,
> `BizNode_Search_Layer_구현_현황서.md`)를 통합·재구성했습니다. 작업이 끝날 때마다
> 이 문서를 갱신합니다.

마지막 갱신: 2026-08-15 · **전체 테스트 188개 전부 PASS**(실제 Docker PostgreSQL/Neo4j/
ChromaDB 대상, mock 없음 — 단 코드 호출 계약을 확인하는 일부 단위 테스트는 예외적으로 monkeypatch/fake repo 사용)

## 1. 구현 현황 요약

| 컴포넌트 | 상태 | 코드 위치 | 테스트 |
|---|---|---|---|
| DTO(`SearchRequest`/`SearchQuery`/`SearchHit`/`SearchResult`) / `EntityType`/`SearchMode`/`Direction` enum | ✅ 완료 | `search/dto/*.py`, `search/model/enums.py` | 31 PASS |
| PostgresRepository | ✅ 완료 | `search/repository/postgres_repository.py` | 13 PASS |
| ChromaRepository | ✅ 완료 | `search/repository/chroma_repository.py` | 9 PASS |
| EntityResolver | ✅ 완료 | `search/service/entity_resolver.py` | 19 PASS |
| QueryRouter | ✅ 완료 | `search/service/query_router.py` | 21 PASS |
| GraphSearcher(엔티티 메타데이터·anchor 없는 검색 포함) | ✅ 완료 | `search/service/graph_searcher.py`, `app/services/graph_service.py`(확장) | 28 PASS |
| VectorSearcher | ✅ 완료 | `search/service/vector_searcher.py` | 18 PASS |
| ResultRanker | ✅ 완료 | `search/service/result_ranker.py` | 13 PASS |
| SearchOrchestrator | ✅ 완료 | `search/service/orchestrator.py` | 22 PASS |
| CacheService / RedisRepository | 🔴 미구현(설계상 후순위) | `search/service/cache_service.py`, `search/repository/redis_repository.py`(둘 다 없음) | - |
| SearchController(API) | 🔴 미구현 | `search/api/`(디렉토리 자체 없음), `app/api/`는 `__init__.py`뿐 | - |
| Agent Tool 연동 | 🔴 미구현 | - | - |

**요약**: 질의 하나를 받아 EntityResolver/QueryRouter → GraphSearcher/VectorSearcher →
ResultRanker → `SearchResult`까지 전체를 지휘하는 SearchOrchestrator가 완료되어, 검색
파이프라인의 "핵심 흐름"은 전부 갖춰졌습니다. 남은 건 이를 감싸는 캐시·API·Agent Tool
노출뿐입니다.

---

## 2. 완료된 작업 상세

### DTO / enum
`SearchRequest`(API 계약, Pydantic) / `SearchQuery`(내부 실행 컨텍스트, dataclass, `direction`
필드 포함) / `SearchHit` / `SearchResult`(`used_semantic_fallback` 필드 포함) / `EntityType`·
`SearchMode`·`Direction` enum. `EntityType`은 `pipeline/validators/matrix.NODE_TYPES`와
assert로 일치를 강제합니다.

### PostgresRepository
```text
resolve_candidates(query, *, limit=10, threshold=0.50)   # 이름 exact + 종목코드 exact + pg_trgm fuzzy 다중 후보
find_by_corp_code(corp_code)
find_corp_codes_by_sector(sectors)
```

### ChromaRepository
```text
search_company(query_text, *, n_results=10, corp_codes=None)
search_evidence(query_text, *, n_results=10, where=None)
fetch_texts(evidence_ids)
```

### EntityResolver
검색어 → `corp_code` 해소. `PostgresRepository.resolve_candidates()`로 위임.

```text
EntityResolver(repo=None)
  .resolve(query) -> Optional[Resolution]
  .resolve_candidates(query) -> list[Resolution]
  .resolve_many(queries) -> dict[str, Optional[Resolution]]
```

후보 선택 우선순위: exact(tier 0) < 정규화/별칭 경유 fuzzy(score=1.0, tier 1) < 순수 fuzzy(tier
2). 동일 tier 내에서는 score 우선, 최상위가 동점이면 `None`(임의 확정 안 함). `companies`(64개
핵심 기업) 존재 여부는 판정하지 않습니다 — `corp_code_master` 식별 가능 여부만 봅니다(stub 기업
정책).

검증 항목: exact 기업명 / 종목코드 / normalization / alias(영문명) / fuzzy / 다중 후보 / stub
기업 / 다중 entity(`resolve_many`).

### QueryRouter
`normalized_query`에서 관계(edge) 키워드와 방향을 감지하는 독립 컴포넌트.

```text
QueryRouter().route(normalized_query) -> RoutingResult(edge_types: list[str], direction: Optional[Direction])
```

- 방향까지 깊게 구현: `SUPPLIES_TO`/`OWNS_STAKE_IN`/`SUES` — 조사 패턴(주체 `가/이`→outgoing,
  대상 `에/에게/를/을`·`피소`→incoming) 기반, 애매하면 `None`.
- 대표 키워드 1개만 얕게 매핑: 나머지 9종(`협력`/`경쟁`/`인수`/`규제`/`개발`/`의존`/`임원`/
  `사건`/`영향`).
- EntityResolver를 호출하지 않고, mode 분류·Orchestrator 편입도 하지 않습니다(Orchestrator가
  이 결과를 소비할 예정).

### GraphSearcher
해소된 기업 + `edge_types`(+`direction`)로 `app.services.graph_service.relations_of()`를
호출해 `SearchHit`으로 변환.

```text
GraphSearcher().search(
    resolved_entities: list[Resolution], edge_types: list[str],
    direction: Optional[Direction] = None, *, top_k: Optional[int] = None,
) -> list[SearchHit]
```

- `relations_of()`를 그대로 호출(Neo4jRepository 우회 없음), `Relation.score`/`freshness`/
  `verdict`는 재계산 없이 그대로 옮깁니다. `propagate_risk()`는 사용하지 않습니다(Event 검색
  자체가 이번 범위 밖).
- `Relation`에 `source_id`/`source_entity_type`/`target_id`/`target_entity_type`을 추가해
  (`app/services/graph_service.py` 확장), 상대 엔티티의 `entity_id`/`entity_type`을 이름 기반
  추측이 아니라 실제 Neo4j 식별자·label로 채웁니다. id 우선순위는 `corp_code > person_key >
  norm_name > event_id > name`(기존 `batch/repair/first_seen.py`의 검증된 패턴 재사용).
- **anchor(해소된 기업)가 있을 때**: 상대측 방향을 판별해(`Relation.source`/`target`을
  `normalize_company_name()`으로 정규화 후 비교) `direction` 필터 적용.
- **anchor가 없을 때**(예: "최근 소송 관련 기업"): source 최대 5 + target 최대 5 = 최대 10건을
  반환(dedup, `relations_of()`의 score 내림차순 유지). 원천 조회량은 `top_k`와 분리해 최소
  50건을 확보하되, 최종 반환은 `top_k`로 절삭 — `top_k`의 "최종 결과 개수 상한" 의미는 유지.
- 알 수 없는 Neo4j label을 만나면(`EntityType`에 `UNKNOWN` 멤버가 없어 임의 추가하지 않음)
  검색 전체를 죽이지 않고 해당 엔티티만 조용히 제외합니다.
- `limit`은 모든 `relations_of()` 호출에 무조건 지정합니다(하드캡 100 — 실측 없는 잠정치).

### VectorSearcher
`company` 컬렉션(ChromaDB) 의미 검색 결과를 `SearchHit`으로 변환. `ChromaRepository.
search_company()`를 그대로 호출(embedding·컬렉션 접근 로직 신규 작성 없음).

```text
VectorSearcher(repo=None)
  .search(normalized_query, *, top_k=None) -> list[SearchHit]
```

- `company` 컬렉션만 검색합니다 — `evidence`(`search_evidence`)는 범위 밖, Person/Event는 벡터
  검색 미지원 정책을 그대로 적용(collection 선택 규칙 미확정 이슈는 이 정책 확정으로 해소).
- `corp_codes` 필터는 이번 Task에서 쓰지 않습니다 — 항상 전체 `company` 컬렉션 대상.
- **Score 정규화(실측, 2026-08-12)**: `company` 컬렉션은 생성 시 `hnsw:space`를 지정하지 않아
  ChromaDB 기본값 `l2`(제곱 유클리드 거리)를 씁니다(`collection.metadata=None` 확인). OpenAI
  임베딩은 단위벡터로 정규화돼 있음을 실측(norm≈1.0)으로 확인 — 단위벡터에서
  `d² = 2(1-cos)`이므로 `cos = 1 - d²/2`, `score = (cos+1)/2 = 1 - d²/4`로 환산합니다.
  **설계 문서 §6-1의 "Chroma=1-distance"는 metric을 확인하지 않은 잠정 가정이라 틀렸습니다** —
  이 문서와 설계 문서 갱신 필요(§4 이슈 표 참고).
- 최소 유사도 컷오프는 적용하지 않습니다 — ChromaDB가 반환하는 top-N을 점수 그대로 노출.
- `entity_id`는 `metadata.corp_code`를 우선 쓰되, 해외 stub 기업은 corp_code가 빈 문자열이라
  (실측: 2219건 중 1400건, 63%) chroma id에서 `co_` 접두어를 뗀 안정 식별자로 대체합니다. 이
  fallback id는 GraphSearcher가 쓰는 norm_name 기반 식별자와 다릅니다(§4 이슈 표 참고).
- `top_k` 기본값 10 · 하드캡 50(둘 다 실측 근거 없는 잠정치).

### ResultRanker
GraphSearcher·VectorSearcher 결과를 Reciprocal Rank Fusion(RRF)으로 병합·정렬·중복 제거.

```text
ResultRanker().rank(
    graph_hits: list[SearchHit], vector_hits: list[SearchHit], *, top_k: Optional[int] = None,
) -> list[SearchHit]
```

- **가중합 아님 — RRF만 사용**: `score(entity) = Σ 1/(k + rank_s(entity))`, k=60(RRF 원 논문·
  TREC 관행값, 실측 튜닝값 아님). GraphSearcher(Neo4j 관계 점수)와 VectorSearcher(Chroma L2
  거리 기반 유사도)는 스케일·의미가 달라 가중합 시 실제보다 정밀해 보이는 착시가 생긴다는
  판단(Task7 지침)에 따름.
- **소스 내부 dedup을 RRF 적용 전에 수행**: 각 소스 리스트를 score 내림차순 정렬 후
  `entity_id` 기준으로 순위 높은 쪽만 남기고 dedup한다. GraphSearcher가 anchor 없는 조회에서
  source/target 슬롯에 같은 엔티티를 중복 반환할 수 있는 케이스(§4 이슈 표)를 여기서 흡수한다
  — dedup 없이 RRF를 돌리면 중복 항목이 이중으로 기여해 점수가 부풀려진다(회귀 테스트로 검증).
- **정렬 미보장 방어**: GraphSearcher의 anchor 없는 출력(`source_hits + target_hits` 연결)은
  전역 score 내림차순을 보장하지 않는다(실측 확인) — RRF는 순위 기반이므로 ResultRanker가 먼저
  자체적으로 재정렬한다. 입력 리스트의 정렬 상태를 신뢰하지 않는다.
- **필드 보존**: `freshness`/`verdict`/`relations`(그래프 전용), `kind`(벡터 전용)는 재계산하지
  않고 원본 `SearchHit`에서 그대로 옮긴다. `entity_id`가 양쪽에 있으면 `sources`가
  `["neo4j", "chroma"]`로 합쳐진다.
- `score` 필드는 RRF 값(순위 신호)이지 확률/confidence가 아니다 — docstring에 명시.
- `top_k`는 최종 병합 결과에만 적용(절삭), `None`이면 전체 반환.

### SearchOrchestrator
EntityResolver → QueryRouter → GraphSearcher/VectorSearcher → ResultRanker를 지휘해
`SearchResult`를 만드는 최종 진입점(API/CacheService는 범위 밖).

```text
SearchOrchestrator(entity_resolver, query_router, graph_searcher, vector_searcher, result_ranker)
  .search(request: SearchRequest, *, today: Optional[date] = None) -> tuple[SearchQuery, SearchResult]
```

- **분기는 `edge_types` 유무로만 결정한다 — GraphSearcher의 결과 유무로 분기하지 않는다.**
  `edge_types`가 있으면 GraphSearcher만 호출하고(anchor 있으면 anchored, 없으면 anchorless는
  GraphSearcher 내부가 이미 처리), 결과가 0건이어도 VectorSearcher를 절대 호출하지 않는다.
  `edge_types`가 없으면 EntityResolver가 명확히 해소됐을 때(`resolve()`가 `None`이 아닐 때)만
  NAME 처리, 그 외에는 VectorSearcher(company 컬렉션) 호출.
- **폴백 정책 정정**: 기존에 "GraphSearcher가 빈 결과면 VectorSearcher로 자동 폴백"하기로 했던
  걸 이번에 철회했다. 실측(Task7, "삼성전자에 납품하는 기업") 결과 VectorSearcher가 실제
  공급사를 0건 맞히고 전부 삼성전자 계열사·지사만 반환한 게 확인됐다 — 관계 질의에 의미검색
  결과를 섞으면 없는 관계를 있는 것처럼 보여주는 사고가 난다. 새 규칙은 정확도보다 정직함을
  택한다: 결과가 적어도 있는 그대로("결과 없음")를 보여준다.
- **호출 순서**: QueryRouter를 먼저 실행한다(순수 함수, EntityResolver와 독립). 분기 결과에
  따라 필요한 EntityResolver 메서드만 부른다 — GraphSearcher 분기는 `resolve_candidates()`,
  NAME/SEMANTIC 분기는 `resolve()` — 같은 질의로 Postgres를 두 번 조회하지 않기 위해서다.
- **NAME 처리는 최소 구현**(`[설계 결정 필요]`로 남김, Task8 지침 §4) — 해소된 엔티티 정보만
  `SearchHit` 1건(`sources=["postgres"]`)으로 반환한다. GraphSearcher를 곁들이는 확장 여부는
  필요성이 확인되면 별도 Task.
- **GraphSearcher-only/VectorSearcher-only 분기도 ResultRanker를 거친다**(건너뛰지 않는다) —
  GraphSearcher의 anchorless 검색은 source/target 슬롯 간 dedup을 의도적으로 안 하고
  ResultRanker로 넘기므로, 여기서 건너뛰면 중복 entity_id가 그대로 샌다. 대가로 이 두 분기의
  `SearchHit.score`가 실제 Neo4j 점수/Chroma 유사도가 아니라 RRF 순위값(`1/(60+rank)`)으로
  바뀐다 — score의 의미가 달라진다는 트레이드오프가 있다(§4 이슈 표 참고). **NAME 분기만
  ResultRanker를 건너뛴다**(단일 확정 hit, 병합 대상 없음).
- `mode`/`used_semantic_fallback`은 사전에 정하지 않고 분기 실행 후 사후에 채운다 —
  `SearchQuery.mode`는 기본값이 없어 분기 결과를 알아야 생성 가능하다: GraphSearcher만 돌면
  결과 유무와 무관하게 `RELATIONSHIP`, VectorSearcher가 돌면 `SEMANTIC`+
  `used_semantic_fallback=True`, 둘 다 안 돌고 해소된 엔티티만 반환하면 `NAME`.

---

## 3. 남은 작업

우선순위는 설계 문서(§4 컴포넌트 표)의 데이터 흐름 순서를 따릅니다.

1. **CacheService + RedisRepository** — Orchestrator 전체를 감싸는 cross-cutting 캐시. Redis가
   프로젝트 어디에서도 실사용된 적이 없어 후순위입니다.
2. **SearchController(API)** — FastAPI `POST /api/search` 엔드포인트, 요청 검증, 에러 → HTTP
   status 매핑.
3. **Agent Tool 연동** — `search_company`/`search_relationship`/`search_semantic`/
   `search_evidence` thin wrapper.
4. **Docker / 배포** — ChromaDB 클라이언트-서버 버전 고정 체크리스트 반영.

---

## 4. 알려진 이슈 / 설계 결정 필요

| 이슈 | 내용 |
|---|---|
| anchor 없을 때 source/target 슬롯 간 전역 dedup 없음 | ✅ **해소(Task7)** — GraphSearcher는 의도적으로 dedup 안 하고 넘기며, ResultRanker가 RRF 적용 전 `entity_id` 기준 소스 내부 dedup(순위 높은 쪽만 유지)으로 흡수. 회귀 테스트로 "dedup 없으면 RRF 점수가 이중 기여로 부풀려짐"을 확인 |
| 여러 `Resolution` 동시 조회 미구현 | GraphSearcher는 점수 최고 1건만 사용, 나머지는 무시 |
| 저신뢰 키워드(9종) 정확도 미검증 | QueryRouter의 대표 키워드 1개씩만 등록, 실데이터 정확도 검증 안 함 |
| fuzzy threshold(0.50) 최종 미확정 | 검색 전용 threshold 분리 필요성은 인지했으나 실측 없어 기존 ER과 같은 값 유지 |
| `_HARD_LIMIT=100`, `_ANCHORLESS_MIN_FETCH=50`(GraphSearcher), `_DEFAULT_TOP_K=10`/`_HARD_LIMIT_TOP_K=50`(VectorSearcher), `_RRF_K=60`(ResultRanker) | 실측 근거 없는 잠정치(`_RRF_K`는 RRF 원 논문·TREC 관행값, 조정 여지로 남김) |
| mode(NAME/RELATIONSHIP/SEMANTIC/HYBRID) 자동 판별 규칙 | ✅ **부분 해소(Task8)** — NAME/RELATIONSHIP/SEMANTIC 규칙 확정: `edge_types` 있으면 결과 유무와 무관하게 RELATIONSHIP, 없고 EntityResolver가 명확히 해소되면 NAME, 그 외 VectorSearcher가 돌면 SEMANTIC. **단, `HYBRID`는 이 규칙으로는 절대 만들어지지 않는 죽은 enum 값이 됐다** — 설계 문서 §6-1 표는 "삼성전자 최근 투자 기업"을 HYBRID로 적어뒀지만 Task8 지침이 이를 명시적으로 override(RELATIONSHIP으로 확정)했다. HYBRID를 실제로 쓸지(예: 관계+시간 정렬 결합) 여부는 [설계 결정 필요]로 남음 |
| 폴백 정책 철회(Task8) | 기존 "GraphSearcher 0건 시 VectorSearcher 자동 폴백" 정책을 철회했다. 근거: 실측("삼성전자에 납품하는 기업")에서 VectorSearcher가 실제 공급사를 0건 맞히고 전부 삼성전자 계열사·지사만 반환 — 관계 질의에 의미검색 결과를 섞으면 없는 관계를 있는 것처럼 보여주는 사고가 남. 새 규칙은 `edge_types` 유무로만 분기(§2 SearchOrchestrator 절 참고) |
| NAME 처리 최소 구현 | [설계 결정 필요]로 유지 — 현재는 해소된 엔티티 1건만 `SearchHit`으로 반환. GraphSearcher를 곁들이는 확장(예: 대표 관계 몇 건 함께 반환) 여부는 별도 Task |
| 단일 소스 hit의 score가 RRF 값으로 대체됨 | SearchOrchestrator가 GraphSearcher-only/VectorSearcher-only 결과도 ResultRanker(dedup 목적)를 거치게 하면서, 이 hit들의 `score`가 실제 Neo4j 관계 점수/Chroma 유사도가 아니라 RRF 순위값(`1/(60+rank)`, 예: 1위 ≈0.0164)으로 바뀜. API 응답에서 score를 사용자에게 노출할 때 이 의미 변화를 어떻게 다룰지 SearchController 설계 시 재검토 필요 |
| `tests/search/test_example_queries.py`의 query2/query5 기대값이 Task8 정책과 어긋남 | 이 파일은 Orchestrator를 호출하지 않고 DTO를 손으로 채우는 Task1 스모크 테스트라 지금 깨지지는 않지만, query2("삼성전자 최근 투자 기업")의 `mode=HYBRID` 기대값과 query5("최근 소송 관련 기업")의 `mode=SEMANTIC`+`sources=["neo4j","chroma"]` 기대값은 이제 실제 Orchestrator 동작과 다르다. 사용자 확인 결과 이번엔 코드 수정 없이 이슈로만 기록(2026-08-15) |
| `top_k` 기본값/상한, Redis TTL | 미확정(설계 문서 §6 참고) |
| Ranking 가중합 도입 여부 | ✅ **Task7에서 확정**: 가중합 기각, RRF(Reciprocal Rank Fusion) 채택 — Neo4j 관계 점수와 Chroma L2 기반 유사도는 스케일·의미가 달라 가중합 시 실제보다 정밀해 보이는 착시가 생긴다는 판단 |
| 설계 문서 §6-1 "Chroma=1-distance" 오기 | 실측 결과 `company` 컬렉션은 l2 metric — 올바른 공식은 `1-distance/4`(단위벡터 가정). 설계 문서 정정 필요 |
| VectorSearcher `entity_id`가 GraphSearcher `entity_id`와 불일치 가능 | ✅ **해소(2026-08-15 patch, Task6→7)** — `company` 컬렉션의 63%(2219건 중 1400건, 해외 stub)가 `corp_code` 없는 문제를, VectorSearcher가 GraphSearcher와 같은 `normalize_company_name()` fallback으로 통일(`coalesce(corp_code, norm_name, ...)` 우선순위 일치). `test_stub_company_entity_id_matches_graph_searcher`(실제 Docker)로 검증됨. 단, entity_id 체계가 다른 제3의 컬렉션(예: evidence)과의 대조는 여전히 범위 밖·[설계 결정 필요]로 남음(Task7 지침 §7) |
| VectorSearcher 컷오프 미적용의 실사용 영향 / company 프로필 변별력 저하 가설 | 실측(§6-6 "HBM을 만드는 기업"): 10건 중 일부(HDC현대산업개발, NAVER J.Hub)는 주제상 무관해 보이나 점수 0.75~0.80대로 다른 결과와 큰 차이 없이 섞여 나옴. **Task7 추가 실측**("삼성전자에 납품하는 기업"): GraphSearcher는 실제 공급사(SFA반도체·세메스·솔브레인 등)를 반환하지만, 같은 질의를 VectorSearcher에 그대로 넣으면 상위 10건이 전부 "삼성전자 OO법인/판매법인" 등 삼성전자 자신의 계열사·지사이고 실제 공급사는 하나도 없음(RRF 병합 시 두 소스 간 `entity_id` 교집합 0건) — company 프로필 문서가 템플릿화돼 있어 임베딩 변별력이 낮다는 가설과 부합. 이 Task에서는 검증하지 않고 이슈로만 기록(데이터 파이프라인 담당 확인 필요, Task7 지침 §10-4) — 컷오프 도입 여부는 이 확인 이후 결정 |

---

## 5. 테스트 및 환경

- **테스트 원칙**: 실제 Docker Compose PostgreSQL/Neo4j/ChromaDB 대상(mock 없음). 순수 로직
  (dedup, tier 판정, 방향 판별 등)만 in-memory 객체로 단위 테스트. 코드 자체의 호출 계약(예:
  "limit이 항상 전달되는가")을 확인해야 하는 경우에만 예외적으로 `monkeypatch` 사용.
- **테스트 실행 환경**: 이 프로젝트의 `.venv`는 Windows 네이티브 Python이라 WSL에서 Docker
  Postgres/Neo4j에 접속하면 TCP는 연결되나 프로토콜 핸드셰이크 단계에서 리셋됩니다. WSL 전용
  venv `.venv-wsl`을 `uv`로 별도 생성해 사용 중입니다(git에는 안 잡힘).
  ```bash
  uv venv .venv-wsl --python 3.10
  uv pip install --python .venv-wsl/bin/python -r requirements.txt pytest
  .venv-wsl/bin/python -m pytest tests/
  ```
- **Docker Desktop(WSL2) 포트포워딩 불안정**: Postgres·Neo4j 컨테이너가 오래 떠 있으면(또는
  재시작 이력이 있으면) 연결이 `connection reset`으로 실패하는 사례가 반복됐습니다. 재현되면
  `docker restart biznode-postgres` / `docker restart biznode-neo4j`로 해결됩니다.
