# BizNode-ETF

ETF 구성종목을 시작점으로 삼아 OpenDART 공시 데이터(최대주주/임원현황/타법인출자 등)를
수집·정규화·검증한 뒤 Neo4j 지식 그래프로 적재하는 배치 파이프라인 프로젝트입니다.

---

## 1. 전체 아키텍처

```
ETF 구성 종목 → Company Master 구축 (STEP 1)
              ├─ DART API 구조화 데이터   (STEP 2, 완료)
              └─ DART 원문 XML 비정형 데이터 (STEP 2, Phase 2 부분 구현)
              → 정규화 → 검증 → Neo4j 적재
                                              ▲
                                      뉴스 Event 지속 업데이트 (미착수)
```

| 단계             | 내용                                                                   | 상태                          |
| ---------------- | ---------------------------------------------------------------------- | ----------------------------- |
| STEP 1           | ETF 구성종목 → `Company`/`Sector`/`ETF` 그래프 적재                    | 완료                          |
| STEP 2-A         | DART 구조화 데이터(최대주주/임원현황/타법인출자) 수집→정규화→검증→적재 | 완료                          |
| STEP 2-B         | DART 원문 사업보고서 XML에서 "사업의 내용" 등 섹션 텍스트 추출         | 부분 구현 (다운로드/추출까지) |
| STEP 3           | 추출 텍스트에 대한 LLM Entity Extraction                               | 미착수                        |
| STEP 4           | 뉴스 Event 수집·그래프 반영                                            | 미착수                        |
| STEP 5           | 변경분 증분 업데이트                                                   | 미착수                        |
| API 서버(`app/`) | FastAPI 진입점                                                         | 스캐폴딩만 존재, 미구현       |

## 2. 폴더 구조

```
BizNode-ETF/
├── app/                        # FastAPI 진입점 (아직 미구현 스캐폴딩)
│   ├── api/                    # 라우터 (비어 있음)
│   ├── core/
│   │   ├── config.py           # .env 값을 읽어오는 설정 모듈
│   │   └── database.py         # Neo4jClient (드라이버 연결/해제)
│   ├── services/
│   │   └── graph_service.py    # 비어 있음
│   └── main.py                 # 비어 있음
│
├── batch/                      # CLI 진입점 (인자 파싱 + pipeline 함수 호출만 담당)
│   ├── fetch_dart_to_json.py       # [STEP 1] DART API 호출 → data/raw_dart/*.json
│   ├── normalize_json.py           # raw_dart → 정규화 → 검증 → data/normalized/*.json
│   ├── import_json_to_neo4j.py     # normalized JSON → Neo4j 적재
│   ├── import_etf_list_to_neo4j.py # ETF 구성종목 마스터 리스트 → Neo4j 적재
│   └── extract_company_report.py   # [Phase 2] 사업보고서 XML → 섹션 텍스트 추출 CLI
│
├── pipeline/                   # 공용 도메인 로직 (app, batch 모두 여기 의존)
│   ├── normalizer/             # API별 정규화 규칙 + LLM 후처리(llm_postprocess.py)
│   ├── validators/             # API별 정규화 결과 검증 규칙
│   ├── importer/
│   │   └── neo4j_importer.py   # entities[]/relationships[] 공통 스키마 → Cypher 적재
│   └── extractors/dart/        # 사업보고서 원문 다운로드(downloader.py)·XML 파싱(xml_parser.py)·
│                                # 텍스트 정제(text_cleaner.py)·섹션 추출(report_extractor.py)
│
├── schemas/                    # 파이프라인 전역에서 공유하는 DTO
│   ├── dart_schemas.py         # entities[]/relationships[] 공통 스키마 DTO
│   ├── company_report.py       # CompanyReport DTO (Phase 2 추출 결과)
│   └── source.py               # 출처(Source) 정보 DTO
│
├── data/                       # 원본/중간/정규화 데이터 (.gitignore 처리, 저장소에 없음)
│   ├── company_list/           # ETF 구성종목 리스트, DART corp_code 마스터
│   ├── raw_dart/                # DART API 원본 응답 JSON (fetch_dart_to_json.py 산출물)
│   ├── raw_reports/             # 사업보고서 원문 zip/xml (extract_company_report.py 산출물)
│   └── normalized/              # 정규화·검증 완료 JSON (normalize_json.py 산출물)
│
├── documents/                  # 설계/작업 기록 문서 (.gitignore 처리, 저장소에 없음)
├── requirements.txt
└── .env                        # 환경변수 (저장소에 없음, 아래 3절 참고)
```

## 3. 사전 준비

- Python 3.10+
- 접근 가능한 Neo4j 인스턴스 (Aura 또는 로컬)
- 아래 API 키
    - `DART_KEY` — [OpenDART](https://opendart.fss.or.kr) 오픈API 키
    - `DATA_GO_KR_SERVICE_KEY` — data.go.kr 서비스 키
    - `ANTHROPIC_API_KEY` — `normalize_json.py`의 LLM 후처리 단계(§1.2, Claude Haiku 4.5 배치 사용)에 필요

## 4. 설치

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

`uv`를 쓰는 경우:

```bash
uv venv
uv pip install -r requirements.txt
```

## 5. 환경 변수

프로젝트 루트에 `.env` 파일을 만들고 아래 키를 채웁니다(저장소에는 포함되지 않으므로 각자
발급받아야 합니다).

```env
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
NEO4J_DATABASE=
DART_KEY=
DATA_GO_KR_SERVICE_KEY=
ANTHROPIC_API_KEY=
```

## 6. 실행 명령어

모든 `batch/*` 스크립트는 `app.core.config` 등을 절대 임포트로 참조하므로, **반드시 프로젝트
루트에서 `-m` 옵션으로 모듈처럼 실행**해야 합니다. `python batch/xxx.py`처럼 직접 실행하면
`ModuleNotFoundError: No module named 'app'`이 발생합니다.

### 6.1 ETF Universe 구축 (STEP 1)

```bash
python -m batch.import_etf_list_to_neo4j
```

### 6.2 DART 구조화 데이터 파이프라인 (STEP 2-A)

```bash
# 1) DART API 호출 → data/raw_dart/*.json
python -m batch.fetch_dart_to_json

# 2) 정규화 + 검증 → data/normalized/*.json
python -m batch.normalize_json                      # 전체 corp_code 처리
python -m batch.normalize_json --corp-code 00126380  # 특정 corp_code만 처리
python -m batch.normalize_json --skip-llm            # LLM 후처리 생략(디버깅/빠른 반복용)

# 3) Neo4j 적재
python -m batch.import_json_to_neo4j
python -m batch.import_json_to_neo4j --corp-code 00126380  # 특정 corp_code만 적재
```

`--skip-llm` 없이 실행하면 전체 corp_code 처리가 끝난 뒤 LLM 후처리가 자동 호출됩니다.
`ANTHROPIC_API_KEY`가 필요하며 Batch API 특성상 완료까지 수 분~1시간 정도 걸릴 수 있습니다.

### 6.3 사업보고서 원문 섹션 추출 (STEP 2-B, Phase 2)

```bash
python -m batch.extract_company_report --company-id 005930 --rcept-no 20250318000763
```

### 6.4 API 서버 (아직 미구현)

`app/main.py`는 현재 빈 파일입니다. FastAPI 앱이 구현되면 다음 형태로 실행할 예정입니다.

```bash
uvicorn app.main:app --reload
```

## 7. 유의사항

- `batch/import_etf_list_to_neo4j.py`는 `Sector`/`ETF` 라벨만 삭제 후 재생성합니다. `Company`
  라벨은 STEP 2 파이프라인과 공유되므로 `corp_code` 유니크 제약 기반 `MERGE`만 수행하며 삭제하지
  않습니다 — 두 스크립트를 어떤 순서로 반복 실행해도 기존 데이터가 유실되지 않도록 설계돼
  있습니다.
- LLM 후처리(`normalize_json.py`, `--skip-llm` 미지정 시)는 비결정적입니다. 동일 입력이라도
  재실행하면 일부 결과가 달라질 수 있으므로, 수작업으로 보정한 값이 있다면 재실행 시 되돌아간다는
  점에 유의하세요.
