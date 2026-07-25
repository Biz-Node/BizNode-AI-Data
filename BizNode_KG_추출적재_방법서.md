# BizNode 지식그래프 구축 방법서 (v1.0)

> DART + 뉴스 → 지식그래프 → Neo4j · ChromaDB · PostgreSQL
> v0.6까지의 논의를 구현 순서대로 재구성한 통합본

---

## 이 문서를 읽는 법

**3개 파트로 되어 있다.**

| Part | 내용 | 언제 보나 |
|---|---|---|
| **I. 무엇을 만드는가** | 스키마 — 노드·엣지·속성 정의 | 설계 확인할 때 |
| **II. 어디서 가져오는가** | 데이터 소스 — DART·뉴스 수집 경로 | 수집기 만들 때 |
| **III. 어떻게 쌓는가** | 파이프라인 — 처리·적재 절차 | 코드 짤 때 |

**Phase 표기**

- 🟢 **P1** — MVP 필수. 정형 데이터로 그래프를 화면에 띄우는 최소 집합
- 🟡 **P2** — 데이터가 쌓인 뒤. 뉴스 파이프라인·고급 기능

---

# Part I. 무엇을 만드는가 — 스키마

## 1. 설계 원칙 세 줄

**① 노드는 개방형, 엣지는 12종 고정, 적재 전 정규화.**
관계 표현('납품/공급/부품을 댐')이 각각 다른 엣지가 되면 다중 홉 추론 경로가 끊긴다(그래프 희소성). 엣지 타입을 유한하게 통제해야 N차 리스크 추론이 가능하다.

**② DART 먼저, 뉴스 나중.**
뉴스 개체 해소(ER)의 1단계가 `corp_code` 정확 매칭이다. DART 골격이 먼저 있어야 뉴스 개체가 "붙으면서" 들어온다. 동시에 하면 기준점이 없어 2단 작업이 되고 오류가 는다.

**③ 저장은 전부, 임베딩은 선별.**
원문은 다 보관하되 Vector DB에는 관계 근거·기업 요약만 넣는다. 전문 임베딩은 비용만 문제가 아니라 검색 정확도를 떨어뜨린다.

> 근거: Kensho(Macro-Edge 통제), Bloomberg(point-in-time), Refinitiv(PermID 마스터 식별자), Diffbot(isCurrent), FIBO(관계 계층), EDC(개방추출→사후정규화), Microsoft GraphRAG(gleaning)

---

## 2. 노드 5종 🟢P1

| 노드 | 설명 | 식별자 |
|---|---|---|
| `Company` | 기업(상장/비상장/해외) | **DART corp_code = BizNode의 PermID** |
| `Person` | 임원·주요 인물 | 이름 + 소속 |
| `Organization` | 비기업 기관(공정위·금융위 등) | 기관명 |
| `Product` | 제품·기술·부품·소재 | 정규화 명칭 |
| `Event` | 사건·이슈 | 유형 + 일자 |

**주요 결정:**
- **Technology를 Product에 흡수** — 'HBM'과 '3nm 공정'을 LLM이 일관되게 구분 못 한다. 나눠서 얻는 실익이 없다
- **Sector는 노드가 아니라 `Company.sector` 속성** — 노드로 두면 한 섹터에 수백 기업이 매달리는 슈퍼노드가 생기고, 추론 시 무관한 기업까지 리스크 영향권으로 끌려온다
- **판단 기준: "관계의 능동적 주체인가?"** — 공정위는 "규제한다"의 주체라 노드, 반도체 섹터는 "속하는" 대상이라 속성

---

## 3. 엣지 12종과 3계층 구조 🟢P1

```
Level 1 — Category (5개)     → 프론트 필터·범례용 (DB 탐색 아님)
Level 2 — Edge Type (12종)   → Cypher 탐색·다중홉 추론 (희소성 통제)
Level 3 — Subtype (확장)      → 상세 패널의 정확한 의미 표시
```

Level 2가 유한해서 탐색이 빠르고, Level 3는 늘어나도 성능에 영향이 없다. 산업군이 추가되면 Level 2는 그대로 두고 Subtype만 확장한다. (FIBO의 `subPropertyOf` 관계 계층 패턴)

### 노드-엣지 허용 매트릭스

**이 표가 스키마 견고함의 핵심이다.** 적재 전 Validator가 이 표로 검증해 `Product → Person` 같은 잘못된 관계를 차단한다.

| L1 Category | Edge Type | 허용 방향 (Source → Target) | 성격 | Phase |
|---|---|---|---|---|
| 소유·지배 | `OWNS_STAKE_IN` | Company/Person/Org → Company | 상태 | 🟢P1 |
| 소유·지배 | `IS_EXECUTIVE_OF` | Person → Company/Org | 상태 | 🟢P1 |
| 거래·협력 | `SUPPLIES_TO` | Company → Company | 상태 | 🟢P1 |
| 거래·협력 | `PARTNERS_WITH` | Company/Org ⇄ Company/Org | 상태 | 🟢P1 |
| 거래·협력 | `ACQUIRES` | Company → Company | 사건 | 🟢P1 |
| 리스크·분쟁 | `SUES` | Company/Person/Org → 동일 | 사건 | 🟢P1 |
| 리스크·분쟁 | `COMPETES_WITH` | Company/Product ⇄ 동일 | 상태 | 🟡P2 |
| 리스크·분쟁 | `REGULATES` | Organization → Company/Product | 상태 | 🟡P2 |
| 제품·기술 | `DEVELOPS` | Company → Product | 상태 | 🟢P1 |
| 제품·기술 | `DEPENDS_ON` | Company/Product → Product | 상태 | 🟢P1 |
| 이벤트·영향 | `HAS_EVENT` | Company/Product → Event | 사건 | 🟢P1 |
| 이벤트·영향 | `IMPACTS` | Event → Company/Product | 사건 | 🟡P2 |

**⇄ 표시는 대칭 엣지** — 논리적으로 양방향이지만 Neo4j는 무방향 엣지를 저장할 수 없다. **적재 시 id 작은 쪽 → 큰 쪽으로 단방향 고정**, 조회 시 화살표 없는 패턴(`-[:COMPETES_WITH]-`)으로 방향 무시.

**주요 결정 근거:**
- **지분 3종 통합** — `OWNS_STAKE_IN`/`RAISES_CAPITAL_FROM`/`SUBSIDIARY_OF`를 하나로. `ratio`·`subtype`으로 구분해 LLM 혼동을 없앴다
- **`DEPENDS_ON` 독립 유지** — `SUPPLIES_TO`(Company→Company)와 노드 타입이 다르다(Company→Product). 합치면 "공급사 찾기" 쿼리에 Product가 딸려온다
- **`DEPENDS_ON` 명칭** — 당초 `INTEGRATES`였으나 변경. "통합한다"보다 **"의존한다"가 리스크 전파 의미에 정확**하다("A가 B 기술에 의존 → B에 문제 생기면 A가 타격"). 공급망 지식그래프들이 쓰는 표준 용어이기도 하다
- **`ACQUIRES` 명칭** — 시제가 현재형이라 과거 사건과 어긋나 보이지만 유지한다. 나머지 11종이 현재형이라 일관성이 우선이고, 시점은 `occurred_at`·`status`(absorbed/subsidiary) 속성이 담당한다. 단 **인수했다고 피인수 노드를 삭제하지 않는다** — 과거 시점 RAG 참조 시 컨텍스트가 붕괴한다(Wikidata의 replaces/replaced by와 같은 승계 처리)
- **`SUES` 독립 유지** — Event로 흡수하면 소송 필터에서 기업 간 직접 엣지가 안 그려지고, 1-hop이 2-hop이 되어 쿼리 비용이 는다
- **Event는 경로 끝단 고정** — 이벤트 노드는 리스크 전파의 시작점(Source)이나 종착점(Sink)만 가능. 경유지(Bridge)로 쓰면 무관한 기업까지 연결된다

```cypher
// 이벤트를 경로 끝에 고정하는 패턴
MATCH p=(:Event)-[:IMPACTS]->(:Company)-[:SUPPLIES_TO*1..3]->(:Company)
```

**Level 3 Subtype 예시**

| L2 Edge | Subtype |
|---|---|
| `OWNS_STAKE_IN` | 최대주주, 5%이상주주, 자회사, 계열사, 전략투자 |
| `SUPPLIES_TO` | 부품납품, 파운드리위탁, 소재공급, 장비공급 |
| `PARTNERS_WITH` | 합작(JV), MOU, 공동개발, 기술이전 |
| `IMPACTS` | 수혜(+), 타격(-), 중립 |

### 다중 관계 — 같은 두 기업 사이에 여러 엣지가 공존한다

**현실에서 두 기업의 관계는 하나가 아니다.** 삼성전자와 SK하이닉스는 DRAM에서 경쟁하면서, 파운드리로 웨이퍼를 공급하기도 하고, HBM 표준 협의체에서 협력하기도 한다. 반도체 산업에서 이런 **coopetition(경쟁적 협력)**은 예외가 아니라 기본값이다.

```
                  ┌── COMPETES_WITH ──┐   (DRAM 시장 경쟁)
삼성전자 ──────────┼── SUPPLIES_TO ────┼──→ SK하이닉스
                  └── PARTNERS_WITH ──┘   (HBM 표준화 협력)
```

Neo4j는 같은 노드 쌍 사이 복수 엣지를 허용한다. **이는 데이터 오류가 아니라 사실의 정확한 표현이므로 전부 유지한다.**

**하나로 합치면 안 되는 이유 — 질의 유형마다 필요한 엣지가 다르다**

| 질의 | 필요 엣지 | 합쳤을 때 문제 |
|---|---|---|
| "삼성 공장 화재의 파급은?" | `SUPPLIES_TO` | 공급 엣지가 지워지면 파급 경로를 못 찾음 |
| "하이닉스 악재로 누가 수혜?" | `COMPETES_WITH` | 경쟁 엣지가 지워지면 반사이익 추론 불가 |

**각 엣지는 서로 다른 근거를 가진다**

```cypher
(삼성전자)-[:SUPPLIES_TO  {subtype:"파운드리_위탁생산", source_doc:"20251219000123",
                          evidence_id:"ev_001"}]->(SK하이닉스)
(삼성전자)-[:COMPETES_WITH {subtype:"DRAM_시장경쟁",   source_doc:"news_20260115",
                          evidence_id:"ev_002"}]-(SK하이닉스)
(삼성전자)-[:PARTNERS_WITH {subtype:"HBM_표준화협의체", source_doc:"news_20260320",
                          evidence_id:"ev_003"}]-(SK하이닉스)
```

`evidence_id`가 각각 다르므로, 사용자가 특정 엣지를 클릭하면 **그 관계의 근거만** 정확히 표시된다.

### ⚠️ 공존 vs 전환 — 반드시 구분할 것

버저닝(11장)과 혼동하기 쉽다. **판별 기준은 "같은 사안인가"이며, `subtype`으로 판단한다.**

| 구분 | 판별 | 예시 | 처리 |
|---|---|---|---|
| **공존** | subtype이 다름 = 다른 사안 | DRAM은 경쟁 + 파운드리는 공급 | **엣지 전부 유지** |
| **전환** | 같은 사안의 관계가 뒤집힘 | 합작사 설립 → 결별 후 경쟁 | 기존 `is_current=false`+`valid_until` → 신규 CREATE |

```
예) PARTNERS_WITH {subtype:"HBM_표준화"} 가 살아있는 상태에서
    COMPETES_WITH {subtype:"DRAM_시장"} 유입
    → subtype이 다름 → 다른 사안 → 공존 (버저닝 아님)

예) PARTNERS_WITH {subtype:"메모리_합작사"} 가 살아있는 상태에서
    COMPETES_WITH {subtype:"메모리_합작결렬"} 유입
    → 같은 사안의 전환 → 버저닝
```

> 전환 판별이 애매하면 **공존을 기본값으로 둔다.** 잘못 버저닝하면 살아있는 관계가 사라지지만, 잘못 공존시키면 이력이 하나 더 남을 뿐이다. 후자가 안전하다.

### UI 렌더링

- 두 노드 사이 선을 **곡률을 다르게 하여 복수로 표시**, 색은 L1 카테고리별(거래·협력=파랑, 리스크·분쟁=빨강 등)
- L1 필터를 켜면 해당 카테고리 선만 남는다 — 카테고리를 5개로 나눈 이유 중 하나
- 노드가 많아 선이 겹치면 **묶어서 하나로 그리고 두께로 관계 수 표시**, 클릭 시 상세 목록 펼침 (실렌더링 후 결정)

---

## 4. 엣지 속성 스키마 🟢P1

모든 엣지가 아래 속성을 가진다. Bloomberg의 point-in-time, Diffbot의 isCurrent가 검증한 표준이다.

```cypher
(A)-[:SUPPLIES_TO {
    // 의미
    subtype:       "파운드리_위탁생산",
    direction:     "outbound",        // outbound / symmetric
    sign:          null,              // IMPACTS 전용: positive/negative/neutral
    revenue_ratio: 0.30,              // SUPPLIES_TO 전용: 매출 의존도
    status:        "active",          // ACQUIRES 전용: absorbed/subsidiary

    // 시점
    valid_from:    "2024-03-01",      // 관계 시작일
    valid_until:   null,              // 🟡P2 버저닝 시 만료일. P1은 null
    last_seen:     "2026-07-24",      // 최근 언급일 → 신선도
    is_current:    true,

    // 근거·신뢰
    confidence:    1.0,               // DART=1.0, 뉴스=LLM 점수
    source_type:   "dart",            // dart / news
    source_doc:    "20251219000123",  // 원문 FK (rcept_no 등)
    evidence_id:   "ev_12345"         // ★Vector 청크 FK — 팩트체크 즉시 조회
}]->(B)
```

**시점 속성이 필수인 이유:** BizNode는 연쇄 리스크를 다룬다. 2년 전 종료된 계약을 현재 리스크로 표시하면 오판이다. `last_seen`만 있어도 "6개월 미갱신 관계는 신뢰도 하향" 처리가 가능하다.

**소스별 시점 처리:**
- DART — `valid_from`을 공시상 날짜로 정확히 기록
- 뉴스 — `valid_from`은 기사 발행일로 대체, `last_seen` 중심 관리

---

## 5. Vector DB 정책 — 선별 임베딩 🟢P1

### 왜 전문 임베딩을 안 하는가

사업보고서 한 건이 수백 페이지인데 실제로 쓰는 건 3개 섹션뿐이다. 나머지(감사보고서, 주주총회 절차, 재무제표 주석)를 전부 임베딩하면 **비용은 다 내면서 검색 시 노이즈만 는다.** 사용자가 공급계약 근거를 물었는데 감사의견 청크가 나오는 식이다.

| 방식 | 기업당 청크 | 상장사 2,600개 기준 |
|---|---|---|
| 전문 임베딩 | 수백~수천 | 수백만~수천만 |
| **선별 임베딩** | 수십 | **수십만** |

### 임베딩 대상 3종

**① 엣지 근거 스니펫 (Evidence Snippet) — 최우선**

관계가 도출된 근거 문장을 중심으로 앞뒤 문맥을 붙인 청크. **엣지 하나당 하나**.

```json
{
  "id": "ev_12345",
  "text": "당사는 ○○전자와 특수합금 공급계약을 체결하였으며, 계약금액은
           516,507,509원으로 최근 매출액 대비 20.19%에 해당합니다.
           계약기간은 2025-12-19부터 2026-03-13까지입니다.",
  "metadata": {
    "edge_id": "e_12345", "edge_type": "SUPPLIES_TO",
    "source_corp": "00164779", "target_corp": "00126380",
    "rcept_no": "20251219000123", "doc_type": "공급계약공시",
    "occurred_at": "2025-12-19"
  }
}
```

엣지의 `evidence_id`로 직접 조회하므로 **팩트체크 패널이 원문 전체를 뒤질 필요가 없다.**

**② 기업 프로파일 요약 (Company Profile)**

기업당 1~3청크. 사업 개요·주요 제품·핵심 관계·최근 이슈를 압축한다.

```json
{
  "id": "prof_00164779",
  "text": "SK하이닉스는 메모리 반도체 전문 기업으로 DRAM·NAND·HBM을
           주력으로 한다. 주요 고객사는 ..., 주요 공급사는 ...",
  "metadata": { "corp_code": "00164779", "updated_at": "2026-07-24", "version": 3 }
}
```

**생성·갱신 흐름:** 프로파일 원본은 **RDBMS에 저장**하고, RDBMS 갱신 시 Vector도 함께 갱신한다(아래 8장).

**③ 이벤트 요약 (Event Summary)**

뉴스·공시 전문이 아니라 "무슨 일이 있었나"를 요약한 청크. Event 노드와 1:1 매핑.

### 무엇을 임베딩하지 않는가

| 대상 | 처리 |
|---|---|
| 공시 원문 전체 | **RDBMS/오브젝트 스토리지에 원문 보관.** 임베딩 안 함. 팩트체크 "전문 보기"에서 그대로 제공 |
| 뉴스 본문 | **URL만 보관.** 근거가 되는 부분은 이미 ①로 임베딩됨. 저작권 이슈도 회피 |
| 사업보고서 비타겟 섹션 | 파싱조차 안 함 |

> **원칙: "저장은 전부, 임베딩은 선별."** 원본이 남아 있으므로 나중에 필요하면 그때 임베딩할 수 있다.

### 이 정책의 부수 효과 — Vector DB의 역할이 바뀐다

| | 기존 설계 | v1.0 |
|---|---|---|
| 성격 | 문서 검색 인덱스 | **그래프의 의미 계층(semantic layer)** |
| 청크 단위 | 문서 조각 | 엣지·노드에 대응하는 근거 단위 |
| 그래프와의 관계 | 느슨 (별도 검색 후 합침) | **강결합** (metadata의 `edge_id`/`corp_code`) |

양방향 흐름이 깔끔해진다:
- **Graph → Vector**: 다중 홉 탐색으로 경로를 찾고, 경로상 엣지의 `evidence_id`로 근거를 정확히 꺼낸다
- **Vector → Graph**: 의미 검색으로 관련 청크를 찾고, metadata의 `corp_code`로 그래프에 진입한다

**잃는 것:** "연구개발 인력이 몇 명이야?" 같은 임의 질문 검색력. 단 BizNode는 범용 문서 QA가 아니라 관계·리스크 추론 플랫폼이므로 감당 가능한 손실이다. 필요하면 원문 보관분에서 키워드 검색으로 보완한다.

---

# Part II. 어디서 가져오는가 — 데이터 소스

## 6. 시드 기업 선정 🟢P1

### ETF는 밸류체인이 아니라 "탐색 시드"

대상 기업군은 ETF 구성종목으로 선정한다(현행: KODEX 반도체 + 로봇액티브 64개사).

**ETF를 쓰는 이유:** 자의성 배제("왜 이 기업들인가"에 객관적 근거), 산업 경계 정의, 재현·확장 가능.

**★ 오해하면 안 되는 것:** ETF는 시가총액·유동성 기준의 **투자 포트폴리오**이지 거래 관계로 묶인 집합이 아니다. 리스트 내 두 기업이 실제 거래 관계가 없을 수 있고, 밸류체인상 핵심인데 ETF에 없어 빠진 기업이 있다.

→ **따라서 DART 공시를 따라가다 리스트 밖 기업이 stub 노드로 나오는 것은 문제가 아니라 의도된 동작이다.** 밸류체인은 원래 ETF 경계를 넘어간다.

**확장 전략:** stub 노드 중 등장 빈도가 높은 기업을 **2차 시드로 승격**시켜 정식 수집 대상에 편입한다.

### 시드 리스트 구조

```json
{
  "companyName": "SK하이닉스",
  "corpCode": "00164779",        // DART 고유번호 = PermID
  "stockCode": "000660",
  "market": "KOSPI",
  "sector": ["반도체", "로봇"],    // 복수 가능 → Company 속성
  "etfList": ["KODEX 반도체", "KODEX 로봇액티브"]
}
```

> `sector`·`etfList`는 DART가 주지 않는다. **API 응답과 병합**해 Company 속성으로 적재한다. `etfList`를 넣으면 "특정 ETF 구성종목만 보기" 필터가 가능해진다.

### 착수 즉시 실측할 것

시드 확정 직후 **전 기업의 최근 2년 「단일판매·공급계약체결」 공시 건수**를 센다. `SUPPLIES_TO` 전체가 이 공시에 의존하므로 이 수치가 P1의 성패를 가른다.

| 실측 결과 | 대응 |
|---|---|
| 기업당 평균 1건 이상 | 계획대로 진행 |
| 0.3~1건 | 대형주 앵커 추가 + 사업보고서 비중 확대 |
| 0.3건 미만 | 시드 재선정 또는 `SUPPLIES_TO`를 P2 뉴스로 이관 |

**시드 선정 점검표:** KOSDAQ 비중(공시 의무 기준이 KOSPI 5% vs KOSDAQ 10%·3억으로 두 배 높아 공시가 적다) · 지주사 포함 여부(없으면 지분 엣지가 전부 리스트 밖으로 향한다) · 비상장 기업(공시 의무가 제한적이라 데이터가 거의 없다)

---

## 7. DART 수집 경로 A / B / C 🟢P1

기업 간 공급·계약 관계는 **정형 API에 없다.** 그러나 **표준 서식 공시**로 제공되므로 확보 가능하다. 이 발견으로 P1 범위가 "지배구조 그래프"에서 "지배구조 + 공급망 + 제품 그래프"로 확장됐다.

| 경로 | 형태 | 난이도 | 산출 엣지 |
|---|---|---|---|
| **A. 정형 API** | JSON | 낮음 | `OWNS_STAKE_IN`, `IS_EXECUTIVE_OF` |
| **B. 공시 원본** | 표준 서식 | 중간 | `SUPPLIES_TO`, `ACQUIRES`, `SUES`, `HAS_EVENT` |
| **C. 사업보고서** | 표+서술 | 높음 | `Product`, `DEVELOPS`, `PARTNERS_WITH`, `DEPENDS_ON` |

> **공시 채널 참고:** DART(금감원)는 정기·주요사항보고서를, KIND(거래소)는 수시공시를 담당한다. 단 동일 서식 항목은 DART 한 번 제출로 양쪽 의무를 이행하므로 「단일판매·공급계약체결」도 DART에서 수집된다.

### 경로 A — 정형 API

| API | 함수 | 산출물 |
|---|---|---|
| 고유번호 | `get_corp_code()` | 전체 기업 목록 → **PermID 마스터** |
| 기업개황 | `get_corp_info()` | Company 노드 속성 |
| 최대주주 현황 | `hyslr_sttus()` | `OWNS_STAKE_IN {subtype:"최대주주", ratio}` |
| 임원 현황 | `exctv_sttus()` | `IS_EXECUTIVE_OF` + Person 노드 |
| 타법인 출자 | `otr_cpr_invstmnt_sttus()` | `OWNS_STAKE_IN {subtype:"출자"/"자회사"}` |
| 대량보유 보고 | `majorstock()` | `OWNS_STAKE_IN {subtype:"5%이상"}` |
| 공시검색 | `search_filings()` | **경로 B의 진입점** |
| 주요계정 | `fnltt_singl_acnt()` | 재무 → **RDBMS**(그래프 아님) |

### 경로 B — 공시 원본 파싱 ★핵심

| 공시 유형 | 생성 엣지 |
|---|---|
| **단일판매·공급계약체결** | `SUPPLIES_TO` |
| 타법인 주식 취득결정 | `OWNS_STAKE_IN` / `ACQUIRES` |
| 회사합병·분할·주식교환 | `ACQUIRES {status}` |
| 소송 등의 제기 | `SUES` |
| 영업·중요자산 양수도 | `ACQUIRES` |
| 부도·회생·영업정지 | `HAS_EVENT` |

**「단일판매·공급계약체결」 필드 매핑** — 우리 스키마와 거의 1:1로 대응한다.

| 공시 필드 | 엣지 속성 |
|---|---|
| 계약상대방 | target (Company) |
| **매출액 대비(%)** | **`revenue_ratio`** ← Bloomberg가 핵심 리스크 지표로 쓰는 값을 DART가 직접 제공 |
| 계약기간 시작/종료일 | `valid_from` / `valid_until` |
| 계약(수주)일자 | `occurred_at` |
| 확정 계약금액 | `contract_amount` |

**한계:** ①계약상대방 **공시유보** 가능 → 엣지 생성 보류, `HAS_EVENT`로 계약 사실만 기록 ②매출 5%(코스닥 10%) 미만은 공시 의무 없어 소규모 공급관계 누락 → P2 뉴스로 보완

### 경로 C — 사업보고서 본문

사업보고서 「II. 사업의 내용」 목차는 표준화되어 있다(1.사업개요 2.주요제품및서비스 3.원재료및생산설비 4.매출및수주상황 5.위험관리 6.주요계약및연구개발활동).

| 섹션 | 산출물 | 형태 | Phase |
|---|---|---|---|
| **II-2. 주요 제품 및 서비스** | `Product` 노드 + `DEVELOPS` | 표 | 🟢P1 |
| **II-6. 주요계약 및 연구개발활동** | `PARTNERS_WITH`, `DEPENDS_ON` | 서술 | 🟢P1 |
| **IX. 계열회사 등에 관한 사항** | `OWNS_STAKE_IN {subtype:"계열사"}` | 표 | 🟢P1 |
| II-4. 매출 및 수주상황 | `SUPPLIES_TO` | 표+서술 | 🟡P2 |
| II-3. 원재료 및 생산설비 | `SUPPLIES_TO`(역방향) | 표+서술 | 🟡P2 |

**II-2·II-6이 P1인 이유:** `Product` 노드와 `DEVELOPS`/`DEPENDS_ON`를 스키마에 넣어놓고 유일한 정형 소스를 미루면 **L1 카테고리 "제품·기술"이 통째로 빈 필터**가 된다. II-2는 자사 제품이라 익명화 대상도 아니다. II-6은 기술도입·라이선스의 유일한 정형 소스다(공시 의무가 없어 여기 아니면 뉴스뿐).

**⚠️ 익명화 대응:** 실제 사업보고서엔 "매입처와의 계약에 의해 영업 기밀로 처리하게 되어 있어 기재 생략" 같은 표기가 흔하다. 익명화 영향은 섹션마다 다르다 — II-2는 영향 없음(자사 제품), II-3·II-4는 심함.
→ **원칙: 익명 건만 스킵하고 섹션은 유지.** 식별 불가 상대방은 엣지도 stub도 만들지 않는다. 커버리지 20%든 50%든 0%보다 낫다.

---

## 8. 뉴스 수집 🟡P2

### 대상 선정

| 전략 | 방식 | 판단 |
|---|---|---|
| **A. 기업 기준** | 시드 기업명 검색 | ★주력 — 대상 명확, ER 매칭률 높음 |
| **B. 키워드 기준** | "인수·공급계약·소송·제휴" 검색 | ○보조 — 관계 밀도 높음 |
| C. 카테고리 전량 | 경제·산업 전체 | ✗ 하루 수만 건, 비용 폭발 |

→ **A 주력 + B 보조.** 시드 기업 × 관계 키워드 조합.

### 본문 확보 이슈 — 착수 전 결정 필요

네이버 뉴스 API는 **제목과 요약(description)만** 준다. 요약만으로는 관계 추출 정확도가 크게 떨어진다. RSS · 크롤링 · 빅카인즈 중 방안을 확정해야 한다.

**단 본문을 확보해도 저장하지 않는다.** 관계 추출에 사용한 뒤 **URL만 보관**하고, 근거가 되는 부분은 evidence 스니펫으로 임베딩되므로 전문은 불필요하다(저작권 이슈도 회피).

### 주기·중복

- 초기 적재: 최근 3~6개월 · 정기: 일 1회 배치
- 통신사 전재로 동일 기사가 여러 매체에 뜬다 → **제목 유사도 dedup 필수**

---

# Part III. 어떻게 쌓는가 — 파이프라인

## 9. 전체 흐름

```
━━━ Phase 1 (정형) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[시드] ETF 구성종목 = 탐색 시작점
   ▼
[A] 정형 API          [B] 공시 원본           [C] 사업보고서
 기업개황→Company      공급계약→SUPPLIES_TO    II-2→Product·DEVELOPS
 임원→Person·EXEC      합병→ACQUIRES           II-6→PARTNERS·DEPENDS_ON
 주주·출자→OWNS        소송→SUES               IX→OWNS(계열사)
 confidence=1.0        공시→HAS_EVENT          익명 건 스킵
   └────────┬──────────┴────────────────────┘
            ▼
   corp_code 매칭 → 미매칭은 stub 노드 (정상 동작)
            ▼
   ┌─────────────┬──────────────┬─────────────┐
   ▼             ▼              ▼             ▼
 Neo4j        ChromaDB        PostgreSQL      오브젝트 스토리지
 노드·엣지    evidence 청크   기업마스터   공시 원문 보관
              프로파일 청크   재무·프로파일
            ▼
   ✅ 캔버스 렌더링 + 근거 조회  ← P1 완료

   ※ 뉴스 샘플 200~300건으로 ER 매칭률 사전 검증

━━━ Phase 2 (비정형) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[D] 뉴스 수집 (A기업기준 + B키워드)
   ▼
전처리 → ★적합성 필터 (규칙 → 제로샷 LLM 라우터) ← 비용 방어
   │ 상위 20~30%만 통과
   ▼
개체추출(개방) → 관계추출(12종+매트릭스) → Gleaning(High만)
   ▼
★개체 ER — Lexical 블로킹 (corp_code 매칭 → n-gram Jaccard → Jaro-Winkler)
   ↑ DART 골격이 앵커. 뉴스 개체가 "붙으면서" 들어옴
   ▼
관계 정규화 (NLI 분류 + 기존 subtype Soft-constraint / 실패는 Hard-reject)
   ▼
Neo4j 적재 (재언급→last_seen / 상충→버저닝 / 사건→개별 CREATE)
   ▼
엣지 12종 완성 (+COMPETES_WITH · REGULATES · IMPACTS)
   ▼
[수렴] reject 빈도 → subtype 승격  [생명주기] Event TTL  [확장] stub→2차 시드
```

---

## 10. Phase 1 적재 절차 🟢P1

**순서를 지킨다.** 노드가 있어야 엣지를 만들고, corp_code가 있어야 개체를 식별한다.

### [1] 기업 기본정보 → Company 노드
```
get_corp_code() → corp_code 마스터 테이블 (RDBMS)
get_corp_info(corp_code) × 시드 → 기업 상세
+ 시드 JSON의 sector·etfList 병합
→ Neo4j Company 노드 + PostgreSQL companies 테이블
```
> 여기서 API 키·호출 한도·응답 구조·DB 연결을 **모두 검증하고** 넘어간다. 가장 안전한 첫 단계다.

### [2] 임원 → Person 노드 + `IS_EXECUTIVE_OF`
```
exctv_sttus() → 임원 목록 → Person 노드 + 엣지
```
**개인 주주 폭발 방지 정책:** 법인은 Company 노드로 생성, 개인은 **임원 겸직자이거나 지분율 임계값(5%) 이상만** Person 노드 생성. 나머지는 `Company.major_shareholder_summary` 속성으로 요약.

### [3] 지분 → `OWNS_STAKE_IN`
```
hyslr_sttus() / otr_cpr_invstmnt_sttus() / majorstock()
→ ratio·subtype 부여 → 엣지
```
> 이 단계부터 **시드 밖 기업이 stub 노드로 등장**한다. 정상이다.

### [4] 공시 원본 → `SUPPLIES_TO` / `ACQUIRES` / `SUES` / `HAS_EVENT`
```
search_filings(유형 필터) → 공시 목록
→ download_document(rcept_no) → 원본
→ 표준 서식 규칙 파싱 (LLM은 서식 파손 예외에만)
→ 엣지 생성 + evidence 스니펫 추출
→ Neo4j 엣지 / ChromaDB evidence 청크 / 스토리지 원문 보관
```

### [5] 사업보고서 → `Product` / `DEVELOPS` / `PARTNERS_WITH` / `DEPENDS_ON`
```
search_filings(pblntf_ty="A") → 사업보고서 rcept_no
→ download_document() → 목차 기준 섹션 분할
→ ★타겟 섹션만 추출 (II-2 · II-6 · IX) — 전체 파싱 금지
→ 표는 규칙 파싱 / 서술은 LLM
→ 익명 표기 필터 → 엣지·노드 생성
```

### [6] 기업 프로파일 생성 → RDBMS + Vector
```
[1]~[5] 완료 후, 기업별로 프로파일 텍스트 생성
→ PostgreSQL company_profiles 테이블 저장 (원본)
→ 임베딩 → ChromaDB 프로파일 청크
```
**갱신 흐름:** 프로파일 원본은 RDBMS가 소유한다. **RDBMS 갱신 시 Vector도 동시 갱신**한다(트리거 또는 배치). 분기 공시 주기에 맞춰 재생성.

```
[RDBMS 갱신] → [프로파일 재생성] → [기존 Vector 청크 삭제] → [신규 임베딩 적재]
                                    ↑ version 필드로 정합성 관리
```

---

## 11. Neo4j 적재 규칙 🟢P1

### 기본 — MERGE로 중복 방지
```cypher
MERGE (c:Company {corp_code: $corp_code})
  ON CREATE SET c.name=$name, c.stock_code=$stock, c.sector=$sector, c.etf_list=$etfs
  ON MATCH SET  c.last_seen=$today

MATCH (a:Company {corp_code:$src}), (b:Company {corp_code:$tgt})
MERGE (a)-[r:SUPPLIES_TO {subtype:$subtype}]->(b)
  ON CREATE SET r.valid_from=$date, r.revenue_ratio=$ratio, r.confidence=$conf,
                r.source_doc=$doc, r.evidence_id=$ev, r.is_current=true
  ON MATCH SET  r.last_seen=$today
```

### 대칭 엣지 — 물리적 단방향 저장
```cypher
// id 작은 쪽 → 큰 쪽으로 고정. A→B, B→A 중복 방지
WITH CASE WHEN $srcId < $tgtId THEN [$srcId,$tgtId] ELSE [$tgtId,$srcId] END AS ord
MATCH (a {corp_code:ord[0]}), (b {corp_code:ord[1]})
MERGE (a)-[r:COMPETES_WITH]->(b)
```
조회는 방향 무시: `MATCH (a)-[:COMPETES_WITH]-(b)`

### 🟡P2 — 상충 엣지 버저닝
관계가 바뀌면(파트너 → 경쟁) 덮어쓰지 않고 이력을 남긴다.

> **⚠️ 먼저 공존 vs 전환을 판별한다(3장).** subtype이 다르면 다른 사안이므로 **버저닝하지 말고 엣지를 추가**한다. 아래는 같은 사안의 관계가 뒤집힌 경우에만 적용한다.

```cypher
// 같은 사안(subtype 대응)의 전환일 때만 실행
MATCH (a)-[old:PARTNERS_WITH {is_current:true}]-(b)
WHERE old.subtype IN $conflicting_subtypes   // 전환 대상 subtype인지 확인
SET old.is_current=false, old.valid_until=$today
CREATE (a)-[:COMPETES_WITH {valid_from:$today, is_current:true, subtype:$newSubtype}]->(b)
```
> **성격별 분기:** 단순 재언급 → `last_seen` 갱신 / 상태 엣지 상충(같은 사안) → 버저닝 / 상태 엣지 병존(다른 사안) → **추가 CREATE** / 사건 엣지 → 항상 개별 CREATE

### 인덱스·제약
```cypher
CREATE CONSTRAINT company_corp_code IF NOT EXISTS
  FOR (c:Company) REQUIRE c.corp_code IS UNIQUE;
CREATE INDEX company_sector IF NOT EXISTS FOR (c:Company) ON (c.sector);
CREATE INDEX rel_is_current IF NOT EXISTS FOR ()-[r:SUPPLIES_TO]-() ON (r.is_current);
CREATE INDEX rel_subtype    IF NOT EXISTS FOR ()-[r:SUPPLIES_TO]-() ON (r.subtype);
```

---

## 12. 뉴스 파이프라인 🟡P2

### 12-1. 적합성 필터 — LLM 진입 최전선
하루 수만 건을 전부 추출 LLM에 넣으면 비용이 감당 안 된다. **무거운 호출 직전에** 가벼운 게이트를 둔다.

```
1차 규칙(무료): 키워드(인수·합병·공급·납품·피소·소송·체결·규제·제휴·지분·투자) 미포함
                & 시드 미언급 → 즉시 컷
2차 제로샷 LLM: 초경량 모델에 {"is_relevant": true/false} JSON만 요청 → true만 통과
```
> **BERT 금지 이유:** MVP엔 "유의미한 관계 기사"를 학습시킬 라벨 데이터가 없다(콜드스타트). 제로샷 LLM은 파인튜닝 없이 즉시 작동하고, 5-7 NLI 분류기와 **같은 인프라를 공유**한다. 데이터가 수만 건 쌓이면 BERT 교체 검토.

### 12-2. 개체·관계 추출
- 개체: 5개 노드 타입만 제시, 구체 개체는 자유 추출
- 관계: **12종 + 노드-엣지 매트릭스**를 프롬프트에 주입. source=주어/target=목적어 고정. 매트릭스 위반은 추출 거부. 미해당은 `OTHER` + 원문 표현 기록
- Gleaning: 1차 결과 재투입 후 "놓친 관계 추가 추출" — **중요도 High만** (비용)

### 12-3. 개체 정규화 (ER) — Lexical 블로킹
```
① 표기 정규화: 특수문자 제거, 법인격 통일((주)/㈜), 대소문자·공백
② corp_code 정확 매칭          ← 대부분 여기서 해소 (DART 앵커 효과)
③ Lexical 블로킹: char n-gram Jaccard(또는 BM25)로 후보 축소
   → 구현: PostgreSQL **pg_trgm**(trigram 유사도 + GIN 인덱스)로 DB에 위임
④ 정밀 비교: 후보만 Jaro-Winkler
⑤ Union-Find 클러스터링 → 대표 개체명 선정
⑥ corp_code 매칭 성공 → 병합 / 실패 → stub 노드
```

> **⚠️ 임베딩을 블로킹에 쓰면 안 된다.** Dense 임베딩은 *의미*로 가깝게 본다. "SK하이닉스"와 "삼성전자"가 둘 다 반도체라 유사도가 높게 나오고(오탐), "SAMSUNG ELEC"은 문맥이 없어 멀게 나온다(누락). **ER이 잡아야 하는 건 의미가 아니라 표기 유사성**이다. ChromaDB는 문맥 검색 전용으로 둔다.

> **복잡도:** 전수 비교 O(N×M)은 기존 10만 × 신규 5천 = 5억 연산/배치로 시스템이 마비된다. 블로킹으로 O(N)화한다.

### 12-4. 관계 정규화 — NLI 분류기
`OTHER`로 분류된 표현을 12종에 매핑한다.

> **⚠️ 임베딩 유사도 매핑 금지.** "공급계약을 **체결**했다"와 "공급계약을 **해지**했다"가 코사인 0.9+로 나온다. 임베딩은 논리 방향을 못 본다. 반의어를 같은 엣지로 만들면 그래프가 사실과 반대가 된다.

```
입력: OTHER 원문 표현 + [해당 L2 하위의 기존 subtype 리스트]
질문: "12종 중 어디에 속하는가? subtype은 기존 리스트에서 고르되 없으면 신규. 매핑 불가면 UNMAPPABLE"
출력: {L2 엣지, subtype, confidence} 또는 UNMAPPABLE
```
- **Soft-constraint** — 기존 subtype 리스트를 함께 주어 "부품납품/부품공급/핵심부품제공" 파편화를 막는다
- **Hard-reject** — 매핑 실패는 **버린다.** 리뷰 큐에 쌓으면 데이터 스왐프가 된다. 단 특정 (source,target) 쌍의 reject 빈도가 주간 임계값을 넘으면 알람 → 신규 엣지 검토

---

## 13. 예외 처리 🟢P1

착수 즉시 만나는 3대 예외. **처음부터 코드에 넣는다.**

| 예외 | 처리 |
|---|---|
| **DART XML 파싱 실패** | 레코드 단위 try-catch(하나의 실패가 배치를 중단시키지 않게). 필수 필드 누락 → 스킵+로그. 선택 필드(지분율 등) 실패 → 해당 속성만 null, 엣지는 생성 |
| **Dangling Edge** (대상 노드 부재) | stub 노드 먼저 생성(`is_stub=true`) 후 엣지 연결. **엣지를 버리지 않는다.** 실제 정보가 들어오면 승격 |
| **corp_code 매칭 실패** | stub 노드(`corp_code=null`, `market="비상장"/"해외"`). 캔버스에서 흐리게 렌더링. 매칭 실패율을 배치마다 기록해 급증 시 ER 임계값 재조정 |

---

## 14. 확장 아키텍처 — 전 종목 지원 🟡P2

시드 64개는 MVP다. 실서비스는 상장사 전체(약 2,600개, DART corp_code 10만+)를 다뤄야 한다. **전량을 똑같이 적재하지 않는다.**

```
[Tier 1] 전량 · 얕게    상장사 전체를 Company 노드로 (속성만)
                        get_corp_code() 한 번이면 됨 → 전 종목 검색 지원
                        관계 없으면 "분석 데이터 준비 중" 표시

[Tier 2] 우선순위·깊게  시총·거래대금 상위 + ETF 구성종목부터
                        관계·공시·evidence 청크 사전 구축
                        조회 로그 기반으로 우선순위 재조정

[Tier 3] 온디맨드       Tier 2에 없는 기업 요청 시 큐잉 → 백그라운드 수집
                        "수집 중" 표시 → 완료 후 Tier 2로 승격
```

**DB별 적재 정책**

| DB | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| PostgreSQL | 기업 마스터 전량 | + 재무·프로파일 | 요청 시 수집 |
| Neo4j | 노드 전량 | + 엣지 | 요청 시 생성 |
| ChromaDB | — | evidence·프로파일 청크 | 요청 시 임베딩 |

> **Tier 1은 지금 해도 된다.** `get_corp_code()` 한 번으로 "전 종목 검색 지원"이 되고, 심사에서 확장성을 물으면 실물로 보여줄 수 있다.

---

## 15. 상향식 온톨로지 수렴 🟡P2

엣지 12종은 확정했으나 Level 3 subtype은 데이터가 결정한다.

```
① 12종 + 매트릭스 확정 (완료)
② 뉴스 200~300건 파이프라인 적용 → OTHER·reject 표현 수집
③ 빈도 클러스터링 → 상위를 subtype 승격
④ OTHER 비율이 목표치(10%) 이하로 떨어질 때까지 반복
```

---

## 16. Event 생명주기 🟡P2

Event를 무한 누적하면 기업 하나에 수만 노드가 매달려 쿼리가 타임아웃된다. **단, 무차별 삭제 금지** — 과거 리스크 이력 보존이 BizNode의 가치다.

| 등급 | 기준 | 처리 |
|---|---|---|
| **구조적** | 대형 리콜·소송·규제 제재, 연결 IMPACTS 다수 | **영구 유지** |
| **단발성** | 일반 실적발표·인사, 연결 IMPACTS 적음 | 3개월 후 **RDBMS 아카이빙**(삭제 아님) |

유사 이벤트는 주간 단위 Macro-Event로 롤업한다.

---

## 17. GraphRAG 탐색 프로파일 🟡P2

질의 유형별로 탐색 엣지를 제한해 환각·노이즈를 차단한다.

| 질의 유형 | 허용 엣지 |
|---|---|
| 공급망 리스크 | `SUPPLIES_TO`, `DEVELOPS`, `DEPENDS_ON`, `OWNS_STAKE_IN` |
| 경쟁·반사이익 | `COMPETES_WITH`, `IMPACTS` |
| 지배구조 | `OWNS_STAKE_IN`, `IS_EXECUTIVE_OF`, `ACQUIRES` |
| 법적·규제 리스크 | `SUES`, `REGULATES`, `IMPACTS` |

- 우호 탐색에서 적대 엣지 배제 → 불필요한 연쇄 환각 차단
- **Organization·Event는 경유지(bridge) 금지** — 끝단으로만
- **다중 관계 상황에서 특히 중요하다.** 삼성전자↔SK하이닉스처럼 공급·경쟁·협력 엣지가 공존할 때, 프로파일이 질의에 맞는 엣지만 타므로 답이 섞이지 않는다. "공장 화재 파급"은 `SUPPLIES_TO`만, "악재 반사이익"은 `COMPETES_WITH`만 탐색한다

---

# 부록

## A. Phase 1 실행 체크리스트

```
── 0. 착수 전 검증 (이 결과가 이후 전제) ──
[ ] OpenDART API 키 발급, 일일 호출 한도 확인
[ ] get_corp_code() → corp_code 마스터 구축
[ ] 시드 리스트 검증 (시장 구분, 비상장 여부)
[ ] ★ 시드 공급계약 공시 보유량 실측 (6장 기준표)
[ ] search_filings() pblntf_ty 코드값 실측
[ ] 「단일판매·공급계약체결」 샘플 10건 → 파싱 가능성 확인
[ ] Docker: Neo4j · PostgreSQL(+pg_trgm) · ChromaDB (+Redis 선택) 기동

── 1. 스키마·기반 ──
[ ] Neo4j 제약·인덱스 (11장)
[ ] 노드-엣지 매트릭스 Validator 구현
[ ] 예외 처리 3종 내장 (13장)

── 2. 경로 A ──
[ ] Company 노드 (시드 JSON 병합)
[ ] Person + IS_EXECUTIVE_OF (개인 주주 정책)
[ ] OWNS_STAKE_IN

── 3. 경로 B ──
[ ] 공시 목록 수집기
[ ] 공급계약 파서 → SUPPLIES_TO + evidence 청크 ★
[ ] 합병·양수도 파서 → ACQUIRES
[ ] 소송 파서 → SUES
[ ] Event + HAS_EVENT
[ ] 원문 스토리지 보관

── 4. 경로 C ──
[ ] 목차 기준 섹션 분할기
[ ] II-2 → Product + DEVELOPS
[ ] II-6 → PARTNERS_WITH + DEPENDS_ON
[ ] IX → OWNS_STAKE_IN(계열사)

── 5. 프로파일·서빙 ──
[ ] 기업 프로파일 생성 → PostgreSQL + ChromaDB
[ ] FastAPI 그래프 API + 평면 JSON 직렬화
[ ] level_1_category 태그 주입
[ ] 근거 조회 API (evidence_id 기반)
[ ] 캔버스 렌더링 연동
[ ] 관계 유형 필터 (L1 5종)

──── P1 완료 ────
[ ] 뉴스 샘플 200~300건 → ER 매칭률 검증
[ ] 뉴스 본문 확보 방안 확정
[ ] P2 진입
```

**P1 성공 기준:** *"시드 기업의 지분·임원·공급·제품 관계가 캔버스에 뜨고, 엣지를 클릭하면 근거가 보이며, 관계 필터 5개가 작동한다."*

## B. 미결 사항

**착수 즉시 실측**
- 시드 공급계약 공시 보유량 / 공시 원본 XML 태그 체계 / `pblntf_ty` 코드값 / 사업보고서 목차 구분 방식 / API 일일 한도

**P1 진행 중**
- ER 임계값 θ · 블로킹 후보 수(k) · Product 명칭 정규화 규칙 · 개인 주주 임계 지분율 · stub 렌더링 스타일 · 프로파일 갱신 주기

**P2 착수 전**
- **뉴스 본문 확보 방안** · 추출 LLM 선정 · 적합성 필터 성능 · subtype 목록 · reject 임계값 · 상충 엣지 정의 · Event 등급 기준

## C. 참조

FIBO(관계 계층·point-in-time) · Bloomberg SPLC(revenue concentration) · Kensho(Macro-Edge) · Refinitiv PermID(마스터 식별자) · Diffbot(isCurrent) · Wikidata(소유 통합·M&A 승계) · EDC(개방추출→정규화) · Microsoft GraphRAG(gleaning)
