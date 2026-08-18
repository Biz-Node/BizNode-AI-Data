# BizNode Search Layer — 현황서

> 이 문서는 **"실제로 어디까지 만들었는가"와 "무엇이 아직 미해결인가"**를 다룹니다.
> 설계 근거·아키텍처는 [`BizNode_Search_Layer_설계.md`](BizNode_Search_Layer_설계.md)를 보세요.
> 작업이 끝날 때마다 이 문서를 갱신합니다.

마지막 갱신: 2026-08-17 · 테스트 **214개 전부 PASS**(2026-08-16 마지막 실행)

---

## 1. 한눈에 보기

Search Layer는 **자연어 질의 하나를 받아, 3개 저장소를 조합해 하나의 결과 목록으로
돌려주는 계층**입니다. 새 검색 알고리즘을 만드는 게 아니라 이미 있는 기능(이름 해소·
그래프 조회·의미 검색)을 정해진 순서로 엮습니다.

```text
"삼성전자에 납품하는 기업"
    │
    │  ① QueryRouter      "납품" → SUPPLIES_TO, 조사 "에" → 방향 INCOMING
    │  ② AnchorExtractor  문장에서 기업명만 잘라냄 → "삼성전자"
    │  ③ EntityResolver   "삼성전자" → corp_code=00126380         [PostgreSQL]
    │  ④ GraphSearcher    삼성전자로 납품하는 관계 조회            [Neo4j]
    │  ⑤ ResultRanker     중복 제거 + RRF 정렬
    ▼
SFA반도체 · 솔브레인 · 세메스 · 원익IPS …
```

기업명이 없는 질의("HBM을 만드는 기업")는 ②③④ 대신 VectorSearcher(ChromaDB)로 갑니다.

**현재 상태 한 줄**: 위 파이프라인(엔진)은 전부 완성됐고 테스트도 실제 DB 대상으로
통과하지만, **이 엔진을 호출하는 외부 진입점(HTTP API)이 아직 없습니다.** 지금은
`SearchOrchestrator.search()`를 파이썬에서 직접 부르는 것 외에는 쓸 방법이 없습니다.

---

## 2. 구현 현황

| 컴포넌트 | 상태 | 코드 위치 | 테스트 |
|---|---|---|---|
| DTO(`SearchRequest`/`SearchQuery`/`SearchHit`/`SearchResult`) / `EntityType`·`SearchMode`·`Direction` enum | ✅ | `search/dto/*.py`, `search/model/enums.py` | 31 |
| PostgresRepository | ✅ | `search/repository/postgres_repository.py` | 16 |
| ChromaRepository | ✅ | `search/repository/chroma_repository.py` | 9 |
| EntityResolver | ✅ | `search/service/entity_resolver.py` | 19 |
| QueryRouter | ✅ | `search/service/query_router.py` | 21 |
| AnchorExtractor | ✅ | `search/service/anchor_extractor.py` | 18 |
| GraphSearcher | ✅ | `search/service/graph_searcher.py` + `app/services/graph_service.py`(확장) | 28 |
| VectorSearcher | ✅ | `search/service/vector_searcher.py` | 18 |
| ResultRanker | ✅ | `search/service/result_ranker.py` | 13 |
| SearchOrchestrator | ✅ | `search/service/orchestrator.py` | 27 |
| **SearchController(HTTP API)** | 🔴 미구현 | `search/api/` 디렉토리 없음, FastAPI 앱 진입점도 없음 | — |
| **CacheService / RedisRepository** | 🔴 미구현 | 파일 없음(Redis 컨테이너·의존성은 이미 준비됨) | — |
| **Agent Tool 연동** | 🔴 미구현 | — | — |

---

## 3. 컴포넌트별 상세

### 3-1. DTO / enum

`SearchRequest`(API 입력 계약, Pydantic) → `SearchQuery`(내부 실행 컨텍스트, dataclass) →
`SearchHit` → `SearchResult`(API 응답, Pydantic). `EntityType` 값은
`pipeline/validators/matrix.NODE_TYPES`와 assert로 일치를 강제하고, `SearchRequest.edge_types`는
`pipeline/ontology.EDGE_TYPES`에 없는 값이면 검증 오류를 냅니다.

### 3-2. PostgresRepository

```text
resolve_candidates(query, *, limit=10, threshold=0.50)  # 이름 exact + 종목코드 exact + pg_trgm fuzzy 다중 후보
best_candidate_match(candidates: list[str])             # 어절 후보 배열 → 최고 유사도 1건
find_by_corp_code(corp_code)
find_corp_codes_by_sector(sectors)
```

### 3-3. ChromaRepository

```text
search_company(query_text, *, n_results=10, corp_codes=None)
search_evidence(query_text, *, n_results=10, where=None)
fetch_texts(evidence_ids)
```

`pipeline/vectorstore`의 `VectorStore` Protocol에 의존합니다(임베딩 호출과 query가 이미
구현돼 있고, Qdrant 전환 시 이 Protocol만 갈아끼우면 됩니다).

### 3-4. EntityResolver

기업명 문자열 → `corp_code` 해소.

```text
EntityResolver(repo=None)
  .resolve(query) -> Optional[Resolution]
  .resolve_candidates(query) -> list[Resolution]
  .resolve_many(queries) -> dict[str, Optional[Resolution]]
```

- 후보 선택 우선순위: exact(tier 0) < 정규화·별칭 경유 fuzzy(score=1.0, tier 1) < 순수
  fuzzy(tier 2). 같은 tier 안에서는 score 우선, 최상위가 동점이면 `None`을 반환합니다
  (임의로 하나를 확정하지 않습니다).
- `companies` 테이블(64개 핵심 기업) 등재 여부는 보지 않고 `corp_code_master` 식별
  가능 여부만 판정합니다(stub 기업 정책).

### 3-5. QueryRouter

`normalized_query`에서 관계(edge) 키워드와 방향을 감지하는 순수 함수. EntityResolver를
호출하지 않습니다.

```text
QueryRouter().route(normalized_query) -> RoutingResult(edge_types: list[str], direction: Optional[Direction])
```

- **방향까지 판정하는 3종**: `SUPPLIES_TO`/`OWNS_STAKE_IN`/`SUES` — 조사 패턴 기반
  (주체 `가/이` → outgoing, 대상 `에/에게/를/을`·`피소` → incoming). 애매하면 `None`.
- **나머지 9종**은 대표 키워드 1개씩만 얕게 매핑(`협력`/`경쟁`/`인수`/`규제`/`개발`/
  `의존`/`임원`/`사건`/`영향`).

### 3-6. AnchorExtractor

자연어 문장에서 기업명(anchor) 후보 1개를 추출합니다. 이게 없으면 Orchestrator가
"삼성전자에 납품하는 기업" 문장 전체를 기업명으로 취급해 해소에 실패하고, GraphSearcher가
anchor 없는 전역 top-N 경로로 빠져 **무관한 기업이 섞여 나옵니다**(실측: 엔비디아·마이크론·
포스코).

```text
AnchorExtractor(repo=None).extract(raw_query: str) -> Optional[str]
```

- **동작**: 어절(공백 분리) 단위로 원본 + 조사 제거 후보를 만들고(긴 조사부터 검사),
  `is_generic_name()`으로 "기업"/"업체" 같은 일반명사를 걸러낸 뒤,
  `PostgresRepository.best_candidate_match()` **단일 쿼리**로 가장 유사한 후보 1건을 찾습니다.
  `normalized_query`(공백 제거됨)가 아니라 원본 `raw_query`를 입력으로 씁니다.
- **정밀도 우선**: 확신이 없으면 추출하지 않고 `None`을 반환합니다. 잘못 추출해 엉뚱한
  기업에 매칭되는 게, anchor 없이 정직하게 무관한 결과를 내는 것보다 나쁘다는 원칙입니다.
- **`word_similarity()`/`<%` 연산자는 폐기했습니다** — `corp_code_master`(118,535건)에서
  `gin_trgm_ops`가 `%`/`%>`만 지원해 `<%`는 Seq Scan(400~800ms)으로 떨어집니다(EXPLAIN
  ANALYZE 실측). 기존 `%` 연산자를 `corp_name % ANY(candidates)`로 묶으면 같은 GIN
  인덱스를 타면서(15~25ms) 어절 후보 전체를 단일 쿼리로 처리할 수 있습니다.
- **적용 범위는 `edge_types` 있는 분기뿐입니다** — 이 분기의 DB 왕복이 1회 → 2회로
  늘었습니다(`best_candidate_match` + `resolve_candidates`). NAME/SEMANTIC 분기는 §5-1 참고.

### 3-7. GraphSearcher

해소된 기업 + `edge_types`(+`direction`)로 `graph_service.relations_of()`를 호출해
`SearchHit`으로 변환합니다.

```text
GraphSearcher().search(
    resolved_entities: list[Resolution], edge_types: list[str],
    direction: Optional[Direction] = None, *, top_k: Optional[int] = None,
) -> list[SearchHit]
```

- `relations_of()`를 그대로 호출하며(임의 Cypher 없음), `Relation.score`/`freshness`/
  `verdict`는 재계산하지 않고 그대로 옮깁니다.
- **anchor가 있을 때**: `Relation.source`/`target`을 `normalize_company_name()`으로 정규화해
  비교하는 방식으로 상대측 방향을 판별하고 `direction` 필터를 적용합니다.
- **anchor가 없을 때**("최근 소송 관련 기업"): source 최대 5 + target 최대 5 = 최대 10건.
  원천 조회량은 `top_k`와 분리해 최소 50건을 확보하되 최종 반환만 `top_k`로 절삭합니다.
  이때 source/target 슬롯 간 중복은 **의도적으로 제거하지 않고** ResultRanker로 넘깁니다.
- `Relation`에 `source_id`/`source_entity_type`/`target_id`/`target_entity_type`을 추가해
  (`app/services/graph_service.py` 확장) 상대 엔티티 식별자를 이름 추측이 아니라 실제 Neo4j
  식별자·label로 채웁니다. id 우선순위는 `corp_code > person_key > norm_name > event_id >
  name`(`batch/repair/first_seen.py`의 기존 패턴 재사용).
- 알 수 없는 Neo4j label을 만나면 검색 전체를 죽이지 않고 해당 엔티티만 조용히 제외합니다.
- `limit`은 모든 `relations_of()` 호출에 무조건 지정합니다(하드캡 100).

### 3-8. VectorSearcher

ChromaDB `company` 컬렉션 의미 검색 결과를 `SearchHit`으로 변환합니다.

```text
VectorSearcher(repo=None).search(normalized_query, *, top_k=None) -> list[SearchHit]
```

- `company` 컬렉션만 검색합니다. `evidence` 컬렉션과 Person/Event 벡터 검색은 범위 밖입니다.
- **점수 환산**: `company` 컬렉션은 생성 시 `hnsw:space`를 지정하지 않아 ChromaDB 기본값
  `l2`(제곱 유클리드)를 씁니다. 임베딩이 단위벡터임을 실측(norm≈1.0)했으므로
  `d² = 2(1-cos)` → **`score = 1 - d²/4`**로 환산합니다.
- 최소 유사도 컷오프는 적용하지 않습니다 — ChromaDB가 반환하는 top-N을 그대로 노출합니다.
- `entity_id`는 `metadata.corp_code`를 우선 쓰되, 해외 stub 기업은 corp_code가 비어 있어
  (실측: 2219건 중 1400건, 63%) GraphSearcher와 동일한 `normalize_company_name()` fallback을
  적용해 식별자 체계를 맞췄습니다.

### 3-9. ResultRanker

GraphSearcher·VectorSearcher 결과를 Reciprocal Rank Fusion(RRF)으로 병합·정렬·중복 제거합니다.

```text
ResultRanker().rank(
    graph_hits: list[SearchHit], vector_hits: list[SearchHit], *, top_k: Optional[int] = None,
) -> list[SearchHit]
```

- **가중합이 아니라 RRF만 사용**: `score(entity) = Σ 1/(60 + rank_s(entity))`. Neo4j 관계
  점수와 Chroma L2 기반 유사도는 스케일·의미가 달라 가중합하면 실제보다 정밀해 보이는
  착시가 생긴다는 판단입니다.
- **소스 내부 dedup을 RRF 적용 전에 수행**합니다 — 각 리스트를 score 내림차순 정렬 후
  `entity_id` 기준으로 순위 높은 쪽만 남깁니다. dedup 없이 RRF를 돌리면 중복 항목이 이중
  기여해 점수가 부풀려집니다(회귀 테스트로 검증).
- **입력 리스트의 정렬 상태를 신뢰하지 않습니다** — GraphSearcher의 anchor 없는 출력
  (`source_hits + target_hits` 연결)은 전역 score 내림차순을 보장하지 않습니다(실측 확인).
- `freshness`/`verdict`/`relations`(그래프 전용), `kind`(벡터 전용)는 재계산 없이 원본에서
  그대로 옮깁니다. 같은 `entity_id`가 양쪽에 있으면 `sources`가 `["neo4j", "chroma"]`로 합쳐집니다.
- **`score`는 RRF 순위값이지 확률·confidence가 아닙니다**(§5-1 참고).

### 3-10. SearchOrchestrator

전체 흐름의 최종 진입점입니다(캐시·API는 범위 밖).

```text
SearchOrchestrator(entity_resolver, query_router, graph_searcher, vector_searcher,
                   result_ranker, anchor_extractor)
  .search(request: SearchRequest, *, today: Optional[date] = None) -> tuple[SearchQuery, SearchResult]
```

- **분기는 `edge_types` 유무로만 결정합니다** — GraphSearcher의 결과 유무로 분기하지
  않습니다. `edge_types`가 있으면 GraphSearcher만 호출하고, 결과가 0건이어도
  VectorSearcher를 절대 호출하지 않습니다.
- **"결과 0건이면 의미검색으로 폴백"은 철회했습니다**(Task 8). 실측("삼성전자에 납품하는
  기업")에서 VectorSearcher가 실제 공급사를 0건 맞히고 전부 삼성전자 계열사·지사만
  반환했습니다 — 관계 질의에 의미검색 결과를 섞으면 없는 관계를 있는 것처럼 보여주는
  사고가 납니다. 정확도보다 정직함을 택합니다.
- **호출 순서**: QueryRouter를 먼저 실행하고(순수 함수), 분기에 따라 필요한 EntityResolver
  메서드만 부릅니다 — GraphSearcher 분기는 `resolve_candidates()`, NAME/SEMANTIC 분기는
  `resolve()`. 같은 질의로 Postgres를 두 번 조회하지 않기 위해서입니다.
- **NAME 처리는 최소 구현**입니다 — 해소된 엔티티 정보만 `SearchHit` 1건
  (`sources=["postgres"]`)으로 반환합니다.
- **GraphSearcher-only / VectorSearcher-only 분기도 ResultRanker를 거칩니다**(dedup이
  필요하므로). **NAME 분기만 건너뜁니다**(단일 확정 hit, 병합 대상 없음).
- `mode`/`used_semantic_fallback`은 분기 실행 후 사후에 채웁니다: GraphSearcher만 돌면
  결과 유무와 무관하게 `RELATIONSHIP`, VectorSearcher가 돌면 `SEMANTIC` +
  `used_semantic_fallback=True`, 해소된 엔티티만 반환하면 `NAME`.

---

## 4. 남은 작업

| 순서 | 작업 | 내용 | 비고 |
|---|---|---|---|
| 1 | **SearchController(API)** | FastAPI 앱 진입점 + `POST /api/search` + 에러 → HTTP status 매핑 | 착수 전 §5-1의 두 결정이 필요. Agent Tool·수동 검증 전부의 병목 |
| 2 | **Agent Tool 연동** | `search_company`/`search_relationship`/`search_semantic`/`search_evidence` thin wrapper. `search_evidence`만 Orchestrator 우회 | API 선행 필요 |
| 3 | **CacheService + RedisRepository** | Orchestrator 전체를 감싸는 cross-cutting 캐시 | Redis 컨테이너(`docker-compose.yml`)와 `redis>=5.0` 의존성은 이미 준비됨 — 코드만 없음. 실사용 트래픽이 없어 효용 측정이 API 이후에나 가능 |
| 4 | **Docker / 배포** | 앱 컨테이너화 | ChromaDB 클라이언트-서버 버전 고정(`chromadb>=0.5.20,<0.6` ↔ 컨테이너 0.5.23)은 이미 반영됨 |

---

## 5. 미해결 이슈

### 5-1. API 착수 전 결정이 필요한 것

| 이슈 | 내용 |
|---|---|
| **`score`의 의미가 바뀌었다** | GraphSearcher-only/VectorSearcher-only 결과도 ResultRanker를 거치게 하면서, `SearchHit.score`가 실제 Neo4j 관계 점수·Chroma 유사도가 아니라 **RRF 순위값**(1위 ≈ 0.0164)이 됐습니다. 이 값을 API에서 그대로 노출할지, 순위로 바꿔 노출할지, 원점수를 별도 필드로 함께 낼지 결정이 필요합니다 |
| **요청 필드 3개가 무시되고 있다** | `SearchRequest.edge_types`를 `SearchOrchestrator`가 읽지 않습니다 — 관계 판정은 전적으로 QueryRouter가 질의문에서 합니다. `entity_types`/`filters`도 `SearchQuery`에 담기기만 하고 어떤 Searcher도 쓰지 않으며, `include_evidence`는 값과 무관하게 항상 evidence가 채워집니다. API 스펙을 확정하기 전에 "지원한다 / 필드에서 뺀다 / 무시된다고 문서화한다" 중 하나를 골라야 합니다 |

### 5-2. 기능 구멍

| 이슈 | 내용 |
|---|---|
| AnchorExtractor가 NAME/SEMANTIC 분기에 미적용 | `edge_types`가 비어 있으면서 문장 속에 기업명이 파묻힌 질의("삼성전자 관련 뉴스")는 여전히 원문 전체가 `EntityResolver.resolve()`로 전달돼 해소에 실패합니다 |
| `SearchMode.HYBRID`가 죽은 값 | 현재 분기 규칙으로는 절대 생성되지 않습니다. 실제로 쓸지(예: 관계 + 시간 정렬 결합), 아니면 enum에서 뺄지 미결 |
| 여러 `Resolution` 동시 조회 미구현 | GraphSearcher는 점수 최고 1건만 쓰고 나머지 후보는 무시합니다 |
| `tests/search/test_example_queries.py`의 기대값이 낡음 | query2("삼성전자 최근 투자 기업")의 `mode=HYBRID`, query5("최근 소송 관련 기업")의 `mode=SEMANTIC`+`sources=["neo4j","chroma"]` 기대값이 현재 동작과 다릅니다. 이 파일은 Orchestrator를 호출하지 않고 DTO를 손으로 채우는 스모크 테스트라 지금 깨지지는 않습니다 |
| Person/Event/Product 검색 미지원 | pg_trgm 인덱스·Neo4j 인덱스·벡터 컬렉션이 전부 Company에만 존재합니다(설계상 범위 밖) |

### 5-3. 실측 근거 없는 잠정치

| 상수 | 값 | 위치 |
|---|---|---|
| `_HARD_LIMIT` | 100 | GraphSearcher — `relations_of()` 조회 상한 |
| `_ANCHORLESS_MIN_FETCH` | 50 | GraphSearcher — anchor 없을 때 최소 원천 조회량 |
| `_DEFAULT_TOP_K` / `_HARD_LIMIT_TOP_K` | 10 / 50 | VectorSearcher |
| `_DEFAULT_TOP_K` / `_MAX_TOP_K` | 10 / 50 | `SearchRequest` |
| `_RRF_K` | 60 | ResultRanker — RRF 원 논문·TREC 관행값 |
| `_MAX_WORDS` / `_MIN_CANDIDATE_LEN` | 10 / 2 | AnchorExtractor — 어절 수·후보 길이 안전장치 |
| fuzzy threshold | 0.50 | EntityResolver, AnchorExtractor `_CONFIDENCE_THRESHOLD` |

fuzzy threshold만 실측 근거가 있습니다 — 정답 후보는 0.5 이상, 노이즈 어절("기업"→기업은행
0.33, "뉴스"→뉴스1 0.4)은 0.33~0.4에 몰려 간격이 뚜렷합니다. 다만 별도 튜닝 데이터셋으로
재검증하지는 않았고, 검색 전용 threshold를 EntityResolver와 분리할 필요성도 미결입니다.
Redis TTL은 CacheService와 함께 정합니다.

### 5-4. 검증 대기 (데이터 파이프라인 확인 필요)

| 이슈 | 내용 |
|---|---|
| **company 프로필의 임베딩 변별력이 낮다는 가설** | "삼성전자에 납품하는 기업"을 VectorSearcher에 넣으면 상위 10건이 전부 "삼성전자 OO법인/판매법인" 등 자기 계열사이고 실제 공급사는 0건입니다(GraphSearcher 결과와 `entity_id` 교집합 0건). "HBM을 만드는 기업"에서도 무관해 보이는 결과(HDC현대산업개발, NAVER J.Hub)가 0.75~0.80대로 다른 결과와 큰 차이 없이 섞여 나옵니다. **company 프로필 문서가 템플릿화돼 임베딩이 서로 비슷해진 것**이라는 가설이 있으나 검증하지 않았습니다 |
| VectorSearcher 컷오프 미적용 | 위 가설이 맞다면 점수 컷오프를 넣어도 걸러지지 않습니다. 컷오프 도입 여부는 위 확인 이후에 결정합니다 |
| 저신뢰 키워드 9종 정확도 미검증 | QueryRouter에 대표 키워드 1개씩만 등록돼 있고 실데이터 정확도를 검증하지 않았습니다 |
| `entity_id` 체계의 제3 컬렉션 대조 | Graph ↔ Vector(company) 간 식별자는 맞췄지만, `evidence` 등 다른 컬렉션과의 대조는 범위 밖입니다 |

---

## 6. 테스트 및 환경

- **테스트 원칙**: 실제 Docker Compose PostgreSQL/Neo4j/ChromaDB 대상(mock 없음). 순수
  로직(dedup, tier 판정, 방향 판별 등)만 in-memory 객체로 단위 테스트하고, 코드의 호출
  계약(예: "limit이 항상 전달되는가")을 확인해야 할 때만 예외적으로 `monkeypatch`를 씁니다.
- **테스트 실행 환경**: 이 프로젝트의 `.venv`는 Windows 네이티브 Python이라 WSL에서 Docker
  Postgres/Neo4j에 접속하면 TCP는 연결되나 프로토콜 핸드셰이크에서 리셋됩니다. WSL 전용
  venv `.venv-wsl`을 `uv`로 별도 생성해 씁니다(git에는 안 잡힘).
  ```bash
  uv venv .venv-wsl --python 3.10
  uv pip install --python .venv-wsl/bin/python -r requirements.txt pytest
  .venv-wsl/bin/python -m pytest tests/
  ```
- **Docker Desktop(WSL2) 포트포워딩 불안정**: Postgres·Neo4j 컨테이너가 오래 떠 있거나
  재시작 이력이 있으면 연결이 `connection reset`으로 실패하는 사례가 반복됐습니다.
  `docker restart biznode-postgres` / `docker restart biznode-neo4j`로 해결됩니다.
