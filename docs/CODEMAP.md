# 코드 지도

파일이 어디에 무엇을 하는지 한 장으로. 자세한 「왜」는 각 파일 맨 위 독스트링에 있다.
설계 근거는 [방법서](BizNode_데이터수집_방법서.md), 서비스 개요는 [README](README.md).

---

## 큰 그림 — 데이터가 흐르는 순서

```
   ①수집          ②파싱·정규화        ③검증        ④적재           ⑤정리·검사      ⑥조회
DART API   ──┐                                                                    
공시 원문   ──┼─→ pipeline/        → validators/ → staged_edges → batch/repair/ → app/services/
구글뉴스    ──┤   extractors         matrix.py      (PostgreSQL)   batch/audit/    graph_service
네이버      ──┘   normalizer                            ↓                              ↓
                  parsers                          Neo4j·ChromaDB              app/api (라우트 21) ─→ 백엔드
                                                          ↓                        ↑
                                                   search/ ─────────────→ app/graph (`/ask`)
```

- **`pipeline/`** 은 라이브러리다. CLI가 없고, 아무것도 스스로 실행하지 않는다.
- **`batch/`** 는 그 라이브러리를 부르는 실행 진입점(CLI)이다.
- 의존은 한 방향: `batch/` → `pipeline/` → `app/core`. **반대 방향은 없다.**
- 조회 쪽도 한 방향: `app/api` → `app/services`·`app/graph` → `search/` → `app/core`.

---

# batch/ — 실행하는 것

디렉터리가 **동사**다. 파일 이름에 `build_`·`fix_`·`audit_` 접두어를 붙이지 않는다.

## batch/build/ — 만든다 (수집·적재)

| 파일 | 하는 일 |
|---|---|
| `graph.py` | **여기서 시작한다.** 시드 기업 노드 + 지분·임원 관계 (경로 A) |
| `financials.py` | 재무 3개년 → PostgreSQL + 노드 스냅샷 |
| `disclosures.py` | 공급계약 공시 → `SUPPLIES_TO` (경로 B) |
| `major_reports.py` | 주요사항보고서 → `ACQUIRES`·`SUES`·사건 (경로 B) |
| `business_reports.py` | 사업보고서 → 제품 노드 + `DEVELOPS` (경로 C) |
| `sales_customers.py` | 사업보고서 「매출 및 수주상황」 → 거래처 |
| `company_detail.py` | 사업보고서 → 기업 개요 · 사업부문 (화면 표시용) |
| `news.py` | 뉴스 → 관계 엣지 (수집 → 필터 → 추출 → 적재) |
| `corp_master.py` | DART 전체 기업 마스터 11만건 (ER 블로킹용) |
| `ownership.py` | 대량보유(5%룰) 원본 수집 |
| `subtype_taxonomy.py` | subtype 분류 체계 수립 (1회성) |
| `company_vectors.py` | 기업 소개 카드 임베딩 — **말로 회사 찾기** (개요+제품+거래처) |
| `stub_profiles.py` | stub에 정체 붙이기 (DART 기업개황 → LLM) — 상세는 PG로 |
| `business_overview.py` | 사업보고서 「사업의 내용」 **원문** → PG (받아 둔 XML 재파싱 · **0원**) |
| `market_data.py` | 주가·거래량(pykrx) + 유통주식수(DART) → PG · 지표는 **뷰로 계산** |
| `dart_aliases.py` | DART 명부에서 별칭 사전 채우기 |
| `news_feed.py` | 뉴스 피드 갱신 — **매일 도는 가벼운 쪽** |
| `all.py` | 위를 순서대로 한 번에 |

## batch/repair/ — 고친다 (이미 들어온 것 · 대부분 0원)

| 파일 | 하는 일 |
|---|---|
| `node_names.py` | 이름이 불량한 노드 복구 (`Event`·NULL·설명형 stub) |
| `node_identity.py` | 정규화키 재계산 → 같아진 노드 병합 |
| `edges.py` | subtype 대표형 통일 + 중복 엣지 클러스터링 |
| `subtypes.py` | subtype 레지스트리 정리 |
| `products.py` | 같은 제품이 표기만 달라 갈린 것 통일 |
| `event_names.py` | 사건 이름 다시 짓기 (기사 제목·프롬프트 유출) |
| `event_types.py` | 사건에 유형 11종 + 리스크 여부 부여 |
| `evidence.py` | 근거 청크 중복 병합 → 고아 삭제 |
| `event_sources.py` | 사건에 **관련 기사 목록** 채우기 (엣지 출처를 노드로) |
| `press_names.py` | 기사의 언론사명을 URL 도메인 다수결로 복구 |
| `executive_titles.py` | 「임원」으로 뭉뚱그려진 직위를 근거에서 되찾기 |
| `stake_subtypes.py` | 최대주주/자회사 라벨 교정 |
| `segment_units.py` | 사업부문 매출 단위 오류 교정 (백만원↔원) |
| `misclassified_edges.py` | 오분류 엣지 재배정 (방향까지 판단) |
| `retypes.py` | 유형오류 판정을 실제로 적용 — **매트릭스가 거른다** |
| `retype_recheck.py` | 유형오류 표시를 다시 확인 |
| `event_merge.py` | 같은 사건 병합 — **어근 매칭**으로 후보를 만든다(토큰겹침은 재현율 38%) |
| `person_merge.py` | 동명 인물 판정·병합 (손 목록이 이긴다) |
| `foreign_merge.py` | 해외 기업 표기 병합 |
| `cluster_reps.py` | 병합 클러스터의 대표 이름 고르기 |
| `org_types.py` | 기관을 10종으로 분류 (규제기관·수사사법·정부부처…) |
| `product_category.py` | 제품 6종 분류 (제품·부품·기술·장비·서비스·소재) |
| `subtype_backfill.py` | 빈 subtype을 근거에서 소급 채우기 |
| `ksic_backfill.py` | 업종코드 소급 채우기 |
| `node_lastseen.py` | 노드의 `last_seen`을 엣지에서 되계산 |
| `ambiguous_corps.py` | 동명 법인이라 못 좁힌 노드 찾기 |
| `corp_code_resolve.py` | 그 노드들을 LLM으로 판정 (`matched`/`none`/`unsure` · 캐시) |
| `purge_unfounded.py` | 근거 없는 엣지를 `purged_edges`에 기록하고 뺀다 |
| `purge_orphans.py` | 관계가 0이 된 노드를 `purged_nodes`에 기록하고 뺀다 |
| `orphan_nodes.py` | 엣지가 하나도 없는 노드를 **표시**하고 검색에서 뺀다 |
| `stale_cards.py` | 그래프에 없는 **기업 카드**를 검색에서 뺀다 |
| `event_split.py` | 섞인 Event 를 **판정대로 갈라 놓는다** — `audit/event_merge.py` 의 짝 |
| `name_overlap.py` | 이름이 겹치는 Company 쌍을 자동 분류 |
| `business_units.py` | 공장·사업부를 **모회사에 매단다** |
| `division_kind.py` | `entity_kind='사업부문'` 인데 **실제 법인**인 노드를 되돌린다 |
| `first_seen.py` | 엣지·노드에 **언제 처음 생겼나**를 기록 |
| `news_topics.py` | 기존 기사에 `topics` 소급 채우기 |
| `evidence_source_type.py` | evidence 청크 메타에 `source_type` 채우기 — **재임베딩 없이** |
| `slash_keys.py` | `norm_name` 의 슬래시 제거 — **키가 URL 에 들어가기 때문** |
| `embedding_cache_column.py` | `embedding_cache.model` → `embedding_model` 개명 (돌고 있는 DB 용) |

**스키마 정리 — 2026-08-15 1회성 (기록으로 남긴다)**

| 파일 | 하는 일 |
|---|---|
| `schema_slim.py` | Company 속성 49 → 11. 사업부문 노드 병합 · PG 이관 |
| `edge_slim.py` | 엣지 속성 정리 — **계산되는 파생값 6종 제거** · ratio 단위 교정 |
| `edge_audit_move.py` | 검사 사유·이력 37종 → PG `edge_audits`. ★`loaded_at`은 **옮기면 안 된다** |
| `pg_tidy.py` | 빈 표·중복 표 정리 (`companies`·`ingest_runs` 삭제) |

## batch/audit/ — 본다 (지우지 않고 표시만)

| 파일 | 하는 일 |
|---|---|
| `grounding.py` | **근거가 관계를 뒷받침하나** — 저장된 문장으로 1차 판정 |
| `grounding_fulltext.py` | 1차에서 걸린 것을 **기사 전문**으로 다시 (2차) |
| `relations.py` | 방향 · 대칭 병렬언급 · 양방향 공급 · 사건성 (`--scope`) |
| `dart.py` | DART 전용 — 필드 값 범위 + 사업보고서 **원문 대조** |
| `freshness.py` | 관계의 **종료** — 뉴스가 말함 / DART 재적재에서 사라짐 |
| `graph.py` | 구조 무결성 42개 검사 — 노드·엣지·값·의미·**라벨·검사표시·타입**·확장 안전성 |
| `selftest.py` | ★**검사기 자체**가 한쪽으로 쏠렸는지 (추출기+검증기) |
| `coverage.py` | ★「무엇이 걸렸나」가 아니라 **「무엇을 아직 안 봤나」** |
| `spot_check.py` | 사람이 읽는 표본 — 아직 정의 못 한 실패 유형 찾기 |
| `queries.py` | 실제 질의를 던져 서비스가 되는지 확인 |

**API·챗봇 검사 — 조회 쪽이 생기면서 붙었다**

| 파일 | 하는 일 |
|---|---|
| `api_contract.py` | 응답 계약과 `app/api/examples.py` 예시를 **살아 있는 DB 와 대조** |
| `api_fuzz.py` | 무작위 기업 조합으로 API 를 **수백 번 두들겨** 이상한 것을 찾는다 |
| `ranking_baseline.py` | ★사건 랭킹 **기준선 고정** — 코드가 바꾼 것과 데이터가 바꾼 것을 가른다 (0원) |
| `ask_graph_parity.py` | `/ask` 출력 대조 — 그래프 경로 vs `AnswerService.ask()` |
| `claim_grounding.py` | 답변 claim 과 근거의 겹침 **분포** — 판정하지 않는다 |
| `discovered_cohesion.py` | `discovered` 앵커의 판정 신호 계측 — 거리인가 응집도인가 |

## batch/ops/ — 돌린다·본다

| 파일 | 하는 일 |
|---|---|
| `finalize.py` | **후처리 전체를 순서대로** (회귀확인 → 정리 → 검사) |
| `run_companies.py` | 여러 기업 추출 — 하나 실패해도 나머지 계속 |
| `pilot_company.py` | 기업 1개로 파이프라인을 돌려 깔때기 실측 |
| `status.py` | 어느 기업을 얼마나 돌렸나 (진행현황 문서 생성) |
| `lookup.py` | 근거 원문 조회 CLI (ChromaDB엔 UI가 없다) |
| `refilter.py` | 저장된 기사에 **현재** 규칙 필터를 다시 적용 |
| `leftover_extract.py` | 필터는 통과했는데 추출이 안 된 기사 마저 돌리기 |
| `collect_state.py` | 수집 상태 점검 |
| `daily.py` | 매일 도는 파이프라인 — **크론이 부르는 한 줄** |

---

# pipeline/ — 라이브러리 (CLI 없음)

## pipeline/ 최상위 — 여러 곳이 공유하는 규칙

| 파일 | 하는 일 |
|---|---|
| `ontology.py` | ★**엣지 12종의 정식 정의.** 추출기·검증기가 **같은 문장**을 쓴다 |
| `llm.py` | ★LLM 호출 한 곳 — 스키마 강제 + **실패를 통과와 구별** |
| `freshness.py` | 관계 신선도 판정 (current / stale / expired) |
| `text.py` | 한국어 문장 생성 (근거 스니펫용) |
| `token_overlap.py` | 주장에 쓴 낱말이 근거 안에 실제로 있나 — **무료 1차 대조** |

## pipeline/extractors/ — 밖에서 가져온다

**dart/** `corp_code`(기업 마스터) · `company_info`(기업개황) · `financials`(재무)
· `disclosure_list`(공시 목록) · `document`·`downloader`(원문 ZIP)
· `xml_parser`·`text_cleaner`(원문 파싱) · `business_report`·`company_detail`·`sales_customers`(절 추출)
· `major_reports`(주요사항보고서) · `shares`(유통주식수 — 시가총액 계산용)

**news/** `naver`(관계 기사 발견) · `gnews`(기간 제약을 푸는 경로) · `rss`(전문 제공 매체)
· `crawler`(본문 크롤 · robots.txt 준수)

## pipeline/news/ — 뉴스 깔때기

| 파일 | 하는 일 |
|---|---|
| `collector.py` | 수집 → 중복 제거 → 필터 |
| `relevance.py` | 적합성 필터 — LLM 진입 최전선 (규칙 → LLM 2단) |
| `extractor.py` | ★기사 → 관계 트리플 (매트릭스를 프롬프트로 주입) |

## pipeline/parsers/ — 공시 본문에서 관계 뽑기

`supply_contract.py`(「단일판매ㆍ공급계약체결」 공시 원문 → `SUPPLIES_TO` + 근거) ·
`product_extractor.py`(사업보고서 II-2 주요 제품 → LLM 추출) ·
`contract_extractor.py`(II-6 주요계약 → `PARTNERS_WITH`·`SUPPLIES_TO`·`DEPENDS_ON`)

## pipeline/normalizer/ — 표기를 통일한다

| 파일 | 하는 일 |
|---|---|
| `base.py` | 이름 정제 공통 유틸 (법인격 접미어 제거 등) |
| `entities.py` | 이름 → 노드 엔티티 빌더 |
| `relations.py` | ★L3 subtype 대표형 통일 + OTHER 매핑 |
| `subtype_registry.py` | 개방형 subtype을 **관리되는 개방형**으로 |
| `product_names.py` | 제품 표기 통일 (`HBM3E` / `HBM 3E`) |
| `generic_names.py` | 설명형·익명 개체명 판별 (「글로벌 대형기업」 차단) |
| `foreign_aliases.py` | 해외 기업 한글·영문 표기 통일 |
| `canonical_name.py` | 대표 표기 결정 |
| `legal_forms.py` | 법인격 표기 (주식회사·(주)·Inc·Ltd) 사전 |
| `translit.py` | 로마자 ↔ 한글 음차 |
| `company_registry.py` | 별칭 사전 조회·기록 (PG `company_aliases`) |
| `product_registry.py` | 알려진 제품 표기 (PG `product_names`) — **사후 병합은 안 한다** |
| `name_judge.py` | 「고유명인가 설명인가」 LLM 판정 + 캐시 |
| `ksic.py` | 업종 중분류 59종 |
| `resolver.py` | 이름 → `corp_code` 개체 해소 |
| `person_index.py` | 이름 → 생년월 인덱스 (인물 분열 방지) |
| `common.py` | 투자조합·신탁·펀드 분류 |
| `shareholder_normalizer.py` | 최대주주 현황 → 지분 관계 (본인/특수관계인 구분) |
| `executive_normalizer.py` | 임원 현황 → 임원 관계 |
| `investment_normalizer.py` | 타법인 출자 → 자회사/출자 |
| `majorstock_normalizer.py` | 대량보유(5%룰) → 지분 관계 |

## pipeline/validators/ — 적재 전 최종 방어선

| 파일 | 하는 일 |
|---|---|
| `matrix.py` | ★**노드-엣지 허용 매트릭스.** 잘못된 조합을 여기서 막는다 |
| `dart.py` | DART 정형 데이터 도메인 검증 (값 범위·형식) |
| `base.py` | 검증 리포트 · 공통 헬퍼 |

## pipeline/importer/ — 저장소에 넣는다

| 파일 | 하는 일 |
|---|---|
| `staging.py` | 정규화 결과 → `staged_edges` ★**권위 저장소** |
| `graph_loader.py` | `staged_edges` → Neo4j (여기서 `loaded_at`을 찍는다) |
| `evidence.py` | 근거 청크 → ChromaDB + 레지스트리 · `fetch_texts()` |
| `neo4j_schema.py` | 제약·인덱스 셋업 |
| `company_loader.py` | 시드 기업 노드 |
| `disclosure_loader.py` | 공급계약 공시 → 엣지 |
| `major_report_loader.py` | 주요사항보고서 → 엣지 |
| `business_report_loader.py` | 사업보고서 → 제품·엣지 |
| `news_loader.py` | 뉴스 관계 → 노드·엣지·근거 |
| `path_a_evidence.py` | 정형 API 엣지의 근거 문장 **생성** (팩트체크용) |
| `person_er.py` | 인물 개체해소 — 근거가 확실한 분열만 병합 |
| `event_er.py` | 사건 개체해소 — 이름만 다른 같은 사건 병합 |
| `extraction_ledger.py` | 기업별 추출 이력 |

## pipeline/vectorstore/

`base.py`(인터페이스) · `chroma_store.py`(ChromaDB + OpenAI 임베딩)

---

# app/ — 밖으로 내보내는 쪽

의존은 한 방향이다: `api/` → `services/`·`graph/` → `search/` · `core/`.

    HTTP 21 라우트 ─┬─ 조회 (18) ──▶ services/{company,workspace,insight,news,relation,search}
                    └─ 챗봇 (2)  ──▶ /retrieve  services/retrieve_service
                                     /ask       graph/ask_graph ─▶ tools/ ─▶ services/

**`/ask` 는 `graph/` 를 거치고 조회 라우트는 거치지 않는다.** 두 경로가 같은
`RetrieveService` 인스턴스를 나눠 쓴다(`main.py::bind_service`) — 두 벌을 만들면
`SearchOrchestrator` 가 둘이 되어 커넥션·캐시가 갈린다.

## app/api/ — 계약

| 파일 | 하는 일 |
|---|---|
| `schemas.py` | ★**응답 계약 61종.** 백엔드·프론트가 보는 유일한 정의 — 코드가 곧 문서 |
| `main.py` | 라우트 21개. **로직을 두지 않는다** — `services/` 로 넘기는 어댑터다 |
| `examples.py` | 응답 계약의 고정 예시. `audit/api_contract.py` 가 이 값을 **DB 와 대조**한다 |

## app/services/ — 조회 (라우트가 직접 부른다)

| 파일 | 하는 일 |
|---|---|
| `graph_service.py` | ★**그래프를 읽는 유일한 통로.** 신선도·근거 검증 결과로 거르고 질의 시점에 파급을 계산한다 |
| `company_service.py` | 기업 조회 — Neo4j 와 PostgreSQL 을 합쳐 한 덩어리로 |
| `workspace_service.py` | 담은 기업들을 하나로 놓고 보기 (합친 그래프·제안·변화) |
| `search_service.py` | 이름으로 기업 찾기 — 그래프 3,432곳 + DART 명부 118,535곳 |
| `insight_service.py` | 합쳐야 드러나는 것 (카드) |
| `relation_service.py` | 관계 하나 — 근거 원문까지 |
| `news_service.py` | 뉴스 피드 — 우리가 모은 기사로 |

## app/services/ — 챗봇 재료와 검사

| 파일 | 하는 일 |
|---|---|
| `retrieve_service.py` | ★**`/retrieve` 의 본체.** 검색 → 앵커 판정 → 재료 조립. `/ask` 도 같은 인스턴스를 쓴다 |
| `query_understanding.py` | `anchor_source` 판정 — 질문이 **무엇을 대상으로** 하는가 (query/context/anchorless/unresolved) |
| `evidence_selector.py` | 질문 의도로 사건을 골라 **근거를 줄인다** (규칙 티어 + 임베딩) |
| `relation_selector.py` | 같은 일을 관계에 — `evidence_selector` 와 대칭 |
| `material_consistency.py` | 그래프 라벨과 근거 원문을 **답변 전에** 대조 (극성·시간 격리) |
| `claim_check.py` | 답변의 주장이 **자기가 든 근거 안의 낱말**을 쓰고 있나 |
| `embedding_cache.py` | 같은 텍스트는 언제나 같은 벡터 (영속 캐시) |
| `answer_service.py` | ★**1차 답변 경로.** 운영은 `graph/` 로 넘어갔고 지금은 출력 대조 기준선이다 |

## app/graph/ — `/ask` 실행 그래프 (LangGraph)

| 파일 | 하는 일 |
|---|---|
| `ask_graph.py` | ★**배선.** 검색 ▸ 앵커 해소 ▸ (Agent ⇄ 도구) ▸ 마감. 조건부 엣지 둘 |
| `state.py` | 한 요청이 노드를 지나며 쌓는 값 |
| `nodes/material.py` | 재료 노드 넷 — `RetrieveService` 에 위임 |
| `nodes/agent_loop.py` | Agent 루프 — **무엇을 고르고 무엇을 못 고르는가** |
| `nodes/answer.py` | 답변 노드 여섯 — 프롬프트 조립·생성·인용 검증·주장 검사 |
| `prompt.py` | 그래프 경로의 프롬프트 조립 — **도구 DTO 를 읽는다** |
| `budget.py` | 탐색 총량 예산 — 인자 길이가 아니라 **누적치**로 센다 |

## app/tools/ — Agent 가 부르는 도구

| 파일 | 하는 일 |
|---|---|
| `dto.py` | ★**도구 반환 계약.** DB row 를 그대로 주지 않고 오해할 값에 **표기**를 붙인다 |
| `agent_tools.py` | 부를 수 있는 도구의 노출 경계 (LangChain 바인딩) |
| `graph_tools.py` | 관계·사건·파급 |
| `company_tools.py` | 기업 사실 (개요·공시·시세) |
| `search_tools.py` | 근거 검색 — 뉴스와 공시를 갈라서 |
| `scope.py` | ★도구가 만질 수 있는 key 범위 — **서버가 정하고 도구가 강제한다** |
| `citation.py` | 도구 결과 중 **무엇을 인용할 수 있나** |
| `errors.py` | 도구 실패를 **빈 결과와 구별한다** |

## app/llm/ — LLM 경계

`adapter.py`(구조화 응답 하나 · **실패를 통과와 구별**) · `schemas.py`(응답 스키마)
· `prompt.py`(★두 답변 경로가 **글자까지 같은** 조립 부분 — 사본을 하나로 줄인 자리)

## app/core/ — 공통

`config.py`(환경 설정) · `database.py`(Neo4j·PostgreSQL 접속) ·
`clock.py`(★「오늘」의 단일 출처) · `trace.py`(요청 하나를 계층 로그로 잇는 키) ·
`observe.py`(★**재기만 한다. 아무 동작도 바꾸지 않는다**) · `querylog.py`(실사용 질의 적재)

---

# search/ — 검색 계층

`RetrieveService` 가 유일한 프로덕션 소비자다. 조립은 `factory.build_orchestrator()`
한 곳에서만 하고 **프로세스당 하나**를 쓴다.

    질의 ─▶ AnchorExtractor ─▶ EntityResolver ─▶ QueryRouter ─┬─▶ GraphSearcher ─┐
                                                              └─▶ VectorSearcher ─┴─▶ ResultRanker(RRF)

| 파일 | 하는 일 |
|---|---|
| `service/orchestrator.py` | ★위 흐름의 지휘자 |
| `service/factory.py` | 협력 객체 6개 조립 — **프로덕션 조립처는 여기 하나** |
| `service/anchor_extractor.py` | 질의 문장에서 기업명 후보 (형태소 분석 · 조사 분리) |
| `service/entity_resolver.py` | 검색어 → `corp_code` |
| `service/query_router.py` | 관계 키워드와 방향 감지 |
| `service/graph_searcher.py` | ★`graph_service` 를 **함수로** 부른다 (저장소 우회 금지) |
| `service/vector_searcher.py` | company 컬렉션 의미 검색 |
| `service/result_ranker.py` | 두 결과를 RRF 로 병합 |
| `repository/` | `postgres_repository.py` · `chroma_repository.py` |
| `dto/` | `search_request.py`(입력 계약) · `search_query.py`(내부 실행 문맥) · `search_hit.py`(결과 단위) · `search_result.py`(응답) |
| `model/enums.py` | 공용 Enum — `EntityType`·`SearchMode`·`Direction` |

---

# schemas/

`dart_schemas.py` — 파이프라인 전역 공유 DTO (엔티티·관계·정규화 문서)

---

## 어디부터 읽어야 하나

| 알고 싶은 것 | 볼 파일 |
|---|---|
| 엣지 12종이 무슨 뜻인가 | `pipeline/ontology.py` |
| 어떤 조합이 허용되나 | `pipeline/validators/matrix.py` |
| 뉴스에서 관계를 어떻게 뽑나 | `pipeline/news/extractor.py` |
| 무엇이 틀렸는지 어떻게 아나 | `batch/audit/grounding.py` → `grounding_fulltext.py` |
| 화면에 뭐가 나가나 | `app/api/schemas.py` → `app/services/graph_service.py` |
| 검색이 어떻게 도나 | `search/service/orchestrator.py` |
| `/ask` 가 어떤 순서로 도나 | `app/graph/ask_graph.py` |
| Agent 가 무엇을 부를 수 있나 | `app/tools/agent_tools.py` · `app/tools/scope.py` |
| 전체를 한 번에 돌리려면 | `batch/ops/finalize.py` |
