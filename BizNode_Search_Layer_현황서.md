# BizNode 검색 · Retrieval — 현황서

> **「실제로 어디까지 됐고, 무엇이 아직 문제인가」** 를 다룹니다.
> 설계 근거·아키텍처는 [설계서](BizNode_Search_Layer_설계.md)를 보세요.
> **작업이 끝날 때마다 이 문서를 갱신합니다.**

마지막 갱신 **2026-08-20** · 테스트 **294개 전부 PASS**

---

## 1. 한눈에 보기

```text
데이터 수집 ─────── 완료
그래프 · 연동 API ── 21개 라우트 중 20개 실동작 (스텁 1개: /news)
검색 엔진 ───────── 완료
챗봇 재료 (/retrieve) ─ 완료          ← 2026-08-20 스텁 해제
LLM 답변 (/ask) ──── 없음        ★ 추론 담당 몫
```

**현재 상태 한 줄** — 질문을 받아 재료를 만들어 주는 데까지 **끝났습니다.**
백엔드는 `POST /retrieve` 를 부르면 되고, 추론 담당은 `app.services.retrieve_service`
를 직접 import 하면 됩니다. 남은 것은 **그 재료로 답을 쓰는 LLM 계층**입니다.

---

## 2. 구현 현황

| 컴포넌트 | 상태 | 코드 | 테스트 |
|---|---|---|---|
| DTO · enum | ✅ | `search/dto/*.py` · `search/model/enums.py` | 34 |
| PostgresRepository | ✅ | `search/repository/postgres_repository.py` | 12 |
| ChromaRepository | ✅ | `search/repository/chroma_repository.py` | 11 |
| EntityResolver | ✅ | `search/service/entity_resolver.py` | 19 |
| QueryRouter | ✅ | `search/service/query_router.py` | 21 |
| AnchorExtractor | ⚠️ | `search/service/anchor_extractor.py` | 18 |
| GraphSearcher | ✅ | `search/service/graph_searcher.py` | 28 |
| VectorSearcher | ✅ | `search/service/vector_searcher.py` | 23 |
| **ResultRanker** (워크스페이스 랭킹) | ✅ | `search/service/result_ranker.py` | 25 |
| SearchOrchestrator | ✅ | `search/service/orchestrator.py` | 44 |
| **Factory** | ✅ | `search/service/factory.py` | 3 |
| `graph_service` 확장(`edge_id`) | ✅ | `app/services/graph_service.py` | 17 |
| **RetrieveService** | ✅ | `app/services/retrieve_service.py` | 21 |
| **`POST /retrieve`** | ✅ | `app/api/main.py` | 7 |
| 대표 질의 스모크 | ✅ | `tests/search/test_example_queries.py` | 11 |
| CacheService / RedisRepository | 🔴 없음 | — | — |
| `POST /ask` (LLM 답변) | 🔴 없음 | — | — |
| Agent Tool 연동 | 🔴 없음 | — | — |

⚠️ AnchorExtractor 는 **동작하지만 알려진 결함**이 있습니다 — §4-1.

### 없어진 것

`/search/nl` (자연어 검색 HTTP 라우트)과 `search/api/` 를 **제거했습니다**(2026-08-20).
Search Layer 는 이제 `RetrieveService` 를 통해서만 노출됩니다. 이 라우트는 백엔드 연동
가이드의 라우트 표에 올라간 적이 없어 **외부 계약에는 영향이 없습니다.**

---

## 3. 백엔드가 알아야 할 것

```http
POST /retrieve
{ "question": "삼성전자에 납품하는 기업", "workspace_keys": ["00126380"] }
```

```text
응답  question · companies · events · relations · propagation · evidence
```

| | |
|---|---|
| **`X-Stub: true` 가 사라졌습니다** | 헤더로 분기 중이면 확인 필요. 계약(`RetrieveResponse`)은 안 바뀌었습니다 |
| **`question` 이 비면 422** | 전에는 검증이 없어 빈 문자열이 통과했습니다 |
| **`workspace_keys` 는 필터가 아닙니다** | 순서만 정합니다. 워크스페이스 밖 기업도, 사건·인물·기관·제품도 그대로 나옵니다 |
| **`evidence[].missing=true` 는 인용 금지** | 원문을 못 찾은 것. 응답에서 지우지는 않습니다 |

실측 응답 (`삼성전자에 납품하는 기업`, 워크스페이스 1곳):

```text
기업 5 · 사건 5 · 관계 20 · 파급 14 · 근거 45   (missing 0)
```

---

## 4. 알려진 결함

### 4-1. ★ AnchorExtractor 가 조사를 기업명으로 오인한다 — **가장 급함**

```text
"SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"  →  anchor = '일이'
                                                        ↑ corp_code 01355031 「일이」로 해소
"SK하이닉스 생산 차질"                              →  anchor = 'SK하이닉스'   ○
"SK하이닉스에 납품하는 기업"                         →  anchor = 'SK하이닉스'   ○
```

`RetrieveResponse` 스키마에 **대표 질문으로 적혀 있는 바로 그 문장**이 재료를 **0건**
돌려줍니다(`app/api/schemas.py`). 챗봇 품질에 직결됩니다.

원인은 어절 단위 fuzzy 매칭이 「일이」처럼 짧은 조사 잔여물을 실재하는 기업명과 매칭하는
것입니다. `_MIN_CANDIDATE_LEN = 2` 가 2글자를 허용하고 threshold 0.50 을 넘겨 버립니다.

### 4-2. `graph_service.Relation.score` 가 1.0 을 넘는다

실측 **0.80 ~ 1.15**. 뒷받침 보정이 최대 ×1.2 라 상한이 1.2 입니다. 그런데
`app/api/schemas.py` 의 `Relation.score` 는 `le=1` 이라 **그대로 실으면 ValidationError**
입니다.

지금은 `company_service` 가 자체 계산한 값을 쓰므로 문제가 없지만, `graph_service` 점수를
응답에 실으려면 변환이 필요합니다.

### 4-3. 그 외

```text
app/api/main.py 의 RiskEvent · TrendingItem import 가 미사용   (이전부터 그랬음)
저신뢰 키워드 9종의 실데이터 정확도 미검증                      (QueryRouter)
여러 Resolution 동시 조회 미구현                               (GraphSearcher 는 최고 1건만 씀)
```

---

## 5. 실측 기록

숫자로 남겨 둔 것들입니다. 다음 판단의 근거가 됩니다.

| 무엇 | 값 | 시점 |
|---|---|---|
| 삼성전자 관계 총량 | **998건** | 2026-08-20 |
| └ SK하이닉스 / 현대자동차 위치(점수순) | 271번째 / 418번째 | 2026-08-20 |
| 삼성전자 관계 상위 10건 중 비-Company 끝 | **5건** (Organization 3 · Product 2) | 2026-08-20 |
| 그래프 전체에서 비-Company 끝 비율 | 400건 중 **194건 (48.5%)** | 2026-08-20 |
| 복수 근거를 든 관계 | 삼성전자 200건 중 **54건** | 2026-08-20 |
| 엣지 : 근거 | 11,060 : 9,228 (한 근거가 15개 엣지에 붙은 사례 있음) | 2026-08-16 |
| `company` 컬렉션 중 프로필 보유 | 2,430건 중 **64건** | — |
| 리스크 파급(모트라스 파업) | 124곳 = 보도 10 + 계산 114 | — |
| 의미검색 정확도 | 「삼성전자에 납품하는 기업」 → 실제 공급사 **0건** | — |

---

## 6. 실측 근거 없는 잠정치

**전부 조정 여지로 남겨 둔 값입니다.** 트래픽·품질 실측 후 정합니다.

| 상수 | 값 | 위치 | 무엇을 정하나 |
|---|---|---|---|
| `_HARD_LIMIT` | 100 | GraphSearcher | 관계 조회 상한 |
| `_ANCHORLESS_MIN_FETCH` | 50 | GraphSearcher | anchor 없을 때 최소 조회량 |
| `_ANCHORLESS_SLOT_SIZE` | 5 | GraphSearcher | source/target 슬롯 크기 |
| `_WORKSPACE_FETCH_CEILING` | 2000 | GraphSearcher | 워크스페이스 랭킹용 후보 천장 |
| `_RRF_K` | 60 | ResultRanker | RRF 원 논문·TREC 관행값 |
| `_DEFAULT_TOP_K` / `_MAX_TOP_K` | 10 / 50 | `SearchRequest` · VectorSearcher | |
| `_MAX_COMPANIES` | 5 | RetrieveService | 재료를 만들 기업 수 |
| `_MAX_RELATIONS_PER_COMPANY` | 10 | RetrieveService | |
| `_MAX_RISK_EVENTS_FOR_PROPAGATION` | 3 | RetrieveService | 파급을 계산할 사건 수 |
| `_MIN_CANDIDATE_LEN` / `_MAX_WORDS` | 2 / 10 | AnchorExtractor | §4-1 과 관련 |
| fuzzy threshold | 0.50 | EntityResolver · AnchorExtractor | 유일하게 근거 있음 ↓ |

fuzzy threshold 만 실측 근거가 있습니다 — 정답 후보는 0.5 이상, 노이즈 어절(「기업」→
기업은행 0.33, 「뉴스」→뉴스1 0.4)은 0.33~0.4 에 몰려 간격이 뚜렷합니다. 다만 별도 튜닝
데이터셋으로 재검증하지는 않았습니다.

**RetrieveService 상한 셋은 잘라낼 때 로그를 남깁니다** — 조용히 자르면 「그게 전부」로
읽히기 때문입니다.

---

## 7. 남은 작업

| 순서 | 작업 | 내용 |
|---|---|---|
| 1 | **AnchorExtractor 결함** | §4-1. 챗봇 대표 질문이 0건이다 |
| 2 | **LLM 답변 계층** | `POST /ask` · 프롬프트 인젝션 방어 · **근거 id whitelist 검증** · 회귀 평가셋 |
| 3 | N+1 실측 | 기업 5곳 기준 `events_of`×5 + `relations_of`×5 + `event_impact`×3 이 실제로 얼마나 드는지. 그 뒤에 배치 최적화 여부 결정 |
| 4 | 단계별 timeout | 실측 뒤에 정한다. **근거 없는 숫자를 새로 만들지 않는다** |
| 5 | 워크스페이스 랭킹 품질 | 대표 질문으로 「원하는 것이 상위에 오는가」 측정 |
| 6 | CacheService + RedisRepository | Redis 컨테이너·의존성은 이미 준비됨. 트래픽이 없으면 효용을 못 잰다 |
| 7 | Agent Tool 연동 | |

### 2번(LLM 답변 계층) 인계 사항

**LLM 이 돌려줄 모양** — `pipeline/llm.ask_json()` 의 스키마로 강제합니다.

```json
{ "answer": "...", "evidence_ids": ["ev_...", "ev_..."] }
```

**서버가 반드시 검증합니다.**

```text
LLM 이 준 evidence_id
      ↓
RetrieveResponse.evidence 에 있던 것인가?
      ├─ 예   → 허용
      └─ 아니오 → 제거하거나 실패 처리     ★없는 id 는 지어낸 것이다
```

**지켜야 할 규약 여섯.**

```text
whitelist 검증   위 흐름. 통과한 것만 사용자에게 보이는 source 가 된다
missing=true     인용 금지 (단, 응답 데이터에서 지우지는 않는다)
stated           보도(true)와 계산(false)을 갈라 말한다
freshness        stale 을 현재형으로 말하지 않는다 → 「2024-06 에 그렇게 보도됨」
evidence         항상 「인용할 데이터」로 취급. 시스템 지시문과 섞지 않는다
LLM 호출          pipeline/llm.ask_json() 재사용 — 창구를 새로 만들지 않는다
                 ★fallback 은 「안전한 쪽」으로. 실패를 통과와 구별한다
```

source 객체의 모양과 클릭 목적지는 [설계서 §11](BizNode_Search_Layer_설계.md) 에 있습니다.

---

## 8. 확인하지 못한 것

이 레포 안에서는 답을 알 수 없어 **팀에 물어봐야 하는** 항목입니다.

| 무엇 | 상태 |
|---|---|
| `/api/v1/workspaces/{workspaceId}/chat/messages` | 이 레포에 **존재하지 않습니다.** 백엔드(Spring) 소유로 보이나 확인 불가 — `/retrieve` 와의 매핑을 맞춰야 합니다 |
| 백엔드가 OpenAPI 로 클라이언트를 코드젠하는가 | 확인 불가. 한다면 `/docs` 변경 시마다 재생성이 필요합니다 |
| 드라이버 레벨 timeout | PostgreSQL `statement_timeout` · Neo4j `tx.timeout` 이 **어느 저장소에도 설정돼 있지 않습니다.** `asyncio.wait_for` 는 threadpool 스레드를 죽이지 못하므로, 진짜 취소가 필요하면 이쪽을 켜야 합니다 |

---

## 9. 최근 변경 이력

| 날짜 | 변경 | 왜 |
|---|---|---|
| 2026-08-20 | **워크스페이스를 hard filter → 랭킹 문맥으로** | 바깥 기업·사건·인물·기관·제품이 후보에서 통째로 사라졌다 |
| 2026-08-20 | **`/retrieve` 실물화** · `X-Stub` 제거 | |
| 2026-08-20 | **`/search/nl` 제거** · `search/api/` 삭제 | Search Layer 는 RetrieveService 를 통해서만 노출 |
| 2026-08-20 | `RetrieveService` · `factory.build_orchestrator()` 신설 | 조립이 세 곳에 중복돼 있었다 |
| 2026-08-20 | `SearchRelation` 타입화 + `edge_id` 보존 | dict 면 `edge_id` 가 조용히 빌 수 있다 |
| 2026-08-20 | `evidence_ids`(복수) 처리 | 단수만 옮겨 근거가 빠지고 있었다 (200건 중 54건) |
| 2026-08-20 | `AskRequest.question` 공백 검증 | 빈 질문이 500 으로 나갔다 |
| 2026-08-19 | `score` → `rank`·`rrf_score`·`source_score` 분리 | RRF 1위 0.0164 를 「신뢰도 1.6%」로 읽는 문제 |
| 2026-08-19 | `entity_types`·`filters` 계약에서 제거 · `SearchMode.HYBRID` 제거 | 읽는 코드가 0곳인 죽은 필드·값 |
| 2026-08-19 | `edge_types` 요청값이 QueryRouter 추론보다 우선 | 챗봇 탐색 프로파일이 엣지를 직접 지정해야 한다 |
| 2026-08-19 | AnchorExtractor 를 모든 분기에 적용 | 「삼성전자 관련 뉴스」가 해소에 실패했다 |
| 2026-08-19 | 의미검색 모집단을 `has_profile` 로 한정 | 이름뿐인 문서가 변별력을 떨어뜨렸다 |

---

## 10. 테스트 · 개발 환경

### 원칙

실제 Docker PostgreSQL/Neo4j/ChromaDB 대상입니다(**mock 없음**). 순수 로직만 in-memory
객체로 단위 테스트하고, **호출 계약**(「limit 이 항상 전달되는가」)을 볼 때만 예외적으로
`monkeypatch` 를 씁니다.

```text
294개
├─ tests/search/     Search Layer         249
└─ tests/services/    graph_service ·      45
                      RetrieveService · API
```

★ `tests/services/test_graph_service.py` 는 **프로덕션 Cypher 의 안전망**입니다.
`tests/search/service/test_graph_searcher.py` 는 `relations_of` 를 monkeypatch 로 통째
대체하므로 Cypher 변경을 감지하지 못합니다. `graph_service` 를 고칠 때는 이 파일을 먼저
보세요.

### ★ 실행 환경 — 매번 걸리는 것

이 프로젝트의 `.venv` 는 **Windows 네이티브 Python** 이라 WSL 에서 Docker DB 에 붙으면
TCP 는 연결되나 프로토콜 핸드셰이크에서 리셋됩니다. **WSL 전용 venv 를 씁니다.**

```bash
uv venv .venv-wsl --python 3.10
uv pip install --python .venv-wsl/bin/python -r requirements.txt pytest
.venv-wsl/bin/python -m pytest tests/ -q
```

Docker Desktop(WSL2) 포트포워딩이 불안정하면:

```bash
docker restart biznode-postgres
docker restart biznode-neo4j
```

### 손으로 한 번 돌려보기

```bash
.venv-wsl/bin/python run_test.py     # SearchOrchestrator 를 직접 호출해 결과 출력
uvicorn app.api.main:app --reload    # /docs 에서 POST /retrieve Try it out
```
