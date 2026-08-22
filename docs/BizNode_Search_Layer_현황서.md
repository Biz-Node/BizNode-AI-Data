# BizNode 검색 · Retrieval — 현황서

> **「실제로 어디까지 됐고, 무엇이 아직 문제인가」** 를 다룹니다.
> 설계 근거·아키텍처는 [설계서](BizNode_Search_Layer_설계.md)를 보세요.
> **작업이 끝날 때마다 이 문서를 갱신합니다.**

마지막 갱신 **2026-08-22** · 테스트 **341개** (339 PASS · 2 xfail = 알려진 결함 §4-6·§4-5)

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
| PostgresRepository | ✅ | `search/repository/postgres_repository.py` | 16 |
| ChromaRepository | ✅ | `search/repository/chroma_repository.py` | 11 |
| EntityResolver | ✅ | `search/service/entity_resolver.py` | 19 |
| QueryRouter | ✅ | `search/service/query_router.py` | 21 |
| AnchorExtractor (Kiwi 조사 분리) | ✅ | `search/service/anchor_extractor.py` | 26 |
| GraphSearcher | ✅ | `search/service/graph_searcher.py` | 33 |
| VectorSearcher | ✅ | `search/service/vector_searcher.py` | 23 |
| **ResultRanker** (워크스페이스 랭킹) | ✅ | `search/service/result_ranker.py` | 25 |
| SearchOrchestrator | ✅ | `search/service/orchestrator.py` | 44 |
| **Factory** | ✅ | `search/service/factory.py` | 3 |
| `graph_service` 확장(`edge_id`) | ✅ | `app/services/graph_service.py` | 17 |
| **RetrieveService** | ✅ | `app/services/retrieve_service.py` | 21 |
| **`POST /retrieve`** | ✅ | `app/api/main.py` | 7 |
| 대표 질의 스모크 | ✅ | `tests/search/test_example_queries.py` | 11 |
| **회귀 평가셋** (20 케이스) | ✅ | `tests/search/eval/` · [평가셋 문서](BizNode_Search_Layer_평가셋.md) | 30 |
| CacheService / RedisRepository | 🔴 없음 | — | — |
| `POST /ask` (LLM 답변) | 🔴 없음 | — | — |
| Agent Tool 연동 | 🔴 없음 | — | — |

§4-1 의 AnchorExtractor 결함은 **2026-08-22 해소했습니다.**

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

### 4-1. ~~AnchorExtractor 가 조사를 기업명으로 오인한다~~ — **해소 (2026-08-22)**

```text
"SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"  →  'SK하이닉스'   ○
"농심에 생산 차질을 일으킬 만한 일이 있었나?"        →  '농심'        ○
"네이버에 생산 차질을 일으킬 만한 일이 있었나?"      →  '네이버'      ○
```

**원인 진단이 틀렸었습니다.** 전에는 `_MIN_CANDIDATE_LEN = 2` 가 2글자를 허용하는
것이 원인이라고 적어 뒀는데, 「농심」(00108241·004370)이 **2글자 실존 상장사**라 상수를
올리면 그쪽이 죽습니다. 진짜 원인은 둘이었습니다.

```text
(a) 조사 잔여물 「일이」가 후보로 살아남는다
    문자열 휴리스틱이 "일이" 끝의 "이"를 떼면 1글자만 남아 절단을 포기하고
    원본을 후보로 남겼다 → 실존 법인 「일이」(01355031)와 1.000 정확 일치

(b) `ORDER BY score DESC LIMIT 1` 이 동점에서 무엇을 고를지 정의돼 있지 않다
    「일이」1.000 과 「SK하이닉스」1.000 의 승부가 물리적 행 순서에 좌우됐다
```

**고친 방법** — Kiwi 형태소 분석기를 **필터가 아니라 「문법적으로 정확한 조사
분리기」**로 넣었습니다. 태그로 거르지 않는 것이 핵심입니다(§5 참고: 상장사의
62.0% 가 `NNG` 라 고유명사 태그로 거르면 그만큼이 죽습니다).

```text
문장 전체를 Kiwi 에 1회 통과 (어절 단위로 돌리면 안 된다 — §5)
     ↓
어절 끝에서 조사·어미로 태깅된 만큼만 **길이로** 절단
     ↓  ← Kiwi 가 앞 음절을 먹는 오분석(10.4%)이 후보에 닿지 않는다
  명사부 ≥2글자 → 명사부 + 원본 어절 둘 다 후보
  명사부 ≤1글자 → 그 어절을 통째로 버린다        ★「일이」가 여기서 죽는다
  꼬리 없음     → 원본 어절 그대로               ★Kiwi 오분석 안전망
     ↓
1차 corp_code_master (기존 DART 기준 그대로)
2차 1차가 비었을 때만, Kiwi 가 NNP 로 본 후보에 한해 company_aliases
     ↓  ← similarity('NAVER','네이버')=0.000 이라 pg_trgm 으로는 영원히 못 잇는다
score 내림차순 → 후보 길이 내림차순
```

`best_candidate_match()`(최댓값 1건)를 `match_candidates()`(통과 후보 전부)로 늘려
**선택 규칙을 저장소가 아니라 호출부가 정하게** 했습니다.

### 4-2. `graph_service.Relation.score` 가 1.0 을 넘는다

실측 **0.80 ~ 1.15**. 뒷받침 보정이 최대 ×1.2 라 상한이 1.2 입니다. 그런데
`app/api/schemas.py` 의 `Relation.score` 는 `le=1` 이라 **그대로 실으면 ValidationError**
입니다.

지금은 `company_service` 가 자체 계산한 값을 쓰므로 문제가 없지만, `graph_service` 점수를
응답에 실으려면 변환이 필요합니다.

### 4-3. 그 외

```text
app/api/main.py 의 RiskEvent · TrendingItem import 가 미사용   (이전부터 그랬음)
GET /health 가 여전히 `"stub": true` 를 하드코딩 (main.py:513)   (이전부터 그랬음)
저신뢰 키워드 9종의 실데이터 정확도 미검증                      (QueryRouter)
여러 Resolution 동시 조회 미구현                               (GraphSearcher 는 최고 1건만 씀)
```

### 4-4. anchor 에 조사가 붙어 나오는 경우가 남았다 (2026-08-22)

상장사 400곳 표본에서 **11곳(2.8%)** 이 정확히 안 나옵니다. 전부 §4-1 이전에는
「일이」로 가던 것이라 **후퇴는 아닙니다.**

```text
9건  Kiwi 가 「에」를 조사로 못 봄 → anchor 에 조사가 붙는다
     삼성FN리츠에 · 원익큐브에 · 동부일렉트로닉스에 · 플리토에 · 사람인에
     남광토건에 · SK리츠에 · 동원데어리푸드에 · 이푸른에
     ※ 이 후보들도 옳은 법인에 매칭은 된다(match_candidates 가 corp_name 을
       맞게 돌려준다). anchor 문자열만 덜 깨끗하다.

2건  과다 절단 — 사명 끝 음절이 조사로 읽힌다
     「우리로에」→「우리」(우리/NP 로/JKB 에/JKB) · 「캔버스엔에」→「캔버스」
```

**고칠 방법이 이미 손에 있습니다** — `match_candidates()` 가 `(후보, 매칭된 법인명,
점수)` 를 주므로 후보 대신 **매칭된 법인명**을 anchor 로 돌리면 9건이 사라집니다.
다만 `extract()` 의 계약이 「질의의 부분 문자열」에서 「법인명」으로 바뀌므로
호출부(orchestrator·EntityResolver) 영향을 확인하고 별도로 정합니다.

### 4-5. 동음이의 사명은 원리적으로 못 가른다 (이전부터 그랬음)

```text
"이 사건의 대상 기업은?"   →  '대상'   (00121941 · 001680 대상그룹)
"동남 지역 기업 현황"      →  '동남'   (00252764 외 7곳)
```

`corp_code_master` 에 실제로 있는 이름이라 DART 1차 경로에서 1.000 으로 잡힙니다.
Kiwi 도입 전 `best_candidate_match()` 도 같은 답을 냈습니다 — **새로 생긴 문제가
아닙니다.** 질의 의도를 봐야 갈리는 문제라 형태소 분석으로는 못 고칩니다.

### 4-6. 별칭으로 잡은 anchor 를 EntityResolver 가 다시 놓친다 (2026-08-22 발견)

```text
"네이버"  →  AnchorExtractor.extract()  = '네이버'      ○  (company_aliases 2차 창구)
          →  EntityResolver.resolve()  = None         ✗
          →  mode = SEMANTIC (기대: NAME)
```

`AnchorExtractor` 는 `alias_exact_match()` 로 「네이버」를 찾아냅니다. 그런데 돌려주는
것이 **별칭 문자열**이라, `SearchOrchestrator` 가 그 문자열을 그대로
`EntityResolver.resolve()` 에 다시 넘깁니다. `resolve()` 는 `corp_code_master` 만 보고
`similarity('NAVER','네이버')=0.000` 이라 해소에 실패합니다 — 두 컴포넌트가 **같은
창구를 쓰지 않습니다.**

의미검색 1위로 `NAVER`(00266961) 가 나오기는 하나, `mode` 가 `NAME` 이 아니라
`SEMANTIC` 이고 `source` 도 `postgres` 가 아니라 `chroma` 입니다. 「네이버」를 물으면
이름 해소로 답해야 합니다.

**고치려면** `alias_exact_match()` 가 별칭이 아니라 corp_code(또는 `Resolution`)를
돌려주게 하거나, `EntityResolver` 에 같은 2차 창구를 붙여야 합니다. `extract()` 의
계약(§4-4 와 같은 지점)이 걸려 **이번 작업에서는 고치지 않고 평가셋에 표시만 했습니다**
— `tests/search/eval` 의 `known-alias-naver` 케이스가 `xfail(strict=True)` 로
지키고 있어, 고쳐지면 XPASS 로 뒤집혀 알려 줍니다.

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
| **anchor 정확도** (상장사 400곳 × 「X에 …일이 있었나?」) | **22.0% → 97.2%** | 2026-08-22 |
| └ 그중 「일이」 오인 | **312건 → 0건** | 2026-08-22 |
| └ 질의당 지연 | 16.4ms → **14.7ms** (후보가 줄어 오히려 빨라짐) | 2026-08-22 |
| Kiwi 가 `NNP`/`SL`/`SN` 을 하나도 안 주는 상장사 | 3,979곳 중 **2,465곳 (62.0%)** | 2026-08-22 |
| Kiwi 명사 토큰으로 사명 원형 복원 실패 | 3,979곳 중 **413곳 (10.4%)** | 2026-08-22 |
| 어절에 명사류 토큰이 0개인 상장사 | 조사 문맥 **4곳(0.10%)** · 단독 **17곳(0.43%)** | 2026-08-22 |
| `company_aliases` 3글자 이하 별칭 중 Kiwi 가 일반명사로 읽는 것 | 523개 중 **215개** | 2026-08-22 |
| Kiwi 로드 / tokenize | 1.3초(프로세스당 1회) / **0.14ms** (질의당) | 2026-08-22 |
| `similarity('NAVER','네이버')` | **0.000** (트라이그램은 한글↔영문을 못 잇는다) | 2026-08-22 |
| 삼성전자 `SUPPLIES_TO` 방향 비 | outgoing **51** : incoming **151** (전체 관계 1,169) | 2026-08-22 |
| └ 「삼성전자가 납품하는 기업」 결과 수 | **2건 → 10건** (top_k 를 채움) | 2026-08-22 |
| └ 방향 지정 질의 6종 전부 | 2~8건 → **10건** · 지연 무변화(60~85ms) | 2026-08-22 |

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
| `_MIN_CANDIDATE_LEN` / `_MAX_WORDS` | 2 / 10 | AnchorExtractor | **2 는 올리면 안 됩니다** — 「농심」이 2글자 실존 상장사 |
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
| 1 | **LLM 답변 계층** | `POST /ask` · 프롬프트 인젝션 방어 · **근거 id whitelist 검증** · 답변 품질 평가셋 |
| 2 | N+1 실측 | 기업 5곳 기준 `events_of`×5 + `relations_of`×5 + `event_impact`×3 이 실제로 얼마나 드는지. 그 뒤에 배치 최적화 여부 결정 |
| 3 | 단계별 timeout | 실측 뒤에 정한다. **근거 없는 숫자를 새로 만들지 않는다** |
| 4 | 워크스페이스 랭킹 품질 | 대표 질문으로 「원하는 것이 상위에 오는가」 측정 |
| 5 | CacheService + RedisRepository | Redis 컨테이너·의존성은 이미 준비됨. 트래픽이 없으면 효용을 못 잰다 |
| 6 | Agent Tool 연동 | |
| 7 | anchor 에 조사가 붙는 11곳 | §4-4. `extract()` 계약 변경이 걸려 별도 판단 |
| 8 | **별칭 anchor 를 EntityResolver 가 못 받는다** | §4-6. 7번과 같은 지점(`extract()` 계약)이라 함께 정합니다 |

### 1번(LLM 답변 계층) 인계 사항

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
| 2026-08-22 | **Search Layer 회귀 평가셋 20 케이스 신설** (`tests/search/eval/`) | 검색 분기(mode·direction·anchor·router·graph·엔티티 타입·ranking·negative)를 한 번에 훑는 것이 없었다. 결과는 [평가셋 문서](BizNode_Search_Layer_평가셋.md) — 18 PASS · 2 FAIL(알려진 결함 §4-5·§4-6) |
| 2026-08-22 | **방향 필터가 걸릴 때 미리 자르지 않도록 `_fetch_limit` 수정** | `relations_of(limit=)` 는 양방향을 섞어 점수순으로 자르는 파이썬 슬라이스인데 방향 필터가 그 뒤에 걸려, 얻는 양이 `top_k × 그 방향의 비율` 로 깎였다. 「삼성전자가 납품하는 기업」이 51건 중 2건만 났다 |
| 2026-08-22 | **Dockerfile 수정** — `search/` COPY 추가 · `COPY data/` 삭제 | 운영 이미지가 **빌드조차 실패**했다(`"/data": not found` — data/ 는 .gitignore 에 있다). 고쳐도 `search/` 가 없어 `POST /retrieve` 가 `ModuleNotFoundError` 로 죽었다. 둘 다 컨테이너 기동 + `/retrieve` 200 으로 검증 |
| 2026-08-22 | `best_candidate_match()` **삭제** | `match_candidates()` 로 대체돼 프로덕션 참조가 0곳이 됐다. 옛 테스트 3개의 의도는 새 API 테스트로 옮겼다 |
| 2026-08-22 | **AnchorExtractor 에 Kiwi 형태소 분석기 도입** | 문자열 휴리스틱이 조사 잔여물 「일이」를 실존 법인으로 오인했다 (§4-1). anchor 정확도 22.0%→97.2% |
| 2026-08-22 | `best_candidate_match()` → **`match_candidates()`(다건)** | 1.000 동점의 승부가 물리적 행 순서에 좌우됐다 |
| 2026-08-22 | **`alias_exact_match()` 2차 창구 신설** | `similarity('NAVER','네이버')=0.000` — pg_trgm 으로는 원리적으로 못 잇는다 |
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
341개
├─ tests/search/     Search Layer         296
│   └─ eval/          회귀 평가셋            30   ← 20 케이스 + 심층 판정 10
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

★`kiwipiepy` 는 모델(`kiwipiepy_model`)이 함께 딸려 와 **설치 후 105MB** 를 씁니다.
`manylinux2014_aarch64` 휠이 있어 ARM(t4g) 서버에서도 컴파일 없이 설치됩니다.

### 운영 이미지 검증 (2026-08-22)

```bash
docker build -t biznode-api:test .
docker run -d --network biznode-ai-data_default --env-file .env \
  -e NEO4J_URI=bolt://neo4j:7687 -e POSTGRES_HOST=postgres \
  -e CHROMA_HOST=chroma -e CHROMA_PORT=8000 -p 18100:8100 biznode-api:test
curl -X POST localhost:18100/retrieve -H 'Content-Type: application/json' \
  -d '{"question":"SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"}'
```

실측 — `HTTP 200 · 2.0초 · 기업 1(SK하이닉스) · 사건 69 · 관계 10 · 파급 249 · 근거 152`.
**§4-1 수정이 운영 이미지에서도 동작합니다** (anchor 가 `SK하이닉스` 로 잡혔습니다).

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

### 회귀 평가셋

검색 분기를 한 번에 훑습니다. 케이스는 `tests/search/eval/cases.py`, 판정은
`tests/search/eval/test_search_eval.py`, 결과 문서는
[평가셋](BizNode_Search_Layer_평가셋.md) 입니다.

```bash
.venv-wsl/bin/python -m pytest tests/search/eval -q          # 평가셋만 (약 16초)
.venv-wsl/bin/python -m pytest tests/search/eval -q -rA      # 케이스별 판정까지
.venv-wsl/bin/python -m tests.search.eval.report \
    -o BizNode_Search_Layer_평가셋.md                        # 결과 문서 다시 만들기
```

**기업명을 못 박는 케이스와 구조 조건만 보는 케이스를 가릅니다**(`EvalCase.kind`).
관계 점수·임베딩 유사도는 데이터가 늘면 순위가 바뀌므로, 이름 해소가 답 그 자체인
케이스와 랭킹 정책을 증명해야 하는 케이스에서만 기업을 고정합니다.
