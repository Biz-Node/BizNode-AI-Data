# BizNode

**GraphRAG 기반 기업 관계·리스크 분석 워크스페이스**

> 2026 한이음 드림업 · 금융/핀테크 · 팀 쑥부르스도나스
> 문서: 이 파일(개요·서비스) · [코드 지도](CODEMAP.md)(파일이 어디에 무엇을) ·
> [데이터 수집 방법서](BizNode_데이터수집_방법서.md)(설계·실측·운영 규칙) ·
> [데이터베이스 ERD](BizNode_데이터베이스_ERD.md)(그래프·벡터·RDB 스키마와 교차 키) ·
> [추출 진행현황](BizNode_추출_진행현황.md)(자동 생성)

---

## 1. 한 줄 정의

DART 공시와 비즈니스 뉴스를 지식그래프로 결합해, 기업 간 **N차 연쇄 파급 리스크**를
시각화하고 **모든 주장에 원문 근거를 붙여** 답하는 분석 플랫폼.

## 2. 문제와 해법

| 문제 | BizNode의 해법 |
|---|---|
| 공급망 리스크가 2~3단계 건너 오는데 개별 기업만 봐서는 안 보인다 | 지식그래프 + 질의 시점 파급 계산 |
| LLM 요약은 그럴듯하지만 출처를 못 댄다 | **모든 엣지에 원문 문장·기사 URL** (현재 6,523/6,523 = 100%) |
| 2년 전 끝난 계약이 현재 리스크로 표시된다 | 신선도 판정(current/stale/expired) + 종료 관계 탐지 |

## 3. 차별점 — 팩트체크

```
엣지 클릭 → 관계 유형 · subtype 전체 · 방향 · 신선도 · 뒷받침 출처 수
          → 원문 문장 그대로 인용
          → 기사 제목 + URL (또는 DART 접수번호)
          → 검증 이력 (방향 교정 · 유형 재분류 · 근거 검증 결과)
```

추출한 관계는 자동 검사 7종을 거친다. 방향·대칭 병렬언급·양방향 공급·근거 정합성은
현재 뉴스 엣지 **100% 커버리지**이며, 커버율과 사각지대는 `batch/audit/coverage.py`가
매 배치마다 출력한다.

---

## 4. 서비스 구성

### 4-1. 홈
```
통합 검색     기업 · 인물 · 사건 · 제품 + 이슈 의미검색(벡터DB)
             → 검색 결과에서 '워크스페이스에 추가'
트렌드        사건 발생 추이 (월별 리스크 건수)
AI 인사이트   ❌ 추론 계층 필요
빠른 탐색     기업 탐색 · 산업 지도(그래프) · 즐겨찾기
최근 활동     ❌ 사용자 DB 필요
```

### 4-2. 워크스페이스 ★핵심
```
┌───────────────┬──────────────────┬───────────────┐
│ (좌) 상세·근거  │ (중) 그래프 캔버스  │ (우) AI 챗봇    │
│                │                  │                │
│ 노드 클릭       │ 노드 4,889        │ 심층 분석       │
│  → 요약 카드    │ 엣지 6,523        │ ❌ 추론 계층    │
│ 엣지 클릭       │ 유형 12종         │    필요        │
│  → 원문 근거    │ 필터·확장·저장     │                │
│ [상세 페이지 →] │                  │                │
└───────────────┴──────────────────┴───────────────┘
```

**노드별 제공 정보**

| 노드 | 요약 (클릭) | 상세 (통합검색) |
|---|---|---|
| Company (시드 64) | 재무 3지표 · 관계 수 · 최근 리스크 3건 | 개요 · 재무 3개년 · 관계 전체 · 사건 전체 · 파급 분석 · 공급망 지도 |
| Company (stub) | 이름 · 관계 목록 (「관계 정보만 있음」 표시) | 동일 |
| Person | 이름 · 생년월 · 소속·직위 | + 관련 사건 |
| Event | 유형(11종) · 리스크 여부 · 시점 · 근거 | + 연결 기업 · 파급 계산 |
| Product | 이름 · 분류 | + 개발사 · 의존 기업 |

### 4-3. 뉴스/이슈
최신 뉴스 목록 (7,185건 · 기업·리스크 유형 필터)
※ 수집이 배치라 **「실시간」이 아닌 「최신」**

### 4-4. 리서치 보관함 · 마이페이지
사용자 DB·인증 필요 — 미구현 (후순위)

---

## 5. 사용자 시나리오

```
1. "SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"
     ↓ [그래프] event_type ∈ {사고재해, 공급망} · is_risk=true
2. 사건 3건 — 청주공장 화재 · HBM4 라인 전환 연기 · 담합 피소
     ↓ [벡터DB] evidence_id → 원문 회수
3. 근거 문장 + 기사 URL 제시
     ↓ [그래프] SUPPLIES_TO 하류로 파급 계산
4. "어디까지 번지나" → 애플·구글·엔비디아 (영향도 0.41)
   경로: 청주 화재 → SK하이닉스 → SUPPLIES_TO → 엔비디아
     ↓
5. 워크스페이스 저장 → 스냅샷 + AI 분석 보관
```

**「보도됨」과 「계산」을 구분해 제시한다.** 기사가 말한 파급과 그래프가 추론한 파급을
섞지 않는 것이 신뢰의 핵심이다.

---

## 6. 아키텍처

```
┌─ 오프라인 (팀 운영 · 배치) ────────────────────────────┐
│  DART API·공시 ──┐                                    │
│                  ├─→ 추출 → 검증 → 마스터 그래프        │
│  구글뉴스·네이버 ─┘        (batch/*.py)                 │
└─────────────────────────────────────────────────────┘
                          ↓ 조회만
┌─ 온라인 (서비스) ──────────────────────────────────────┐
│  Neo4j        관계·구조 (노드 4,889 · 엣지 6,523)       │
│  ChromaDB     근거 청크 6,240 (원문+제목+URL · 의미검색) │
│  PostgreSQL   재무 · 뉴스 메타 · 추출 대장 · 사용자 DB   │
│      ↓                                                │
│  app/services/graph_service.py                        │
│    relations_of()    신선도 적용 조회                   │
│    propagate_risk()  질의 시점 2홉 파급 계산             │
│      ↓                                                │
│  API ❌ → 프론트엔드 ❌                                 │
└─────────────────────────────────────────────────────┘
```

**사용자가 기업을 추가해도 수집이 돌지 않는다.** 마스터 그래프를 조회할 뿐이고,
사용자 DB에는 워크스페이스와 참조(기업 ID)만 저장한다.

## 7. 기술 스택

```
저장   Neo4j 5 · ChromaDB · PostgreSQL 16(+pg_trgm) · Redis
추출   OpenAI gpt-4o (트리플 추출) · gpt-4o-mini (라우터·검증·분류)
수집   OpenDART API · 구글 뉴스 RSS · 네이버 검색 API · trafilatura
언어   Python 3.11 · Docker Compose
```

---

## 8. 현재 상태 (2026-08-01)

```
시드 64개사 중 뉴스 추출 완료   29개사
그래프                        노드 4,883 · 엣지 6,518 · Event 566 (리스크 203)
근거                          엣지 100%가 원문 보유
검사 커버율                    뉴스 근거정합성 3,770/3,770 (100%)
                              DART 본문파싱 원문대조 528/542 (97%)
                              DART 구조화필드 12종 검사 · 매트릭스 위반 0건
표시된 의심                    456건 / 6,518 (7.0%) — 삭제하지 않고 조회에서 거른다
기업 상세                      개요 60개사 · 사업부문 201건(금액 신뢰 155)
누적 추출 비용                 약 3.5만원
```

| 기능 | 상태 |
|---|---|
| 그래프 데이터 · 원문 팩트체크 | ✅ |
| 이슈 의미검색 · 사건 분류(11종) | ✅ |
| 리스크 파급 계산 | ✅ |
| 기업 개요 · 사업부문 | ✅ 60개사 (신뢰 표시 포함) |
| 주가 · 시세 | ⏸ 보류 (방법서 확장 테이블) |
| 추론 계층 (자연어 질의 → 답변) | ❌ |
| API · 프론트엔드 | ❌ |
| 사용자 인증 · 보관함 | ❌ |

**신뢰할 수 없는 값은 지우지 않고 표시한다.** 화면에 내보낼 때 걸러야 할 것:

| 표시 | 뜻 | 화면 처리 |
|---|---|---|
| `grounding_suspect` + `unfounded`/`insufficient` | 근거가 관계를 뒷받침 못함 (431건) | **`relations_of()`가 이미 숨김** |
| `grounding_suspect` + `wrong_type` | 관계는 실재하나 유형·방향이 틀림 | 숨기지 않고 점수 ×0.5 |
| `field_suspect` | DART 필드 값이 범위·구조에 안 맞음 (11건) | 값을 표시하지 말 것 |
| `parsed_suspect` | 사업보고서 원문에 그 이름이 없음 (14건) | 「출처 확인 중」 |
| `business_segments.revenue_trusted=false` | 부문 매출 단위 판정 불가 (12개사) | 금액 감추고 비중만 |
| `business_segments.ratio_trusted=false` | 비중 합계가 100%에서 벗어남 (7개사) | 비중 감추고 금액만 |
| `eventness_suspect` | 사건 이름에 행위가 없음 | 개명 대기 |

**조회 계층이 이미 거릅니다.** `relations_of()`·`propagate_risk()`가 근거 의심을
필터링하므로 API는 그냥 부르면 됩니다. 검토 화면에서 의심분까지 보려면
`relations_of(name, hide_verdicts=())`.

---

## 9. 실행

### 준비
```bash
cp .env.example .env      # OPENAI_API_KEY · DART_API_KEY · NAVER_*
docker compose up -d      # Neo4j · PostgreSQL · ChromaDB · Redis
pip install -r requirements.txt
```

### 데이터 구축
```bash
# 1) DART — 마스터·재무·지분·임원·공시·제품·거래처
python -m batch.build.graph
python -m batch.build.disclosures
python -m batch.build.business_reports
python -m batch.build.sales_customers
python -m batch.build.company_detail       # 개요·사업부문

# 2) 뉴스 — 3~5개사씩 나눠서 (구글 속도 제한 회피)
#    ★설정을 손으로 주지 않는다. 기본값이 곧 표준(5년 · 월별분할 · 상한 240)이다.
python -m batch.ops.run_companies --plan 5

# 3) 정리·검증 — 배치가 끝나면 한 번. **아래를 전부 순서대로 실행한다.**
python -m batch.ops.finalize
```

### 확정된 수집 절차 (2026-08-02)

**표준 설정 = 5년 · 월별분할 · 상한 240.** `run_companies`의 기본값에 박아 뒀다.
바꾸면 그 기업만 연결 밀도가 달라져 **다른 기업과 위험도를 비교할 수 없다**
— 리스크 파급이 연결 수에 직접 반응하기 때문이다(허브 감점 `40/(40+차수-1)`).

```bash
# ⓪ 넣기 전 — 시드가 기존 노드에 붙는지 확인 (무료·1분)
python -m batch.audit.graph          # 「시드 추가 시 노드가 갈리는 기업: 0곳」이어야 한다

# ① 표준이 아닌 설정으로 모은 기업을 먼저 맞춘다 (17곳 · 약 7,000원)
python -m batch.ops.run_companies --recollect 5      # 5곳씩 나눠서
python -m batch.ops.finalize

# ② 미진행 기업 확장 (35곳 · 약 45,000원) — 밸류체인 우선순위로 자동 정렬
python -m batch.ops.run_companies --plan 5           # 5곳씩, 며칠에 나눠
python -m batch.ops.finalize                         # 5곳 끝날 때마다

# ③ 다 끝나면 사람이 본다
python -m batch.audit.spot_check --source news
```

> **왜 5곳씩인가** — 5년·월별이면 기업당 60개 질의라, 52곳을 한 번에 돌리면
> 3,000개가 넘어 구글이 막는다(실측: 심텍 480개 질의 전량 503).
> 막히면 `exit 3`으로 끝나고 **대장에 기록하지 않아** 나중에 다시 돌 수 있다.

> **비용은 상한이 아니라 예상치를 본다** — 추출량은 상한이 아니라
> 「관련 판정을 통과한 기사 수」가 정한다. 29개사 중 상한을 채운 건 3곳뿐이다.
> `run_companies`가 둘 다 찍는다.

`finalize`가 도는 순서(개별 실행도 가능):

```bash
# ① 회귀확인 — 프롬프트를 고쳤다면 여기서 먼저 걸린다 (150원·30초)
python -m batch.audit.selftest                 # 추출기·검증기 양쪽 쏠림 검사

# ② 정리 — 노드·엣지·근거를 제자리로 (대부분 0원)
python -m batch.repair.node_names              # 이름이 불량한 노드 복구
python -m batch.repair.event_names             # 사건 이름 다시 짓기
python -m batch.repair.node_identity           # 정규화키 재계산 → 중복 병합
python -m batch.repair.products                # 제품 표기 통일
python -m batch.repair.edges                   # 엣지 정규화·클러스터링
python -m batch.repair.subtypes                # subtype 수렴
python -m batch.repair.executive_titles        # 근거에서 직위 복원
python -m batch.repair.stake_subtypes          # 최대주주/자회사 라벨 교정
python -m batch.repair.evidence                # 근거 청크 중복 병합 → 고아 삭제
python -m batch.repair.event_sources           # 사건에 관련 기사 목록 채우기
python -m batch.repair.press_names             # 언론사명을 URL 도메인 다수결로 복구
python -m batch.build.stub_profiles            # stub 정체 한 줄 (신규분만)
python -m batch.build.company_vectors          # 기업 카드 임베딩 (변경분만)

# ③ 검사 — 무엇이 틀렸나 (지우지 않고 표시만)
python -m batch.audit.grounding --llm --apply --all --source news
python -m batch.audit.grounding_fulltext       # 의심분을 기사 전문으로 다시
python -m batch.repair.retypes                 # 유형오류 교정 (매트릭스 통과분만)
python -m batch.audit.dart --apply             # DART 필드 범위 + 원문 대조 (0원)
python -m batch.audit.relations --scope all    # 방향·대칭·양방향·사건성
python -m batch.audit.freshness                # 종료된 관계 (뉴스 + DART 재적재)
python -m batch.audit.graph                    # 구조 무결성
python -m batch.audit.coverage                 # ★무엇을 아직 안 봤나

# ④ 사건 분류 · 진행현황
python -m batch.repair.event_types             # event_type 11종 + is_risk
python -m batch.ops.status --write-doc
```

### 사람이 읽어야 하는 것
```bash
python -m batch.audit.spot_check --source news  # 표본 심층검사
python -m batch.audit.queries                   # 실제 질의로 서비스 가능성 확인
python -m batch.ops.lookup <검색어>              # 근거 원문 조회 CLI
```

설계 근거·실측·운영 규칙은 **[데이터 수집 방법서](BizNode_데이터수집_방법서.md)** 참조.

---

## 10. 폴더 구조

**`batch/`는 동사로 나뉜다** — 만든다 / 고친다 / 검사한다 / 돌린다.
파일 이름에 `build_`·`fix_`·`audit_` 접두어를 붙이지 않는다. 디렉터리가 그 역할이다.

```
batch/
  build/     수집·적재 — 데이터를 **만든다**
             graph · disclosures · business_reports · sales_customers
             company_detail · major_reports · news · financials · corp_master
             ownership · subtype_taxonomy · all
  repair/    교정 — 이미 들어온 것을 **고친다** (대부분 LLM 없이 0원)
             node_names · node_identity · edges · subtypes · products
             event_names · event_types · evidence · executive_titles
             stake_subtypes · segment_units · misclassified_edges · retypes
  audit/     검사 — 무엇이 틀렸나 **본다** (지우지 않고 표시만)
             grounding → grounding_fulltext   ← 2단: 저장 문장 → 기사 전문
             dart          DART 전용 (필드 범위 + 원문 XML 대조)
             relations · graph · freshness · coverage · spot_check
             selftest      ★검사기 자체의 회귀 확인
             queries       실제 질의로 서비스 가능성 확인
  ops/       운영 — **돌린다·본다**
             finalize · run_companies · pilot_company · status · lookup · refilter

pipeline/    라이브러리 (CLI 없음)
  extractors/  dart/ · news/ — 수집·파싱
  importer/    정규화 → staging → Neo4j 적재 · 개체해소(ER)
  normalizer/  이름·관계·제품·subtype 정규화
  news/        수집·필터·라우터·트리플 추출
  validators/  matrix.py — 노드-엣지 허용 매트릭스 ★적재 전 최종 방어선
  vectorstore/ ChromaDB 래퍼
  ontology.py  엣지 12종 정의 — 추출기·검증기가 **같은 문장**을 쓴다
  freshness.py 관계 신선도 판정

app/
  core/        설정 · DB 커넥션
  services/    graph_service.py — 신선도·근거 필터 조회 · 파급 계산
  api/         (미구현)

data/
  company_list/ 시드 64개사
  raw_reports/  사업보고서 원문 (재파싱 캐시 · audit.dart가 원문 대조에 씀)
  documents/    공시 원문
```

**의존 방향은 한 방향이다** — `batch/` → `pipeline/` → `app/core`.
`pipeline/`은 CLI를 갖지 않고, `batch/`는 서로를 import하지 않는다
(예외: `ops/finalize`가 다른 배치를 **서브프로세스로** 실행).
