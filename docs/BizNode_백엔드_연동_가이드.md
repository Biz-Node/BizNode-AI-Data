# BizNode 데이터 API — 백엔드 연동 가이드

> 이 문서는 **「어떻게 붙이나」**입니다. 「왜 이렇게 설계했나」는
> [연동 계획](BizNode_연동_계획.md), 데이터 구조는 [ERD](BizNode_데이터베이스_ERD.md)를 보세요.
>
> **라우트별 상세 명세는 이 문서에 없습니다.** `/docs`가 코드에서 자동 생성되므로
> 문서로 옮겨 적으면 반드시 어긋납니다. 여기에는 `/docs`가 알려줄 수 없는 것만 씁니다.

작성 2026-08-18 · 갱신 2026-08-23 · 라우트 22개 — **전부 실물** (고정값 라우트 없음)

---

## 1. 5분 안에 붙이기

```bash
# 1) DB 셋을 띄운다 (Neo4j 7687 · PostgreSQL 5432 · ChromaDB 8001)
docker compose up -d

# 2) 가상환경 세팅 후 API 를 띄운다
python -m uvicorn app.api.main:app --port 8100 --reload
```

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

|           | AI-Data (이 API)               | 백엔드                                   |
| --------- | ------------------------------ | ---------------------------------------- |
| 아는 것   | 노드 키, 관계, 근거, 온톨로지  | 사용자, 세션, 워크스페이스 소유권        |
| 모르는 것 | **누가 로그인했는지 모릅니다** | Cypher, 엣지 12종, 근거 검증, 신선도     |
| 저장      | 그래프·재무·시세·기사          | 계정 · 워크스페이스 · 보관함 · 알림 구독 |

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

### 인사이트 카드 — 눌러서 근거까지 (2026-09-05 개정)

카드는 **한 번의 호출로 완성**됩니다. 추가 호출은 **누를 때만** 합니다.

```
POST /insights {keys, limit}            ← 카드 12장. 이 응답만으로 목록이 그려집니다
      ↓ 카드를 누르면
GET  /relations/{edge_id}               ← 기사·공시 원문 (카드의 edge_ids 개수만큼)
GET  /events/{event_id}/impact          ← 영향받는 기업 (사건 카드만)
      ↓ 기업 이름을 누르면
GET  /companies/{key}                   ← 유일한 «페이지 이동»
```

★**새 엔드포인트는 없습니다.** 카드에 `edge_ids` 가 생겨서 기존 상세 라우트로
이어지는 것뿐입니다.

#### 카드 종류 12개 — 축 다섯

| 축 | `kind` | 문안 예시 |
|---|---|---|
| 사건 | `inbound_risk` ★신설 | 레인보우로보틱스에 악재로 작용했습니다 |
| | `cascade_risk` | SK하이닉스에서 한미반도체로 번질 수 있습니다 |
| | `shared_risk` | 8곳 중 2곳이 걸려 있습니다 |
| 진행 | `event_ongoing` ★신설 | 3단계까지 진행됐습니다 |
| 예정 | `contract_expiring` ★신설 | 뉴로메카–포스코 거래가 3개월 뒤 끝납니다 |
| 구조 | `bottleneck` ★신설 | 8곳 중 4곳이 엔비디아를 통해 납품합니다 |
| | `shared_customer` · `shared_supplier` · `shared_owner` | 8곳 중 5곳이 납품합니다 — 반도체 장비 |
| | `internal_competition` · `sector_concentration` | 워크스페이스 안에서 4쌍이 서로 경쟁합니다 |
| 공백 | `no_overlap` | 이 8곳은 서로 겹치는 것이 없습니다 |

★신설 넷은 **전부 시점이 있습니다.** 기존 카드가 구조 위주라 홈이 어제와
오늘이 같았습니다. `kind` 를 모르는 값으로 받아도 죽지 않게 기본 렌더러를
두세요 — 종류는 앞으로도 늘어납니다.

#### `edge_ids` — 근거로 가는 열쇠

```jsonc
{ "kind": "bottleneck", "subject": "엔비디아",
  "headline": "8곳 중 4곳이 엔비디아를 통해 납품합니다 — TC본더 · HBM",
  "why": "마이크론, 코닝으로 가는 거래가 모두 엔비디아를 지납니다 · 거래 상대 34곳",
  "keys": ["00161383", "01105153"],      // 걸린 기업. 눌러서 기업 상세로
  "edge_ids": ["5:...:1", "5:...:2"],    // ★GET /relations/{edge_id} 에 그대로
  "event_id": null }
```

★**`edge_ids` 가 빈 카드가 있습니다** — `sector_concentration` · `no_overlap` ·
`event_ongoing`. 관계가 아니라 **분류·부재·국면**에서 나온 카드라 인용할 원문이
없습니다. 「근거 없음」이 아니라 **근거의 종류가 다른 것**이므로, 「원문이
없습니다」로 쓰지 말고 **어떻게 셌는지**를 보여 주세요:

```
sector_concentration → 표준산업분류(KSIC) 중분류로 묶어 셌습니다 · 8곳 중 8곳 일치
no_overlap           → 거래·지분·사건·업종 네 축으로 찾았고 겹치는 대상이 없습니다
cascade_risk         → 공급 관계를 따라 계산 · 점수 0.066 · 기사에 없는 추정
```

#### 출처 링크는 두 형식입니다

`evidence[].source_doc` 이 **URL 이거나 DART 접수번호**입니다. 그대로 `href` 에
넣으면 공시가 깨진 링크가 됩니다.

```js
/^\d+$/.test(v) ? `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${v}`
                : v.replace(/^news:/, '')
```

실측(2026-09-05): 엣지 10,970건 중 URL 75.6% · 접수번호 24.4%. **관계 종류마다
다릅니다** — `IMPACTS`·`HAS_EVENT` 는 URL 100%, `OWNS_STAKE_IN`(지분)은 **28%**.
지분 카드에서 바로 걸립니다.

#### 파급 경로를 **그래프에서 보기**

`Propagation.path` 는 사람이 읽는 설명이고, 같이 오는 `edge_ids` 가 **되짚을
수 있는 열쇠**입니다. `path` 는 `[노드, 관계, 노드, 관계, 노드]` 꼴이라
**홀수 자리가 관계**이고, `edge_ids` 가 그 순서로 옵니다.

```
사건 → IMPACTS(negative) → 마이크론 → SUPPLIES_TO(공급 차질) → 한미반도체
        edge_ids[0]                    edge_ids[1]
```

★**엣지 하나를 그래프에서 보려고 새 API 를 만들 필요가 없습니다.**
`POST /workspace/graph` 를 **그 엣지의 양 끝 두 곳**으로 부르면 됩니다 —
캔버스 규칙이 「담긴 기업끼리 직접 이어진 엣지는 언제나 포함」이라 반드시
그려집니다(실측: 양끝이 기업인 엣지 46/46 포함).

```js
const { relation } = await get(`/relations/${edgeId}`);
await post('/workspace/graph', { keys: [relation.source.key, relation.target.key] });
```

워크스페이스 전체 키로 부르면 **안 담깁니다**(실측 50개 중 23개). 캔버스는
담은 기업끼리만 그리므로 엔비디아·국민연금공단 같은 바깥 상대는 빠집니다.
`max_nodes` 를 올려도 소용없습니다.

★한계 — `IMPACTS`(사건→기업)는 출발이 Event 라 이 방법이 안 됩니다.
사건의 확산은 `/events/{id}/impact` 가 담당합니다.

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

```
GET /news?category=공급망&workspace_keys=...&risk_only=true
```

**축 셋을 겹쳐 쓸 수 있습니다** — 주제 / 범위 / 최신순.

```
category        공급망 · 지분 · 규제 · 사건   (없으면 전체)
workspace_keys  담은 기업 중 하나라도 걸린 기사
risk_only       사건 갈래만
```

`categories`는 **여러 개가 정상**입니다. 「공정위, 납품단가 담합 제재」는
공급망이면서 규제입니다. 하나로 고르면 화면이 절반을 놓칩니다.

**외부 뉴스 API를 쓰지 않습니다.** 축 1(주제)을 외부가 만들 수 없고, 국내는
뉴스 저작권 때문에 무료 API가 사실상 없습니다(빅카인즈는 전재·복제·배포 금지,
구글 뉴스 RSS는 개인·비상업 용도만).

기사는 **매일 `batch.ops.daily`**가 채웁니다. 자세한 설계는
[방법서 §11-2](BizNode_데이터수집_방법서.md)를 보세요.

```
① 뉴스 수집   PG     무료    매일 07:30    서비스 중 가능
② 시세       PG     무료    평일 16:00    서비스 중 가능
③ 관계 추출   Neo4j  유료    매일 02:00    야간만
④ 근거 검증   Neo4j  유료    ③ 직후        야간만
```

**①②는 PostgreSQL만 쓰므로 서비스가 안 멈춥니다.** 그래서 뉴스 화면과 주가는
낮에도 갱신됩니다. 그래프를 고치는 ③④만 야간입니다.

`/companies/{key}/news`는 **다른 것**입니다 — 「이 관계의 근거가 된 기사」입니다.

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

**워크스페이스에 담을 수 없게 막습니다.** 담아 봐야 그래프에 안 그려집니다.
막는 자리는 **프론트의 「담기」 버튼**입니다 — 이 API는 워크스페이스를
저장하지 않으므로 막을 자리가 없습니다.

그래도 섞여 들어오면 조용히 사라지지 않고 `unknown_keys`로 알려 드립니다.

```json
POST /workspace/graph  { "keys": ["00111704", "01095722", "01622599"] }

{ "nodes": [ ...2개... ], "unknown_keys": ["01622599"] }
```

화면은 「한화오션엔지니어링은 아직 수집하지 않아 그래프에 없습니다」라고
쓰면 됩니다.

### ② `detail_level` — 화면을 세 갈래로 가릅니다

**이 기업을 얼마나 수집했나**입니다. 어떻게 생긴 노드인가가 아니라
**화면이 뭘 그릴 수 있나**를 뜻합니다.

```
full       64곳    사업보고서까지 다 돌았다      상세 페이지가 꽉 찬다
partial   416곳    숫자까지 있다 (재무·시세)     공시·개요·사업부문이 없다
none    2,952곳    수집 작업을 하지 않았다       다른 기업을 수집하다 관계로 딸려온 노드
```

```
full · partial   상세 페이지 O + 좌 패널 O
none             좌 패널만 — 「아직 수집하지 않았습니다」
```

**`none`이라고 빈 노드가 아닙니다.** 관계·사건·뉴스는 있습니다.

```
엔비디아   detail_level=none   관계 59 · 블록 7/11
                              products related risk news ownership overview graph
```

좌 패널 요약은 반드시 그려야 하고, **상세 페이지 진입만** 막으면 됩니다.

> **「비상장이라서」로 설명하지 마세요.** `none`에 국내 상장사도 섞여 있습니다 —
> 「현대차」·「현대중공업」이 별칭 노드로 갈라져 여기 들어와 있습니다(데이터 정리
> 예정). 「아직 수집하지 않았습니다」가 안전합니다.

블록별 세부는 `blocks`를 보세요. 「재무는 있는데 공시가 없다」는 `detail_level`
하나로 표현이 안 됩니다.

```
전체 3,432곳 중   재무 477 (14%) · 시세 417 (12%) · 공시 64 (1.9%)

원시 시세는 427종목이지만 시총·PER 은 재무와 이어져야 나오므로 417곳입니다.
```

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

★**추정을 기본으로 펼치지 마세요.** 실측(2026-09-05, 「삼성전자 본사 압수수색」):
94곳 = 보도 2 + 추정 92이고 추정의 **점수 중앙값이 0.13**입니다. 전량을 뿌리면
목록이지 인사이트가 아닙니다. 보도는 먼저 펼치고, 추정은 접어서 상위 8곳
정도만 보여 주세요.

★`edge_ids`(2026-09-05 신설)로 **경로의 각 관계를 열 수 있습니다.** 「마이크론을
거쳐 왔다」고 해놓고 확인할 길이 없으면 사용자가 계산을 믿지 못합니다.

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

| 상황                | 코드  | 응답                                          |
| ------------------- | ----- | --------------------------------------------- |
| 없는 기업           | `404` | `{"detail": "해당 키의 기업이 없습니다"}`     |
| 없는 관계           | `404` | 검증에서 제외됐거나 종료된 관계일 수 있습니다 |
| 없는 사건           | `404` |                                               |
| 근거 원문을 못 꺼냄 | `503` | ChromaDB 가 안 떠 있습니다                    |
| 요청 형식 오류      | `422` | FastAPI 기본                                  |

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

| 태그         | 라우트                            | 비고                                   |
| ------------ | --------------------------------- | -------------------------------------- |
| 검색         | `GET /search`                     | 부분 일치 · 명부 포함                  |
| 기업         | `GET /companies/{key}`            | **페이지 한 방**                       |
|              | `GET /companies/{key}/graph`      | `depth`·`max_nodes` 조절용             |
|              | `GET /companies/{key}/market`     | **차트는 이것만** (상세엔 series 없음) |
|              | `GET /companies/{key}/events`     | 더보기                                 |
|              | `GET /companies/{key}/news`       | 근거가 된 기사 · 더보기                |
|              | `GET /companies/{key}/filings`    | 더보기                                 |
|              | `GET /companies/{key}/products`   | 더보기                                 |
|              | `GET /companies/{key}/executives` | 더보기                                 |
|              | `GET /companies/{key}/ownership`  | 양방향 · 더보기                        |
|              | `GET /companies/{key}/relations`  | 더보기                                 |
| 관계         | `GET /relations/{edge_id}`        | **근거 원문** + 리스크 전파            |
|              | `GET /events/{event_id}/impact`   | 사건이 어디까지 번지나                 |
| 워크스페이스 | `POST /workspace/graph`           | 캔버스                                 |
|              | `POST /workspace/summary`         | 노드 클릭                              |
|              | `POST /workspace/suggest`         | 담을 기업 추천                         |
|              | `POST /workspace/changes`         | 알림                                   |
| 홈           | `POST /insights`                  | 인사이트 카드                          |
| 뉴스         | `GET /news`                       | 주제·워크스페이스·최신순 3축           |
| 챗봇         | `POST /retrieve`                  | 챗봇 **재료** (문장 생성 없음)         |
|              | `POST /ask`                       | **답변 문장 + 검증된 근거**            |
| 운영         | `GET /health`                     |                                        |

**고정값을 돌려주는 라우트는 없습니다** (2026-08-23 확인).

⚠ `X-Stub: true` 헤더가 `GET /news` 하나에 아직 붙습니다. `/news` 는 실제로
PostgreSQL 을 읽으므로(실측 12,250건) **이 헤더를 실물 여부 판단에 쓰지 마세요.**
헤더를 정리할지는 아직 정하지 않았습니다.

---

## 10. 아직 안 된 것 · 결정 대기

### 스텁 없음 (2026-08-23)

`/retrieve` 는 2026-08-20 에 실물이 됐고, `POST /ask` 가 2026-08-22 에 추가됐습니다.

```
/retrieve   챗봇 재료 — question · match_type · companies · events
            · relations · propagation · evidence
/ask        답변 문장 — answer · sources[] · failed
            요청 바디는 /retrieve 와 같습니다(AskRequest). 새 이름을 만들지 않았습니다.
```

★`/ask` 에서 `workspace_keys` 는 **필수**입니다 — 그래프 안에서 인사이트를
만드는 챗봇이라 워크스페이스 없이 부를 수 없습니다. 스키마 기본값이
`default_factory=list` 라 **422 는 아니지만**, 2026-08-26 부터 **서버가 검색 전에
거부**하고 `anchor_source="unresolved"` 로 고정 문구를 돌려줍니다.

★**요청 계약은 바뀌지 않습니다** (2026-08-25 확인). `/ask` 는 계속
`{ question, workspace_keys }` 를 받고, `workspace_keys` 는 **현재 워크스페이스 기업의
`corp_code` 배열**입니다 — 요청마다 실어 보내 주세요. 워크스페이스 동기화 API 는
**만들지 않습니다.**

★**응답 필드 둘이 2026-08-26 에 추가됐습니다** —
`AskResponse.anchor_source`(`query`/`workspace`/`unresolved`)와
`RetrieveResponse.anchors[]`. 뜻은
[설계서 §14](BizNode_Search_Layer_설계.md#14-앵커-출처--무엇을-대상으로-답하는가) ·
[현황서 §3-2](BizNode_Search_Layer_현황서.md#3-2-반드시-알아야-할-계약-아홉) 를 보세요.

★`anchor_source` 가 `unresolved` 면 **`failed=false` 인데 `sources` 가 빕니다** — 서버
오류가 아니라 「그 기업을 못 찾았다」는 뜻이고, `answer` 에 대안이 담깁니다. 화면에서
`failed=true`(LLM 실패)와 **다르게** 다뤄 주세요.

★`failed=true` 면 `answer` 는 고정 문구이고 **HTTP 는 200** 입니다. `sources` 는
그대로 나가므로 화면이 「답은 못 썼지만 근거는 있다」를 보여줄 수 있습니다.

### 언제나 빈 배열인 필드 하나

`/workspace/changes`의 `relation_ended`입니다. 「이번 재적재에서 빠진 관계 =
종료」로 판정하는데 비교 기준(`loaded_at`)이 2026-07-31에 도입돼 대상이 없습니다.
다음 DART 재적재 이후 채워집니다. **필드는 미리 열어 뒀으니 그때 계약이
바뀌지 않습니다.**

### 정해진 것 (2026-08-18)

```
1  접근 방식      HTTP 호출. 배포 때 CORS 에 백엔드 도메인만 추가하면 됩니다
2  캐시 위치      백엔드. /insights(1.5초) · /companies/{key}(최대 1초)가 대상입니다
3  필드 이름      우리 API 기준으로 백엔드가 맞춥니다. 바꿀 일이 생기면 먼저 알립니다
4  명부 기업       워크스페이스에 담을 수 없게 합니다 (프론트의 「담기」 버튼에서)
5  실시간 수집     하지 않습니다 — 한 기업 40분 + 구글 차단 위험(12시간)
6  검색 범위       corp_code_master 와 그래프 전부. 정렬이 수집분을 앞에 놓습니다
```

```
7  뉴스        외부 API 를 쓰지 않습니다. 우리가 모은 기사로 3축 필터를 만듭니다
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
