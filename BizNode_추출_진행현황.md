# BizNode 뉴스 추출 진행현황

> ⚠ **이 문서는 자동 생성됩니다. 직접 고치지 마세요.**
> `python -m batch.ops.status --write-doc` 으로 갱신하세요.
> 사실의 출처는 PostgreSQL `extraction_runs` 대장입니다.

생성 시각: 2026-08-01 01:28

## 요약

| 항목 | 값 |
|---|---:|
| 시드 기업 | 64 |
| 추출 완료 | **29** |
| 미진행 | 35 |
| 누적 추출 비용 | 29,210원 |
| 뉴스 엣지(대장 누계) | 7,745 |
| 그래프 전체 노드 | 4,828 |
| 그래프 전체 엣지 | 6,445 |
| 그래프 뉴스 엣지 | 3,793 |
| Event 노드 | 579 |
| Product 노드 | 1,175 |

## 완료 기업

| 기업 | 시장 | 섹터 | 추출 기사 | 생성 엣지 | 비용 | 최근 실행 |
|---|---|---|---:|---:|---:|---|
| 삼성전자 | KOSPI | 반도체 · 로봇 | 400 | 1931 | 6,030원 | 2026-07-30 |
| SK하이닉스 | KOSPI | 반도체 · 로봇 | 285 | 1213 | 4,302원 | 2026-07-31 |
| 두산로보틱스 | KOSPI | 로봇 | 216 | 629 | 3,243원 | 2026-07-31 |
| 한미반도체 | KOSPI | 반도체 | 100 | 531 | 1,532원 | 2026-07-28 |
| 레인보우로보틱스 | KOSDAQ | 로봇 | 100 | 415 | 1,509원 | 2026-07-28 |
| 로보티즈 | KOSDAQ | 로봇 | 100 | 354 | 1,501원 | 2026-07-31 |
| DB하이텍 | KOSPI | 반도체 | 75 | 301 | 1,131원 | 2026-07-31 |
| 뉴로메카 | KOSDAQ | 로봇 | 84 | 298 | 1,262원 | 2026-07-31 |
| 하나마이크론 | KOSDAQ | 반도체 | 48 | 216 | 722원 | 2026-07-30 |
| ISC | KOSDAQ | 반도체 | 59 | 206 | 887원 | 2026-07-31 |
| HPSP | KOSDAQ | 반도체 | 46 | 196 | 691원 | 2026-07-30 |
| LX세미콘 | KOSPI | 반도체 | 40 | 153 | 601원 | 2026-07-31 |
| 두산테스나 | KOSDAQ | 반도체 | 33 | 129 | 497원 | 2026-07-29 |
| 하나머티리얼즈 | KOSDAQ | 반도체 | 30 | 125 | 451원 | 2026-07-30 |
| 리노공업 | KOSDAQ | 반도체 | 43 | 116 | 655원 | 2026-07-31 |
| 원익IPS | KOSDAQ | 반도체 | 24 | 107 | 364원 | 2026-07-30 |
| 넥스틴 | KOSDAQ | 반도체 | 21 | 86 | 315원 | 2026-07-30 |
| 티씨케이 | KOSDAQ | 반도체 | 20 | 79 | 300원 | 2026-07-30 |
| 주성엔지니어링 | KOSDAQ | 반도체 | 26 | 78 | 398원 | 2026-07-30 |
| 심텍 | KOSDAQ | 반도체 | 19 | 74 | 289원 | 2026-07-31 |
| 테크윙 | KOSDAQ | 반도체 | 25 | 69 | 376원 | 2026-07-29 |
| 유진로봇 | KOSDAQ | 로봇 | 28 | 65 | 426원 | 2026-07-31 |
| 피에스케이 | KOSDAQ | 반도체 | 17 | 60 | 258원 | 2026-07-30 |
| 가온칩스 | KOSDAQ | 반도체 | 23 | 60 | 347원 | 2026-07-31 |
| 이오테크닉스 | KOSDAQ | 반도체 | 13 | 58 | 197원 | 2026-07-29 |
| SFA반도체 | KOSDAQ | 반도체 | 14 | 58 | 210원 | 2026-07-29 |
| 고영 | KOSDAQ | 반도체 · 로봇 | 20 | 56 | 302원 | 2026-07-30 |
| 제주반도체 | KOSDAQ | 반도체 | 13 | 46 | 203원 | 2026-07-31 |
| 유진테크 | KOSDAQ | 반도체 | 14 | 36 | 211원 | 2026-07-30 |

## 미진행 기업 (섹터별)

### 반도체 — 21/32 완료

- 파두(KOSDAQ) · 피에스케이홀딩스(KOSDAQ) · 코미코(KOSDAQ) · RFHIC(KOSDAQ)
- 케이씨텍(비상장) · 태성(비상장) · 에스앤에스텍(KOSDAQ) · HD현대에너지솔루션(KOSPI)
- 필옵틱스(KOSDAQ) · 와이씨(KOSDAQ) · 덕산네오룩스(KOSDAQ)

### 로봇 — 5/29 완료

- 현대모비스(KOSPI) · LG전자(KOSPI) · 현대오토에버(KOSPI) · 현대차증권(KOSPI)
- NAVER(KOSPI) · LG이노텍(KOSPI) · 삼성에스디에스(KOSPI) · 에스피지(KOSDAQ)
- HD현대(KOSPI) · 케이티(KOSPI) · 클로봇(KOSDAQ) · 유일로보틱스(KOSDAQ)
- 현대로템(KOSPI) · 원익홀딩스(KOSDAQ) · 큐렉소(KOSDAQ) · 삼현(KOSDAQ)
- 에스비비테크(KOSDAQ) · 제이브이엠(KOSDAQ) · 카카오(KOSPI) · 엔젤로보틱스(KOSDAQ)
- 하이젠알앤엠(KOSDAQ) · 비에이치(KOSPI) · 에스오에스랩(KOSDAQ) · 나우로보틱스(KOSDAQ)

### 반도체 · 로봇 — 3/3 완료

전부 완료.

## 다음 배치 추천 (밸류체인 응집 순)

무작위로 고르면 서로 안 이어져 2홉 경로가 생기지 않습니다.
이미 stub으로 등장하는 기업을 먼저 하면 새 노드를 만드는 대신
**기존 stub을 실제 데이터로 채우게** 되어 연결이 촘촘해집니다.

1. `python -m batch.ops.pilot_company 유일로보틱스 --years 3 --limit 100`
2. `python -m batch.ops.pilot_company 나우로보틱스 --years 3 --limit 100`
3. `python -m batch.ops.pilot_company 에스피지 --years 3 --limit 100`
4. `python -m batch.ops.pilot_company 하이젠알앤엠 --years 3 --limit 100`
5. `python -m batch.ops.pilot_company 현대모비스 --years 3 --limit 100`
6. `python -m batch.ops.pilot_company LG전자 --years 3 --limit 100`
7. `python -m batch.ops.pilot_company LG이노텍 --years 3 --limit 100`
8. `python -m batch.ops.pilot_company 현대오토에버 --years 3 --limit 100`
9. `python -m batch.ops.pilot_company 삼성에스디에스 --years 3 --limit 100`
10. `python -m batch.ops.pilot_company 파두 --years 3 --limit 100`

예상 비용 약 26,000원 (≈ $18.8)

## 배치 실행 순서

```bash
# 1) 추출 — 기업별로 (대장에 자동 기록됨)
python -m batch.ops.pilot_company <기업명> --years 3 --limit 100

# 2) 정리·검사 — 배치가 끝나면 한 번 (전량 대상, 증분 실행)
python -m batch.ops.finalize

# 3) 표본 심층검사 — 사람이 12건 정독 (새 실패 유형 찾기)
python -m batch.audit.spot_check --per-type 1 --source news

# 4) 이 문서 갱신
python -m batch.ops.status --write-doc
```
