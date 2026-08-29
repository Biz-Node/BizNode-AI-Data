# BizNode Agent 루프 — 평가셋

> **이 파일은 생성물입니다.** 손으로 고치지 말고 아래로 다시 만드세요.
> ```bash
> .venv-wsl/bin/python -m tests.agent.eval.report \
>     -o docs/BizNode_Agent_평가셋.md
> ```

마지막 실행 **2026-08-29** · 케이스 **20개**

케이스 정의는 `tests/agent/eval/cases.py`, 판정은 `tests/agent/eval/test_agent_eval.py` 에 있습니다.

```bash
pytest -m needs_llm tests/agent/eval -q     # Agent 평가셋 (LLM 실호출)
pytest tests/search/eval -q                 # 검색 회귀 기준선 (별개)
```

★**검색 평가셋과 섞지 않습니다.** `tests/search/eval/` 20 케이스는 검색 계층의 회귀 기준선으로 **그대로 보존**되어 있고, 이 문서는 그 위에서 Agent 가 도구를 골라 재료를 모으는 루프를 잽니다.

★**ranking 은 이 단계에서 바꾸지 않았습니다.** 링 분포·kept/cut·인용된 링은 **관측만** 합니다. 링 랭킹을 바꿀지는 이 수치를 보고 정합니다.

## 1. 한눈에 보기

| 판정 | 케이스 |
|---|---|
| PASS | 20 |

| # | 케이스 | 질문 | anchor | Agent | 도구 호출 | 판정 |
|---:|---|---|---|---|---:|---|
| 1 | `query-relations-supplies` | 삼성전자에 납품하는 기업은? | query | 호출 | 1 | PASS |
| 2 | `query-events-labor` | SK하이닉스 노조 관련 리스크 알려줘 | query | 호출 | 3 | PASS |
| 3 | `query-overview-context-only` | HD현대는 무슨 사업을 하는 회사야? | query | 호출 | 1 | PASS |
| 4 | `query-market-no-evidence-id` | 삼성전자 시가총액이랑 PER 알려줘 | query | 호출 | 1 | PASS |
| 5 | `query-filings-list` | HD현대가 낸 공시 목록을 보여줘 | query | 호출 | 1 | PASS |
| 6 | `query-dart-evidence` | 현대로템 사업보고서 내용에서 주요 제품을 찾아줘 | query | 호출 | 1 | PASS |
| 7 | `query-news-citable` | 파두 실적 논란 어떻게 됐어? | query | 호출 | 3 | PASS |
| 8 | `query-multi-company-suit` | 삼성전자와 SK하이닉스 둘 다 관련된 소송 있어? | query | 호출 | 2 | PASS |
| 9 | `query-multi-company-relation` | 한미반도체와 SK하이닉스 관계 알려줘 | query | 호출 | 4 | PASS |
| 10 | `query-multitool-invest-and-price` | 레인보우로보틱스 최근 투자 상황이랑 주가도 같이 알려줘 | query | 호출 | 2 | PASS |
| 11 | `query-event-info-leak` | 현대오토에버 정보유출 사건 | query | 호출 | 3 | PASS |
| 12 | `query-event-capital-smallcap` | 심텍 최근 자본거래 알려줘 | query | 호출 | 2 | PASS |
| 13 | `ws-semantic-strike` | 반도체 업계 파업 위험이 있나? | workspace | 호출 | 2 | PASS |
| 14 | `ws-semantic-capital-trend` | 최근 자본거래 동향 알려줘 | workspace | 호출 | 2 | PASS |
| 15 | `ws-semantic-collusion` | 메모리 가격 담합 관련 소식 | workspace | 호출 | 2 | PASS |
| 16 | `ws-semantic-quality` | 품질 문제로 논란된 사례 있어? | workspace | 호출 | 2 | PASS |
| 17 | `ws-market-across-workspace` | 우리 워크스페이스 기업들 주가 어때? | workspace | 호출 | 2 | PASS |
| 18 | `ws-relationship-regulator` | 최근 규제당국 조사 동향 | workspace | 호출 | 2 | PASS |
| 19 | `unresolved-unknown-company` | 무한상사 실적 알려줘 | unresolved | 미호출 | 0 | PASS |
| 20 | `unresolved-gibberish` | storminmvpsdjfk 이 뭐야 | unresolved | 미호출 | 0 | PASS |

### 반복 실행 — 변동폭

이 실행은 **1회**입니다. 변동폭은 잴 수 없습니다 — `--agent-eval-repeat=N` 으로 N 번 돌리세요 (**비용이 N 배**입니다).

## 2. 비용 — 도구·예산·임베딩

| 항목 | 값 |
|---|---|
| Agent 가 불린 케이스 | 18 / 20 |
| 도구 호출 총계 | 36 |
| 케이스당 도구 호출 | 최소 1 · 중앙 2 · 최대 4 (상한 12) |
| ★**Agent 루프가 예산으로 잘린** 케이스 | 0 / 20 |
| 최종 `budget_exhausted` 플래그 | 0 / 20 |
| 임베딩 호출 | 33회 · 캐시 적중 1171 · 빗나감 11 |

★**두 줄을 갈라 읽으세요.** 플래그는 `fetch_propagation` 이 Agent 루프 **뒤에** 파급 예산을 채워도 켜집니다. 「상한을 올려야 하나」의 답이 갈립니다 — 루프가 잘렸으면 **도구** 예산 얘기고, 뒤에서 찬 것이면 **파급** 예산 얘기입니다.

### LLM 토큰 — 모델별

| 모델 | 호출 | 입력 | 출력 | (그중 추론) |
|---|---:|---:|---:|---:|
| `gpt-4o-mini-2024-07-18` | 61 | 251,034 | 11,948 | 0 |

★**모델명은 응답이 말한 것**입니다(`gpt-4o-mini-2024-07-18`). 설정에 적은 별칭이 아니라 **실제로 답한 스냅샷**이라, 별칭이 다른 모델을 가리키게 돼도 여기서 드러납니다.

★**추론 토큰은 출력 안에 이미 포함돼 있습니다** — 따로 더하지 마세요. gpt-5 계열로 바꾸면 이 열이 커지고, 그게 비용 증가의 출처입니다.

★**비용(달러)은 여기 적지 않습니다.** 단가는 코드 밖에서 바뀌므로 박아 두면 조용히 틀린 값이 됩니다. `토큰 × 그날의 단가` 로 계산하세요.

### 카운터별 소진 — 어느 상한이 실제로 무는가

| 카운터 | 상한 | 최대 사용 | 상한에 닿은 케이스 |
|---|---:|---:|---:|
| `tool_calls_used` | 12 | 4 | 0 |
| `events_used` | 40 | 20 | 0 |
| `propagations_used` | 12 | 12 | 3 |
| `hops_used` | 6 | 0 | 0 |

★상한값 4개는 아직 **실측 근거가 없는 잠정치**입니다(현황서 §9). 이 표가 그 근거입니다 — 한 번도 안 무는 상한과, 늘 무는 상한을 갈라 봅니다.

★**`propagations_used` 는 소진 판정 대상이 아닙니다**(2026-08-29 · `budget._CAPS`). `fetch_propagation` 은 Agent 도구가 아니라 결정론 노드라 반복 호출로 우회할 수 없고, 도구가 자기 상한 3 을 먼저 걸어 이 상한은 한 번도 문 적이 없습니다. 위 표의 「상한에 닿은 케이스」는 이 줄에 한해 **소진 신호가 아니라 관측치**입니다.

★캐시가 **11건 빗나갔습니다** — 그만큼 이 실행에서 직접 계산했고, 그 값은 실행마다 흔들립니다(현황서 §8-13). 기준선으로 쓰려면 `EMBED_CACHE_STRICT=1` 로 다시 재세요.

### 도구별 호출 빈도

| 도구 | 호출 | 쓴 케이스 | 인용 가능 | 거부 |
|---|---:|---:|---|---:|
| `get_relations` | 3 | 3 | 불가 | 0 |
| `get_events` | 11 | 9 | 불가 | 0 |
| `search_news` | 6 | 6 | 가능 | 0 |
| `search_dart` | 3 | 3 | 불가 | 0 |
| `get_business_overview` | 2 | 2 | 불가 | 0 |
| `get_market` | 4 | 3 | 불가 | 0 |
| `get_filings` | 7 | 6 | 불가 | 0 |

## 3. 링(ring) — 관측만 합니다

링은 워크스페이스에서 몇 걸음 떨어진 관계인가입니다. **작을수록 안쪽**이고, 관계는 링 순서로 줄을 세운 뒤에 자릅니다(설계서 §3).

- **Ring 0** — 양끝 모두 워크스페이스 안
- **Ring 1** — 워크스페이스 ↔ 바깥 기업
- **Ring 2** — 워크스페이스 ↔ 비-Company
- **Ring 3** — 워크스페이스와 안 닿음

| 링 | 도구가 본 관계 | 상한에 남은 것 | **최종 인용** |
|---|---:|---:|---:|
| R0 | 9 | 9 | 1 |
| R1 | 746 | 101 | 5 |
| R2 | 203 | 0 | 1 |
| R3 | 50 | 0 | 0 |
| **합계** | 1008 | 110 | 7 |

관계 kept **110** · cut **898** · 링 없는 근거 인용 **34**건(사건·검색히트·뉴스 근거에는 링이 없습니다 — **정상**) · 관계인데 링을 못 찾은 인용 **0**건(**0 이어야 정상**).

### 이 표에서 읽히는 것 — ★판정이 아니라 관측입니다

- **Ring R2, R3 는 본 것이 있는데 상한에 하나도 못 남았습니다** — 안쪽 링이 먼저 자리를 채우고 끝났다는 뜻입니다. 링 순서가 의도대로 동작한 결과일 수도, 상한이 너무 낮은 것일 수도 있습니다.
- **최종 인용 41건 중 34건이 관계가 아닙니다**(사건·뉴스 근거). 링 랭킹을 손대도 인용의 대부분은 안 움직인다는 뜻입니다.

## 4. 주장(claim) — 관측만 합니다

| 항목 | 값 |
|---|---|
| `check_claims` 를 지난 케이스 | 18 / 20 |
| 주장 총계 | 37 |
| ★**uncited**(근거를 안 단 주장) | 0 · **0.0%** |
| no_text(근거 원문을 못 찾음) | 0 |
| unlinked(질문 의도와 연결 없음) | 4 |

★**`check_claims` 를 지난 케이스 수를 먼저 보세요.** 주장 0건과 「그 노드를 안 지났다」는 다른 사실인데, 총계만 보면 같은 0 입니다.

★**이 값으로 답변 품질을 판정하지 않습니다.** `claim_check` 는 검증기가 아니라 **의심 탐지기**라 낮은 점수가 곧 거짓이 아닙니다. 답변 모델을 바꿀 때 **바꾸기 전 값과 비교**하라고 둔 자리입니다.

## 5. 케이스

### 1. `query-relations-supplies` — **PASS**

| | |
|---|---|
| 질문 | `삼성전자에 납품하는 기업은?` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_relations` |
| 무엇을 검증하나 | 기업을 지정한 관계 질의가 QUERY 앵커로 가고, Agent 가 관계 도구를 골라 재료를 채우는가. 실측: SUPPLIES_TO 1,179건 · 삼성전자 차수 1,169 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_relations, scale:단일 도구, company:단일 |
| 실제 anchor | query · ['삼성전자'] |
| 실제 도구 | `get_relations`×1 (호출 1) |
| 재료 | 관계 50 · 사건 0 · 근거 65 · 최종 인용 5 |
| 링 | 본 것 R0:3 · R1:169 · R2:44 · R3:13 / 인용 R0:1 · R1:4 |
| 임베딩 | 2회 (적중 16 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 1/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 15469ms |

### 2. `query-events-labor` — **PASS**

| | |
|---|---|
| 질문 | `SK하이닉스 노조 관련 리스크 알려줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_events`, `search_news` |
| 무엇을 검증하나 | 사건 질의가 사건 도구를 끌어오는가. 실측: SK하이닉스 사건 69건에 노무 포함 · 노무는 28사 94건 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_events, company:단일, topic:이벤트 탐색, event:노무 |
| 실제 anchor | query · ['SK하이닉스'] |
| 실제 도구 | `get_events`×1 · `search_dart`×1 · `search_news`×1 (호출 3) |
| 재료 | 관계 0 · 사건 10 · 근거 16 · 최종 인용 3 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 3회 (적중 74 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 3/12 · 사건 10/40 · 파급 8/12) |
| 소요 | 9319ms |

### 3. `query-overview-context-only` — **PASS**

| | |
|---|---|
| 질문 | `HD현대는 무슨 사업을 하는 회사야?` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_business_overview` |
| 무엇을 검증하나 | ★사업의 내용은 **참고 맥락이고 인용할 수 없다**(`citation.py` CONTEXT_ONLY). 도구가 불려 재료로 들어와도 근거 화이트리스트에 오르지 않는가. 실측: HD현대 사업개요 16,623자 — 64사 중 가장 길다 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_business_overview, company:단일, citation:인용 불가(사업개요) |
| 실제 anchor | query · ['HD현대'] |
| 실제 도구 | `get_business_overview`×1 (호출 1) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 1/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 3364ms |

### 4. `query-market-no-evidence-id` — **PASS**

| | |
|---|---|
| 질문 | `삼성전자 시가총액이랑 PER 알려줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_market` |
| 무엇을 검증하나 | ★시세는 **계산값이라 근거 id 가 없다**(`get_market` 에 evidence_id 없음). 근거 없이도 답이 나가되, 그 값이 근거인 척하지 않는가. 실측: market_data 53,045행 · 64사 × 125거래일 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_market, company:단일, citation:근거 id 없음(계산값) |
| 실제 anchor | query · ['삼성전자'] |
| 실제 도구 | `get_market`×1 (호출 1) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 1/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 2542ms |

### 5. `query-filings-list` — **PASS**

| | |
|---|---|
| 질문 | `HD현대가 낸 공시 목록을 보여줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_filings` |
| 무엇을 검증하나 | 공시 **목록**(제목까지, 본문 없음)을 끌어오는가. 실측: HD현대 documents 82건 — 파두 68 · 현대로템 55 다음으로 많다 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_filings, company:단일 |
| 실제 anchor | query · ['HD현대'] |
| 실제 도구 | `get_filings`×1 (호출 1) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 1/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 10550ms |

### 6. `query-dart-evidence` — **PASS**

| | |
|---|---|
| 질문 | `현대로템 사업보고서 내용에서 주요 제품을 찾아줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_dart`, `get_business_overview` |
| 무엇을 검증하나 | ★공시 근거는 `search_dart` 가 집는다 — `search_news` 와 **같은 컬렉션**을 `source_type` 으로만 가른다. 실측: 현대로템 dart 32 + dart_filing 27 (dart_filing 은 전 기업 통틀어 113건뿐) |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:search_dart, company:단일 |
| 실제 anchor | query · ['현대로템'] |
| 실제 도구 | `get_business_overview`×1 (호출 1) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 1/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 4415ms |

### 7. `query-news-citable` — **PASS**

| | |
|---|---|
| 질문 | `파두 실적 논란 어떻게 됐어?` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_news`, `get_events` |
| 무엇을 검증하나 | ★`search_news` 만 **인용 가능**하다(작업 B). 뉴스 근거가 재료로 들어오면 화이트리스트에 오를 수 있는가. 실측: 파두 news 97건 · 사건 26건에 실적 포함 · 실적은 30사 60건 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:search_news, company:단일, citation:인용 가능(뉴스), event:실적 |
| 실제 anchor | query · ['파두'] |
| 실제 도구 | `get_events`×1 · `get_filings`×1 · `search_news`×1 (호출 3) |
| 재료 | 관계 0 · 사건 10 · 근거 16 · 최종 인용 4 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 3회 (적중 37 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 3/12 · 사건 10/40 · 파급 10/12) |
| 소요 | 8843ms |

### 8. `query-multi-company-suit` — **PASS**

| | |
|---|---|
| 질문 | `삼성전자와 SK하이닉스 둘 다 관련된 소송 있어?` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_relations`, `get_events`, `search_news` |
| 무엇을 검증하나 | 기업 둘을 담은 질문에서 앵커는 **하나**로 좁혀지되(최고점 1개, `query_understanding._primary`) 재료는 워크스페이스 양쪽에서 온다. 실측: SUES 339건 · 분쟁소송 36사 88건 |
| 커버 분기 | anchor:QUERY, agent:호출됨, company:복수, scale:multi-tool |
| 실제 anchor | query · ['SK하이닉스'] |
| 실제 도구 | `get_events`×1 · `get_relations`×1 (호출 2) |
| 재료 | 관계 50 · 사건 19 · 근거 79 · 최종 인용 2 |
| 링 | 본 것 R0:3 · R1:415 · R2:115 · R3:37 / 인용 R1:1 |
| 임베딩 | 3회 (적중 145 · 빗나감 2) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 19/40 · 파급 8/12) |
| 소요 | 6182ms |

### 9. `query-multi-company-relation` — **PASS**

| | |
|---|---|
| 질문 | `한미반도체와 SK하이닉스 관계 알려줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_relations` |
| 무엇을 검증하나 | 두 기업 **사이의** 관계를 묻는 질문. 실측: 한미반도체 차수 164 · 근거 200건(news 169 · dart 28) |
| 커버 분기 | anchor:QUERY, agent:호출됨, company:복수, tool:get_relations |
| 실제 anchor | query · ['SK하이닉스'] |
| 실제 도구 | `get_events`×1 · `get_relations`×1 · `search_dart`×1 · `search_news`×1 (호출 4) |
| 재료 | 관계 10 · 사건 10 · 근거 31 · 최종 인용 4 |
| 링 | 본 것 R0:3 · R1:162 · R2:44 / 인용 R2:1 |
| 임베딩 | 3회 (적중 71 · 빗나감 3) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 4/12 · 사건 10/40 · 파급 6/12) |
| 소요 | 9486ms |

### 10. `query-multitool-invest-and-price` — **PASS**

| | |
|---|---|
| 질문 | `레인보우로보틱스 최근 투자 상황이랑 주가도 같이 알려줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_relations`, `get_market`, `get_events` |
| 무엇을 검증하나 | ★한 질문이 **성격이 다른 두 도구**를 요구한다 — 관계(그래프)와 시세(계산값). Agent 가 한 바퀴에 둘을 고르는가. 실측: 레인보우로보틱스 차수 181 · 사건 21건 · 근거 246건 |
| 커버 분기 | anchor:QUERY, agent:호출됨, scale:multi-tool, company:단일 |
| 실제 anchor | query · ['레인보우로보틱스'] |
| 실제 도구 | `get_filings`×1 · `get_market`×1 (호출 2) |
| 재료 | 관계 0 · 사건 0 · 근거 25 · 최종 인용 3 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 2회 (적중 10 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 6303ms |

### 11. `query-event-info-leak` — **PASS**

| | |
|---|---|
| 질문 | `현대오토에버 정보유출 사건` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_events`, `search_news` |
| 무엇을 검증하나 | 사건 유형의 **꼬리**를 덮는다 — 정보유출은 14사 19건뿐이다. 드문 유형도 재료가 잡히는가. 실측: 현대오토에버 사건 13건에 정보유출 포함 |
| 커버 분기 | anchor:QUERY, agent:호출됨, tool:get_events, topic:이벤트 탐색, event:정보유출 |
| 실제 anchor | query · ['현대오토에버'] |
| 실제 도구 | `get_events`×1 · `get_filings`×1 · `search_news`×1 (호출 3) |
| 재료 | 관계 0 · 사건 10 · 근거 17 · 최종 인용 4 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 3회 (적중 25 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 3/12 · 사건 10/40 · 파급 7/12) |
| 소요 | 11440ms |

### 12. `query-event-capital-smallcap` — **PASS**

| | |
|---|---|
| 질문 | `심텍 최근 자본거래 알려줘` |
| 기대 anchor_source | query |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_events`, `search_news`, `search_dart` |
| 무엇을 검증하나 | ★**중소형 기업 · 얇은 재료**. 기존 20질문이 삼성전자·SK하이닉스에 쏠려 못 보던 자리다. 실측: 심텍 근거 42건(news 23 · dart 18) — 삼성전자 1,247건의 3%. 자본거래는 47사 72건 |
| 커버 분기 | anchor:QUERY, agent:호출됨, topic:이벤트 탐색, event:자본거래, company:단일 |
| 실제 anchor | query · ['심텍'] |
| 실제 도구 | `get_filings`×1 · `search_dart`×1 (호출 2) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 4815ms |

### 13. `ws-semantic-strike` — **PASS**

| | |
|---|---|
| 질문 | `반도체 업계 파업 위험이 있나?` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_news`, `get_events` |
| 무엇을 검증하나 | ★**기업을 지정하지 않았는데 Agent 가 불린다.** 앵커는 워크스페이스 기업이 되고, 검색은 SEMANTIC 으로 간다. `UNRESOLVED` 와 갈리는 지점이다 — 저쪽은 Agent 를 아예 안 부른다. 실측: SEMANTIC 10건 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, topic:산업·주제 탐색, tool:search_news |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_events`×1 · `search_news`×1 (호출 2) |
| 재료 | 관계 0 · 사건 19 · 근거 26 · 최종 인용 3 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 3회 (적중 193 · 빗나감 1) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 19/40 · 파급 12/12) |
| 소요 | 6888ms |

### 14. `ws-semantic-capital-trend` — **PASS**

| | |
|---|---|
| 질문 | `최근 자본거래 동향 알려줘` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_news`, `get_events`, `search_dart` |
| 무엇을 검증하나 | 기업도 관계 키워드도 없는 **주제 탐색**. 워크스페이스가 대상 문맥이 되고 의미검색이 재료를 연다. 실측: SEMANTIC 10건 · 자본거래 47사 72건 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, topic:산업·주제 탐색, event:자본거래 |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_filings`×2 (호출 2) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 4068ms |

### 15. `ws-semantic-collusion` — **PASS**

| | |
|---|---|
| 질문 | `메모리 가격 담합 관련 소식` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_news`, `get_relations` |
| 무엇을 검증하나 | 제품·행위만 있는 질문. 「메모리」가 기업으로 오인되지 않고 WORKSPACE 로 가는가. 실측: SEMANTIC 10건 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, topic:산업·주제 탐색 |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_events`×1 · `search_news`×1 (호출 2) |
| 재료 | 관계 0 · 사건 16 · 근거 28 · 최종 인용 3 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 3회 (적중 188 · 빗나감 2) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 16/40 · 파급 6/12) |
| 소요 | 7563ms |

### 16. `ws-semantic-quality` — **PASS**

| | |
|---|---|
| 질문 | `품질 문제로 논란된 사례 있어?` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `search_news`, `get_events` |
| 무엇을 검증하나 | 사건 유형 중 **가장 얇은 축**(품질 7사 18건)을 기업 지정 없이 찾는다. 재료가 얇을 때 Agent 가 도구를 더 부르는지 보는 자리이기도 하다. 실측: SEMANTIC 10건 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, topic:이벤트 탐색, event:품질 |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_events`×2 (호출 2) |
| 재료 | 관계 0 · 사건 19 · 근거 21 · 최종 인용 3 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 4회 (적중 203 · 빗나감 3) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 20/40 · 파급 12/12) |
| 소요 | 8532ms |

### 17. `ws-market-across-workspace` — **PASS**

| | |
|---|---|
| 질문 | `우리 워크스페이스 기업들 주가 어때?` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_market` |
| 무엇을 검증하나 | ★기업 미지정 + **계산값 도구**. 앵커가 워크스페이스 전체라 `get_market` 을 기업마다 불러야 한다 — 도구 호출 횟수가 늘어나는 자리다(예산 관측). 실측: 워크스페이스 2사 × 125거래일 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, tool:get_market, company:복수, scale:multi-tool |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_market`×2 (호출 2) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 3544ms |

### 18. `ws-relationship-regulator` — **PASS**

| | |
|---|---|
| 질문 | `최근 규제당국 조사 동향` |
| 기대 anchor_source | workspace |
| 기대 Agent 호출 | 예 |
| 끌어오려는 도구 | `get_relations`, `get_events` |
| 무엇을 검증하나 | ★기업 미지정인데 검색은 **SEMANTIC 이 아니라 RELATIONSHIP** 으로 간다 — 관계 키워드가 잡혔기 때문이다. WORKSPACE 앵커가 의미검색과 1:1이 아님을 드러내는 대조군. 실측: RELATIONSHIP 10건 · REGULATES 398건 · 규제수사 26사 50건 |
| 커버 분기 | anchor:WORKSPACE, agent:호출됨, topic:산업·주제 탐색, tool:get_relations |
| 실제 anchor | workspace · ['삼성전자', 'SK하이닉스'] |
| 실제 도구 | `get_events`×2 (호출 2) |
| 재료 | 관계 0 · 사건 16 · 근거 65 · 최종 인용 7 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 4회 (적중 209 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 2/12 · 사건 20/40 · 파급 12/12) |
| 소요 | 9726ms |

### 19. `unresolved-unknown-company` — **PASS**

| | |
|---|---|
| 질문 | `무한상사 실적 알려줘` |
| 기대 anchor_source | unresolved |
| 기대 Agent 호출 | **아니오** |
| 끌어오려는 도구 | 없음 |
| 무엇을 검증하나 | ★**지정했는데 못 찾으면 워크스페이스로 갈아타지 않는다** — 그러면 「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이 된다(설계서 §14-3). `halt_no_material` 로 빠져 **Agent 를 아예 안 부르므로** 도구 호출도 0 이어야 한다 |
| 커버 분기 | anchor:UNRESOLVED, agent:미호출 |
| 실제 anchor | unresolved · 없음 |
| 실제 도구 | 없음 (호출 0) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 0/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 282ms |

### 20. `unresolved-gibberish` — **PASS**

| | |
|---|---|
| 질문 | `storminmvpsdjfk 이 뭐야` |
| 기대 anchor_source | unresolved |
| 기대 Agent 호출 | **아니오** |
| 끌어오려는 도구 | 없음 |
| 무엇을 검증하나 | 의미 없는 문자열도 **이름으로 지정된 것**으로 읽혀 UNRESOLVED 로 간다. 의미검색이 10건을 냈어도 그것을 이름 해소로 둔갑시키지 않는가 — 재료가 있다고 Agent 를 부르면 안 된다 |
| 커버 분기 | anchor:UNRESOLVED, agent:미호출 |
| 실제 anchor | unresolved · 없음 |
| 실제 도구 | 없음 (호출 0) |
| 재료 | 관계 0 · 사건 0 · 근거 0 · 최종 인용 0 |
| 링 | 본 것 없음 / 인용 없음 |
| 임베딩 | 0회 (적중 0 · 빗나감 0) |
| 예산 | 루프가 잘림 아니오 · 최종 플래그 아니오 (호출 0/12 · 사건 0/40 · 파급 0/12) |
| 소요 | 210ms |

