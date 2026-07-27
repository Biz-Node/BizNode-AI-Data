# BizNode 플랫폼 개발 계획서 (Phase 1)

> **상태: 확정 (2026-07-24) — 구현 대기**
> 근거: `BizNode_KG_추출적재_방법서.md` (v1.0) · 현행 코드베이스 정렬
> 시드: KODEX 반도체 + 로봇액티브 64개사 (`data/company_list/company_list_etf.json`)
> 목표: 정형(DART) 데이터로 지식그래프를 캔버스에 띄우고, 엣지 클릭 시 근거가 보이는 상태

---

## 0. 이 계획서의 전제 — 현행 자산 처리 방침

방법서가 v0.5 → v1.0으로 바뀌며 **스키마·소스 범위가 크게 확장**됐다. 기존 `data/`는 구(舊) 스키마 산출물이므로 **"참고"하되 그대로 쓰지 않는다.**

| 기존 자산 | 처리 방침 |
|---|---|
| `data/raw_dart/*` (320개, 5 API 원본 응답) | **재사용.** 원본 API 응답이라 스키마와 무관 → 새 정규화기로 **재처리**. 5개 API는 재수집 불필요 |
| `data/normalized/*` (187개, 구스키마 3종 엣지) | **폐기 후 재생성.** `OWNS_SHARE`/`INVESTS_IN`/`IS_OUTSIDE_DIRECTOR_OF` 구스키마라 신스키마로 재정규화 |
| `data/company_reports·raw_reports` (삼성 샘플 2건) | **참고.** 경로 C 파서 개발용 샘플로 활용 |
| `data/company_list/*` (시드 64개, corp_code 맵) | **유지.** PermID 마스터 |
| 코드: batch/pipeline/schemas 3계층 | **골격 유지.** 아래 §2 자산 평가표 참조 |

> **원칙:** fetch(수집)는 이미 받은 5개 API 재활용 + 신규 API만 추가. normalize/적재는 신스키마로 전면 재작성.

---

## 1. 최종 목표 아키텍처 (3-DB 적재)

방법서 9장 흐름도를 이 프로젝트의 물리 저장소로 확정한다.

```
                        [시드 64개사 = 탐색 시작점]
                                   │
        ┌──────────────┬───────────┴───────────┬──────────────┐
        ▼              ▼                        ▼              ▼
   [A] 정형 API    [B] 공시 원본           [C] 사업보고서    (재무)
   기업개황·임원   공급계약·합병·소송      II-2·II-6·IX      주요계정
   ·지분           ·부도                                        │
        └──────────────┴───────────┬───────────┴──────────────┘
                                    ▼
                    corp_code 매칭 (미매칭 → stub 노드)
                                    ▼
   ┌────────────────────┬────────────────────┬─────────────────────┐
   ▼                    ▼                    ▼                     ▼
 Neo4j              ChromaDB→(Qdrant)     PostgreSQL          파일 스토리지
 (관계 토폴로지)      (선별 임베딩)         (정량·마스터·원문)  (공시 원문)
 · 노드 5종           · evidence 청크        · companies         · data/documents/
 · 엣지 8종(P1)         (엣지당 1)           · financials          rcept_no별 원문
 · corp_code UNIQUE   · profile 청크         · company_profiles
                        (기업당 1~3)         · corp_code_master
                                             · documents(원문 메타)
                                             · ★pg_trgm(ER 블로킹)
                                    ┌────────┴────────┐
                                    ▼                 ▼
                              Redis (선택)      Tier3 큐 · API rate limit
```

### 1-1. DB별 역할 분담 (방법서 5·10장)

| 용도 | 선택 | 저장 대상 · 이유 |
|---|---|---|
| **Graph DB** | **Neo4j Community** | 노드·엣지 + 엣지 속성(subtype·시점·confidence·`evidence_id`). Cypher 생태계·GraphRAG 통합·자료 풍부 |
| **RDBMS** | **PostgreSQL** | 기업 마스터(전량 Tier1)·재무·프로파일 원본·공시 원문 메타. **JSONB**(sector/etf_list 배열)·**pg_trgm**(ER 블로킹)·시계열 확장 |
| **Vector DB** | **ChromaDB → (Qdrant)** | ① 엣지 근거 스니펫(엣지당 1, `id=evidence_id`) ② 기업 프로파일(1~3). MVP는 설치 편의, 확장 시 메타데이터 필터링 우위로 Qdrant 전환 |
| **캐시/큐** | **Redis** (선택) | Tier 3 온디맨드 수집 큐, DART API rate limit 카운터 |
| **파일/오브젝트 스토리지** | 로컬 → S3/MinIO | 공시 원문 전체 (임베딩 안 함). "저장은 전부, 임베딩은 선별" |

> **재무 수치 저장(정정):** **RDB 전용 + 노드엔 표시용 스냅샷만.** 당초 이중 저장으로 잡았으나 갱신 빈도·탐색 무관·페이로드 문제로 철회했다. 근거는 §6-2.

### 1-2. ★ PostgreSQL 채택의 파이프라인 상 이점

단순 취향 변경이 아니라 **방법서 요구사항과 직결**된다.

| PG 기능 | 방법서 대응 | 효과 |
|---|---|---|
| **`pg_trgm`** (trigram 유사도 + GIN 인덱스) | **12-3 개체 ER — Lexical 블로킹** ("임베딩 금지, char n-gram Jaccard로 후보 축소") | **블로킹 로직을 DB에 위임.** `similarity()`/`%` 연산자로 후보 추출 → 애플리케이션은 Jaro-Winkler 정밀비교만 담당. 별도 n-gram 인덱스 자체 구현 불필요 |
| **JSONB + GIN** | `Company.sector`·`etf_list` 배열 속성 | "반도체 섹터만"·"특정 ETF 구성종목만" 필터를 인덱스로 처리 |
| **파티셔닝/시계열 확장** | `financials` 연도·분기 누적 | 재무 시계열 조회 성능 |
| **배열 타입·CTE·윈도우 함수** | 프로파일 버전 관리, 매칭 실패율 집계 | 배치 통계 쿼리 단순화 |

> **주의:** `pg_trgm`은 **P2 뉴스 ER에서 진가를 발휘**한다. P1은 corp_code 정확 매칭으로 대부분 해소되므로, P1에서는 확장만 설치해두고 실제 블로킹은 P2에 활성화한다.

---

## 2. 현행 코드 자산 평가 (KEEP / REFACTOR / NEW)

| 구분 | 대상 | 조치 |
|---|---|---|
| 🟢 **KEEP** | `batch`/`pipeline`/`schemas` 3계층 구조 | 그대로 유지 |
| 🟢 KEEP | corp_code 매칭 ([base.py](pipeline/normalizer/base.py) `resolve_investee_corp_code`) | 방법서 ER 2단계 = 그대로 재사용 |
| 🟢 KEEP | 정규화 유틸(날짜·숫자·이름 파싱), `is_investment_vehicle` | 재사용 |
| 🟢 KEEP | `neo4j_importer` 제네릭 `apoc.merge` | 엣지 타입 바뀌어도 무수정 동작 |
| 🟢 KEEP | LLM 배치 인프라([llm_postprocess.py](pipeline/normalizer/llm_postprocess.py), Haiku 4.5 Batch) | 경로 C 서술 추출에 재활용 |
| 🟢 KEEP | 사업보고서 추출기([report_extractor.py](pipeline/extractors/dart/report_extractor.py) 등) | 경로 C 골격 |
| 🟡 **REFACTOR** | [dart_schemas.py](schemas/dart_schemas.py) | `OWNS_SHARE`+`INVESTS_IN`→`OWNS_STAKE_IN{subtype}`, `IS_OUTSIDE_DIRECTOR_OF`→`IS_EXECUTIVE_OF{subtype}`, 표준 메타 속성 추가, Product/Event/Organization DTO 신설 |
| 🟡 REFACTOR | normalizer 3종 | 신스키마·subtype·시점 속성 생성 |
| 🟡 REFACTOR | validators 3종 | 값 검증 + **노드-엣지 매트릭스 타입 검증** 추가 |
| 🔴 **NEW** | 경로 B 공시 원본 파서 | `SUPPLIES_TO`/`ACQUIRES`/`SUES`/`HAS_EVENT` — 최대 신규 작업 |
| 🔴 NEW | 경로 C 섹션 파서 확장 | `Product`/`DEVELOPS`/`PARTNERS_WITH`/`DEPENDS_ON`/계열사 |
| 🔴 NEW | fetch 확장 | 기업개황 `company`, 대량보유 `majorstock`, 임원소유 `elestock`, 주요계정 `fnltt`, 공시검색 `list`, 원문 `document` |
| 🔴 NEW | **PostgreSQL 계층** | 스키마 + 적재기 (기업마스터·재무·프로파일·원문) + `pg_trgm`/JSONB 인덱스 |
| 🔴 NEW | **Vector 계층 (ChromaDB)** | evidence·프로파일 청크 임베딩·적재. **어댑터 인터페이스로 추상화** → Qdrant 전환 대비 |
| 🔴 NEW | Redis (선택) | Tier3 수집 큐 · DART API rate limit 카운터 |
| 🔴 NEW | 프로파일 생성기 | RDBMS→Vector 동기 갱신 |
| 🔴 NEW | FastAPI 서빙 | 그래프 JSON API(`level_1_category` 주입) + evidence 조회 API |

---

## 3. 스키마 확정 (Part I)

### 3-1. 노드 5종
`Company` · `Person` · `Organization`(P1 스키마만, 데이터는 P2) · `Product`(경로 C) · `Event`(경로 B)
- **Sector는 속성** (`Company.sector` 배열) — 슈퍼노드 방어
- **InvestmentVehicle → Company 흡수(확정):** 펀드·신탁·투자조합도 전부 `Company` 노드로 생성(`market:"펀드"` 태그). **"지배 의도" 구분은 노드가 아니라 엣지가 담당한다** — `OWNS_STAKE_IN.subtype`(자회사·계열사=지배 / 출자·5%이상=재무투자) + `ratio` + `purpose`(타법인출자 `invstmnt_purps`, [investment_normalizer.py](pipeline/normalizer/investment_normalizer.py)가 이미 추출 중)가 의도를 표현. 노드 타입을 나누면 OWNS_STAKE_IN의 target 타입이 오염되므로 흡수가 옳다.

### 3-2. 엣지 — P1 범위 8종

| 엣지 | 소스 경로 | subtype 예시 |
|---|---|---|
| `OWNS_STAKE_IN` | A(17·18·30·majorstock·elestock) + C(IX 계열사) | 최대주주·5%이상주주·자회사·출자·계열사 |
| `IS_EXECUTIVE_OF` | A(임원·사외이사) | 직위, 사외이사 |
| `SUPPLIES_TO` | B(단일판매·공급계약) | 부품납품·파운드리위탁·소재공급 |
| `ACQUIRES` | B(합병·양수도·타법인주식취득) | 합병·영업양수도·주식취득 |
| `SUES` | B(소송 등의 제기) | — |
| `HAS_EVENT` | B(부도·회생·영업정지) | — |
| `DEVELOPS` | C(II-2 주요제품) | 개발·생산·제조 |
| `PARTNERS_WITH` `DEPENDS_ON` | C(II-6 주요계약·연구개발) | JV·MOU·기술이전 / 기술의존 |

> P2 승격분: `COMPETES_WITH`·`REGULATES`·`IMPACTS` (뉴스 필요).
> **⚠️ 개명 반영:** 구 `INTEGRATES` → **`DEPENDS_ON`**. 스키마·Validator·프론트 전부 신명칭.

### 3-3. 엣지 표준 속성 (전 엣지 필수, 방법서 4장)
`subtype` · `direction` · `sign`(IMPACTS) · `revenue_ratio`(SUPPLIES_TO) · `status`(ACQUIRES) · `valid_from` · `valid_until`(P1 null) · `last_seen` · `is_current` · `confidence`(DART=1.0) · `source_type` · `source_doc`(rcept_no) · **`evidence_id`**(Vector FK)

---

## 4. 데이터 소스 확정 (Part II) — DART API 목록

이전 확정안 반영. ✅=raw_dart에 이미 있음, ➕=신규 수집.

**경로 A — 정형 API**
| API | 엔드포인트 | 산출 | 상태 |
|---|---|---|---|
| 고유번호 | `corpCode.xml` | corp_code 마스터→PostgreSQL | ✅(company_map) |
| 기업개황 | `company.json` | Company 속성(대표자·업종·상장일) | ➕ |
| 최대주주 현황 | `hyslrSttus` | OWNS_STAKE_IN(최대주주) | ✅ |
| 최대주주 변동 | `hyslrChgSttus` | OWNS_STAKE_IN 변동이력 | ✅ 수집만→정규화 |
| 임원 현황 | `exctvSttus` | IS_EXECUTIVE_OF + Person | ✅ |
| 사외이사 현황 | `outcmpny…` | IS_EXECUTIVE_OF(사외이사) | ✅ 수집만→정규화 |
| 타법인 출자 | `otrCprInvstmntSttus` | OWNS_STAKE_IN(출자/자회사) | ✅ |
| 대량보유(5%) | `majorstock.json` | OWNS_STAKE_IN(5%이상) | ➕ |
| 임원·주요주주 소유 | `elestock.json` | OWNS_STAKE_IN(주요주주) | ➕ |
| 주요계정(재무) | `fnlttSinglAcnt` | 재무→PostgreSQL+속성 | ➕ |

**경로 B — 공시 원본** (`list.json`으로 검색 → `document.xml`로 원문): 단일판매·공급계약체결 / 회사합병·분할 / 영업·자산 양수도 / 타법인주식취득결정 / 소송 등의 제기 / 부도·영업정지·회생

**경로 C — 사업보고서 본문** (`pblntf_ty="A"`): II-2 주요제품 / II-6 주요계약·연구개발 / IX 계열회사

> **⚠️ 착수 즉시 실측(방법서 6장):** 시드 64개의 최근 2년 「단일판매·공급계약체결」 공시 건수. `SUPPLIES_TO` 전체가 여기 의존 → 기업당 평균 0.3건 미만이면 P2 뉴스로 이관 판단.

---

## 5. 개발 순서 (Part III) — 스프린트

방법서 부록 A 체크리스트를 코드 작업 단위로 그룹핑. **순서 엄수** — 노드 먼저, 엣지 나중, corp_code 먼저.

### ★ Sprint 0 실측 결과 (2026-07-25)

**인프라:** Docker 4종 기동·검증 완료, PostgreSQL 12종 스키마, corp_code_master **118,535건**(상장 3,979) 적재, pg_trgm ER 블로킹 실동작 확인.

**시드 공급계약 공시 보유량 (최근 2년):**

| 지표 | 값 |
|---|---|
| 총 체결 공시 | 305건 (정정 포함, 순 계약은 다소 적음) |
| 기업당 평균 | **4.77건/사** → §6 형식 통과(≥1) |
| **0건 기업** | **38/64 (59%)** ← ★주의 |

**핵심 발견 — 커버리지 비대칭:** 0건이 하필 반도체 핵심(삼성전자 2·SK하이닉스 0·원익IPS·리노공업·코미코·하나마이크론 등). 이유는 공시 의무 threshold(코스피 5%·코스닥 10%) — 대기업은 단일 계약이 매출 대비 작아 공시 안 함. 반대로 팹리스(파두 64·가온칩스 14)·방산(HD현대 58·현대로템 35)은 풍부.

**★ 비대칭은 완화됨 (원문 파싱으로 확인):** 대기업이 *스스로* 공급계약을 안 낼 뿐, **공급사들 공시에서 계약상대로 잡혀 inbound SUPPLIES_TO 엣지를 받는다.** 샘플 12건 중 SK하이닉스 3회·삼성전자·삼성디스플레이·기아·KAI가 계약상대로 등장. 밸류체인 리스크의 핵심(누가 납품하나)이 채워짐.

**전략 함의:**
1. 경로 B(공급계약)는 P1 그대로 — 305건, filer(공급사) + 계약상대(대기업 inbound) 양쪽 엣지 생성.
2. 대기업도 inbound 공급 엣지를 받으므로 P1 공급망이 생각보다 덜 빈약. 단 여전히 지분·임원·제품 축이 대기업의 주력.
3. II-4(매출·수주→SUPPLIES_TO) P1 당김은 **후순위로** (inbound로 상당 커버되므로 급하지 않음).

**공급계약 원문 파싱 — 완전 검증 (한미반도체→SK하이닉스 샘플):**
`계약상대방·계약금액(442억)·매출액대비(7.66%)·계약기간(2026-06-08~09-02)` 전부 추출, 방법서 §7 표와 1:1 대응. 1836 bytes UTF-8 XML. Sprint 2 파서 구현 확실.

**계약상대 ER 검증 (샘플 12건):** 국내 실명 7건 → 7건 매칭. 실패 5건은 전부 해외(Micron Malaysia·Raytheon Canada 등)·익명화("해외 Nand 제조사") = 방법서 §7 예측대로 unresolved stub/무엣지 처리.

**→ Sprint 1·2 반영할 결함 2개:**
- **파서**: 필드명은 `계약상대방`(계약상대 아님) — 정규식 정정.
- **ER 정밀도**: 동명 충돌(기아→stock_code 없는 다른 기아) → **동점 시 상장사 우선** 타이브레이크. KAI는 sim 0.308로 정답이나 낮음 → 임계값 상한 주의.

**부수 실측:** list.json 응답에 pblntf_ty 없음 → report_nm 매칭이 정답.

---

### Sprint 0 — 착수 전 검증 (인프라·실측)
- [ ] Docker Compose: **Neo4j Community + PostgreSQL + ChromaDB (+ Redis 선택)** 기동
- [ ] PostgreSQL `CREATE EXTENSION pg_trgm` 확인
- [ ] `.env` 확장 (PostgreSQL·Chroma·Redis 접속정보)
- [ ] OpenDART 키·일일 한도 확인, 신규 API(`majorstock`/`elestock`/`company`/`list`/`document`) 응답 구조 실측
- [ ] `corpCode.xml` → **전체 corp_code 마스터 → PostgreSQL `corp_code_master`** (Tier1, 전 종목 검색 기반)
- [ ] ★**시드 공급계약 공시 보유량 실측** (6장 기준표) — P1 성패 좌우
- [ ] `list.json` `pblntf_ty` 코드값 실측 + 공급계약 샘플 10건 파싱 가능성 확인
- [ ] **★DART 라이브러리 채택 검증** (§9-2) — 공시 유형 커버리지 / 원문 XML 원형 보존 / 호출 딜레이·재시도 제어 가능 여부. 미달 항목은 직접 `requests` 폴백으로 확정

### ✅ Sprint 1 완료 결과 (2026-07-25)

정형 골격 그래프 구축 완료. **경로 A(정형 API) = Company/Person 노드 + OWNS_STAKE_IN/IS_EXECUTIVE_OF 엣지.**

| 저장소 | 적재 결과 |
|---|---|
| **Neo4j** | Company 1,464(시드 64 full + stub 1,400) · Person 1,504 · 엣지 3,353 |
| **PostgreSQL** | corp_code_master 118,535 · companies 64 · financials 186(62사×3년) · staged_edges 3,365 |

**엣지 분포:** OWNS_STAKE_IN(출자 837·최대주주 546·자회사 515·5%이상주주 194) + IS_EXECUTIVE_OF(직위별·사외이사 160). 매트릭스 위반 0건.

**검증된 실제 밸류체인:** LG전자→LG이노텍(40.79%) · 삼성전자→레인보우로보틱스(35%) · 원익홀딩스→원익IPS(32.9%) · 삼성전자 지분(삼성생명 8.51%·이재용 1.65%) · 삼성전자 대량보유(삼성물산 19.69%) · 삼성전자 2025 매출 333.6조.

**핵심 구현 결정/발견:**
- person_key 통합(2-pass): 오너-경영자(곽동신 등)가 최대주주+임원 한 노드로 병합
- self-loop 가드 + fuzzy ER 임계값 0.50 → 오탐(고영→현대모비스) 제거
- **elestock 제외**: 3,364행 대부분 임원 0% 보유(폭발) + 5%↑는 majorstock 중복
- 재무 = RDB 전용 + 노드 최신매출 스냅샷만(§6-2)

**미적용(향후):** 개인주주 5%/임원겸직 필터(§10[2], shareholder_summaries) · fuzzy ER 정규화 컬럼 · 임원 subtype 정규화

---

### Sprint 1 — 스키마·기반·경로 A (세부 순서 확정 2026-07-25)
> 결정: **staged_edges 도입 · ER=corp_code_master(pg_trgm) 전환 · 범위 1A~1D 전체(5%지분·재무 포함)**

**1A. 스키마·기반**
- [ ] 1. DTO 리팩토링 [schemas/dart_schemas.py]: `OWNS_SHARE`+`INVESTS_IN`→`OWNS_STAKE_IN{subtype,ratio,purpose}` / `IS_OUTSIDE_DIRECTOR_OF`→`IS_EXECUTIVE_OF{subtype:"사외이사"}` / 표준 메타(valid_from·is_current·confidence·source_type·source_doc·evidence_id·created_at) / Person `person_key` / Product·Event·Organization DTO
- [ ] 2. 노드-엣지 매트릭스 Validator [pipeline/validators/matrix.py 신규] — 방법서 2-2 매트릭스 코드화
- [ ] 3. Neo4j 제약·인덱스 실행 모듈 [pipeline/importer/neo4j_schema.py 신규] — ERD §2-5 DDL

**1B. 적재 파이프라인 (기존 코드 리팩토링)**
- [ ] 4. ER 정비 [base.py] — corp_code_master(pg_trgm) 기반 전환 + **상장사 우선 타이브레이크**(Sprint0 결함)
- [ ] 5. 정규화기 3종 수정 [shareholder/executive/investment_normalizer.py] — 새 DTO 생성, raw_dart 재사용
- [ ] 6. staged_edges 적재기 [pipeline/importer/staging.py 신규] — 정규화 결과 → PostgreSQL staged_edges(Validator 결과 기록)
- [ ] 7. Neo4j 적재기 수정 [neo4j_importer.py] — staged_edges→MERGE + stub 3단 매칭·사건 멱등·예외처리 3종
- [ ] 8. Company 노드 [기업개황 company.json fetch + 시드 병합] — companies 테이블 + Neo4j Company

**1C. ★ 실행·검증 (첫 화면 결과)**
- [ ] 9. 64개 시드 재적재 실행
- [ ] 10. ★ Neo4j Browser 골격 그래프 확인 (지분·임원 엣지)

**1D. 확장**
- [ ] 11. 5% 지분공시(majorstock/elestock) → `OWNS_STAKE_IN{subtype:"5%이상"}`
- [ ] 12. 재무(fnltt) → financials 테이블 + Company 스냅샷

- **결과: 지분·임원 골격 그래프가 캔버스에 뜬다**

### ✅ P1 최종 검토 (2026-07-27) — 사용성 관점 결함 점검

플랫폼 사용성 기준으로 전수 점검하고 결함을 수정했다.

**수정 완료 (🔴 심각):**

| # | 결함 | 조치 |
|---|---|---|
| 1 | **전 엣지 근거 누락** — OWNS_STAKE_IN·IS_EXECUTIVE_OF 2,174건에 `source_doc`·`evidence_id` 없음 | 원본 `rcept_no` 연결 + 경로 A용 evidence 스니펫 생성기([path_a_evidence.py](pipeline/importer/path_a_evidence.py)). **근거 100% 달성** |
| 2 | **APOC MERGE 버그** — `apoc.merge.relationship`의 4번째 인자는 onCreate라 기존 엣지 속성이 재적재해도 갱신 안 됨 | onMatch 인자 추가(관계만, 노드는 시드 보호 위해 onCreate 유지) |
| 3 | **`last_seen` 전 엣지 0건** — 신선도 판정 불가 | 아래 §신선도 참조 |

**★ 신선도 판정 — 방법서 §4의 "6개월 룰"을 소스별 주기로 정교화:**

방법서는 "6개월 미갱신 → 과거 관계로 간주"라 했으나 **일괄 적용하면 정상 관계를 오판**한다.
삼성생명의 삼성전자 8.51% 지분은 10년째 유지되는데 사업보고서는 **연 1회**뿐이다.

```python
SOURCE_REFRESH_CYCLE_DAYS = {
    "dart":        365,  # 사업보고서 연 1회 → 11개월 미갱신도 정상
    "dart_filing":   0,  # 개별 공시 → valid_until이 진짜 종료일
    "news":        180,  # 뉴스만 6개월 룰 (P2)
}
```
판정 우선순위: ①`is_current=false`/`valid_until` 경과 = **expired**(명확한 사실 우선)
②`last_seen`이 주기×1.5 이내 = **current** ③초과 = **stale**(신뢰도 0.6배)

구현: [pipeline/freshness.py](pipeline/freshness.py) — `assess()` / `effective_confidence()`.
실측 검증: 208일 경과 임원 관계가 `CURRENT` 판정(6개월 룰이면 오판했을 케이스).

**남은 한계 (P2·서빙에서 해결):**

| 항목 | 현황 | 해결 경로 |
|---|---|---|
| **공급망 2홉 경로 1건** | 체인 중간 노드가 시드여야 성립하는데 타겟 대부분이 stub | 시드 확대(구조) + P2 뉴스(공시 없는 관계) |
| 슈퍼노드 | NAVER 296·LG전자 210·삼성전자 177 | 서빙 API에서 degree 제한·페이징 |
| 고립 시드 2곳 | 케이씨텍·태성(비상장, 공시 없음) | 프론트 "데이터 준비 중" 안내 |
| 제품 0개 3곳 | 현대차증권·케이티·제이브이엠(금융·통신 목차 상이) | 매체별 파서 or P2 |
| `ingest_runs` 미사용 | 배치 이력·매칭 실패율 미기록(§13) | 운영 단계 |

**audit 확장:** `[F-근거]`(source_doc·evidence_id 누락) + `[F-신선도]`(last_seen 누락) 검사 추가 → 재발 시 즉시 탐지.

---

### ✅ Sprint 2 완료 (2026-07-26) — 경로 B 공시 원본 전체

경로 B 완결: SUPPLIES_TO(2C) + ACQUIRES/SUES/Event(2D).

**2D 사건 엣지 (주요사항보고서 구조화 API):**
| 유형 | 소스 | 결과 |
|---|---|---|
| **ACQUIRES** | 합병(cmpMgDecsn)·주식취득(otcprStkInvscrInhDecsn) | **14건** (카카오→다음글로벌, 두산로보틱스→두산에너빌리티, 코미코→미코세라믹스 등) |
| **SUES** | 소송(lwstLg) | 0 (상대 익명 개인 → Event로) |
| **Event+HAS_EVENT** | 소송/부도/회생 | **1건** (현대차증권 신주발행금지가처분) — 첫 Event 노드 |

**실측 결론:** 건강한 반도체·로봇 시드라 ACQUIRES는 실질 데이터, 부도·회생·영업정지=0(Event는 P2 뉴스 영역). 소송 상대가 익명이면 SUES 대신 Event+HAS_EVENT로 사실 기록.

**데이터 품질 감사(audit_graph) 확립:** 6범주 ~22검사(무결성·참조·범위·통계·크로스-DB). 🔴 무결성 오류 0건. 적재마다 실행해 회귀 방지. 이 과정에서 발견·수정: Person 폭발(1504→827, 미등기 필터), SUPPLIES_TO garbage(선주·문장 → 0), Organization 오분류, Event name/title 검사.

---

### Sprint 2C 결과 — 공급계약 → SUPPLIES_TO + 팩트체크

경로 B 공급계약 파싱 완료. **ChromaDB 첫 적재 + 팩트체크(엣지→근거) 작동.**

| 저장소 | 결과 |
|---|---|
| **Neo4j** | SUPPLIES_TO **140건** 추가 (총 엣지 OWNS_STAKE_IN 2092·IS_EXECUTIVE_OF 1261·SUPPLIES_TO 140) · Company 1,583 |
| **ChromaDB** | evidence 컬렉션 140청크 (OpenAI text-embedding-3-small) |
| **PostgreSQL** | documents 393(원문메타) · vector_chunks 141(레지스트리) |
| **파일** | data/documents/{rcept_no}/ 원문 393건 |

**실제 반도체·로봇 밸류체인:** 테크윙·와이씨→삼성전자, 주성엔지니어링·넥스틴·한미반도체(7.7%)→SK하이닉스, 삼성에스디에스→삼성전자(3.6%), 뉴로메카→큐렉소. 대기업(삼성·SK)이 다수 공급사의 inbound 엣지를 받음(Sprint 0 inbound 통찰 실증).

**팩트체크 흐름 완성:** 엣지 클릭 → `evidence_id` → ChromaDB → 근거문("한미반도체는 SK하이닉스와 공급계약 체결, 442억원, 매출 7.66%…"). BizNode XAI 정체성 작동.

**핵심 구현 결정/발견:**
- **결정적 evidence_id 해시**(§5-3): 재실행 멱등, 엣지-청크 고아 방지
- **크로스-DB 쓰기 순서**(§5-2): staged→Chroma→Neo4j→loaded_at
- **파서 결함 2건 수정**: ①필드 라벨 "계약상대"/"계약상대방" 혼재 ②과다포착으로 실명 계약 오판 → 정밀 종료자 + 설명형("해외 Nand 제조사") 명시 스킵. 수정으로 **엣지 78→140 회복**
- 원문 로컬 캐시 → 재실행 시 DART 재호출 없음
- 실제 유보율 ~20%(78건, 팹리스·소부장 영업기밀) — 방법서 §7 예측대로. 방산(HD현대·현대로템)은 계약상대 공개

**범위:** 공급계약(SUPPLIES_TO)만. ACQUIRES·SUES·Event는 다음(2D).

---

### Sprint 2 — 경로 B 공시 원본 ★핵심
> **수집=라이브러리 / 파싱·정규화·적재=자체** (§9-2). 아래 앞 2줄만 라이브러리, 나머지는 전부 자체 구현.

- [ ] 공시 목록 수집기(유형 필터) — 라이브러리 `search_filings` 래핑
- [ ] 원본 다운로드 → **파일 스토리지 보관** + PostgreSQL `documents` 메타 — 라이브러리 `document` 래핑
- [ ] **공급계약 파서 → SUPPLIES_TO** + evidence 스니펫 → ChromaDB (매출대비%→`revenue_ratio`, 계약기간→`valid_from/until`)
- [ ] 합병·양수도 파서 → ACQUIRES / 소송 파서 → SUES / 부도·회생 → Event+HAS_EVENT
- [ ] 계약상대방 공시유보 → 엣지 보류, HAS_EVENT로 사실만 기록
- **결과: 공급망·M&A·리스크 엣지 + 근거 조회**

### ✅ Sprint 3 완결 (2026-07-27) — 경로 C 전체 (제품 + 계약관계)

**II-6 주요계약 → PARTNERS_WITH / DEPENDS_ON 추가 (LLM 추출):**

| 엣지 | 건수 | 실제 예 |
|---|---|---|
| `PARTNERS_WITH` | 32 | 삼성전자↔Google·Qualcomm·Ericsson·Huawei·Nokia(특허 라이선스), SK하이닉스↔Rambus(크로스 라이선스) |
| `DEPENDS_ON` | 11 | HD현대 정유 공정 라이선스 8종(FCC 촉매분해·탈황·황산재생), SK하이닉스 NAND |

- **대칭 엣지 처리**: PARTNERS_WITH는 방법서 §11대로 키 사전순 단방향 저장(A→B, B→A 중복 방지), 조회는 방향 무시.
- **LLM + 코드 가드 2단**: 프롬프트로 소유권이전 거래(양수도·신주인수·SHA) 제외 지시 + 코드에서 재차 필터(프롬프트만 신뢰하지 않음). 일반명사 counterparty("계열회사"·"기술사용허락")도 차단.

**⚠️ IX 계열회사 → OWNS_STAKE_IN 엣지로 만들지 않기로 결정(방법서 §7 문자와 다름):**
계열사는 "소유"가 아니라 **같은 그룹 형제사**다. 삼성전자→삼성물산을 OWNS_STAKE_IN으로 만들면 ①거짓 소유 관계 ②67개사 clique(2,200+ 허위 엣지) ③실제 지분은 이미 경로 A(출자30·최대주주17)가 커버. → **`Company.business_group` 속성**으로 두는 것이 의미상 정확(Sector를 속성화한 것과 같은 원리). P1에서는 미구현, 필요 시 속성으로 추가.

**경로 C 최종:** Product 335노드 · DEVELOPS 357 · PARTNERS_WITH 32 · DEPENDS_ON 11.

---

### Sprint 3 핵심 (2026-07-27) — 사업보고서 II-2 → Product + DEVELOPS

경로 C 착수, **LLM 서술 추출 첫 도입**(OpenAI gpt-4o-mini). 표·서술이 지저분해 규칙 대신 LLM 구조화 추출.

| 저장소 | 결과 |
|---|---|
| **Neo4j** | Product 270노드 · DEVELOPS 282엣지 |
| **ChromaDB** | evidence 282청크 |

**★공유 Product 노드로 경쟁구도 자동 형성:** 삼성↔LG(TV·냉장고·에어컨), 삼성↔제주반도체(DRAM·NAND), 유일↔나우↔클로봇(로봇), LG전자↔LG이노텍(Camera). "누가 DRAM 만드나" 질의 가능 → P2 COMPETES_WITH 추론 기반.

**추출 흐름:** find 사업보고서(pblntf_ty A) → parse_sections(목차 분리) → II-2 텍스트 → OpenAI JSON 구조화 추출 → Product norm_name(소문자·공백제거)로 공유 노드.

**현재 6개 엣지 타입:** OWNS_STAKE_IN 1667·IS_EXECUTIVE_OF 495·DEVELOPS 282·SUPPLIES_TO 122·ACQUIRES 14·HAS_EVENT 1. 노드: Company 1573·Person 506·Product 270·Event 1.

**남은 경로 C(선택):** IX 계열사→OWNS_STAKE_IN{계열사}(30번 출자와 중복), II-6→PARTNERS_WITH·DEPENDS_ON(서술 sparse, "해당없음" 다수).

---

### Sprint 3 — 경로 C 사업보고서
> 원본 확보는 Sprint 2와 동일 정책(라이브러리). 기존 [downloader.py](pipeline/extractors/dart/downloader.py)와 통합.

- [ ] 목차 기준 섹션 분할기(기존 `report_extractor` 확장) — **타겟 섹션만**(II-2·II-6·IX)
- [ ] II-2 → Product + DEVELOPS (표=규칙) / II-6 → PARTNERS_WITH + DEPENDS_ON (서술=LLM 배치) / IX → OWNS_STAKE_IN(계열사)
- [ ] **익명 표기 필터** — "기재 생략" 건 스킵, 섹션은 유지
- **결과: 제품·기술 카테고리 필터가 채워짐**

### Sprint 4 — 프로파일·서빙
- [ ] 기업 프로파일 생성 → PostgreSQL `company_profiles` + ChromaDB profile 청크 (버전 관리, RDBMS 갱신 시 Vector 동기)
- [ ] FastAPI: 그래프 JSON API(**`level_1_category` 백엔드 주입**) + evidence 조회 API(`evidence_id` 기반)
- [ ] 캔버스 렌더링 연동 + 관계 L1 필터 5종
- **결과: P1 완료 — 성공기준 충족**

### P1 완료 게이트 → P2
- [ ] 뉴스 샘플 200~300건 → ER 매칭률 사전 검증 / 뉴스 본문 확보 방안 확정 → 12장 뉴스 파이프라인 착수

---

## 6. PostgreSQL 스키마 초안

> **정본 DDL은 [infra/postgres/init/02_schema.sql](infra/postgres/init/02_schema.sql)** (+ [01_extensions.sql](infra/postgres/init/01_extensions.sql)).
> 전부 멱등이라 실행 중인 DB에도 재적용 가능하다.

### 6-1. 테이블 9종

| # | 테이블 | 역할 | 비고 |
|---|---|---|---|
| 1 | `corp_code_master` | 전 종목(10만+) 얕은 마스터 | **Tier 1 전 종목 검색** · `corp_name` pg_trgm GIN = **ER 블로킹 기반** |
| 2 | `companies` | 시드 기업 상세 | `sector`·`etf_list` JSONB + GIN |
| 3 | `financials` | 재무 시계열 | **RDB 전용**(§6-2 참조) |
| 4 | `documents` | 공시 원문 **메타** | 전문은 파일 스토리지 |
| 5 | `company_profiles` | 프로파일 원본 | Vector 청크의 소유자 |
| 6 | `ingest_runs` | 배치 실행 로그 | 매칭 실패율 추적 |
| 7 | **`shareholder_summaries`** | 개인주주·기관투자자 **요약** | ★Person 노드 폭발 방어 |
| 8 | **`vector_chunks`** | Vector 청크 레지스트리 | ★청크 생명주기 관리 |
| 9 | **`staged_edges`** | 파서 → Neo4j 사이 착지대 | ★재적재·검증·정형/비정형 대조 |

**7~9번 추가 근거**

- **`shareholder_summaries`** — 방법서 10장 [2] "개인은 임원겸직·5% 이상만 Person 노드, 나머지는 요약". 이 요약의 저장처가 없으면 개발 중 Neo4j 노드로 만들어버려 **정확히 막으려던 Person 폭발**이 발생한다. `companies`의 JSONB 컬럼이 아니라 별도 테이블인 이유: 지분 요약은 공시 주기마다 바뀌는 **시점 데이터**라, 정적 마스터에 섞으면 덮어쓰기로 이력이 소멸한다(= `financials`를 분리한 것과 같은 이유). PK `(corp_code, base_date)`.
- **`vector_chunks`** — 방법서 5장·10장 [6]이 "RDBMS 갱신 시 Vector 동시 갱신"을 요구하는데, **무엇을 지울지 알 방법이 없었다.** 이 표가 없으면 ①프로파일 갱신 시 삭제 대상 특정 불가 ②엣지 `evidence_id`의 실존 검증 불가 ③임베딩 모델 교체 시 재임베딩 대상 특정 불가. `content_hash`로 내용 무변경 건은 재임베딩을 건너뛴다.
- **`staged_edges`** — 파서 산출물을 Neo4j 직행시키지 않는다. ①**재적재**: 그래프를 비우고 다시 넣을 때 DART 재호출 불필요(호출 한도 방어) ②**품질 점검**: 매트릭스 위반 건을 `validation_error`에 남겨 파서 정확도 추적 ③**P2 대조**: 뉴스 관계가 DART에 있는지 SQL 비교. `loaded_at IS NULL`이 미적재 큐 역할.

> 기존 `data/normalized/*.json`이 파일 기반 스테이징 역할을 하고 있었으나, **집계·조인이 불가**해 위 3가지 용도를 못 한다. RDB 스테이징으로 승격한다.

### 6-2. ⚠️ 재무 저장 정책 정정 — 이중 저장 철회

당초 "Company 노드 속성 + RDB 이중 저장"으로 잡았으나 **RDB 전용 + 노드엔 표시용 스냅샷만**으로 정정한다.

| | 철회 이유 |
|---|---|
| 갱신 빈도 | 분기마다 바뀌는 값이라 그래프가 계속 변한다 |
| 탐색 무관 | 재무는 다중홉 경로 탐색에 쓰이지 않는다(Finance Agent가 RDB 직조회) |
| 페이로드 | 노드가 무거워져 서브그래프 반환 시 응답이 커진다 |

→ **Company 노드**: 최근 매출·시가총액 정도의 **표시용 최소 스냅샷**만
→ **`financials` 테이블**: 전체 시계열 (연도·분기별)

방법서 경로 A 표의 "재무 → **RDBMS**(그래프 아님)"와도 이쪽이 일치한다.

### 6-3. 다루지 않는 것

| 항목 | 처리 |
|---|---|
| stub 기업 빈도 추적 | `staged_edges`의 미매칭 target을 집계해 도출(별도 테이블 불필요). 2차 시드 승격 판단에 사용 |
| 뉴스 URL 레지스트리 | **P2 착수 시 추가** |
| API 호출 로그 | **Redis**로 처리 (분당 한도 카운터는 RDB에 부적합) |

---

## 7. Vector DB 컬렉션 초안 (ChromaDB → Qdrant)

```
evidence   : id=evidence_id, text=근거 스니펫,
             metadata={edge_id, edge_type, source_corp, target_corp, rcept_no, occurred_at}
profile    : id=prof_{corp_code}, text=기업 요약,
             metadata={corp_code, version, updated_at}
```
> Neo4j 엣지의 `evidence_id` ↔ Vector `id` 강결합. 팩트체크 = 엣지 클릭 → `evidence_id`로 직접 조회(전문 스캔 불필요).

**Qdrant 전환 대비 — 어댑터로 추상화한다.**
`pipeline/vectorstore/base.py`에 인터페이스(`upsert`/`query`/`delete_by_corp`)를 두고 `chroma_store.py`를 구현체로 둔다. 전환 시 `qdrant_store.py`만 추가.

| | ChromaDB (MVP) | Qdrant (확장) |
|---|---|---|
| 채택 이유 | 설치·기동 간편, 임베디드 모드 | **메타데이터 필터링 성능·표현력** |
| 전환 시점 | — | evidence 청크가 수십만 건 넘고, `edge_type`+`corp_code` 복합 필터 지연이 체감될 때 |

> 우리 evidence 조회는 **메타데이터 필터 의존도가 높다**(특정 기업·엣지타입의 근거만). 이게 Qdrant를 예정 전환 대상으로 두는 이유다.

---

## 8. P1 성공 기준 (방법서)

> **"시드 기업의 지분·임원·공급·제품 관계가 캔버스에 뜨고, 엣지를 클릭하면 근거가 보이며, 관계 필터 5개가 작동한다."**

---

## 9. 결정사항

### 9-1. 확정 (2026-07-24)

| # | 결정 | 내용 |
|---|---|---|
| 1 | **InvestmentVehicle → Company 흡수** | 펀드·신탁·조합 전부 Company(`market:"펀드"`). 지배 의도는 `OWNS_STAKE_IN`의 `subtype`+`ratio`+`purpose`가 표현 (§3-1) |
| 2 | **재무 = RDB 전용** (정정) | `financials` 테이블에 시계열, Company 노드엔 표시용 스냅샷만. 이중 저장 철회 — §6-2 |
| 3 | **공시 수집=라이브러리 / 파싱·정규화·적재=자체 구현** | 아래 9-2 참조 |
| 4 | **P1 엣지 범위** | 지분·임원 + M&A/사건 + 공급·제품 (8종) |
| 5 | **DB 스택 확정** | Graph=**Neo4j Community** / RDBMS=**PostgreSQL**(JSONB·pg_trgm·시계열) / Vector=**ChromaDB→(Qdrant)** / 캐시·큐=**Redis**(선택). 근거는 §1-1·§1-2 |

### 9-2. 경로 B/C 수집 방식 — 하이브리드 (확정)

**역할 분리를 명확히 한다.**

| 단계 | 담당 | 비고 |
|---|---|---|
| 공시 **검색·원본 확보** (`search_filings`, `document` 다운로드) | **라이브러리 활용** (OpenDartReader 등) | 접수번호 조회·zip 해제 등 보일러플레이트 절감 |
| 원본 **파싱** (표준 서식 → 필드) | **자체 구현** | 서식별 규칙 파서. 우리 스키마에 1:1 매핑 필요 |
| **정규화·검증·적재** | **자체 구현** | 기존 `pipeline/normalizer`·`validators`·`importer` 계층 확장 |

> **이탈 조건:** 라이브러리가 (a) 필요한 공시 유형을 커버 못 하거나 (b) 원문 XML 원형을 손실하거나 (c) 호출 제어(딜레이·재시도)가 불가하면 → **해당 부분만 직접 `requests`로 대체**. 현행 [fetch_dart_to_json.py](batch/fetch_dart_to_json.py) 방식이 폴백.
> Sprint 0에서 라이브러리 실측(공시 유형 커버리지·원문 보존 여부)을 먼저 수행해 채택 여부를 확정한다.

### 9-3. 미결 (진행 중 실측 후 확정)

| # | 항목 | 시점 |
|---|---|---|
| 5 | 개인주주 Person 생성 임계 지분율 | Sprint 1 (기본값 5%) |
| 6 | 파일 스토리지: 로컬 `data/documents/` vs S3/MinIO | MVP 로컬 → 확장 시 전환 |
| 7 | 임베딩 모델 (한국어 성능) | Sprint 2 (P1 evidence만이라 소량) |
| 8 | 사업보고서 대상 연도 범위 | Sprint 3 (기본: 최신 1건) |
| 9 | ER 임계값 θ · Product 명칭 정규화 규칙 | 실측 후 |
```
