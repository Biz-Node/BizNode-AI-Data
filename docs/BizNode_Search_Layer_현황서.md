# BizNode 검색 · 챗봇 — 현황서

> **「지금 어디까지 됐고, 무엇이 고장났고, 다음에 무엇을 하는가」** 를 다룹니다.
> 설계 근거·아키텍처는 **[설계서](BizNode_Search_Layer_설계.md)** 를 보세요. 이 둘이 짝입니다.
> **작업이 끝날 때마다 이 문서를 갱신합니다.**

마지막 갱신 **2026-08-23** · 기준 커밋 `8f88d58`(브랜치 `yun`)
· 테스트 **487개** (485 passed · 2 xfailed = 알려진 결함 [§4-5](#4-5-동음이의-사명은-원리적으로-못-가른다)·[§4-6](#4-6-별칭으로-잡은-anchor-를-entityresolver-가-다시-놓친다-todo))

> 📌 **파일 이름이 `Search_Layer` 지만 내용은 검색 + 챗봇 둘 다입니다.** 예전에는
> 「기술 부채 및 설계 검토 사항」이라는 세 번째 문서가 따로 있었는데, **2026-08-23 에
> 이 문서로 합쳤습니다** — 「무엇이 고장났나」와 「무엇을 언제 할까」를 두 파일로 갈라
> 두니 같은 항목이 양쪽에 나뉘어 어느 쪽이 최신인지 알 수 없었습니다.

### 상태 태그 범례

| 태그 | 뜻 | 다음 행동 |
|---|---|---|
| `[DECIDE]` | **사람이 방향을 정해야** 진행된다 — 코드로 풀 문제가 아니다 | 담당자를 정해 결정하고, 결정 즉시 `[TODO]`로 바뀐다 |
| `[TODO]` | 결정은 됐고 구현·수정만 남았다 | 작업으로 바로 들어갈 수 있다 |
| `[MEASURE]` | **실측이 있어야** 다음 결정을 할 수 있다 | 「실측 근거 없는 숫자를 새로 만들지 않는다」 원칙 |
| `[VERIFY]` | 됐다고 보이지만 확인이 안 됐다 | 운영 이미지 기동·실제 응답처럼 한 번 검증하면 끝 |
| `[DEFER]` | 지금 할 필요가 없다(이유 있음) | 트리거 조건(트래픽·서버 이전 등)이 오면 재검토 |
| `[DONE]` | 완료. 기록용으로만 남긴다 | 없음 |

---

## 0. 30초 요약 — 지금 어디까지 됐나

```text
데이터 수집 ─────────── 완료
그래프 · 연동 API ────── 21개 라우트 중 20개 실동작 (스텁 1개: /news)
검색 엔진 ───────────── 완료
챗봇 재료 (/retrieve) ── 완료          2026-08-20 스텁 해제
LLM 답변 (/ask) ─────── 동작함          2026-08-22 신설
   └ 구조적 안전성 ───── 완료          환각 근거 차단 · 인젝션 방어 · 실패 구별
   └ 답변 내용 품질 ──── 🟡 미검증      사람이 채점한 적이 없다
아키텍처 재정의 ──────── 완료(문서)     2026-08-23. 구현은 §6 부터
```

**한 줄** — **질문을 받아 답까지 돌려주는 것은 됩니다.** 백엔드는 `POST /ask` 를 부르면
되고, 추론 담당은 `app.services.answer_service` 를 직접 import 하면 됩니다.

**다만 「답이 나온다」와 「답이 좋다」는 다릅니다.** 지금 검증된 것은 **구조적 안전성**
(지어낸 근거를 못 싣는다 · 인젝션이 안 통한다 · 실패를 성공으로 안 판다)뿐이고,
**내용 품질은 아무도 채점한 적이 없습니다**([§5](#5-알려진-결함--챗봇)).

**처음 오신 분은** [설계서 §0 오리엔테이션](BizNode_Search_Layer_설계.md#0-처음-오신-분께--10분-오리엔테이션)을
먼저 읽으세요. 용어와 큰 그림이 거기 있습니다.

---

## 1. 구현 현황

### 1-1. 검색 (Search Layer · `search/`)

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
| ResultRanker (워크스페이스 랭킹) | ✅ | `search/service/result_ranker.py` | 25 |
| SearchOrchestrator | ✅ | `search/service/orchestrator.py` | 44 |
| Factory | ✅ | `search/service/factory.py` | 3 |
| 대표 질의 스모크 | ✅ | `tests/search/test_example_queries.py` | 11 |
| 회귀 평가셋 (20 케이스) | ✅ | `tests/search/eval/` · [평가셋 문서](BizNode_Search_Layer_평가셋.md) | 30 |
| CacheService / RedisRepository | 🔴 없음 | — | — |

### 1-2. 재료 (Retrieve Layer)

| 컴포넌트 | 상태 | 코드 | 테스트 |
|---|---|---|---|
| `graph_service` 확장(`edge_id`) | ✅ | `app/services/graph_service.py` | 17 |
| RetrieveService | ✅ | `app/services/retrieve_service.py` | 21 |
| `POST /retrieve` | ✅ | `app/api/main.py` | 7 |
| 기업별 evidence scope (`HAS_EVENT` 엣지) | ✅ | `company_service._own_evidence_ids()` | 포함 |
| 사건 의도 선택 | ✅ | `app/services/evidence_selector.py` | 15 |

### 1-3. 챗봇 (Answer Layer)

| 컴포넌트 | 상태 | 코드 | 테스트 |
|---|---|---|---|
| AnswerService · `POST /ask` | ✅ | `app/services/answer_service.py` | 24 |
| `match_type`(EXACT/SEMANTIC) 노출 + 헤징 | ✅ | `retrieve_service` · `answer_service` | 포함 |
| claim 관측 (`claims[]` · 판정 없음) | ✅ | `app/services/claim_check.py` | 12 |
| 낱말 겹침 (문장용 토크나이저·날짜 정규화) | ✅ | `pipeline/token_overlap.py` | 20 |
| claim 분포 수집 배치 | ✅ | `batch/audit/claim_grounding.py` | — |
| **claim type 분류 · 타입별 대조** | 🔴 없음 | — | — |
| **Query Understanding 계층** | 🔴 없음 | 조각이 3곳에 흩어져 있음 | — |
| **`relation_selector`** (관계 의도 선택) | 🔴 없음 | — | — |
| Agent Tool 연동 | 🔴 없음 | — | — |

### 1-4. 없어진 것

`/search/nl` (자연어 검색 HTTP 라우트)과 `search/api/` 를 **제거했습니다**(2026-08-20).
Search Layer 는 이제 `RetrieveService` 를 통해서만 노출됩니다. 이 라우트는 백엔드 연동
가이드의 라우트 표에 올라간 적이 없어 **외부 계약에는 영향이 없습니다.**

---

## 2. 아키텍처 재정의 (2026-08-23) — 무엇이 바뀌었나

**코드는 안 바뀌었고 문서만 바뀌었습니다.** `/ask` 를 만든 뒤 문제를 하나씩 막아 왔는데
(기업별 근거 오염 → 사건 상한 → 인과 금지 → claim 관측 → 한국어 토큰화), 문제가 계속
나오는 이유가 **개별 결함이 아니라 「`/ask` 가 무엇인지」가 못박히지 않은 데** 있었습니다.

그래서 코드와 데이터 구조를 독립적으로 다시 읽고 최종 형태를 정의했습니다 —
결과는 [설계서 3부](BizNode_Search_Layer_설계.md#3부--챗봇-답변을-쓴다) 입니다.

| 무엇이 확정됐나 | 어디에 |
|---|---|
| `/ask` 는 Graph QA → Evidence-grounded RAG → Insight **3단이고 순서가 고정** | [설계서 §8](BizNode_Search_Layer_설계.md#8-ask-는-무엇인가--셋의-결합이고-순서가-정해져-있다) |
| 데이터 계층 책임 — Company/Relationship/Event/metadata/Evidence 가 각각 무엇을 대답하나 | [설계서 §9](BizNode_Search_Layer_설계.md#9-데이터-계층의-책임--무엇이-무엇을-대답하나) |
| 10단계 flow — 단계별 입력·출력·책임·**금지사항** | [설계서 §10](BizNode_Search_Layer_설계.md#10-ask-전체-flow--10단계) |
| Relationship 과 Event 는 **다른 파이프라인**이어야 한다 | [설계서 §11](BizNode_Search_Layer_설계.md#11-relationship-과-event-는-다른-문제다) |
| 사실 / 관측된 인과 / 계산된 전망 / Insight **4등급** | [설계서 §12](BizNode_Search_Layer_설계.md#12-사실과-추론의-경계--4등급) |
| claim **5종**과 타입별 검증 원천 | [설계서 §13](BizNode_Search_Layer_설계.md#13-claim-5종과-검증) |
| 앵커 없는 질의는 **Query Understanding 에서** 푼다 | [설계서 §14](BizNode_Search_Layer_설계.md#14-앵커-없는-질의) |

★**이 재정의가 새로 발견한 결함 5건**은 [§5](#5-알려진-결함--챗봇)에 있습니다.

---

## 3. 백엔드/프론트가 알아야 할 것

### 3-1. 라우트 둘

```http
POST /retrieve                       # 재료만 (문장 생성 없음)
{ "question": "삼성전자에 납품하는 기업", "workspace_keys": ["00126380"] }
→ question · match_type · companies · events · relations · propagation · evidence
```

```http
POST /ask                            # 답변 문장 + 검증된 근거
{ "question": "삼성전자에 납품하는 기업", "workspace_keys": ["00126380"] }
→ answer · sources[] · failed
```

요청 바디는 **둘이 같습니다**(`AskRequest`). 새 이름을 만들지 않았습니다.

### 3-2. 반드시 알아야 할 계약 여섯

| | |
|---|---|
| **`X-Stub: true` 가 사라졌습니다** | 헤더로 분기 중이면 확인 필요. 계약(`RetrieveResponse`)은 안 바뀌었습니다 |
| **`question` 이 비면 422** | 전에는 검증이 없어 빈 문자열이 통과했습니다 |
| **`workspace_keys` 는 필터가 아닙니다** | **순서만** 정합니다. 워크스페이스 밖 기업도, 사건·인물·기관·제품도 그대로 나옵니다 |
| **`evidence[].missing=true` 는 인용 금지** | 원문을 못 찾은 것. **응답에서 지우지는 않습니다** — 지우면 「근거가 없는 관계」로 읽힙니다 |
| **`failed=true` 면 `answer` 는 고정 문구** | LLM 호출이 실패한 것. **HTTP 는 200 입니다.** `sources` 는 그대로 나가므로 화면이 「답은 못 썼지만 근거는 있다」를 보여줄 수 있습니다 |
| **`sources[]` 는 화이트리스트를 통과한 것만** | LLM 이 지어낸 `evidence_id` 는 서버가 버립니다. 여기 있는 id 는 전부 실재합니다 |

### 3-3. 실측 응답

```text
POST /retrieve  「삼성전자에 납품하는 기업」 · 워크스페이스 1곳
  → 기업 5 · 사건 5 · 관계 20 · 파급 14 · 근거 45   (missing 0)

POST /retrieve  「SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?」 (운영 이미지)
  → HTTP 200 · 2.0초 · 기업 1 · 사건 69 · 관계 10 · 파급 249 · 근거 152
```

### 3-4. 아직 못 맞춘 것

`/api/v1/workspaces/{workspaceId}/chat/messages` 라우트와 `/ask` 의 매핑이 안 정해졌습니다.
[§7](#7-결정해야-할-것--decide) 참고 — 코드 작업이 아니라 **팀에 물어볼 것**입니다.

---

## 4. 알려진 결함 — 검색

### 4-1. ~~AnchorExtractor 가 조사를 기업명으로 오인한다~~ — **해소 (2026-08-22)**

```text
"SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"  →  'SK하이닉스'   ○
"농심에 생산 차질을 일으킬 만한 일이 있었나?"        →  '농심'        ○
"네이버에 생산 차질을 일으킬 만한 일이 있었나?"      →  '네이버'      ○
```

**원인 진단이 틀렸었습니다.** 전에는 `_MIN_CANDIDATE_LEN = 2` 가 2글자를 허용하는 것이
원인이라고 적어 뒀는데, 「농심」(00108241·004370)이 **2글자 실존 상장사**라 상수를 올리면
그쪽이 죽습니다. 진짜 원인은 둘이었습니다.

```text
(a) 조사 잔여물 「일이」가 후보로 살아남는다
    문자열 휴리스틱이 "일이" 끝의 "이"를 떼면 1글자만 남아 절단을 포기하고
    원본을 후보로 남겼다 → 실존 법인 「일이」(01355031)와 1.000 정확 일치

(b) `ORDER BY score DESC LIMIT 1` 이 동점에서 무엇을 고를지 정의돼 있지 않다
    「일이」1.000 과 「SK하이닉스」1.000 의 승부가 물리적 행 순서에 좌우됐다
```

**고친 방법** — Kiwi 형태소 분석기를 **필터가 아니라 「문법적으로 정확한 조사 분리기」**로
넣었습니다. 태그로 거르지 않는 것이 핵심입니다([§8](#8-실측-기록) 참고: 상장사의 **62.0%**
가 `NNG` 라 고유명사 태그로 거르면 그만큼이 죽습니다).

```text
문장 전체를 Kiwi 에 1회 통과 (어절 단위로 돌리면 안 된다)
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

### 4-2. `graph_service.Relation.score` 가 1.0 을 넘는다 `[TODO]`

실측 **0.80 ~ 1.15**. 뒷받침 보정이 최대 ×1.2 라 상한이 1.2 입니다. 그런데
`app/api/schemas.py` 의 `Relation.score` 는 `le=1` 이라 **그대로 실으면 ValidationError**
입니다.

지금은 `company_service` 가 자체 계산한 값을 쓰므로 무해하지만, `graph_service` 점수를
응답에 실으려는 순간 터집니다 — **변환 함수를 미리 준비해 두는 게 쌉니다.**

### 4-3. anchor 에 조사가 붙어 나오는 경우가 남았다 `[TODO]`

상장사 400곳 표본에서 **11곳(2.8%)** 이 정확히 안 나옵니다. 전부 §4-1 이전에는 「일이」로
가던 것이라 **후퇴는 아닙니다.**

```text
9건  Kiwi 가 「에」를 조사로 못 봄 → anchor 에 조사가 붙는다
     삼성FN리츠에 · 원익큐브에 · 동부일렉트로닉스에 · 플리토에 · 사람인에
     남광토건에 · SK리츠에 · 동원데어리푸드에 · 이푸른에
     ※ 이 후보들도 옳은 법인에 매칭은 된다 — anchor 문자열만 덜 깨끗하다.

2건  과다 절단 — 사명 끝 음절이 조사로 읽힌다
     「우리로에」→「우리」(우리/NP 로/JKB 에/JKB) · 「캔버스엔에」→「캔버스」
```

**고칠 방법이 이미 손에 있습니다** — `match_candidates()` 가 `(후보, 매칭된 법인명, 점수)`
를 주므로 후보 대신 **매칭된 법인명**을 anchor 로 돌리면 9건이 사라집니다. 다만
`extract()` 의 계약이 「질의의 부분 문자열」에서 「법인명」으로 바뀌므로 호출부
(orchestrator·EntityResolver) 영향을 확인하고 §4-6 과 **함께** 정합니다.

### 4-4. 그 외 (이전부터 그랬음)

```text
app/api/main.py 의 RiskEvent · TrendingItem import 가 미사용
GET /health 가 여전히 `"stub": true` 를 하드코딩 (main.py:513)
저신뢰 키워드 9종의 실데이터 정확도 미검증                      (QueryRouter)
여러 Resolution 동시 조회 미구현                               (GraphSearcher 는 최고 1건만 씀)
```

마지막 항목은 §4-5(동음이의)와 맞물려 있습니다 — 「몇 건까지 병렬로 볼까」는 결정이
아니라 **질의 의도를 어떻게 좁힐지**부터 정해야 해서 [§7](#7-결정해야-할-것--decide) 이 먼저입니다.

### 4-5. 동음이의 사명은 원리적으로 못 가른다

```text
"이 사건의 대상 기업은?"   →  '대상'   (00121941 · 001680 대상그룹)
"동남 지역 기업 현황"      →  '동남'   (00252764 외 7곳)
```

`corp_code_master` 에 실제로 있는 이름이라 DART 1차 경로에서 1.000 으로 잡힙니다.
Kiwi 도입 전에도 같은 답을 냈습니다 — **새로 생긴 문제가 아닙니다.**
**질의 의도를 봐야 갈리는 문제라 형태소 분석으로는 못 고칩니다.**

### 4-6. 별칭으로 잡은 anchor 를 EntityResolver 가 다시 놓친다 `[TODO]`

```text
"네이버"  →  AnchorExtractor.extract()  = '네이버'      ○  (company_aliases 2차 창구)
          →  EntityResolver.resolve()  = None         ✗
          →  mode = SEMANTIC (기대: NAME)
```

`AnchorExtractor` 는 `alias_exact_match()` 로 「네이버」를 찾아냅니다. 그런데 돌려주는
것이 **별칭 문자열**이라, `SearchOrchestrator` 가 그 문자열을 그대로 `resolve()` 에 다시
넘깁니다. `resolve()` 는 `corp_code_master` 만 보고 `similarity('NAVER','네이버')=0.000`
이라 해소에 실패합니다 — **두 컴포넌트가 같은 창구를 쓰지 않습니다.**

의미검색 1위로 `NAVER`(00266961)가 나오기는 하지만, `mode` 가 `NAME` 이 아니라 `SEMANTIC`
이고 `source` 도 `postgres` 가 아니라 `chroma` 입니다. **「네이버」를 물으면 이름 해소로
답해야 합니다.**

**고치려면** `alias_exact_match()` 가 별칭이 아니라 `corp_code`(또는 `Resolution`)를
돌려주게 하거나, `EntityResolver` 에 같은 2차 창구를 붙여야 합니다. §4-3 과 같은 지점
(`extract()` 계약)이라 **함께 정합니다.** `tests/search/eval` 의 `known-alias-naver`
케이스가 `xfail(strict=True)` 로 지키고 있어, 고쳐지면 XPASS 로 뒤집혀 알려 줍니다.

---

## 5. 알려진 결함 — 챗봇

> ★**여기 있는 것 중 5건(5-3 ~ 5-7)은 2026-08-23 아키텍처 재검증이 새로 찾은 것**입니다.
> 나머지는 그 전 실측에서 나왔습니다.

### 5-1. `[사실]` 블록 안에 사실이 아닌 것이 있다 `[DECIDE]`

`stated=False` 인 **계산된 파급**이 `[사실]` 이라는 이름의 블록에 들어갑니다.

```text
파급: 심텍 (2홉, stated=False, 경로: … → IMPACTS(negative) → 마이크론
                                  → SUPPLIES_TO(공급 차질) → 심텍)
```

실측으로 나온 결과 문장:

```text
「마이크론의 공급 차질로 인해 심텍은 매출 상실의 리스크가 존재한다」    겹침 0.12
「뉴로메카는 공급업체에 공급 차질이 발생할 수 있는 상황에 직면해 있다」  겹침 0.29
```

둘 다 근거 원문에 없습니다. 프롬프트 규칙(「저희가 공급망으로 계산한 것」·인과 금지)이
**발생률은 낮췄지만 없애지는 못합니다** — 규칙 준수에만 걸려 있고 서버 검증이 없습니다.
게다가 **파급 줄에는 인용할 `evidence_id` 가 아예 없어** 화이트리스트가 닿지 않습니다.

**결정할 것** — (a) 계산된 파급을 `[사실]` 에서 빼고 별도 블록으로 분리 (b) 합성 근거 id 를
붙여 화이트리스트 대상으로 (c) 지금처럼 프롬프트 준수에만. 설계서는 **(a)** 를 권합니다
([설계서 §12](BizNode_Search_Layer_설계.md#12-사실과-추론의-경계--4등급)).

### 5-2. 앵커 없는 질문에서 무관한 기업의 evidence 가 선택된다 `[DECIDE]`

「메모리 가격 담합」·「반도체 업계 파업 위험」처럼 기업명이 없는 질의에서 답변 재료가
무너집니다 — 실측으로 하나마이크론 불성실공시·뉴로메카 투자위험종목·삼현 로봇 신사업이
올라왔습니다.

★**근거 자체는 멀쩡합니다**(겹침 0.75). 문제는 grounding 이 아니라 **관련성**이라
`claim_check` 가 잡지 못합니다.

원인이 둘 겹쳐 있습니다 — (1) 앵커가 없으면 `intent_of()` 가 지울 것이 없어 질문 전체가
의도가 되고, (2) `companies` 를 SEMANTIC 검색 결과가 채웁니다.

**Search Layer 는 수정 대상이 아니므로** 다른 계층에서 다뤄야 합니다. 설계서는
**Query Understanding** 을 권합니다([설계서 §14](BizNode_Search_Layer_설계.md#14-앵커-없는-질의)).

★§4-3·§4-6(AnchorExtractor)과는 **다른 문제입니다** — 그쪽은 앵커가 있는데 못 뽑는
경우고, 이건 **애초에 앵커가 없는 질의**입니다.

### 5-3. ★앵커 없는 **관계** 질의가 `match_type=EXACT` 로 나간다 `[DECIDE]`

**신규 발견 (2026-08-23).** 관계 키워드는 있는데 앵커가 없는 질의(「납품 단가 압박」)는
`mode=RELATIONSHIP` → `MatchType.EXACT` 로 매핑됩니다. 그러면 프롬프트가 LLM 에게
「이름 또는 관계가 그래프에서 **정확히 일치**한 결과입니다」라고 말합니다.

실제로는 `_search_anchorless()` 가 source 5 + target 5 슬롯을 **점수순으로 아무거나**
채운 것이라 정확히 일치한 게 아닙니다. **헤지가 아예 안 걸려서 §5-2(SEMANTIC)보다 나쁩니다.**

`_MATCH_TYPE_BY_MODE` 만 고쳐서는 안 됩니다 — `mode` 는 `RELATIONSHIP` 이 맞습니다.
**앵커 유무가 별도 신호여야 합니다.** 계약 변경이라 §5-8(`AskResponse.match_type`)과
**묶어서** 결정합니다.

### 5-4. ★관계에는 의도 필터가 없다 (사건에는 있는데) `[TODO]`

**신규 발견 (2026-08-23).** 비대칭입니다.

```text
사건   events_of() → evidence_selector 가 질문 의도로 고른다        ✅
관계   relations_of(key, limit=10) → 8종을 점수순으로 자를 뿐        🔴
```

게다가 **`SearchQuery.edge_types`·`direction` 을 `retrieve_service` 가 한 번도 참조하지
않습니다**(grep 0회). 질문이 무슨 관계를 물었는지가 Retrieve 경계에서 사라집니다.

부작용 — **검색이 찾아낸 바로 그 엣지가 `relations[]` 에 들어간다는 보장이 없습니다.**
관계가 10건을 넘는 기업에서는 질문이 물은 엣지가 점수순 상위 10에 못 들면 빠집니다.
근거는 살아남으므로 답은 나오지만, LLM 이 관계를 **원문에서 읽어내야** 하는 상태가 됩니다.

**고칠 방법** — `relation_selector` 를 `evidence_selector` 와 대칭으로 신설
([설계서 §11](BizNode_Search_Layer_설계.md#11-relationship-과-event-는-다른-문제다)).
**Search Layer 수정은 필요 없습니다** — 신호가 이미 Retrieve 손에 와 있습니다.

### 5-5. ★`role`·`symmetric`·`press` 가 프롬프트에 안 간다 `[TODO]`

**신규 발견 (2026-08-23).** 재료는 이미 `RetrieveResponse` 안에 있는데 프롬프트가 안 씁니다.

| 안 가는 값 | 대상 규모 | 생기는 일 |
|---|---|---|
| `Relation.symmetric` | 엣지 **1,615건** (협력 1,410 + 경쟁 205) | `PARTNERS_WITH`·`COMPETES_WITH` 는 「키 작은 쪽 → 큰 쪽」으로 고정한 **인공 방향**인데 프롬프트가 `A --PARTNERS_WITH--> B` 로 찍는다. LLM 이 **없는 방향**을 만든다 |
| `Event.role` | `mentioned` 65 + `counterparty` 69 = **134건** | 「당사자인가 그냥 언급인가」를 가르는 유일한 값인데 셋을 똑같이 찍는다. **남의 사건에 연루된 것처럼** 말할 수 있다 |
| `Evidence.press` | 전체 | 「어느 언론이 보도했나」를 답할 수 없다 |

★**새 조회도 스키마 변경도 필요 없습니다.** 가장 싼 수정입니다.

### 5-6. ★claim 을 한 종류로 취급한다 `[TODO]`

**신규 발견 (2026-08-23).** 점수 하나가 서로 다른 세 질문에 동시에 답하려 합니다 —
「근거 원문에서 왔나」·「그래프 값에서 왔나」·「우리 계산에서 왔나」.

실측: 토큰화 잡음을 걷어낸 뒤 남은 저겹침 claim `≤0.5` **11건 중 7건**이 파급·영향
문장이었습니다. 그건 **채점 실패가 아니라 분류 실패**입니다 — 파급 문장은 근거 원문에
없는 것이 정상입니다.

★**임계값을 아직 정하지 않는 이유가 바뀌었습니다.** 전에는 「분포를 더 봐야 해서」였는데,
이제는 **「타입을 안 가르고 정한 임계값은 어차피 틀리기 때문」**입니다
([설계서 §13](BizNode_Search_Layer_설계.md#13-claim-5종과-검증)).

### 5-7. ★`RetrieveResponse.companies` 의 뜻이 스키마 설명과 다르다 `[DECIDE]`

**신규 발견 (2026-08-23).** 스키마 설명은 「질문에서 찾아낸 기업」인데, RELATIONSHIP
모드에서 실제로 담기는 것은 **앵커가 아니라 상대편 기업**입니다.

```text
「삼성전자에 납품하는 기업」
  companies[] = [SFA반도체, 원익IPS, 세메스, …]   ← 삼성전자가 없다
```

결과적으로 `events_of(상대편) × 5` 가 **질문과 무관한 사건**을 재료로 올립니다.

**결정할 것** — (a) 스키마 설명을 사실에 맞춘다 (b) 앵커를 별도 필드로 싣는다.
(b)는 외부 계약 변경이라 백엔드가 이 필드를 어떻게 쓰는지 확인이 먼저입니다.

### 5-8. `AskResponse` 에 `match_type` 이 없어 결정론적 백스톱이 없다 `[DECIDE]`

설계서 답변 규약의 형제 규칙들은 전부 구조적 강제가 있습니다 — `evidence_id` 인용은
화이트리스트가, `missing=true` 인용 금지는 근거 블록 제외가 강제합니다. 그런데
**SEMANTIC 무게 구분만 프롬프트 문장 하나에 걸려 있고 서버 검증이 없습니다.**

게다가 `AskResponse` 에 `match_type` 이 없어 프론트가 「유사 검색 결과입니다」 배지를
붙일 수단조차 없습니다 — LLM 이 규칙을 무시하면 그 답변은 EXACT 답변과 **구별 불가능하게**
사용자에게 갑니다.

**결정할 것** — `AskResponse.match_type` 을 노출할지, 「LLM 준수에만 의존」을 의도된
트레이드오프로 확정할지. API 계약 변경이라 백엔드/프론트와 협의가 필요합니다.
★**§5-3(앵커 없는 관계 질의)·§5-2(재료가 약하다는 신호)와 같은 결정입니다 — 묶어서 한 번에.**

### 5-9. 시스템 프롬프트의 SEMANTIC 설명이 실제 동작과 어긋난다 `[TODO]`

규칙은 「그 아래 기업·**관계**는 … 의미가 비슷해서 찾은 것」이라고 말하지만, SEMANTIC
모드에서 의미 유사도로 고른 것은 **기업뿐**입니다. `retrieve_service` 가 그 기업 키로
Neo4j 에서 **정확한 그래프 엣지**를 다시 조회하기 때문입니다.

즉 관계 자체는 정확한 사실이고, 불확실한 것은 「이 기업이 질문이 물은 그 기업인가」입니다.
헤지 방향은 안전한 쪽이라 버그는 아니지만 **틀린 이유를 LLM 에게 가르치고 있습니다.**

제안 문구: *「…그 아래 기업은 이름이 아니라 의미 유사도로 고른 것입니다 — 질문이 물은
기업이 아닐 수 있습니다. 그 기업에 딸린 관계·사건은 그래프의 정확한 값이지만, 기업 선택이
틀렸다면 전부 무관한 정보입니다.」*

### 5-10. 인젝션 방어가 `[근거]` 블록만 덮고 `[사실]` 블록은 안 덮는다 `[DEFER]`

`_fact_lines()` 가 보간하는 값들(`c.name`·`event.name`·`event.event_type`·
`relation.subtype`·`prop.target`·`prop.path`)은 **델리미터 중화도 개행 처리도 안 거칩니다.**
이 값들은 뉴스 → LLM 추출 → Neo4j 경로로 들어온 **신뢰 안 된 텍스트**입니다. 개행이 섞인
이름 하나면 `[사실]` 블록 안에 가짜 「검색 방식: EXACT …」 줄을 심을 수 있습니다.

**선행 코드의 문제이고**, 규칙이 「맨 앞의」로 위치를 못 박아 뒤에 오는 가짜 줄은 우선순위가
낮으며, 뚫려도 화이트리스트는 그대로라 **근거 날조는 불가능**하고 최대 피해가 「헤지 안 함」
입니다. 가장 싼 수정은 `_fact_lines` 보간값의 개행 제거 — §5-5 와 같이 처리하면 됩니다.

### 5-11. 답변 내용 품질을 아무도 채점한 적이 없다 `[TODO]`

`/ask` 는 **구조적 안전성**(화이트리스트·인젝션 방어·실패 구별)만 검증됐습니다.
「EXACT/SEMANTIC 을 답변이 정말 다르게 말하는가」와 전반적 자연스러움을 **사람이 읽어본
적이 없습니다.** 검색의 20케이스 회귀 평가셋과 짝을 이루는 절차가 아직 없습니다.

★자동 채점기(LLM-judge)를 새로 만들지 않는다는 원칙은 유지합니다.

---

## 6. 다음 개발 순서

### 6-1. 지금까지 만든 것을 셋으로 나누면

**A. 그대로 유지 — 되돌리지 않는다**

| 무엇 | 왜 유지인가 |
|---|---|
| **기업별 evidence scope** (`HAS_EVENT` 엣지에서) | 최종 아키텍처의 ⑤단계 그 자체다. 사건 85건 중 71건이 섞일 수 있었다 |
| **사건 의도 선택** (`evidence_selector`) | ④b 그 자체다. 규칙 티어 + 임베딩 · 하드 필터 아님 · 잘라낸 개수 로그 — 이 셋은 `relation_selector` 에도 그대로 쓴다 |
| **인과 금지 · 보도일 명시** (프롬프트 규칙) | 4등급의 ②를 막는 유일한 장치다. 서버 강제가 아니라는 한계가 있을 뿐, 없애면 더 나빠진다 |
| **`claims[]` 관측 · 판정 없음 · 외부 계약 무변경** | 「타입 분류가 먼저」이므로 **판정하지 않은 것이 옳았다.** 임계값을 박았으면 지금 되돌려야 했다 |
| **문장용 토크나이저** (`sentence_tokens`·날짜 정규화) | 채점 잡음을 걷어냈고, 그 덕에 남은 저점수가 **한 부류로 수렴**해서 §5-6 진단이 가능해졌다 |
| **`_MAX_PROPAGATION_LINES` + 「몇 곳을 뺐는지 적는다」** | 조용히 자르지 않는 원칙 |
| **`token_overlap.tokens()` 기본 경로 불변** | `batch/audit/grounding.py` 의 `0.34` 가 이 동작에 맞춰 잡힌 값이다 |
| **`ask()` 가 `retrieve()` 를 먼저 부르는 순서** | 3단 구조를 코드가 이미 강제한다 |
| **화이트리스트 · `missing` 제외 · `failed` 구별** | 사실 등급의 **유일한 구조적 강제**다 |

**B. 수정이 필요하지만 지금은 구현하지 않는다**

| 무엇 | 어긋난 점 | 왜 지금 안 하나 |
|---|---|---|
| `[사실]` 블록에 계산값이 섞임 | §5-1 | 블록을 가르면 프롬프트 규칙 문구도 같이 바뀐다. **①(freeze) 이후 한 번에** |
| 관계 의도 필터 없음 | §5-4 | `relation_selector` 신설이 필요하다 → ② |
| `edge_types`·`direction` 미사용 | §5-4 | 위와 같은 작업 |
| `role`·`symmetric`·`press` 미노출 | §5-5 | 싸지만 `_fact_lines()` 를 손대는 작업이라 위와 **같은 커밋에 묶는 것이 맞다** |
| 앵커 없는 관계 질의가 EXACT | §5-3 | 계약 변경이라 §5-8 과 함께 |
| claim 타입 구분 없음 | §5-6 | 타입 분류 설계가 먼저다 → ⑤ |
| `score={...}` 를 척도 설명 없이 LLM 에 줌 | §4-2 와 같은 뿌리 | 「신뢰도 90%」로 읽힐 소지. 블록 재설계 때 같이 |

**C. 후속 단계에서 새로 구현한다**

| 무엇 | 어디에 | 왜 새로 만드나 |
|---|---|---|
| **Query Understanding 계층** | `app/services/` 신설 (Search Layer 밖) | 조각이 3곳(`QueryRouter`·`AnchorExtractor`·`intent_of()`)에 흩어져 있고, 「앵커가 있나」·「어떤 질의인가」를 **한 번만** 판정하는 곳이 없다 |
| **`relation_selector`** | `app/services/` — `evidence_selector` 와 대칭 | §5-4 |
| **Context Builder 분리** | `answer_service` 안 또는 별도 모듈 | §5-1 — 블록을 이름으로 가른다 |
| **claim type 분류 + 타입별 대조기** | `claim_check` 확장 | §5-6. ①②③⑤ 는 LLM judge 없이 결정론적 대조로 풀린다 |
| **주제 질의 응답 형태** | 프롬프트 + 재료 구성 | §5-2 — 목록형 답변 |

### 6-2. 순서 — 목적에서 역산한 6단계

```text
① Architecture freeze ─→ ② Query Understanding + Retrieval ─→ ③ Evidence grounding
                                                                      │
     ⑥ Evaluation ←─ ⑤ Claim validation ←─ ④ Insight generation ←────┘
```

**① Architecture freeze** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | [설계서 3부](BizNode_Search_Layer_설계.md#3부--챗봇-답변을-쓴다)를 팀 합의 기준으로 확정. [§7](#7-결정해야-할-것--decide) 의 `[DECIDE]` 에 답을 붙인다 |
| 수정 대상 | **문서만** |
| 수정하지 않을 대상 | 코드 전부 |
| 완료 조건 | §7 의 `[DECIDE]` 가 전부 `[TODO]` 로 바뀐다 |
| 필요한 테스트 | 없음 |

**② Query Understanding + Retrieval** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | 「이 질문이 무엇을 묻는가」를 **한 번만** 판정하고 Retrieve 전체가 그것을 쓴다. 관계 선택을 사건 선택과 대칭으로 만든다 |
| 수정 대상 | `retrieve_service.py` · **신설** `query_understanding` · **신설** `relation_selector` |
| 수정하지 않을 대상 | **`search/` 전체** · `company_service`·`relation_service`·`graph_service` · Neo4j 구조 · `AskResponse` |
| 완료 조건 | (ⓐ) 「삼성전자가 납품하는 기업」에서 `relations[]` 가 `SUPPLIES_TO` 를 위에 싣는다 (ⓑ) 「SK하이닉스 노조」 사건 선택이 회귀하지 않는다 (ⓒ) 앵커 유무가 **하나의 값**으로 판정되어 아래 단계가 읽을 수 있다 (ⓓ) 잘라낸 관계 개수가 로그에 남는다 |
| 필요한 테스트 | `relation_selector` 단위(의도 정렬·하드 필터 아님·동점 시 입력 순서 보존) · `retrieve_service` 통합(대표 질의 A/B) · **기존 485 무회귀** |

**③ Evidence grounding** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | 재료가 질문에 **관련 있다**는 것과 근거가 **그 기업 것**이라는 것을 유지·강화. 주제 질의에서 「재료가 약하다」 신호를 만든다 |
| 수정 대상 | `evidence_selector`(주제 질의 분기) · `retrieve_service`(주제 질의 상한) |
| 수정하지 않을 대상 | **엣지 기반 evidence scope**(되돌리지 않는다) · 전역 evidence 검색을 도입하지 않는다 |
| 완료 조건 | 「메모리 가격 담합」·「반도체 업계 파업 위험」에서 **재료가 약하다는 신호가 나온다.** 무관 기업을 억지로 0 으로 만들지 않는다 |
| 필요한 테스트 | 앵커 없는 질의 2건 회귀 · 기업별 scope 회귀(현대오토에버 기사가 SK하이닉스 답변에 안 섞임) |

**④ Insight generation** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | 4등급이 **프롬프트 구조로** 갈린다. 계산값이 「사실」이라는 이름의 블록에서 나온다 |
| 수정 대상 | `answer_service._fact_lines()` → 블록 분할 · 프롬프트 규칙 문구(§5-9) · `role`·`symmetric`·`press` 노출(§5-5) · 보간값 개행 제거(§5-10) |
| 수정하지 않을 대상 | `AskResponse` 계약 · `relation_service.event_impact()` 계산 로직 · LLM 모델/temperature([§9](#9-실측-근거-없는-잠정치) `[MEASURE]` 가 먼저) |
| 완료 조건 | `[확인된 사실]`·`[계산된 파급]`·`[근거]` 분리 · 파급 유래 문장이 「저희가 계산한 것」임을 밝힌다 · `PARTNERS_WITH` 를 방향 있는 관계로 말하지 않는다 · `role=mentioned` 를 당사자로 말하지 않는다 |
| 필요한 테스트 | 프롬프트 조립 단위(블록 분리·`symmetric` 표기·`role` 표기·델리미터 무결성 유지) · 대표 질문 사람 검증 |

**⑤ Claim validation** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | claim 을 5종으로 가르고 타입별 원천에 대조. **여전히 판정하지 않는다** |
| 수정 대상 | `claim_check.py`(타입 분류 + 타입별 대조) · `batch/audit/claim_grounding.py`(타입별 분포 출력) |
| 수정하지 않을 대상 | `token_overlap.tokens()` 기본 경로 · `batch/audit/grounding.py` 의 `0.34` · `AskResponse` 계약 · **LLM judge 도입 안 함** |
| 완료 조건 | 20개 대표 질문의 claim 이 타입별로 갈려 분포가 나온다. **④ Evidence-derived 만의 분포**가 처음으로 보인다 |
| 필요한 테스트 | 타입 분류 단위 · 타입별 대조 단위 · 기존 `claim_check` 회귀 |

**⑥ Evaluation** `[TODO]`

| 항목 | 내용 |
|---|---|
| 목표 | ②~⑤ 가 실제로 답변을 좋게 했는지 사람이 확인. **그 뒤에야** 임계값을 논한다 |
| 수정 대상 | `batch/audit/claim_grounding.py` 질문 세트 · 회귀 절차 문서 |
| 수정하지 않을 대상 | 자동 채점기를 새로 만들지 않는다 |
| 완료 조건 | 대표 질문 20개 답변을 사람이 읽고, 타입별 분포로 임계값 후보를 **처음으로** 제안할 수 있다 |
| 필요한 테스트 | 회귀 평가셋 갱신 |

### 6-3. 병행 가능한 독립 작업

위 ①~⑥ 과 선후관계가 없어 언제든 끼워 넣을 수 있는 것들입니다.

| 항목 | 상태 | 우선순위 | 왜 |
|---|---|---|---|
| **`/ask` 운영 이미지 기동 검증** (`docker build` → 기동 → `POST /ask` 200) | `[VERIFY]` | 즉시 | `/retrieve` 때 `search/` COPY 누락으로 **빌드 자체가 실패**한 전례가 있다. 다만 ④ 가 `answer_service.py` 를 바꿀 예정이라 **그 뒤 최종 모양으로 한 번** |
| `app/api/main.py` 스텁 개수 문구 정정 (`"21개 중 20개 실동작··· /retrieve 스텁"`) | `[TODO]` | 즉시 | `/ask` 까지 생겨 완전히 틀린 문장이 됐다. 백엔드가 실제로 읽는 `/docs` 설명이라 방치 비용이 크다 |
| `BizNode_백엔드_연동_가이드.md` 에 `/ask` 추가, `/retrieve` 스텁 표시 제거 | `[TODO]` | 즉시 | 백엔드가 `/ask` 를 부르기 전에 계약 문서가 맞아야 한다 |
| **timeout** — 단계별 타임아웃 없음. 드라이버 레벨도 미설정 | `[MEASURE]` → `[DECIDE]` | 단기 | `/ask` 로 동기 경로에 OpenAI 호출까지 더해져 「긴 꼬리」 위험이 늘었다. `asyncio.wait_for` 는 threadpool 스레드를 못 죽이므로 진짜 취소는 드라이버 레벨(PG `statement_timeout`·Neo4j `tx.timeout`)이어야 한다 |
| **N+1 실측** — 기업 5곳 기준 `events_of`×5 + `relations_of`×5 + `event_impact`×3 | `[MEASURE]` | 단기 | timeout 결정의 선행 조건 |
| **prompt token·context 크기** — `_fact_lines`/`_evidence_block` 길이를 잰 적 없음 | `[MEASURE]` | 단기 | 재료 상한 셋은 **개수용**으로 정한 값이지 토큰 예산이 아니다. ④ 이후가 낫다 |
| **anchor / EntityResolver** — §4-3 + §4-6 | `[TODO]` | 단기 | 둘 다 `extract()` 계약 변경이 걸려 함께 처리해야 한다. 검색 품질 문제가 곧바로 **답변 품질** 문제로 전이된다 |
| **ranking 품질** — 대표 질문으로 「원하는 게 상위에 오는가」 | `[MEASURE]` | 단기 | `/ask` 는 상위 랭킹 결과를 그대로 재료로 쓴다 |
| `graph_service.Relation.score` 변환 함수 (§4-2) | `[TODO]` | 단기 | 지금은 무해하지만 실으려는 순간 `ValidationError` |
| **Agent Tool 연동** | `[TODO]` | 단기 | `/ask` 가 있으니 자연스러운 다음 확장 |
| **모델/temperature 적합성** — `gpt-4o-mini`·`temperature=0.0` 재사용 중 | `[MEASURE]` | 단기 | 이 기본값은 **판정·분류·요약**(재현 가능해야 하는 배치)용으로 정해진 것이다. `/ask` 는 자연어 **생성**이라 성격이 다르다. 실사용 트래픽 없이 지금 바꾸면 근거 없는 변경 |
| **Redis / CacheService** | `[DEFER]` | 트래픽 발생 후 | 컨테이너·의존성은 이미 준비됨. 트래픽이 없으면 효용을 못 잰다 |
| GraphSearcher 다중 `Resolution` 동시 조회 | `[DEFER]` | — | §4-5(동음이의)와 맞물려 있다. 「몇 건까지 볼까」보다 **질의 의도를 어떻게 좁힐지**가 먼저 |
| Tier B(실 OpenAI 호출) 테스트가 「정답」을 두 번째 `retrieve()` 호출로 만듦 | `[DEFER]` | — | 테스트 플레이키 위험일 뿐 코드 결함은 아니다. 다음 실콜 테스트를 추가할 때 레코딩 방식으로 같이 손보면 된다 |

### 6-4. 파일별 책임 — 최종 아키텍처에서 적절한가

| 파일 | 현재 책임 | 판정 |
|---|---|---|
| `app/services/retrieve_service.py` | 검색 호출 + 재료 조립 (flow ②③④⑤⑥) | ⚠ **책임 과다 경로.** 지금 296줄로 관리 가능하지만 ①·④a 가 들어오면 넘친다. **선택을 밖으로 빼는 방향**이 맞다 |
| `app/services/evidence_selector.py` | 사건 의도 선택 (④b) | ✅ **적절.** 다만 **이름이 하는 일과 다르다** — evidence 가 아니라 **event** 를 고른다. `event_selector` 가 정확하다(순수 리네임이라 언제든 싸다) |
| `app/services/answer_service.py` | 프롬프트 조립(⑦) + LLM 호출(⑧) + 화이트리스트 + claim 관측 | ⚠ **⑦을 분리하는 것이 맞다.** 블록 분할이 들어오면 `_fact_lines()` 가 세 갈래로 늘어난다 |
| `app/services/claim_check.py` | claim → (상태, 겹침 점수) | ✅ **위치 적절.** 「판정하지 않는다」를 명시한 것이 특히 옳았다 |
| `pipeline/token_overlap.py` | 낱말 겹침 계산 | ✅ **적절.** `app`·`batch` 양쪽이 쓰므로 `pipeline` 이 맞는 자리다 |
| `batch/audit/claim_grounding.py` | 20개 질문 분포 수집 | ✅ **적절.** `AnswerService.ask()` 를 안 쓰고 같은 프롬프트를 직접 부르는 구조도 옳다(외부 계약 무변경 유지) |
| `app/services/company_service.py`·`relation_service.py`·`graph_service.py` | 그래프 읽기·파급·근거 조립 | ✅ **적절 · 무수정.** 화면(`/companies`·`/relations`)과 공유하므로 `/ask` 사정으로 바꾸면 안 된다 |
| `search/` 전체 | ② | ✅ **무수정 유지.** 이 문서의 어떤 제안도 `search/` 수정을 요구하지 않는다 |

---

## 7. 결정해야 할 것 — `[DECIDE]`

**코드로 풀 문제가 아닙니다.** 사람이 정해야 다음이 진행됩니다.

### 7-1. ★묶어서 한 번에 정해야 하는 것 — API 계약 변경

셋 다 「재료·답변의 신뢰도를 화면에 어떻게 내릴까」라는 **같은 질문**입니다. 따로 정하면
계약을 세 번 바꾸게 됩니다.

| 무엇 | 선택지 | 관련 |
|---|---|---|
| `AskResponse` 에 `match_type` 을 노출할까 | (a) 노출해 결정론적 배지 (b) 「LLM 준수에만 의존」을 의도된 트레이드오프로 확정 | [§5-8](#5-8-askresponse-에-match_type-이-없어-결정론적-백스톱이-없다-decide) |
| 앵커 없는 질의에 「재료가 약하다」를 응답으로 내릴까 | (a) 별도 필드 (b) `match_type` 확장 (c) 답변 문장으로만 | [§5-2](#5-2-앵커-없는-질문에서-무관한-기업의-evidence-가-선택된다-decide) · [§5-3](#5-3-앵커-없는-관계-질의가-match_typeexact-로-나간다-decide) |
| `RetrieveResponse.companies` 의 뜻을 어디에 맞출까 | (a) 스키마 설명을 사실에 맞춘다 (b) 앵커를 별도 필드로 싣는다 | [§5-7](#5-7-retrieveresponsecompanies-의-뜻이-스키마-설명과-다르다-decide) |

### 7-2. 챗봇 품질 정책

| 무엇 | 선택지 | 왜 사람이 정하나 |
|---|---|---|
| **`[사실]` 블록을 가를까** | (a) `[확인된 사실]`/`[계산된 파급]` 분리 (b) 파급에 합성 근거 id (c) 프롬프트 준수에만 | 설계서는 (a)를 권한다. 프롬프트 규칙 문구가 함께 바뀐다 → [§5-1](#5-1-사실-블록-안에-사실이-아닌-것이-있다-decide) |
| **claim type 을 누가 정할까** | (a) LLM 자기 신고(`claims[].claim_type`) (b) 서버 규칙만(파급 대상·엣지 양끝·사건명 대조) (c) 둘을 대조해 **불일치 자체를 신호로** | (a)는 싸지만 신뢰 대상이 아니고, (b)는 확실하나 놓치는 게 생기며, (c)는 가장 강하지만 구현이 는다. **정확도와 비용의 교환** |
| **주제 질의를 목록형으로 바꿀까** | (a) 도입 (b) 서술 유지하고 헤지만 강화 | 화면에 어떻게 보일지의 문제라 프로덕트/프론트와 맞춰야 한다 |
| **답변 형식/톤/길이 기준** | — | 시스템 프롬프트에 답변 규칙만 있고 문단/목록·길이 상한·인용 밀도 지침이 없다. **엔지니어링 단독 결정 사안이 아니다** |

### 7-3. 검색 · 팀 · 인프라

| 무엇 | 왜 |
|---|---|
| 백엔드 `/api/v1/workspaces/{workspaceId}/chat/messages` ↔ `/ask` 매핑 | 이 레포에 없는 라우트다. 백엔드(Spring) 소유로 보이나 확인 불가 — **팀에 물어볼 것** |
| **동음이의 사명**(「대상」·「동남」)을 못 가르는 문제 | 형태소 분석으로는 해결 불가. **질의 의도를 어떻게 좁힐지** 방법론 자체가 없다 → §4-5 |
| 클라우드 사업자(AWS/NCP/KT) 선정 | 팀 전체가 정할 사안. 실제 트래픽이 생기는 시점에 맞추는 게 맞다 — 지금 개발 노트북 Docker 로 충분히 돌아간다 |
| 「갱신 버튼」(온디맨드 뉴스 갱신) 확장 여부 | 원칙은 이미 있음(큐잉 + 서비스 시간 밖 처리 + 알림)이나 실제 요구가 생기면 세부 설계 필요 |

---

## 8. 실측 기록

숫자로 남겨 둔 것들입니다. **다음 판단의 근거가 됩니다.**

### 8-1. 그래프 · 검색

| 무엇 | 값 | 시점 |
|---|---|---|
| 삼성전자 관계 총량 | **998건** | 2026-08-20 |
| └ SK하이닉스 / 현대자동차 위치(점수순) | 271번째 / 418번째 | 2026-08-20 |
| 삼성전자 관계 상위 10건 중 비-Company 끝 | **5건** (Organization 3 · Product 2) | 2026-08-20 |
| 그래프 전체에서 비-Company 끝 비율 | 400건 중 **194건 (48.5%)** | 2026-08-20 |
| 복수 근거를 든 관계 | 삼성전자 200건 중 **54건** | 2026-08-20 |
| 엣지 : 근거 | 11,060 : 9,228 (한 근거가 15개 엣지에 붙은 사례 있음) | 2026-08-16 |
| `company` 컬렉션 중 프로필 보유 | 2,430건 중 **64건** | — |
| 리스크 파급(모트라스 파업) | 124곳 = **보도 10 + 계산 114** | — |
| 의미검색 정확도 | 「삼성전자에 납품하는 기업」 → 실제 공급사 **0건** | — |
| 삼성전자 `SUPPLIES_TO` 방향 비 | outgoing **51** : incoming **151** (전체 관계 1,169) | 2026-08-22 |
| └ 「삼성전자가 납품하는 기업」 결과 수 | **2건 → 10건** (top_k 를 채움) | 2026-08-22 |
| └ 방향 지정 질의 6종 전부 | 2~8건 → **10건** · 지연 무변화(60~85ms) | 2026-08-22 |

### 8-2. anchor · 이름 해소

| 무엇 | 값 | 시점 |
|---|---|---|
| **anchor 정확도** (상장사 400곳 × 「X에 …일이 있었나?」) | **22.0% → 97.2%** | 2026-08-22 |
| └ 그중 「일이」 오인 | **312건 → 0건** | 2026-08-22 |
| └ 질의당 지연 | 16.4ms → **14.7ms** (후보가 줄어 오히려 빨라짐) | 2026-08-22 |
| Kiwi 가 `NNP`/`SL`/`SN` 을 하나도 안 주는 상장사 | 3,979곳 중 **2,465곳 (62.0%)** | 2026-08-22 |
| Kiwi 명사 토큰으로 사명 원형 복원 실패 | 3,979곳 중 **413곳 (10.4%)** | 2026-08-22 |
| 어절에 명사류 토큰이 0개인 상장사 | 조사 문맥 **4곳(0.10%)** · 단독 **17곳(0.43%)** | 2026-08-22 |
| `company_aliases` 3글자 이하 별칭 중 Kiwi 가 일반명사로 읽는 것 | 523개 중 **215개** | 2026-08-22 |
| Kiwi 로드 / tokenize | 1.3초(프로세스당 1회) / **0.14ms**(질의당) | 2026-08-22 |
| `similarity('NAVER','네이버')` | **0.000** (트라이그램은 한글↔영문을 못 잇는다) | 2026-08-22 |

### 8-3. 챗봇 재료 · 근거

| 무엇 | 값 | 시점 |
|---|---|---|
| 사건 상한이 없을 때 재료 크기 | 「SK하이닉스」 사건 69 · 근거 82 · **13,916자** | 2026-08-23 |
| └ 질문을 바꿔도 동일 | 「SK하이닉스 노조 관련 리스크」 **완전히 같은 재료** | 2026-08-23 |
| └ 다중 기업 질의 | 「담합 소송」 사건 155 · 근거 205 · **34,430자** | 2026-08-23 |
| 여러 기업이 공유하는 Event | 사건 938건 중 **85건** | 2026-08-23 |
| └ 그중 근거가 섞일 수 있었던 것 | **71건** | 2026-08-23 |
| 사건 라벨 임베딩 소요 | 라벨 69건 0.25s · 155건 0.76s · 의도 1건 0.12s | 2026-08-23 |
| 파급 줄 수 (상한 없을 때) | 「SK하이닉스 안전사고」 한 질문에 **45줄 초과** · 전부 `stated=False` | 2026-08-23 |
| `HAS_EVENT` 엣지 중 `evidence_id` 보유 | **1,062 / 1,062 (100%)** | 2026-08-23 |
| `occurred_at` 이 `last_seen` 과 같은 사건 | 1,062건 중 **1,059건** (= 보도일이지 발생일이 아니다) | 2026-08-23 |

### 8-4. claim 겹침 채점

같은 20개 질문 · **같은 claim 83건을 3단계로 동시 채점**했습니다.

```text
                          min    p10    p25    p50    p75    p90   mean
    v0 원본                0.0    0.0    0.2  0.375  0.556  0.688  0.364
    v1 조사·날짜 정규화     0.0  0.429  0.667  0.800  0.909  1.000  0.754
    v2 +문장 불용어·날짜    0.0  0.500  0.750  0.909  1.000  1.000  0.832

    ≤0.34 「의심」 …… 47.0% → 6.0% → 4.8%
```

| 무엇 | 값 |
|---|---|
| v1 이전 저점수의 지배적 원인 | **한국어 조사** — 「삼성전자에」가 「삼성전자」를 담은 근거에서 없는 토큰으로 잡힘 |
| v2 이전 못 맞춘 토큰 123개 중 날짜 | **26개 (21.1%)** — 단일 최대 원인 |
| 나머지 상위 | 서술 명사 — 발생 7 · 기업 5 · 진행 3 · 보도 3 · 발표 3 … |
| **v2 에서 `≤0.5` 인 11건 중 파급·영향 문장** | **7건** ★[§5-6](#5-6-claim-을-한-종류로-취급한다-todo) 의 근거 |

---

## 9. 실측 근거 없는 잠정치

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
| `_MAX_EVENTS_PER_COMPANY` | 10 | RetrieveService | **기업마다 따로** 적용 — 전체 상한 하나로 두면 사건 많은 기업이 다 먹는다 |
| `_MAX_RISK_EVENTS_FOR_PROPAGATION` | 3 | RetrieveService | 파급을 계산할 사건 수 |
| `_MAX_PROPAGATION_LINES` | 15 | AnswerService | 프롬프트에 실을 파급 줄 수 |
| `_EVENT_TYPE_KEYWORDS` 12종 | — | evidence_selector | 사건 유형 티어 규칙. **하드 필터가 아니라** 티어만 정한다 |
| `_MIN_CANDIDATE_LEN` / `_MAX_WORDS` | 2 / 10 | AnchorExtractor | **2 는 올리면 안 됩니다** — 「농심」이 2글자 실존 상장사 |
| `_SHALLOW_KEYWORDS` 9종 | — | QueryRouter | 대표 키워드 1개씩. 동의어 확장·정확도 미검증 |
| claim 겹침 임계값 | **없음** | claim_check | 아직 정하지 않았다 → [§5-6](#5-6-claim-을-한-종류로-취급한다-todo) |
| fuzzy threshold | 0.50 | EntityResolver · AnchorExtractor | **유일하게 근거 있음** ↓ |

fuzzy threshold 만 실측 근거가 있습니다 — 정답 후보는 0.5 이상, 노이즈 어절(「기업」→
기업은행 0.33, 「뉴스」→뉴스1 0.4)은 0.33~0.4 에 몰려 **간격이 뚜렷합니다.** 다만 별도
튜닝 데이터셋으로 재검증하지는 않았습니다.

★**상한들은 잘라낼 때 로그를 남깁니다** — 조용히 자르면 「그게 전부」로 읽히기 때문입니다.

---

## 10. 확인하지 못한 것

이 레포 안에서는 답을 알 수 없어 **팀에 물어봐야 하는** 항목입니다.

| 무엇 | 상태 |
|---|---|
| `/api/v1/workspaces/{workspaceId}/chat/messages` | 이 레포에 **존재하지 않습니다.** 백엔드(Spring) 소유로 보이나 확인 불가 — `/retrieve`·`/ask` 와의 매핑을 맞춰야 합니다 |
| 백엔드가 OpenAPI 로 클라이언트를 코드젠하는가 | 확인 불가. 한다면 `/docs` 변경 시마다 재생성이 필요합니다 |
| 드라이버 레벨 timeout | PostgreSQL `statement_timeout` · Neo4j `tx.timeout` 이 **어느 저장소에도 설정돼 있지 않습니다** |

---

## 11. 후순위 정리 대상 `[DEFER]`

기능·성능에 영향이 없는 순수 정리 대상입니다. **삭제하지 않고 여기 남겨 둡니다** —
누군가 해당 파일을 만질 때 같이 처리하면 됩니다.

| 항목 | 왜 미루나 |
|---|---|
| `app/api/main.py` 의 미사용 import (`RiskEvent`·`TrendingItem`) | 빌드/런타임에 영향 없음. 이전부터 그랬음 |
| `GET /health` 가 여전히 `"stub": true` 를 하드코딩 | 이전부터 그랬음 |
| `Source.source_type` 기본값이 `Evidence.source_type` 과 다름(`news` vs `dart`) | `_source_from_evidence()` 가 항상 명시적으로 채워서 실질적 영향 없음 |
| `QueryRouter` 저신뢰 키워드 9종의 실데이터 정확도 미검증 | 리스크는 낮다. 실사용 트래픽이 쌓이면 같이 측정할 만하다 |
| 테스트 파일의 import 배치/정렬 (모듈 중간 삽입·지역 import) | 이 저장소에 린트 게이트가 없어 기능적 영향 없음 |
| 인젝션 방어를 구조적 방어로만 한정 | 설계 단계에서 **의도적으로** 내린 트레이드오프. 결함이 아니라 결정 |

### 인프라 · 데이터 (중장기)

| 항목 | 상태 | 왜 |
|---|---|---|
| 서버 이전 — 클라우드 선정 | `[DECIDE]` | 팀 전체가 정할 사안 → [§7-3](#7-3-검색--팀--인프라) |
| 배치 자동화(cron) — 시세·공시·재무·감사 6종 | `[DEFER]` | 개발 노트북에 cron 을 걸 이유가 없다. 서버 이전 후 |
| 공시·사업부문 확장 (시드 64곳뿐) | `[DEFER]` | 스키마가 안 겹쳐 언제든 병행 가능하지만 지금 급하지 않다 |
| 사업 개요에서 제품 추출 | `[DEFER]` | 원문이 서술형이라 **방법론 자체가 아직 없다** |
| ★뉴스 수집 자동화(cron) 불가 | `[DONE]`(정책) | 부채가 아니라 **의도된 운영 정책**이다. Google News RSS 가 연속 호출을 차단해 사람이 수동으로 돌리기로 확정했고, 우회(IP 교체·프록시·UA 위장)는 **안 하기로** 이미 정했다 |

---

## 12. 변경 이력

| 날짜 | 변경 | 왜 |
|---|---|---|
| 2026-08-23 | **문서 3개 → 2개로 통합** | 「기술 부채 및 설계 검토 사항」을 이 문서로, 챗봇 아키텍처를 설계서 3부로 합쳤다. 같은 항목이 두 문서에 나뉘어 어느 쪽이 최신인지 알 수 없었다 |
| 2026-08-23 | **`/ask` 최종 아키텍처 재정의** (코드 변경 없음) | 3단 정의 · 10단계 flow · claim 5종 · 사실/추론 4등급 확정. **새 결함 5건 발견** → [§5](#5-알려진-결함--챗봇) |
| 2026-08-23 | **claim 겹침 채점에서 잡음 2종 제거** | 날짜(못 맞춘 토큰의 21.1%)와 서술 명사. corpus 를 **보여 준 것과 같게** 맞추고 문장 전용 불용어 신설. 중앙값 0.800 → **0.909** |
| 2026-08-23 | **한국어 조사 분리 + 날짜 정규화** (`sentence_tokens`) | 저겹침의 지배적 원인이 근거 부실이 아니라 **형태소**였다. 중앙값 0.375 → **0.800** |
| 2026-08-23 | **claim 단위 관측 신설** (`claims[]` · 판정 없음) | 답변이 통짜 문자열이면 「어떤 주장이 어떤 근거에 기대는가」가 데이터로 없다. 실측으로 질소 누출 답변이 HBM3E 양산 근거를 인용한 적이 있다 |
| 2026-08-23 | **인과 금지 · 보도일 명시** (프롬프트 규칙 추가) | 「2024년 2월에 질소 누출 사고」라고 답했는데 근거 원문은 2015년 사고였다 — 환각이 아니라 **우리가 그렇게 말한 것** |
| 2026-08-23 | **사건을 질문 의도로 선택** (`evidence_selector`) | 질문이 무엇이든 같은 재료가 나갔다. 실험 3회로 순위 규칙 확정 |
| 2026-08-23 | **사건 근거를 기업의 `HAS_EVENT` 엣지로 좁힘** | Event 노드의 `evidence_ids` 는 **모든 기업의 합집합**이라 남의 기사가 섞였다. 85건 중 71건이 대상 |
| 2026-08-23 | **`RetrieveResponse.match_type` 노출 + 답변 헤징** | 「SEMANTIC 결과를 같은 무게로 말하지 않는다」를 시스템 프롬프트가 못 지키고 있었다 |
| 2026-08-22 | **`POST /ask` 신설** (`AnswerService`) | 재료를 받아 LLM 으로 답변을 쓰고 `evidence_id` 화이트리스트로 검증한다. 인젝션 방어는 구조적 방어만, 실패 시 200+고정문구로 성공과 구별 |
| 2026-08-22 | **회귀 평가셋 20 케이스 신설** (`tests/search/eval/`) | 검색 분기를 한 번에 훑는 것이 없었다. 18 PASS · 2 FAIL(§4-5·§4-6) |
| 2026-08-22 | **방향 필터가 걸릴 때 미리 자르지 않도록 수정** | 「삼성전자가 납품하는 기업」이 51건 중 2건만 났다 |
| 2026-08-22 | **Dockerfile 수정** — `search/` COPY 추가 · `COPY data/` 삭제 | 운영 이미지가 **빌드조차 실패**했다. 고쳐도 `search/` 가 없어 `/retrieve` 가 `ModuleNotFoundError` 로 죽었다 |
| 2026-08-22 | **AnchorExtractor 에 Kiwi 도입** · `best_candidate_match()` → `match_candidates()` | 조사 잔여물 「일이」를 실존 법인으로 오인했다. 정확도 22.0% → 97.2% |
| 2026-08-22 | **`alias_exact_match()` 2차 창구 신설** | `similarity('NAVER','네이버')=0.000` — pg_trgm 으로는 원리적으로 못 잇는다 |
| 2026-08-20 | **워크스페이스를 hard filter → 랭킹 문맥으로** | 바깥 기업·사건·인물·기관·제품이 후보에서 통째로 사라졌다 |
| 2026-08-20 | **`/retrieve` 실물화** · `X-Stub` 제거 · `/search/nl` 제거 | Search Layer 는 `RetrieveService` 를 통해서만 노출 |
| 2026-08-20 | `RetrieveService` · `factory.build_orchestrator()` 신설 | 조립이 세 곳에 중복돼 있었다 |
| 2026-08-20 | `SearchRelation` 타입화 + `edge_id` 보존 · `evidence_ids`(복수) 처리 | dict 면 `edge_id` 가 조용히 빌 수 있다. 단수만 옮겨 근거가 빠지고 있었다(200건 중 54건) |
| 2026-08-19 | `score` → `rank`·`rrf_score`·`source_score` 분리 | RRF 1위 0.0164 를 「신뢰도 1.6%」로 읽는 문제 |
| 2026-08-19 | `entity_types`·`filters` 제거 · `SearchMode.HYBRID` 제거 | 읽는 코드가 0곳인 죽은 필드·값 |
| 2026-08-19 | `edge_types` 요청값이 QueryRouter 추론보다 우선 | 챗봇 탐색 프로파일이 엣지를 직접 지정해야 한다 |
| 2026-08-19 | AnchorExtractor 를 모든 분기에 적용 · 의미검색 모집단을 `has_profile` 로 한정 | 「삼성전자 관련 뉴스」가 해소에 실패했다. 이름뿐인 문서가 변별력을 떨어뜨렸다 |

---

## 13. 테스트 · 개발 환경

### 13-1. 원칙

실제 Docker PostgreSQL/Neo4j/ChromaDB 대상입니다(**mock 없음**). 순수 로직만 in-memory
객체로 단위 테스트하고, **호출 계약**(「limit 이 항상 전달되는가」)을 볼 때만 예외적으로
`monkeypatch` 를 씁니다.

```text
487개  (485 passed · 2 xfailed)
├─ tests/search/      Search Layer              296
│   └─ eval/           회귀 평가셋                30   ← 20 케이스 + 심층 판정 10
├─ tests/services/     graph_service ·           
│                      RetrieveService ·         
│                      AnswerService · API
└─ tests/pipeline/     token_overlap
```

★`tests/services/test_graph_service.py` 는 **프로덕션 Cypher 의 안전망**입니다.
`tests/search/service/test_graph_searcher.py` 는 `relations_of` 를 monkeypatch 로 통째
대체하므로 **Cypher 변경을 감지하지 못합니다.** `graph_service` 를 고칠 때는 이 파일을
먼저 보세요.

### 13-2. ★실행 환경 — 매번 걸리는 것

이 프로젝트의 `.venv` 는 **Windows 네이티브 Python** 이라 WSL 에서 Docker DB 에 붙으면
TCP 는 연결되나 프로토콜 핸드셰이크에서 리셋됩니다. **WSL 전용 venv 를 씁니다.**

```bash
uv venv .venv-wsl --python 3.10
uv pip install --python .venv-wsl/bin/python -r requirements.txt pytest
.venv-wsl/bin/python -m pytest tests/ -q
```

★`kiwipiepy` 는 모델(`kiwipiepy_model`)이 함께 딸려 와 **설치 후 105MB** 를 씁니다.
`manylinux2014_aarch64` 휠이 있어 ARM(t4g) 서버에서도 컴파일 없이 설치됩니다.

Docker Desktop(WSL2) 포트포워딩이 불안정하면 — 컨테이너는 healthy 인데 연결이 리셋되면
코드가 아니라 **프록시 문제**입니다.

```bash
docker restart biznode-postgres
docker restart biznode-neo4j
```

### 13-3. 운영 이미지 검증 (2026-08-22)

```bash
docker build -t biznode-api:test .
docker run -d --network biznode-ai-data_default --env-file .env \
  -e NEO4J_URI=bolt://neo4j:7687 -e POSTGRES_HOST=postgres \
  -e CHROMA_HOST=chroma -e CHROMA_PORT=8000 -p 18100:8100 biznode-api:test
curl -X POST localhost:18100/retrieve -H 'Content-Type: application/json' \
  -d '{"question":"SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"}'
```

실측 — `HTTP 200 · 2.0초 · 기업 1(SK하이닉스) · 사건 69 · 관계 10 · 파급 249 · 근거 152`.

★**`POST /ask` 는 아직 운영 이미지에서 검증되지 않았습니다** → [§6-3](#6-3-병행-가능한-독립-작업).

### 13-4. 손으로 한 번 돌려보기

```bash
.venv-wsl/bin/python run_test.py     # SearchOrchestrator 를 직접 호출해 결과 출력
uvicorn app.api.main:app --reload    # /docs 에서 POST /retrieve · /ask Try it out
```

### 13-5. 회귀 평가셋 (검색)

검색 분기를 한 번에 훑습니다. 케이스는 `tests/search/eval/cases.py`, 판정은
`tests/search/eval/test_search_eval.py`, 결과 문서는
[평가셋](BizNode_Search_Layer_평가셋.md) 입니다.

```bash
.venv-wsl/bin/python -m pytest tests/search/eval -q          # 평가셋만 (약 16초)
.venv-wsl/bin/python -m pytest tests/search/eval -q -rA      # 케이스별 판정까지
.venv-wsl/bin/python -m tests.search.eval.report \
    -o docs/BizNode_Search_Layer_평가셋.md                   # 결과 문서 다시 만들기
```

**기업명을 못 박는 케이스와 구조 조건만 보는 케이스를 가릅니다**(`EvalCase.kind`).
관계 점수·임베딩 유사도는 데이터가 늘면 순위가 바뀌므로, 이름 해소가 답 그 자체인
케이스와 랭킹 정책을 증명해야 하는 케이스에서만 기업을 고정합니다.

### 13-6. claim 분포 수집 (챗봇)

**실제 OpenAI 호출이 들어가 비용이 듭니다.** 대표 질문 20개로 claim 겹침 분포를 뽑습니다 —
**판정하지 않고 점수만 늘어놓습니다.**

```bash
.venv-wsl/bin/python -m batch.audit.claim_grounding              # 20개 전부
.venv-wsl/bin/python -m batch.audit.claim_grounding --limit 5    # 앞 5개만
```

질문 세트는 모드(NAME/RELATIONSHIP/SEMANTIC)·사건 유형·기업 수·재료 없음까지 고르게
섞었습니다 — **한쪽만 보면 분포가 거짓말을 합니다.**
