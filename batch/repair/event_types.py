"""Event에 **고정 유형**과 **리스크 여부**를 붙인다.

왜 필요한가 (2026-08-01)

`event_type`이 전 사건 575건에서 **전부 「뉴스이슈」**였다. 그래서 리스크 추론의
가장 기본 질의가 안 됐다:

    "두산로보틱스에 어떤 악재가 있나"
      → M&A · 미국 신공장 가동 · 원엑시아 인수 · 코스모스 쿡오프 1위
      → **악재가 아니라 그냥 사건 목록**이다

`IMPACTS.sign`이 있지만 그건 **영향의 방향**이지 사건의 성격이 아니고, 실측상
대부분 비어 있었다. 사건 자체에 유형과 리스크 여부가 있어야 한다.

    python -m batch.repair.event_types --dry-run
    python -m batch.repair.event_types

【두 축으로 나눈다】
  event_type : 무슨 종류의 일인가 (12종 고정)
  is_risk    : 기업에 **부정적**인 일인가

둘을 나누는 이유 — 같은 유형이라도 방향이 다를 수 있다:
    「특허 소송 패소」  분쟁 · risk=true
    「특허 소송 승소」  분쟁 · risk=false
유형만으로 리스크를 판정하면 이 구분이 사라진다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_WORKERS = 8

# ── 고정 유형 12종 ────────────────────────────────────────────
# 리스크 조회에 쓸 수 있도록 **원인별로** 나눈다. 「뉴스이슈」 하나로는
# 「어떤 악재가 있나」에 답할 수 없었다.
EVENT_TYPES = [
    "사고재해",    # 화재·폭발·누출·붕괴·사망·중대재해
    "노무",       # 파업·임단협·노사갈등·쟁의
    "규제수사",    # 과징금·제재·압수수색·기소·시정명령·수출규제
    "분쟁소송",    # 소송·특허침해·경영권분쟁·주주갈등
    "품질",       # 리콜·결함·불량·수율 문제
    # ★2026-08-15 신설. 「기타」 146건을 열어 보니 15건이 여기였다 —
    #   「삼성전자 반도체 핵심기술 유출」·「코미코 해킹 피해」·「개인정보 유출 사건」.
    #   규제수사도 분쟁소송도 아니고, **반도체 산업에서는 핵심 리스크**다.
    "정보유출",    # 기술·영업비밀·개인정보 유출 · 해킹 · 산업스파이
    "실적",       # 적자·어닝쇼크·신용등급·상장폐지
    "공급망",      # 공급차질·감산·생산중단·조달 문제
    "사업확장",    # 증설·착공·신공장·양산개시·수주
    "자본거래",    # 인수·합병·매각·상장·유상증자·지분변동
    "제품기술",    # 개발·출시·인증·기술확보
    "기타",
]

# ★「기타」도 다시 본다(2026-08-15). 「뉴스이슈」만 대상이면 한 번 「기타」로
#   떨어진 것은 영영 그대로 남는다 — 근거가 늘어도 다시 안 묻는다.
#   사건 국면이 병합되면 이름이 구체적으로 바뀌므로 재판정 가치가 있다.
# ★`--event-id` 는 **이미 분류된 것 하나만** 다시 묻는 길이다(2026-09-05).
#   프롬프트에 오분류 예시를 더한 뒤 그 사건들만 되돌리려는데, `--full` 은
#   1,000건 넘는 전체를 다시 부른다 — 비용도 비용이지만 **멀쩡한 라벨까지
#   흔들린다.** 고친 것만 확인하고 싶을 때 쓴다.
_FIND = """
MATCH (e:Event)
WHERE ($ids IS NOT NULL AND e.event_id IN $ids)
   OR ($ids IS NULL AND
       ($full OR e.event_type IS NULL OR e.event_type IN ['뉴스이슈', '기타']))
OPTIONAL MATCH (e)-[r]-(c:Company)
RETURN e.event_id AS eid, e.name AS name,
       collect(DISTINCT c.name)[0..4] AS companies,
       collect(DISTINCT coalesce(r.occurred_at, r.valid_from))[0..2] AS dates,
       e.timeline AS timeline
"""

_APPLY = """
MATCH (e:Event {event_id: $eid})
SET e.event_type = $etype, e.is_risk = $risk, e.classified_at = datetime()
"""

_SYSTEM = f"""사건에 **유형**과 **리스크 여부**를 붙이세요.

【유형 — 아래 12종 중 하나만】
· 사고재해 : 화재·폭발·누출·붕괴·사망·중대재해·안전사고
· 노무     : 파업·임단협·노사갈등·쟁의·성과급 분쟁
· 규제수사 : 과징금·제재·압수수색·기소·시정명령·수출규제·조사 착수
· 분쟁소송 : 소송·특허침해·경영권 분쟁·주주 갈등·가처분
· 품질     : 리콜·결함·불량·수율 문제
· 정보유출 : 기술·영업비밀·개인정보 유출 · 해킹 · 산업스파이 · 보안 사고
            ★수사로 이어져도 **유출 자체가 사건**이면 여기입니다.
              「핵심기술 유출 혐의로 기소」 → 정보유출 (규제수사 아님)
· 실적     : 적자·어닝쇼크·신용등급 변동·상장폐지·급감
· 공급망   : 공급 차질·감산·생산중단·조달 문제·물량 배분
· 사업확장 : 증설·착공·신공장·양산 개시·수주·투자 결정
· 자본거래 : 인수·합병·매각·상장·유상증자·지분 변동
· 제품기술 : 개발 완료·출시·인증·기술 확보·수율 향상
· 기타     : 위 어디에도 안 맞는 것

【리스크 여부 — is_risk】
그 기업의 **사업·재무·평판에 실질적으로 부정적 영향**을 주면 true.

★유형과 리스크는 **별개**입니다. 같은 유형이라도 방향이 다릅니다:
    「특허 소송 패소」   분쟁소송 · is_risk=true
    「특허 소송 승소」   분쟁소송 · is_risk=false
    「공장 화재」        사고재해 · is_risk=true
    「신공장 착공」      사업확장 · is_risk=false
    「경쟁사 제재」      규제수사 · is_risk=false  ← 우리 기업엔 오히려 호재

★**기업 리스크가 아닌 것**을 리스크로 잡지 마세요. 실측된 오분류:
    「이천캠퍼스 사고」 — 직원이 연습면허로 운전하다 낸 **개인 접촉사고**
      → 회사 부지에서 일어났을 뿐 사업에 영향이 없다. is_risk=false
  다음은 사건 유형이 무엇이든 **is_risk=false**입니다:
    · 임직원 개인의 사고·비위·신상 (회사가 당사자가 아닌 것)
    · 업계 일반론·시장 전망·경쟁사만의 문제
    · 규모가 미미해 사업에 영향이 없는 일
  판단 기준: **이 일로 회사의 생산·매출·주가·신뢰가 실제로 흔들리는가.**

★「의혹」「논란」을 「기타·비리스크」로 버리지 마세요 — 실측된 오분류입니다.
  이 낱말이 붙었다는 건 **회사가 문제 제기의 대상이 됐다**는 뜻이고, 대개
  평판·규제 리스크입니다. 내용을 보고 제 유형에 넣으세요:

      「포장갈이 의혹」          제품을 재포장해 납품했다는 의혹
                              → 기타 ✗   품질 · is_risk=true ✓
      「위장계열사 논란」         공정위 신고·검찰 고발로 이어진 사안
                              → 기타 ✗   규제수사 · is_risk=true ✓
      「투자정보 유출 의혹」       미공개정보 이용 여부가 쟁점
                              → 기타 ✗   규제수사 · is_risk=true ✓
      「위임장 논란」            주주총회 표대결 관련 분쟁
                              → 기타 ✗   분쟁소송 · is_risk=true ✓

  단 **결과가 나와 해소된 것**은 리스크가 아닙니다:
      「D램 가격 담합 의혹 항소심 완승」  → 규제수사 · is_risk=false

★**판촉·마케팅 「이벤트」를 사업확장으로 잡지 마세요** — 실측된 오분류입니다.
  「이벤트」는 한국어에서 **사건**과 **판촉 행사** 둘 다 뜻하는데, 뒤쪽은
  기업 관계에 아무 값이 없습니다. 사업확장은 **생산·수주·투자 결정**이지
  고객 모으기가 아닙니다:

      「신규 계좌 개설 이벤트」              → 사업확장 ✗   기타 ✓
      「100만명 돌파 기념 포인트 적립 이벤트」 → 사업확장 ✗   기타 ✓
      「신규 고객 대상 우량주 추첨 이벤트」    → 사업확장 ✗   기타 ✓

  가르는 기준: **회사의 생산능력·계약·지분이 움직였나.** 경품·적립·할인·추첨·
  가입 유치는 안 움직입니다. 반대로 「수주」·「증설」은 판촉 낱말이 섞여 있어도
  사업확장입니다.

★「기타」는 11종 어디에도 정말 안 맞을 때만 쓰세요. 지금 「기타」가 전체의
  9%인데 대부분 위처럼 제 유형이 있는 사건이었습니다.

판단이 어려우면 유형은 「기타」, is_risk는 false로 두세요.
연결된 기업과 발생 시점을 참고로 제공합니다."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "is_risk": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["event_type", "is_risk", "reason"],
    "additionalProperties": False,
}


def _classify(row: dict) -> tuple[dict, dict]:
    comps = [c for c in (row.get("companies") or []) if c]
    dates = [str(d)[:10] for d in (row.get("dates") or []) if d]
    user = (f"사건: 「{row['name']}」\n"
            f"연결 기업: {', '.join(comps[:4]) or '(없음)'}\n"
            f"시점: {', '.join(dates[:2]) or '(미상)'}")
    # ★국면이 있으면 함께 준다(2026-08-15). 사건 이름 하나만으로는 「뉴스이슈」로
    #   떨어지던 것이, 국면을 보면 성격이 드러난다:
    #       「HBM4」 만으로는 모름 → 국면에 「양산 일정 연기」·「생산라인 전환」이
    #       보이면 공급망이다.
    phases = [p.split("|")[1] for p in (row.get("timeline") or [])
              if isinstance(p, str) and "|" in p]
    if phases:
        user += "\n국면: " + " → ".join(phases[:6])
    return row, ask_json(_SYSTEM, user, schema=_SCHEMA, name="event_class",
                         fallback={"event_type": "기타", "is_risk": False,
                                   "reason": ""})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full", action="store_true", help="이미 분류한 것도 다시")
    ap.add_argument("--event-id", nargs="+", metavar="ID",
                    help="이 사건들만 다시 묻는다 (이미 분류됐어도)")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND, full=args.full,
                                             ids=args.event_id)]
    print(f"분류 대상 Event {len(rows)}건 (약 {len(rows)*0.3:.0f}원)")
    if not rows:
        print("분류할 사건이 없습니다.")
        return 0

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(_classify, rows))

    tally, risk_n, failed = Counter(), 0, 0
    for _, v in results:
        if v.get("failed"):
            failed += 1
            continue
        tally[v["event_type"]] += 1
        risk_n += bool(v["is_risk"])
    ok = len(results) - failed
    print(f"\n{'유형':12}{'건수':>6}{'비율':>7}")
    print("-" * 28)
    for t in EVENT_TYPES:
        if tally[t]:
            print(f"{t:12}{tally[t]:>6}{tally[t]/max(ok,1)*100:>6.0f}%")
    print("-" * 28)
    print(f"{'리스크':12}{risk_n:>6}{risk_n/max(ok,1)*100:>6.0f}%")
    if failed:
        print(f"  ⚠ 분류 실패 {failed}건 — 기록하지 않습니다")

    print("\n리스크 사건 예시:")
    for r, v in [(r, v) for r, v in results
                 if v.get("is_risk") and not v.get("failed")][:10]:
        print(f"   [{v['event_type']:6}] {r['name'][:44]}")

    if args.dry_run:
        print("\n[dry-run] 변경 없음")
        return 0
    with neo4j_session() as session:
        for r, v in results:
            if v.get("failed"):
                continue
            session.run(_APPLY, eid=r["eid"], etype=v["event_type"],
                        risk=bool(v["is_risk"]))
    print(f"\n✅ {ok}건 분류 적용 (event_type + is_risk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
