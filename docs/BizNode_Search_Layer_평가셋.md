# BizNode Search Layer — 회귀 평가셋

> **이 파일은 생성물입니다.** 손으로 고치지 말고 아래로 다시 만드세요.
> ```bash
> .venv-wsl/bin/python -m tests.search.eval.report \
>     -o docs/BizNode_Search_Layer_평가셋.md
> ```

마지막 실행 **2026-08-23** · 케이스 **20개**

케이스 정의는 `tests/search/eval/cases.py`, 판정은 `tests/search/eval/test_search_eval.py`에 있습니다.

```bash
.venv-wsl/bin/python -m pytest tests/search/eval -q       # 평가셋만
.venv-wsl/bin/python -m pytest tests/ -q                  # 전체
```

## 1. 한눈에 보기

| 판정 | 케이스 |
|---|---|
| PASS | 19 |
| FAIL (known issue) | 1 |

| # | 케이스 | 질의 | 판정 방식 | 결과 |
|---:|---|---|---|---|
| 1 | `name-exact-dart` | 삼성전자 | 고정값 | PASS |
| 2 | `name-two-char-company` | 농심 최근 실적 | 고정값 | PASS |
| 3 | `name-josa-noise-ilii` | SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나? | 고정값 | PASS |
| 4 | `name-english-alias` | NAVER | 고정값 | PASS |
| 5 | `rel-supplies-outgoing` | 삼성전자가 납품하는 기업은? | 구조 조건 | PASS |
| 6 | `rel-supplies-incoming` | 삼성전자에 납품하는 기업은? | 구조 조건 | PASS |
| 7 | `rel-stake-outgoing` | 삼성전자가 투자한 기업은? | 구조 조건 | PASS |
| 8 | `rel-stake-incoming` | 삼성전자에 투자한 기업은? | 구조 조건 | PASS |
| 9 | `rel-stake-bidirectional` | 삼성전자 최근 투자 기업 | 구조 조건 | PASS |
| 10 | `rel-sues-incoming` | SK하이닉스를 제소한 기업 | 구조 조건 | PASS |
| 11 | `rel-shallow-partners` | 삼성전자와 협력한 기업 | 구조 조건 | PASS |
| 12 | `rel-person-executive` | 삼성전자 임원 | 구조 조건 | PASS |
| 13 | `rel-organization-regulates` | 삼성전자를 규제한 기관 | 구조 조건 | PASS |
| 14 | `rel-anchorless-sues` | 최근 소송 관련 기업 | 구조 조건 | PASS |
| 15 | `rel-request-edge-override` | SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나? | 구조 조건 | PASS |
| 16 | `rank-workspace-relationship` | 삼성전자에 납품하는 기업은? | 고정값 | PASS |
| 17 | `sem-hbm-anchorless` | HBM을 만드는 기업 | 구조 조건 | PASS |
| 18 | `sem-unknown-company` | 존재하지않는기업 관련 뉴스 | 구조 조건 | PASS |
| 19 | `known-alias-naver` | 네이버 | 고정값 | PASS |
| 20 | `known-generic-noun-daesang` | 이 사건의 대상 기업은? | 구조 조건 | FAIL (known issue) |

## 2. 검색 분기 커버리지

| 분기 | 케이스 |
|---|---|
| anchor:DART 1차 | `name-exact-dart`, `name-english-alias` |
| anchor:Kiwi 문맥 분석 | `name-two-char-company` |
| anchor:Kiwi 조사 분리 | `name-josa-noise-ilii` |
| anchor:company_aliases fallback | `known-alias-naver` |
| anchor:추출 실패 | `rel-anchorless-sues`, `sem-hbm-anchorless`, `sem-unknown-company`, `known-generic-noun-daesang` |
| direction:INCOMING | `rel-supplies-incoming`, `rel-stake-incoming`, `rel-sues-incoming`, `rank-workspace-relationship` |
| direction:OUTGOING | `rel-supplies-outgoing`, `rel-stake-outgoing` |
| direction:없음(양방향) | `rel-stake-bidirectional`, `rel-shallow-partners`, `rel-anchorless-sues`, `rel-request-edge-override` |
| entity:Company | `rel-supplies-outgoing`, `rel-supplies-incoming`, `rel-sues-incoming` |
| entity:Organization | `rel-sues-incoming`, `rel-organization-regulates` |
| entity:Person | `rel-person-executive` |
| graph:anchored | `rel-supplies-outgoing`, `rel-supplies-incoming`, `rel-stake-outgoing`, `rel-stake-incoming`, `rel-stake-bidirectional`, `rel-sues-incoming`, `rel-shallow-partners`, `rel-person-executive`, `rel-organization-regulates`, `rel-request-edge-override`, `rank-workspace-relationship` |
| graph:anchorless | `rel-anchorless-sues`, `known-generic-noun-daesang` |
| mode:NAME | `name-exact-dart`, `name-two-char-company`, `name-josa-noise-ilii`, `name-english-alias`, `known-alias-naver` |
| mode:RELATIONSHIP | `rel-supplies-outgoing`, `rel-supplies-incoming`, `rel-stake-outgoing`, `rel-stake-incoming`, `rel-stake-bidirectional`, `rel-sues-incoming`, `rel-shallow-partners`, `rel-person-executive`, `rel-organization-regulates`, `rel-anchorless-sues`, `rel-request-edge-override`, `rank-workspace-relationship`, `known-generic-noun-daesang` |
| mode:SEMANTIC | `sem-hbm-anchorless`, `sem-unknown-company` |
| negative:2글자 기업명 | `name-two-char-company` |
| negative:일반명사 오인 방지 | `name-josa-noise-ilii`, `known-generic-noun-daesang` |
| negative:조사에 따른 방향 반전 | `rel-supplies-incoming`, `rel-stake-incoming` |
| negative:존재하지 않는 기업 | `sem-unknown-company` |
| negative:한글/영문 alias | `name-english-alias`, `known-alias-naver` |
| ranker:NAME 분기 건너뜀 | `name-exact-dart` |
| ranking:RRF | `rel-supplies-incoming`, `sem-hbm-anchorless` |
| ranking:freshness | `rel-supplies-outgoing` |
| ranking:workspace_keys | `rank-workspace-relationship` |
| router:깊은 규칙 | `rel-supplies-outgoing`, `rel-supplies-incoming`, `rel-stake-outgoing`, `rel-stake-incoming`, `rel-stake-bidirectional`, `rel-sues-incoming` |
| router:얕은 키워드 | `rel-shallow-partners`, `rel-person-executive`, `rel-organization-regulates` |
| router:요청 edge_types 우선 | `rel-request-edge-override` |
| vector:company 컬렉션 | `sem-hbm-anchorless`, `sem-unknown-company` |

## 3. 케이스

### 1. `name-exact-dart` — **PASS**

| | |
|---|---|
| query | `삼성전자` |
| expected_mode | NAME |
| expected_anchor | '삼성전자' |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['postgres'] · 정확히 1건 · 고정 기업: 삼성전자(00126380) |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | 기업명만 던졌을 때 DART 1차 정확 일치로 NAME 분기에 들어가고, ResultRanker를 건너뛰어 rrf_score가 비어 있는가 |
| 커버 분기 | mode:NAME, anchor:DART 1차, ranker:NAME 분기 건너뜀 |
| 현재 실제 결과 | mode=NAME · anchor='삼성전자' · direction=없음 · edge_types=없음 · 1건 · postgres · Company 1 · 상위 삼성전자 · 56ms |

### 2. `name-two-char-company` — **PASS**

| | |
|---|---|
| query | `농심 최근 실적` |
| expected_mode | NAME |
| expected_anchor | '농심' |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['postgres'] · 정확히 1건 · 고정 기업: 농심(00108241) |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | 2글자 실존 상장사가 _MIN_CANDIDATE_LEN 필터에 탈락하지 않는가 (상수를 2에서 올리면 이 케이스가 죽는다) |
| 커버 분기 | mode:NAME, anchor:Kiwi 문맥 분석, negative:2글자 기업명 |
| 현재 실제 결과 | mode=NAME · anchor='농심' · direction=없음 · edge_types=없음 · 1건 · postgres · Company 1 · 상위 농심 · 46ms |

### 3. `name-josa-noise-ilii` — **PASS**

| | |
|---|---|
| query | `SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?` |
| expected_mode | NAME |
| expected_anchor | 'SK하이닉스' |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['postgres'] · 정확히 1건 · 고정 기업: SK하이닉스(00164779) |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | Kiwi가 「SK하이닉스에」에서 조사 「에」를 떼고, 조사 잔여물 「일이」를 실존 법인(01355031)으로 오인하지 않는가 (현황서 §4-1) |
| 커버 분기 | mode:NAME, anchor:Kiwi 조사 분리, negative:일반명사 오인 방지 |
| 현재 실제 결과 | mode=NAME · anchor='SK하이닉스' · direction=없음 · edge_types=없음 · 1건 · postgres · Company 1 · 상위 SK하이닉스 · 40ms |

### 4. `name-english-alias` — **PASS**

| | |
|---|---|
| query | `NAVER` |
| expected_mode | NAME |
| expected_anchor | 'NAVER' |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['postgres'] · 정확히 1건 · 고정 기업: NAVER(00266961) |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | corp_code_master에 영문으로 등재된 법인은 영문 질의로 NAME 분기에 들어간다 — 한글 질의(known-alias-naver)와의 비대칭을 드러내는 대조군 |
| 커버 분기 | mode:NAME, anchor:DART 1차, negative:한글/영문 alias |
| 현재 실제 결과 | mode=NAME · anchor='NAVER' · direction=없음 · edge_types=없음 · 1건 · postgres · Company 1 · 상위 NAVER · 38ms |

### 5. `rel-supplies-outgoing` — **PASS**

| | |
|---|---|
| query | `삼성전자가 납품하는 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | outgoing |
| expected_edge_type | ['SUPPLIES_TO'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company'] 안 · 반드시 포함: Company |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 주체 조사 「가」로 direction=OUTGOING을 잡고, 결과가 전부 삼성전자를 source로 하는 관계인가. 겸해서 freshness가 순위에 반영되는가(expired 배제 · stale 감점)를 본다 |
| 커버 분기 | mode:RELATIONSHIP, direction:OUTGOING, router:깊은 규칙, graph:anchored, entity:Company, ranking:freshness |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=outgoing · edge_types=['SUPPLIES_TO'] · 10건 · neo4j · Company 10 · 신선도 current 6 stale 4 · 상위 구글 · AMD · CSOT · 103ms |

### 6. `rel-supplies-incoming` — **PASS**

| | |
|---|---|
| query | `삼성전자에 납품하는 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | incoming |
| expected_edge_type | ['SUPPLIES_TO'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company'] 안 · 반드시 포함: Company |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 대상 조사 「에」 하나로 같은 edge_type의 방향이 뒤집히는가. 겸해서 단일 소스 RRF 값이 1/(60+rank)로 매겨지는가를 본다 |
| 커버 분기 | mode:RELATIONSHIP, direction:INCOMING, router:깊은 규칙, graph:anchored, entity:Company, ranking:RRF, negative:조사에 따른 방향 반전 |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=incoming · edge_types=['SUPPLIES_TO'] · 10건 · neo4j · Company 10 · 신선도 current 10 · 상위 SFA반도체 · ㈜원익아이피에스 · 링크솔루션 · 85ms |

### 7. `rel-stake-outgoing` — **PASS**

| | |
|---|---|
| query | `삼성전자가 투자한 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | outgoing |
| expected_edge_type | ['OWNS_STAKE_IN'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company', 'Organization'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | OWNS_STAKE_IN에서도 주체 조사가 OUTGOING을 만드는가 (투자자 → 피투자사) |
| 커버 분기 | mode:RELATIONSHIP, direction:OUTGOING, router:깊은 규칙, graph:anchored |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=outgoing · edge_types=['OWNS_STAKE_IN'] · 10건 · neo4j · Company 10 · 신선도 current 10 · 상위 스킬드AI · 미스트랄AI · 주타코어 · 98ms |

### 8. `rel-stake-incoming` — **PASS**

| | |
|---|---|
| query | `삼성전자에 투자한 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | incoming |
| expected_edge_type | ['OWNS_STAKE_IN'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company', 'Organization', 'Person'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 같은 edge_type이 대상 조사에서 INCOMING으로 뒤집히는가 (피투자사 ← 투자자). 상대가 Person일 수도 있다(EDGE_MATRIX) |
| 커버 분기 | mode:RELATIONSHIP, direction:INCOMING, router:깊은 규칙, graph:anchored, negative:조사에 따른 방향 반전 |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=incoming · edge_types=['OWNS_STAKE_IN'] · 10건 · neo4j · Company 7 Person 3 · 신선도 current 6 stale 4 · 상위 삼성생명보험㈜ (특별계정) · 삼성화재해상보험 · 삼성복지재단 · 73ms |

### 9. `rel-stake-bidirectional` — **PASS**

| | |
|---|---|
| query | `삼성전자 최근 투자 기업` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['OWNS_STAKE_IN'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company', 'Organization', 'Person'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 조사가 없으면 방향을 강제하지 않고(direction=None) 양방향 관계가 모두 후보에 남는가 |
| 커버 분기 | mode:RELATIONSHIP, direction:없음(양방향), router:깊은 규칙, graph:anchored |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=없음 · edge_types=['OWNS_STAKE_IN'] · 10건 · neo4j · Company 10 · 신선도 current 10 · 상위 스킬드AI · 삼성생명보험㈜ (특별계정) · 삼성화재해상보험 · 73ms |

### 10. `rel-sues-incoming` — **PASS**

| | |
|---|---|
| query | `SK하이닉스를 제소한 기업` |
| expected_mode | RELATIONSHIP |
| expected_anchor | 'SK하이닉스' |
| expected_direction | incoming |
| expected_edge_type | ['SUES'] |
| expected_result/source | source=['neo4j'] · 3건 이상 · 엔티티 ['Company', 'Organization', 'Person'] 안 · 반드시 포함: Company |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 목적격 조사 「를」+제소가 INCOMING(피고가 앵커)으로 읽히는가 |
| 커버 분기 | mode:RELATIONSHIP, direction:INCOMING, router:깊은 규칙, graph:anchored, entity:Company, entity:Organization |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='SK하이닉스' · direction=incoming · edge_types=['SUES'] · 8건 · neo4j · Company 4 Organization 3 Person 1 · 신선도 stale 6 current 2 · 상위 소비자 집단 · 김진원 · 넷리스트 · 49ms |

### 11. `rel-shallow-partners` — **PASS**

| | |
|---|---|
| query | `삼성전자와 협력한 기업` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['PARTNERS_WITH'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company', 'Organization'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 대표 키워드 1개만 등록된 얕은 규칙은 edge_type만 잡고 direction은 None으로 둔다 — 방향을 지어내지 않는가 |
| 커버 분기 | mode:RELATIONSHIP, direction:없음(양방향), router:얕은 키워드, graph:anchored |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=없음 · edge_types=['PARTNERS_WITH'] · 10건 · neo4j · Company 9 Organization 1 · 신선도 current 10 · 상위 화웨이 · 레인보우로보틱스 · 에릭슨 · 70ms |

### 12. `rel-person-executive` — **PASS**

| | |
|---|---|
| query | `삼성전자 임원` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['IS_EXECUTIVE_OF'] |
| expected_result/source | source=['neo4j'] · 3건 이상 · 엔티티 ['Person'] 안 · 반드시 포함: Person |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 상대 엔티티가 Person인 관계도 라벨을 지어내지 않고 그대로 싣는가. 앵커가 Company인 IS_EXECUTIVE_OF는 EDGE_MATRIX상 상대가 Person뿐이다 |
| 커버 분기 | mode:RELATIONSHIP, router:얕은 키워드, graph:anchored, entity:Person |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=없음 · edge_types=['IS_EXECUTIVE_OF'] · 10건 · neo4j · Person 10 · 신선도 current 6 stale 4 · 상위 신제윤 · 김준성 · 허은녕 · 48ms |

### 13. `rel-organization-regulates` — **PASS**

| | |
|---|---|
| query | `삼성전자를 규제한 기관` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['REGULATES'] |
| expected_result/source | source=['neo4j'] · 3건 이상 · 엔티티 ['Organization'] 안 · 반드시 포함: Organization |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 상대 엔티티가 Organization인 관계. REGULATES의 source는 EDGE_MATRIX상 Organization뿐이다 |
| 커버 분기 | mode:RELATIONSHIP, router:얕은 키워드, graph:anchored, entity:Organization |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=없음 · edge_types=['REGULATES'] · 10건 · neo4j · Organization 10 · 신선도 current 10 · 상위 미국 정부 · 금융위원회 증권선물위원회 · 서울남부지검 금융·증권범죄합동수사부 · 50ms |

### 14. `rel-anchorless-sues` — **PASS**

| | |
|---|---|
| query | `최근 소송 관련 기업` |
| expected_mode | RELATIONSHIP |
| expected_anchor | None |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['SUES'] |
| expected_result/source | source=['neo4j'] · 2건 이상 · 엔티티 ['Company', 'Organization', 'Person'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 기업명이 없으면 anchorless 경로로 빠져 source/target 슬롯을 따로 채우는가. 앵커가 없으므로 관계의 direction은 지어내지 않고 None이며, 결과가 있어도 VectorSearcher를 섞지 않는다 |
| 커버 분기 | mode:RELATIONSHIP, direction:없음(양방향), anchor:추출 실패, graph:anchorless |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor=None · direction=없음 · edge_types=['SUES'] · 8건 · neo4j · Company 8 · 신선도 current 8 · 상위 한미반도체 · 한화세미텍 · 넷리스트 · 123ms |

### 15. `rel-request-edge-override` — **PASS**

| | |
|---|---|
| query | `SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | 'SK하이닉스' |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['SUPPLIES_TO', 'DEPENDS_ON'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company', 'Product'] 안 · 요청 edge_types=['SUPPLIES_TO', 'DEPENDS_ON'] |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | QueryRouter가 아무 키워드도 못 잡는 질의라도 요청이 edge_types를 실으면 RELATIONSHIP으로 간다 — 같은 질의가 name-josa-noise-ilii 에서는 NAME이었다(챗봇 탐색 프로파일 배선) |
| 커버 분기 | mode:RELATIONSHIP, direction:없음(양방향), graph:anchored, router:요청 edge_types 우선 |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='SK하이닉스' · direction=없음 · edge_types=['SUPPLIES_TO', 'DEPENDS_ON'] · 10건 · neo4j · Company 10 · 신선도 current 10 · 상위 넥스틴 · SFA반도체 · 엔비디아 · 66ms |

### 16. `rank-workspace-relationship` — **PASS**

| | |
|---|---|
| query | `삼성전자에 납품하는 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | '삼성전자' |
| expected_direction | incoming |
| expected_edge_type | ['SUPPLIES_TO'] |
| expected_result/source | source=['neo4j'] · 5건 이상 · 엔티티 ['Company'] 안 · 고정 기업: SK하이닉스(00164779) · workspace_keys=['00164779'] |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | 워크스페이스는 필터가 아니라 랭킹 문맥이다 — 워크스페이스에 닿는 관계가 점수를 이기고 먼저 오되, 바깥 기업이 후보에서 사라지지는 않는가. SK하이닉스는 점수순으로는 271번째라 워크스페이스 없이는 top-10에 못 든다(현황서 §5) |
| 커버 분기 | mode:RELATIONSHIP, direction:INCOMING, graph:anchored, ranking:workspace_keys |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='삼성전자' · direction=incoming · edge_types=['SUPPLIES_TO'] · 10건 · neo4j · Company 10 · 신선도 current 9 stale 1 · 상위 SK하이닉스 · SFA반도체 · ㈜원익아이피에스 · 84ms |

### 17. `sem-hbm-anchorless` — **PASS**

| | |
|---|---|
| query | `HBM을 만드는 기업` |
| expected_mode | SEMANTIC |
| expected_anchor | None |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['chroma'] · 정확히 10건 · 엔티티 ['Company'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 기업명도 관계 키워드도 없으면 VectorSearcher로 빠지는가. company 컬렉션만 보므로 결과는 전부 Company다 |
| 커버 분기 | mode:SEMANTIC, anchor:추출 실패, vector:company 컬렉션, ranking:RRF |
| 현재 실제 결과 | mode=SEMANTIC · anchor=None · direction=없음 · edge_types=없음 · 10건 · chroma · Company 10 · 상위 한미반도체 · HD현대 · SK하이닉스 · 1440ms |

### 18. `sem-unknown-company` — **PASS**

| | |
|---|---|
| query | `존재하지않는기업 관련 뉴스` |
| expected_mode | SEMANTIC |
| expected_anchor | None |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['chroma'] · 정확히 10건 · 엔티티 ['Company'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 없는 기업명을 실존 기업으로 잘못 해소하지 않는가 — anchor는 None, EntityResolver도 None이어야 하고, 의미검색 결과를 이름 해소로 둔갑시키지 않는다(mode는 SEMANTIC이지 NAME이 아니다) |
| 커버 분기 | mode:SEMANTIC, anchor:추출 실패, vector:company 컬렉션, negative:존재하지 않는 기업 |
| 현재 실제 결과 | mode=SEMANTIC · anchor=None · direction=없음 · edge_types=없음 · 10건 · chroma · Company 10 · 상위 하나마이크론 · 코미코 · 뉴로메카 · 178ms |

### 19. `known-alias-naver` — **PASS**

| | |
|---|---|
| query | `네이버` |
| expected_mode | NAME |
| expected_anchor | 'NAVER Corporation' |
| expected_direction | 해당 없음(관계 질의가 아니다) |
| expected_edge_type | 없음 |
| expected_result/source | source=['postgres'] · 정확히 1건 · 고정 기업: NAVER(00266961) |
| 판정 방식 | 고정값(기업명·corp_code를 못 박는다) |
| 무엇을 검증하나 | corp_code_master에 'NAVER'로만 있는 회사를 한글 「네이버」로 물어도 이름 해소로 답한다. company_aliases 2차 창구가 **정식 법인명**을 돌려주므로 EntityResolver의 fuzzy 경로가 normalize로 'naver'를 만들어 1.000으로 붙는다 — 두 컴포넌트가 같은 창구를 쓴다(2026-08-23 해소) |
| 커버 분기 | mode:NAME, anchor:company_aliases fallback, negative:한글/영문 alias |
| 현재 실제 결과 | mode=NAME · anchor='NAVER Corporation' · direction=없음 · edge_types=없음 · 1건 · postgres · Company 1 · 상위 NAVER · 49ms |

### 20. `known-generic-noun-daesang` — **FAIL (known issue)**

| | |
|---|---|
| query | `이 사건의 대상 기업은?` |
| expected_mode | RELATIONSHIP |
| expected_anchor | None |
| expected_direction | 없음(양방향) |
| expected_edge_type | ['HAS_EVENT'] |
| expected_result/source | source=['neo4j'] · 0건 이상 · 엔티티 ['Company', 'Event', 'Product'] 안 |
| 판정 방식 | 구조 조건만(기업명 미고정) |
| 무엇을 검증하나 | 일상어 「대상」이 동명 실존 법인(00121941 대상)으로 잡히면 안 된다. 지금은 corp_code_master 1차에서 1.000 정확 일치라 앵커가 붙고, 그 결과 HAS_EVENT anchored 검색이 0건을 낸다 |
| 커버 분기 | mode:RELATIONSHIP, anchor:추출 실패, graph:anchorless, negative:일반명사 오인 방지 |
| 현재 실제 결과 | mode=RELATIONSHIP · anchor='대상' · direction=없음 · edge_types=['HAS_EVENT'] · 0건 · 53ms |
| ⚠ known issue | 동음이의 사명은 형태소 분석으로 못 가른다(현황서 §4-5). 질의 의도를 봐야 갈린다. 이번 작업 범위 밖(수정 금지) |

## 4. 알려진 결함 (이번 작업에서 고치지 않았다)

`xfail(strict=True)`로 돌아갑니다 — **지금은 실패로 집계되고**, 결함이 고쳐지면 XPASS로 뒤집혀 평가셋을 갱신하라고 알립니다.

### `known-generic-noun-daesang` — 이 사건의 대상 기업은?

- **기대** RELATIONSHIP · anchor=None
- **실제** mode=RELATIONSHIP · anchor='대상' · direction=없음 · edge_types=['HAS_EVENT'] · 0건 · 53ms
- **왜** 동음이의 사명은 형태소 분석으로 못 가른다(현황서 §4-5). 질의 의도를 봐야 갈린다. 이번 작업 범위 밖(수정 금지)

## 5. 분기별 심층 판정

케이스 공통 판정으로는 못 보는 것들입니다. 케이스 하나에 묶이지 않아 따로 돌아갑니다.

| 테스트 | 결과 |
|---|---|
| `test_alias_fallback_is_the_second_window` | PASS |
| `test_anchorless_search_fills_both_slots` | PASS |
| `test_bidirectional_query_keeps_both_directions` | PASS |
| `test_coverage_is_complete` | PASS |
| `test_direction_flips_with_josa` | PASS |
| `test_excluded_relations_never_leak` | PASS |
| `test_freshness_demotes_stale_relations` | PASS |
| `test_rrf_score_follows_rank_for_single_source` | PASS |
| `test_unknown_company_is_not_resolved` | PASS |
| `test_workspace_keys_rank_without_filtering` | PASS |

