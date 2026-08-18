# BizNode 검색·챗봇 작업 계획

> 이 문서는 **「검색 로직과 챗봇을 맡은 사람이 무엇을 어떤 순서로 하나」**입니다.
>
> 검색 엔진의 설계 근거는 [Search Layer 설계](BizNode_Search_Layer_설계.md),
> 구현 현황은 [Search Layer 현황서](BizNode_Search_Layer_현황서.md),
> 백엔드와의 계약은 [백엔드 연동 가이드](BizNode_백엔드_연동_가이드.md),
> 전체 일정은 [연동 계획](BizNode_연동_계획.md)을 보세요.

작성 2026-08-19 · 담당 범위 **Search Layer 노출 + 챗봇 재료 계층 + LLM 답변 생성**

---

## 0. 지금 어디에 있나

| 영역 | 상태 | 근거 |
|---|---|---|
| 백엔드용 데이터 API | 라우트 21개 중 **19개 실동작** · 스텁 2개 | `app/api/main.py:110` `_STUB` |
| Search Layer 엔진 | 9개 컴포넌트 **전부 완성** · 테스트 214 PASS | `search/service/*` |
| Search Layer 진입점 | **없음** — `search/api/` 디렉터리가 없다 | — |
| 캐시 | `CacheService`·`RedisRepository` 파일 없음 (컨테이너·의존성만 준비됨) | 현황서 §2 |
| 챗봇 | `/retrieve` 는 `ex.*` 고정값 · `/ask` 는 존재하지 않음 | `app/api/main.py:471` |

**남은 구멍이 정확히 내 담당 둘입니다.** 수집·그래프·연동 API 는 사실상 끝났고,
검색을 밖으로 꺼내는 일과 챗봇이 통째로 비어 있습니다.

```
데이터 수집 ──── 완료
그래프·연동 API ─ 19/21 완료
검색 엔진 ────── 완료          ← 부를 방법이 없음
검색 API ─────── 없음    ★
챗봇 재료 ────── 스텁    ★
LLM 답변 ─────── 없음    ★
```

---

## 1. 착수 전에 정해야 할 것 넷

현황서 §5-1 이 「API 착수 전 결정 필요」로 못 박은 둘에, 문서 대조에서 나온 둘을 더합니다.

### D1 · Search Layer HTTP 를 어디에 붙이나 ★

설계 §7 은 `search/api/search_controller.py` 와 **별도 FastAPI 진입점**을 전제로
쓰였습니다. 그 문서가 작성된 뒤 `app/api/main.py` 가 실제 앱으로 자리를 잡았습니다.
지금 설계대로 만들면 **앱이 둘**이 되고 백엔드는 `/docs` 를 두 곳 봐야 합니다.

이름도 겹칩니다.

```
GET  /search        이름 부분일치 · 이미 실동작 (app/services/search_service.py)
POST /api/search    자연어 질의 · 미노출 (SearchOrchestrator)
```

| | 장점 | 주의 |
|---|---|---|
| **A) `main.py` 에 APIRouter 로 mount** | `/docs` 하나 · CORS·미들웨어 재사용 · 배포 1벌 | 경로·태그를 갈라야 `GET /search` 와 안 섞임 |
| B) 별도 앱 | 관심사 분리 | 포트 2개 · 배포 2벌 · 백엔드가 문서를 두 곳 봄 |

**권장은 A** 입니다. 태그를 「검색(자연어)」로 갈라 두고 경로도 `GET /search` 와
구분되게 둡니다.

### D2 · `score` 를 어떻게 노출하나

RRF 순위값이라 **1위가 0.0164** 입니다. 그대로 내보내면 프론트가
「신뢰도 1.6%」로 읽습니다.

```
① rank 로 바꿔 노출          ② 원점수를 별도 필드로 병기          ③ 그대로 노출 + 문서화
```

### D3 · 무시되는 요청 필드 넷

`SearchRequest` 의 `edge_types`·`entity_types`·`filters` 는 `SearchQuery` 에
담기기만 하고 어떤 Searcher 도 쓰지 않습니다. `include_evidence` 는 값과 무관하게
**항상** evidence 가 채워집니다.

```
지원한다  /  필드에서 뺀다  /  무시된다고 문서화한다     ← 셋 중 하나
```

**계약 확정 전이 제일 쌉니다.** 연동 가이드 §10 도 「필드 이름은 지금 바꾸는 게
싸다」고 적고 있습니다.

### D4 · 챗봇 라우트 이름 — 문서끼리 어긋남

연동 계획 4단계는 「`POST /ask` 라우트는 추론 담당이 작성」이라 했는데, 실제 코드엔
`/retrieve` 만 있고 연동 가이드는 `/retrieve` 를 챗봇 스텁으로 소개합니다.
**둘은 역할이 다르고 둘 다 필요합니다.**

| 라우트 | 돌려주는 것 | 상태 |
|---|---|---|
| `POST /retrieve` | **재료만** — 답변 문장을 만들지 않는다 | 계약 확정됨 (`RetrieveResponse`) · 스텁 |
| `POST /ask` | 재료 + **LLM 답변** + 근거 id | 없음 — 내가 만들 것 |

이걸 명시해야 백엔드가 무엇을 부를지 압니다.

---

## 2. Phase A — 검색 로직을 밖으로 꺼낸다

| # | 작업 | 끝났다는 검증 |
|---|---|---|
| A1 | **SearchController** — D1 결정대로 라우터 작성, `SearchOrchestrator.search()` 를 감싼다 | `/docs` 에서 설계 §6-1 대표 질의 5개 Try it out 통과 |
| A2 | 에러 → HTTP status 매핑 | 422·400·503·504 각각 재현 · 0건은 200 (`hits: []`) |
| A3 | **AnchorExtractor 를 NAME/SEMANTIC 분기에도 적용** | 「삼성전자 관련 뉴스」가 NAME 으로 해소되는지 |
| A4 | `SearchMode.HYBRID` — 쓸지 enum 에서 뺄지 결정 후 반영 | 죽은 값이 남지 않음 |
| A5 | `tests/search/test_example_queries.py` 낡은 기대값 수정 (query2·query5) | 214개 전부 PASS 유지 |
| A6 | **company 임베딩 변별력 가설 검증** ★ | 아래 참고 |
| A7 | CacheService + RedisRepository | 후순위 — 트래픽이 없으면 효용을 못 잰다 |

### A3 이 필요한 이유 (현황서 §5-2)

`edge_types` 가 비어 있으면서 문장 속에 기업명이 파묻힌 질의는 원문 전체가
`EntityResolver.resolve()` 로 넘어가 **해소에 실패**합니다.

```
"삼성전자에 납품하는 기업"   edge_types 있음 → AnchorExtractor 적용   ○
"삼성전자 관련 뉴스"        edge_types 없음 → 원문 전체가 그대로 감  ×
```

### A6 이 제일 위험합니다 (현황서 §5-4)

실측에서 「삼성전자에 납품하는 기업」을 VectorSearcher 에 넣으면 상위 10건이 전부
삼성전자 자기 계열사·판매법인이고 **실제 공급사는 0건**이었습니다. 「HBM 을 만드는
기업」에서도 무관해 보이는 결과가 0.75~0.80 대로 섞여 나왔습니다.

**company 프로필 문서가 템플릿화돼 임베딩이 서로 비슷해졌다**는 가설이 있으나
검증되지 않았습니다.

> 이게 Phase C 에 직결됩니다. 의미검색이 무관한 기업을 재료로 내보내면 챗봇이
> **틀린 답에 근거까지 붙여** 말합니다. 프롬프팅 전에 재료 품질의 하한을 먼저 재야
> 합니다. 컷오프 도입 여부도 이 검증 뒤에나 결정할 수 있습니다.

---

## 3. Phase B — 챗봇 재료 계층 (`retrieve` 실물화)

[데이터수집 방법서 §21 「추론 계층 — 남은 과제」](BizNode_데이터수집_방법서.md)의 네 항목이
그대로 이 단계입니다.

| # | 작업 | 내용 |
|---|---|---|
| B1 | **NL → 탐색 프로파일 변환** (§21 ①) | QueryRouter 가 지금은 관계 키워드 12종만 본다. **사건·리스크 축**을 추가 |
| B2 | `retrieve()` 서비스 구현 | Search Layer 결과 + `events_of` + `propagate_risk` + 근거 원문을 조합 |
| B3 | **사건 단위 중복 제거** (§21 ②) | 같은 Event 에 엣지가 여러 개 붙어 같은 사건이 두 번 조회된다. 엣지가 아니라 **Event 노드 기준**으로 묶는다 |
| B4 | 파급 신뢰도 문턱 (③) · 심각도 점수 (④) | 아래 참고 |
| B5 | `/retrieve` 스텁 교체 + `main.py` `_STUB` 에서 제거 | **`X-Stub` 헤더가 사라지는 게 완료 신호** |

### B1 — 무엇을 추가하나

```
"생산 차질"   → event_type ∈ {사고재해, 공급망}   · 엣지 SUPPLIES_TO / DEPENDS_ON
"규제 리스크"  → event_type ∈ {규제수사, 분쟁소송}  · 엣지 SUES / REGULATES
```

> **선행 확인** — QueryRouter 의 저신뢰 9종은 대표 키워드 1개씩만 등록돼 있고
> **실데이터 정확도가 검증되지 않았습니다**(현황서 §5-4). 사건 축을 얹기 전에
> 이 9종부터 재는 것이 순서입니다.

### B2 — 채울 모양은 이미 확정돼 있다

`app/api/schemas.py` 의 `RetrieveResponse` 가 계약입니다. 새로 정하지 않습니다.

```
question · companies · events · relations · propagation · evidence
                                                          ↑
                              **인용에 쓸 원문.** 답변에 근거 id 를 반드시 붙인다
```

### B4 — 맞바꿔야 하는 것

```
파급 신뢰도 문턱   corroboration >= 2 가 필요하나 근거 2건 이상이 5% 뿐이다
                 → 회수량과 맞바꿔야 한다. 문턱을 올리면 답이 비고, 내리면 잡음이 섞인다

심각도 점수       is_risk 는 있으나 정도가 없다 — 화재와 전략적 연기가 같은 무게다
                 심각도 = is_risk x 유형가중 x log(근거 수) x 신선도
```

### 다시 만들지 말 것

| 기능 | 위치 |
|---|---|
| 신선도 판정 | `pipeline/freshness.py` |
| 신선도 적용 조회 | `app/services/graph_service.relations_of()` |
| 리스크 파급 계산 | `app/services/graph_service.propagate_risk()` |
| 사건 유형·리스크 | Event 의 `event_type`·`is_risk` |
| 근거 회수·의미검색 | ChromaDB `evidence` 컬렉션 |
| LLM 호출 | `pipeline/llm.py` — 스키마 강제 + **실패를 통과와 구별** |

---

## 4. Phase C — LLM 답변 생성 · 프롬프팅

| # | 작업 | 지켜야 할 규약 |
|---|---|---|
| C1 | 질문 분류기 | 질문 유형 → 어떤 retrieve 프로파일을 부를지. B1 과 짝 |
| C2 | **프롬프트 인젝션 방어** | 설계 §6 — evidence 는 **항상 「인용할 데이터」**로 취급하고 시스템 지시문과 섞지 않는다 |
| C3 | **근거 id 강제** | 연동 계획 4단계 규약 — 답변에 `evidence_id` 를 반드시 함께 반환 |
| C4 | **`stated` 를 갈라 말한다** | 아래 참고 |
| C5 | **`freshness` 를 표현한다** | `stale` 을 현재형으로 말하지 않는다 → 「2024-06 에 그렇게 보도됨」 |
| C6 | `pipeline/llm.py` 재사용 | LLM 호출 창구를 새로 만들지 않는다 |
| C7 | `POST /ask` 라우트 | D4 결정 반영 |
| C8 | **프롬프트 회귀 평가셋** | C2 와 **동시에** 만든다 |

### C2 — 왜 인젝션이 실제 표면인가

evidence 는 **뉴스 원문 인용**입니다. 기사 본문에 지시문처럼 읽히는 문장이 들어
있으면 그대로 프롬프트에 섞입니다. 설계 §6 이 이미 이 규칙을 확정해 뒀습니다.

### C4 — 섞으면 추론을 사실로 파는 것이 된다

연동 가이드 §5-⑤ 에 실측이 있습니다.

```
모트라스 파업 파급 124곳     보도 10곳 (stated=true) + 계산 114곳 (stated=false)
```

`stated=true` 는 기사가 「이 회사가 영향받는다」고 **직접 말한 것**이고,
`false` 는 우리가 공급망을 타고 **계산한 것**입니다. 답변 문장에서 섞으면
「기아가 영향받는다」(보도)와 「테라파워가 영향받는다」(2홉 추론)가 같은 무게로
읽힙니다.

`path` 를 함께 보여 주면 사용자가 되짚을 수 있습니다.

```
모트라스 파업 → IMPACTS(negative) → 현대차 → SUPPLIES_TO(공급 차질) → 현대차증권
```

### C5 — 신선도별로 말투가 달라야 한다

```
current   최근 관측       현재형으로 말해도 된다
stale     오래됨          「2024-06 에 그렇게 보도됨」 — 지우지도, 현재형으로 말하지도 않는다
expired   종료 확인       응답에 애초에 나오지 않는다
unknown   날짜 없음       단정하지 않는다
```

### C8 — 마지막이 아니라 C2 와 함께 만든다

방법서에 이미 같은 교훈이 기록돼 있습니다 — **「프롬프트가 한쪽으로 쏠린 채 전량을
돌리면 늦다」**. 그래서 수집 파이프라인은 회귀확인(`audit.selftest`)을 맨 앞에 둡니다.
챗봇도 같은 함정이라 같은 순서를 씁니다.

---

## 5. Phase D — 마감

```
현황서 §2 표 갱신          🔴 → ✅ · §5 미해결 이슈 정리
연동 가이드 §10 갱신       「스텁 둘」에서 /retrieve 항목 해소
연동 가이드 §9 라우트 표    자연어 검색 · /ask 추가
백엔드에 계약 변경 통보     D2·D3 결정 결과
```

---

## 6. 의존 관계

```
D1·D2·D3 결정 ──→ A1 A2 ──┐
                          ├──→ B1 B2 B3 B4 ──→ B5 ──→ C1..C8 ──→ D
A6 (임베딩 검증) ─────────┘         ↑
                                D4 결정

A3 A4 A5 A7  ── 독립. 언제든
```

**A6 과 B1 은 A1 을 기다리지 않습니다.** 파이썬에서 `SearchOrchestrator.search()` 를
직접 부르면 되므로, D1~D3 결정이 늦어지면 이쪽을 먼저 잡는 편이 낫습니다.

---

## 7. 지금 바로 할 것

```
1  D1~D4 를 팀에 올린다
   ★D1 은 백엔드가 /docs 를 어디서 볼지 정하는 문제라 혼자 못 정한다

2  A6 실측을 시작한다
   결정 대기 중에도 진행 가능하고, 챗봇 품질의 하한을 여기서 알게 된다
```

**가장 위험한 항목은 A6 입니다.** 의미검색이 실제 공급사를 0건 맞혔다는 게 이미
실측돼 있는데 원인이 안 밝혀진 채로 챗봇을 얹으면 **근거까지 붙은 틀린 답**이
나갑니다.

---

## 8. 테스트 환경 메모

현황서 §6 에 있는 내용이지만 매번 걸리므로 다시 적습니다.

```bash
# .venv 는 Windows 네이티브 Python 이라 WSL 에서 Docker DB 에 붙으면
# TCP 는 연결되나 프로토콜 핸드셰이크에서 리셋된다. WSL 전용 venv 를 쓴다
uv venv .venv-wsl --python 3.10
uv pip install --python .venv-wsl/bin/python -r requirements.txt pytest
.venv-wsl/bin/python -m pytest tests/

# Docker Desktop(WSL2) 포트포워딩이 불안정하면
docker restart biznode-postgres
docker restart biznode-neo4j
```
