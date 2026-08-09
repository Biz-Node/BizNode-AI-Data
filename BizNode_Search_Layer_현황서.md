# BizNode Search Layer — 현황서

> 이 문서는 **"실제로 어디까지 만들었는가"만** 다룹니다. 설계 근거·아키텍처는
> [`BizNode_Search_Layer_설계.md`](BizNode_Search_Layer_설계.md)를 보세요.
> 이전 개별 문서(`BizNode_AI에이전트_검색레이어_구현현황.md`,
> `BizNode_Search_Layer_구현_현황서.md`)를 통합·재구성했습니다. 작업이 끝날 때마다
> 이 문서를 갱신합니다.

마지막 갱신: 2026-08-09 · **전체 테스트 132개 전부 PASS**(실제 Docker PostgreSQL/Neo4j/
ChromaDB 대상, mock 없음 — 단 코드 호출 계약을 확인하는 일부 단위 테스트는 예외적으로 monkeypatch 사용)

## 1. 구현 현황 요약

| 컴포넌트 | 상태 | 코드 위치 | 테스트 |
|---|---|---|---|
| DTO(`SearchRequest`/`SearchQuery`/`SearchHit`/`SearchResult`) / `EntityType`/`SearchMode`/`Direction` enum | ✅ 완료 | `search/dto/*.py`, `search/model/enums.py` | 31 PASS |
| PostgresRepository | ✅ 완료 | `search/repository/postgres_repository.py` | 13 PASS |
| ChromaRepository | ✅ 완료 | `search/repository/chroma_repository.py` | 9 PASS |
| EntityResolver | ✅ 완료 | `search/service/entity_resolver.py` | 19 PASS |
| QueryRouter | ✅ 완료 | `search/service/query_router.py` | 21 PASS |
| GraphSearcher(엔티티 메타데이터·anchor 없는 검색 포함) | ✅ 완료 | `search/service/graph_searcher.py`, `app/services/graph_service.py`(확장) | 28 PASS |
| VectorSearcher | 🔴 미구현 | `search/service/vector_searcher.py`(없음) | - |
| ResultRanker | 🔴 미구현 | `search/service/result_ranker.py`(없음) | - |
| SearchOrchestrator | 🔴 미구현 | `search/service/orchestrator.py`(없음) | - |
| CacheService / RedisRepository | 🔴 미구현(설계상 후순위) | `search/service/cache_service.py`, `search/repository/redis_repository.py`(둘 다 없음) | - |
| SearchController(API) | 🔴 미구현 | `search/api/`(디렉토리 자체 없음), `app/api/`는 `__init__.py`뿐 | - |
| Agent Tool 연동 | 🔴 미구현 | - | - |

**요약**: 검색 흐름의 "앞단"(질의 → 기업 식별 → 관계 라우팅 → Neo4j 관계 검색)까지 완료됐고,
"뒷단"(의미 검색 → 결과 통합 → 전체 지휘 → API 노출)은 아직 없습니다.

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

---

## 3. 남은 작업

우선순위는 설계 문서(§4 컴포넌트 표)의 데이터 흐름 순서를 따릅니다.

1. **VectorSearcher** — `ChromaRepository`를 감싸 `company`/`evidence` 컬렉션 의미 검색 →
   `SearchHit` 변환. collection 선택 규칙(질의 → `company` vs `evidence`)은 아직 미확정입니다.
2. **ResultRanker** — GraphSearcher·VectorSearcher 결과를 점수 정규화 후 병합·정렬·중복 제거.
   초기 "단순안"(소스별 기존 점수 그대로 + `entity_id` 기준 최댓값 dedup)만 우선 구현 대상.
3. **SearchOrchestrator** — 캐시 확인 → EntityResolver/QueryRouter → GraphSearcher/
   VectorSearcher(병렬 가능) → ResultRanker → `SearchResult` 반환까지 전체 지휘. QueryRouter가
   반환하는 `edge_types`/`direction`을 GraphSearcher에 연결하는 것도 이 단계 책임입니다.
4. **CacheService + RedisRepository** — Orchestrator 전체를 감싸는 cross-cutting 캐시. Redis가
   프로젝트 어디에서도 실사용된 적이 없어 후순위입니다.
5. **SearchController(API)** — FastAPI `POST /api/search` 엔드포인트, 요청 검증, 에러 → HTTP
   status 매핑.
6. **Agent Tool 연동** — `search_company`/`search_relationship`/`search_semantic`/
   `search_evidence` thin wrapper.
7. **Docker / 배포** — ChromaDB 클라이언트-서버 버전 고정 체크리스트 반영.

---

## 4. 알려진 이슈 / 설계 결정 필요

| 이슈 | 내용 |
|---|---|
| anchor 없을 때 source/target 슬롯 간 전역 dedup 없음 | 같은 엔티티가 두 슬롯에 다 나올 수 있음(의도적) — 전역 중복 제거는 ResultRanker 책임으로 남김 |
| 여러 `Resolution` 동시 조회 미구현 | GraphSearcher는 점수 최고 1건만 사용, 나머지는 무시 |
| 저신뢰 키워드(9종) 정확도 미검증 | QueryRouter의 대표 키워드 1개씩만 등록, 실데이터 정확도 검증 안 함 |
| fuzzy threshold(0.50) 최종 미확정 | 검색 전용 threshold 분리 필요성은 인지했으나 실측 없어 기존 ER과 같은 값 유지 |
| `_HARD_LIMIT=100`, `_ANCHORLESS_MIN_FETCH=50`(GraphSearcher) | 실측 근거 없는 잠정치 |
| mode(NAME/RELATIONSHIP/SEMANTIC/HYBRID) 자동 판별 규칙 | 미확정 — Orchestrator 구현 시 결정 필요 |
| `top_k` 기본값/상한, Redis TTL, Ranking 가중합 도입 여부 | 전부 미확정(설계 문서 §6 참고) |

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
