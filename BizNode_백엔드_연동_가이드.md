# BizNode 데이터 API — 백엔드 연동 가이드

> 이 문서는 **「어떻게 붙이나」**입니다. 「왜 이렇게 설계했나」는
> [연동 계획](BizNode_연동_계획.md), 데이터 구조는 [ERD](BizNode_데이터베이스_ERD.md)를 보세요.
>
> **라우트별 상세 명세는 이 문서에 없습니다.** `/docs`가 코드에서 자동 생성되므로
> 문서로 옮겨 적으면 반드시 어긋납니다. 여기에는 `/docs`가 알려줄 수 없는 것만 씁니다.

작성 2026-08-18 · 라우트 21개 (실제 19 · 스텁 2)

---

## 1. 5분 안에 붙이기

```bash
# 1) DB 셋을 띄운다 (Neo4j 7687 · PostgreSQL 5432 · ChromaDB 8001)
docker compose up -d

# 2) API 를 띄운다
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --port 8100
```

> `python -m uvicorn` 으로 실행하면 시스템 파이썬을 잡아 `psycopg` 를 못 찾습니다.
> **반드시 `.venv` 의 파이썬**을 쓰세요.

```
http://localhost:8100/docs        라우트 21개 · Try it out 으로 바로 호출
http://localhost:8100/preview     응답이 화면에서 어떻게 보이는지
http://localhost:8100/openapi.json  클라이언트 자동 생성용
```

`/preview`는 프론트엔드가 아니라 **응답 확인용 도구**입니다. 검색 · 기업 상세 ·
워크스페이스 · 인사이트 네 탭이 있고, 실제 응답으로 그립니다. 「이 필드가 화면에서
어떻게 보이나」를 눈으로 확인할 때 쓰세요.

### 환경 변수

`.env.example`을 복사해 `.env`를 만들고 **본인 키를 넣으세요.**
`.env`는 절대 공유·커밋하지 않습니다.

```
NEO4J_URI · NEO4J_USER · NEO4J_PASSWORD
POSTGRES_HOST · POSTGRES_PORT · POSTGRES_DB · POSTGRES_USER · POSTGRES_PASSWORD
CHROMA_HOST · CHROMA_PORT
DART_KEY · OPENAI_API_KEY · NAVER_CLIENT_ID · NAVER_CLIENT_SECRET   ← 수집용, 조회에는 불필요
```

조회만 할 거면 **DART·OpenAI·NAVER 키는 없어도 됩니다.**

---

## 2. 경계 — 무엇이 누구 것인가

이게 이 연동에서 제일 중요합니다.

| | AI-Data (이 API) | 백엔드 |
|---|---|---|
| 아는 것 | 노드 키, 관계, 근거, 온톨로지 | 사용자, 세션, 워크스페이스 소유권 |
| 모르는 것 | **누가 로그인했는지 모릅니다** | Cypher, 엣지 12종, 근거 검증, 신선도 |
| 저장 | 그래프·재무·시세·기사 | 계정 · 워크스페이스 · 보관함 · 알림 구독 |

**「사용자가 어느 기업을 담아 뒀나」는 그래프가 아니라 사용자 데이터입니다.**
백엔드 DB에 두고 이 API에는 **키 목록만** 넘기세요.

```
POST /workspace/graph   { "keys": ["01095722", "00164779"] }
                          ↑ 백엔드가 자기 DB 에서 꺼내 보낸 목록
```

### 경계가 깨지는 신호 둘

```
백엔드에 Cypher 가 등장한다        → 조회 함수로 옮겨야 합니다
이 API 가 user_id 를 받기 시작한다  → 백엔드가 해야 할 일을 넘긴 것입니다
```

---

## 3. 키 규칙 — 이름을 키로 쓰지 않습니다

DART 법인 명부 118,535건 중 **11.3%가 동명**입니다(「신우」 138건). 그래서
`GET /companies/{이름}` 같은 주소는 만들지 않았습니다.

```
1  GET /search?q=삼성          →  hits[].key 를 고른다
2  그 key 를 이후 모든 조회에 쓴다
```

`key`는 두 가지입니다.

```
corp_code   "00126380"        DART 에 등록된 회사 (8자리)
norm_name   "엔비디아"          corp_code 가 없는 회사 (정규화된 이름)
```

둘 다 URL 경로에 그대로 넣으면 됩니다. 한글도 됩니다(URL 인코딩만 하세요).

---

## 4. 화면별 호출 순서

### 홈

```
GET  /search?q=...                      검색창
POST /insights  {keys: [...]}           인사이트 카드 (워크스페이스가 있을 때)
```

「최근 활동」은 백엔드 것입니다(사용자 데이터).

### 워크스페이스 — 핵심 화면

```
POST /workspace/graph    {keys, refs, max_nodes}    캔버스
POST /workspace/suggest  {keys}                     같이 담을 기업 추천
POST /workspace/summary  {key, workspace_keys}      ← 노드를 클릭했을 때
GET  /relations/{edge_id}                           ← 선을 클릭했을 때
POST /workspace/changes  {keys, since}              알림
```

**노드 클릭과 선 클릭이 서로 다른 라우트**입니다. 좌 패널이 그에 따라 바뀝니다.

기업을 검색해 담는 흐름에서 **2번은 백엔드 몫**입니다.

```
1  GET  /search                고를 key 를 얻는다
2  백엔드가 자기 DB 에 저장       ← 우리는 누구의 워크스페이스인지 모릅니다
3  POST /workspace/suggest      다음에 담을 것
4  POST /workspace/graph        다시 그린다
```

### 기업 상세 — **한 번만 부르면 됩니다**

```
GET /companies/{key}
```

목업 4-3의 블록이 **전부 이 응답 안에** 있습니다 — 시장 · 재무 3개년 · 사업 개요 ·
사업부문 · **관계 그래프** · 연관 기업 · 사건 · 지배구조 · 제품 · 인물 · 뉴스 · 공시.

목록은 **블록마다 10건까지**입니다(관계 그래프는 60). 「더보기」를 누르면 그때
서브 라우트를 부릅니다.

```
GET /companies/{key}/events        사건 전체        (삼성전자 148건)
GET /companies/{key}/relations     관계 전체        (526건)
GET /companies/{key}/products      제품 전체        (152건)
GET /companies/{key}/ownership     지배구조 전체     (자회사 157곳)
GET /companies/{key}/executives    임원 전체
GET /companies/{key}/news?limit=   기사 전체        (583건)
GET /companies/{key}/filings       공시 전체
GET /companies/{key}/graph?depth=  그래프 다시 그리기
```

**차트만 예외입니다.** 상세에는 최신 시세 한 건만 있고 시계열이 없습니다.

```
GET /companies/{key}/market?days=30    latest + series
```

### 사건을 클릭했을 때

```
GET /events/{event_id}/impact
```

### 뉴스 / 이슈

**이 화면은 외부 뉴스 API로 붙이기로 했습니다.** `/news`는 스텁으로 남아 있습니다.

단, `/companies/{key}/news`는 **다른 것**입니다 — 「이 관계의 근거가 된 기사」라
우리만 갖고 있고 외부 API로 대체할 수 없습니다.

### 리서치 보관함 · 마이페이지

**필요한 함수 없음.** 백엔드 소관입니다(스냅샷 저장·조회는 사용자 데이터).

---

## 5. 반드시 다뤄야 하는 것 여섯

`/docs`에도 적혀 있지만, 놓치면 화면이 틀리게 나오는 것들입니다.

### ① `in_graph = false` — 「없는 회사」가 아닙니다

```json
{ "key": "한화오션엔지니어링", "in_graph": false }
```

**실재하지만 우리가 아직 수집하지 않은 회사**입니다. DART 명부 118,535곳 중
우리가 모은 건 3,432곳입니다.

```
화면    「수집되지 않은 기업입니다」
금지    「검색 결과가 없습니다」 · 관계·재무 조회를 거는 것
```

### ② `detail_level = relations_only` — 재무가 없는 게 정상입니다

```
전체 3,432곳 중   재무 477 (14%) · 시세 417 (12%) · 공시 64 (1.9%)

원시 시세는 427종목이지만 시총·PER 은 재무와 이어져야 나오므로 417곳입니다.
```

나머지 2,900여 곳은 이름·업종·관계만 있습니다. **오류로 처리하면 안 됩니다.**
블록별로는 `blocks`를 보세요 — 「재무는 있는데 공시가 없다」는 `detail_level`
하나로 표현이 안 됩니다.

### ③ `counts` vs 목록 길이 — 배열 길이를 세지 마세요

상세의 목록은 10건에서 잘립니다.

```json
"counts": { "events": 148, "relations": 1169, "news": 583 },
"events": [ ...10건... ]
```

```
화면    「148건 중 10건」
금지    len(events) 로 「4건」이라고 쓰는 것
```

### ④ `islands` — 억지로 잇지 않습니다

워크스페이스에 담았는데 다른 기업들과 관계가 없는 곳입니다.

```
화면    「이 회사는 담긴 다른 회사들과 직접 연결이 없습니다」
```

허브(삼성전자 등)를 다리로 쓰면 거의 모든 쌍이 이어져 **의미가 0인 그래프**가
되므로 쓰지 않습니다. 없는 관계를 그리는 것보다 없다고 말하는 게 낫습니다.

### ⑤ `stated` — 보도와 계산을 **반드시 갈라 그리세요**

`/events/{id}/impact` · `/relations/{edge_id}` · `/insights`에 나옵니다.

```
stated = true    기사가 「이 회사가 영향받는다」고 직접 말했다
stated = false   우리가 공급망을 타고 계산했다
```

실측(모트라스 파업): 124곳 중 **보도 10곳 + 계산 114곳**. 섞어서 그리면
「기아가 영향받는다」(보도)와 「테라파워가 영향받는다」(2홉 추론)가 같은 무게로
읽힙니다. **추론을 사실로 파는 셈입니다.**

`path`를 그대로 보여 주면 사용자가 되짚을 수 있습니다.

```
모트라스 파업 → IMPACTS(negative) → 현대차 → SUPPLIES_TO(공급 차질) → 현대차증권
```

### ⑥ `freshness` — 오래된 관계를 지우지 않습니다

```
current   최근 관측         가중치 1.0
stale     오래됨            0.6      ← 지우지 말고 「2024-06에 그렇게 보도됨」으로
expired   종료 확인          0.3      ← 응답에 아예 안 나옵니다
unknown   날짜 없음          0.7
```

뉴스는 관계의 **시작만 보도하고 종료는 보도하지 않습니다.** 그래서 오래됐다고
지우면 살아 있는 관계를 잃고, 그대로 두면 끝난 관계를 현재형으로 말하게 됩니다.

---

## 6. 단위 — 한 곳에서만 정합니다

```
금액   원 (int)              억·백만으로 접지 않습니다. 화면이 정하세요
비율   퍼센트 (float 0~100)   0~1 소수가 아닙니다
날짜   ISO 8601 (str)        "2026-08-14"
```

**시가총액·PER·PBR·PSR은 저장하지 않고 조회할 때 계산합니다.** 그래서
`fin_year`·`fs_div`가 값과 함께 나갑니다 — 화면이 「2025년 연결 기준」이라고
밝힐 수 있습니다. 적자면 `per`이 `null`입니다(음수 PER을 만들지 않습니다).

---

## 7. 오류 처리

| 상황 | 코드 | 응답 |
|---|---|---|
| 없는 기업 | `404` | `{"detail": "해당 키의 기업이 없습니다"}` |
| 없는 관계 | `404` | 검증에서 제외됐거나 종료된 관계일 수 있습니다 |
| 없는 사건 | `404` | |
| 근거 원문을 못 꺼냄 | `503` | ChromaDB 가 안 떠 있습니다 |
| 요청 형식 오류 | `422` | FastAPI 기본 |

**`/relations/{edge_id}`가 `503`을 내는 이유** — 근거 없는 관계는 애초에 응답에서
빠집니다. 그러니 `evidence: []`를 주면 「근거가 없는 관계」로 읽힙니다. 사실은
「우리가 못 꺼냈다」인데요. 거짓말하지 않고 503을 냅니다.

**빈 결과는 오류가 아닙니다.**

```
GET  /search?q=              200  hits: []
POST /insights  keys 1곳      200  []        ← 「겹친다」가 성립하지 않습니다
```

---

## 8. 성능 — 캐시가 필요한 곳

2회째 응답 시간입니다(로컬, 데워진 상태).

```
/search                       238ms
/workspace/graph  4곳          332ms
/events/{id}/impact           319ms
/companies/{key}  심텍          492ms     ← 보통 기업
/companies/{key}/graph        529ms
/companies/{key}  삼성전자       985ms     ← 최악값
/insights  4곳               1,521ms    ← 제일 느립니다
```

**느린 이유는 노드 수가 아니라 읽는 양입니다.** 삼성전자는 관계가 1,169개로
DB에서 가장 큰 노드고(LG전자 599 · SK하이닉스 494 · 중앙값 80 안팎), 그
속성 뭉치를 전부 꺼내 근거·신선도를 판정합니다.

```
캐시 권장   /insights            워크스페이스가 안 바뀌면 결과가 안 바뀝니다
            /companies/{key}     상세 페이지는 자주 다시 열립니다
캐시 무효   배치 재적재 후 (야간)
```

`/insights`는 홈 화면에서 매번 부르면 부담이 됩니다. **백엔드에서 워크스페이스
단위로 캐시**하고, 기업을 담거나 뺄 때만 무효화하는 것을 권합니다.

---

## 9. 라우트 21개

상세는 `/docs`를 보세요. 여기는 지도입니다.

| 태그 | 라우트 | 비고 |
|---|---|---|
| 검색 | `GET /search` | 부분 일치 · 명부 포함 |
| 기업 | `GET /companies/{key}` | **페이지 한 방** |
| | `GET /companies/{key}/graph` | `depth`·`max_nodes` 조절용 |
| | `GET /companies/{key}/market` | **차트는 이것만** (상세엔 series 없음) |
| | `GET /companies/{key}/events` | 더보기 |
| | `GET /companies/{key}/news` | 근거가 된 기사 · 더보기 |
| | `GET /companies/{key}/filings` | 더보기 |
| | `GET /companies/{key}/products` | 더보기 |
| | `GET /companies/{key}/executives` | 더보기 |
| | `GET /companies/{key}/ownership` | 양방향 · 더보기 |
| | `GET /companies/{key}/relations` | 더보기 |
| 관계 | `GET /relations/{edge_id}` | **근거 원문** + 리스크 전파 |
| | `GET /events/{event_id}/impact` | 사건이 어디까지 번지나 |
| 워크스페이스 | `POST /workspace/graph` | 캔버스 |
| | `POST /workspace/summary` | 노드 클릭 |
| | `POST /workspace/suggest` | 담을 기업 추천 |
| | `POST /workspace/changes` | 알림 |
| 홈 | `POST /insights` | 인사이트 카드 |
| 뉴스 | `GET /news` | **스텁** — 외부 API 로 대체 예정 |
| 챗봇 | `POST /retrieve` | **스텁** |
| 운영 | `GET /health` | |

**스텁은 `X-Stub: true` 헤더가 붙습니다. 헤더가 없으면 진짜입니다.**

---

## 10. 아직 안 된 것 · 결정 대기

### 스텁 둘

```
/news       외부 뉴스 API 로 대체하기로 했습니다. 붙이실 API 가 정해지면 알려 주세요
/retrieve   챗봇 재료. 추론 담당이 app/services 를 직접 import 하므로
            이 HTTP 라우트는 「백엔드가 볼 모양」입니다
```

### 언제나 빈 배열인 필드 하나

`/workspace/changes`의 `relation_ended`입니다. 「이번 재적재에서 빠진 관계 =
종료」로 판정하는데 비교 기준(`loaded_at`)이 2026-07-31에 도입돼 대상이 없습니다.
다음 DART 재적재 이후 채워집니다. **필드는 미리 열어 뒀으니 그때 계약이
바뀌지 않습니다.**

### 확인해 주셨으면 하는 것

```
1  접근 방식      A) HTTP 호출  B) 패키지 import — 지금은 A 전제입니다
2  캐시 위치      백엔드에서 하실지, 우리가 붙일지
3  필드 이름      바꿔야 할 것이 있으면 지금이 쌉니다
4  in_graph=false 기업을 워크스페이스에 담게 할지 (자료가 없어 그래프에 안 그려집니다)
5  뉴스 API       무엇을 붙일지
```

### 배포할 때

```
DB 포트 7687 · 5432 · 8001 을 인터넷에 열지 마세요
Neo4j · PostgreSQL 기본 비밀번호를 바꾸세요
.env 는 서버에 직접 두고 레포에 넣지 마세요
CORS 는 지금 localhost:3000 · 5173 만 열려 있습니다 (app/api/main.py)
```

---

## 11. 막히면

```
/docs        라우트별 상세 · Try it out
/preview     응답이 화면에서 어떻게 보이는지
/openapi.json  클라이언트 자동 생성

python -m batch.audit.api_contract    계약과 예시가 DB 와 맞는지
python -m batch.audit.api_fuzz        무작위 기업 조합으로 불변식 검사
```

응답이 이상하면 **먼저 `/preview`에서 같은 키로 확인**해 주세요. 화면 문제인지
데이터 문제인지가 그 자리에서 갈립니다.
