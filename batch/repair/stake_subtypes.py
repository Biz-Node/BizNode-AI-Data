"""이미 적재된 `OWNS_STAKE_IN`의 **subtype 오분류**를 고친다. 비용 0.

★왜 필요한가 (2026-08-01)

`audit_dart_fields`가 「최대주주인데 지분 5% 미만」 40건을 잡았다. 근거를 열어
보니 데이터가 아니라 **라벨**이 틀려 있었다:

    현대글로비스 -최대주주-> 현대모비스   지분 0.72%   relate=계열회사
    삼성생명   -최대주주-> 삼성에스디에스  지분 0.06%   relate=계열회사
    기아      -최대주주-> 현대차증권     지분 3.95%   relate=특수관계인

DART 「최대주주 및 특수관계인 현황」 API는 **그룹 전원**을 돌려준다. relate가
「본인」인 행 하나만 진짜 최대주주이고 나머지는 특수관계인인데, 로더가 전원을
`최대주주`로 붙였다. 「최대주주」는 지배구조 화면의 핵심 라벨이라 틀리면
바로 보인다.

같이 잡힌 것 하나 더 — **자회사 판정 경계값**. 상법상 자회사는 지분 50%
**초과**인데 로더가 `>=`로 판정해 정확히 50%인 **합작(JV)** 14건이 자회사가 됐다:

    현대모비스 → Beijing Hyundai Mobis Parts (50.0%)   베이징현대와의 합작
    LG전자   → Arcelik-LG Klima (50.0%)               아르첼릭과의 합작

로더(`shareholder_normalizer` · `investment_normalizer`)는 고쳤다. 이 도구는
**이미 들어와 있는 것**을 같은 규칙으로 맞춘다.

    python -m batch.repair.stake_subtypes --dry-run
    python -m batch.repair.stake_subtypes

★지분을 다 판 관계를 끝난 것으로 표시한다 (2026-08-03 추가)

「지분 기준 관계인데 지분 0.0%」인 엣지가 13건 있었다. 파싱 오류인 줄 알았는데
DART 원문이 실제로 `보유비율 0.00`을 준다.

  ★그런데 **0.00%의 뜻이 출처마다 달랐다.** 처음엔 13건을 다 「전량 처분」으로
    보려다, 6건이 살아 있는 관계임을 발견해 7건으로 좁혔다.

  ① 대량보유(5%룰) 7건 → **전량 처분이 맞다.** 증감이 같이 찍혀 있다

        이준호           -44.17%p   증여/수증으로 특별관계 해소
        세종텔레콤         -6.47%p   시간외 대량매매로 주식 전량 매각
        키움-라피스        -5.42%p   전환사채 풋옵션 행사
        프레스토제6호      -41.52%p

     그런데 그래프는 `is_current=true`로 두어 **「지금도 주주다」라고 말하고 있었다.**

  ② 사업보고서 최대주주현황 6건 → **반올림해서 0일 뿐 주식은 있다**

        삼성에스디에스 신현한    900주 · 0.00%
        원익홀딩스 오창희      2,900주 · 0.00%
        카카오는 0.00%인 특수관계인이 **77명**

     끝난 게 아니다. 표시하면 살아 있는 지배구조가 화면에서 사라진다.

  ③ 국민연금공단 → NAVER는 또 다르다. DART가 「공단 본인 0.00% + 국민연금기금
     특수관계인 9.25%」로 준다. 실제 보유는 기금 쪽이고 공단은 대표로 이름만
     올라간 것 — 애초에 0을 보고한 것이지 판 게 아니다.

  그래서 ①만 처리한다. 「출자」도 뺀다 — 출자 지분 0%는 정상이다(청산·평가손실 등).

  지우지 않는다. `is_current=false`로 표시하면 신선도가 expired로 잡고
  조회 계층이 화면에서 뺀다. 이력은 남는다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.core.database import neo4j_session
from pipeline.normalizer.shareholder_normalizer import _stake_subtype

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 최대주주 API에서 온 엣지 — shareholder_relation이 있는 것이 그것이다.
_FIND_MAJOR = """
MATCH (a)-[r:OWNS_STAKE_IN]->(b)
WHERE r.source_type = 'dart' AND r.subtype IN ['최대주주', '특수관계인']
RETURN elementId(r) AS eid, a.name AS a, b.name AS b,
       r.subtype AS cur, coalesce(r.shareholder_relation, '') AS relate,
       toFloat(r.ratio) AS ratio
"""

# 타법인 출자현황에서 온 엣지 — 지분율로만 갈린다.
_FIND_INVEST = """
MATCH (a)-[r:OWNS_STAKE_IN]->(b)
WHERE r.source_type = 'dart' AND r.subtype IN ['자회사', '출자']
      AND r.ratio IS NOT NULL
RETURN elementId(r) AS eid, a.name AS a, b.name AS b,
       r.subtype AS cur, toFloat(r.ratio) AS ratio
"""

# 이 지분을 넘으면 relate가 뭐라 적혀 있든 지배주주로 본다.
_MAJORITY_RATIO = 50.0

_APPLY = ("MATCH ()-[r]->() WHERE elementId(r) = $eid "
          "SET r.subtype = $new, r.subtype_corrected_from = $old")

# ── 전량 처분 — 대량보유(5%룰)에서만 ────────────────────────────
#
# ★지분 0.00%의 뜻이 **출처마다 다르다**(2026-08-03 실측). 처음엔 13건을 모두
#   「전량 처분」으로 보려다 6건이 살아 있는 관계임을 발견해 좁혔다:
#
#   대량보유(5%룰) 7건 → **전량 처분이 맞다.** 증감이 함께 찍혀 있다
#       이준호 -44.17%p(증여로 특별관계 해소) · 세종텔레콤 -6.47%p(전량 매각)
#       키움-라피스 -5.42%p(전환사채 풋옵션) · 프레스토제6호 -41.52%p
#
#   사업보고서 최대주주현황 6건 → **반올림해서 0일 뿐 주식은 있다**
#       삼성에스디에스 신현한   900주 · 0.00%
#       원익홀딩스 오창희     2,900주 · 0.00%
#       카카오는 0.00%인 특수관계인이 **77명**
#       → 끝난 게 아니다. 표시하면 살아 있는 지배구조가 화면에서 사라진다.
#
#   국민연금공단 → NAVER는 또 다르다. DART가 「공단 본인 0.00% + 국민연금기금
#   특수관계인 9.25%」로 주는데, 실제 보유는 기금 쪽이고 공단은 대표로 올라간 것뿐이다.
#
#   그래서 **5%이상주주(대량보유)만** 본다. `shareholder_relation`이 있으면
#   사업보고서에서 온 것이므로 제외한다.
_RATIO_SUBTYPES = ["5%이상주주"]

_FIND_ZERO = """
MATCH (a)-[r:OWNS_STAKE_IN]->(b)
WHERE r.subtype IN $subs AND toFloat(r.ratio) = 0.0
  AND coalesce(r.is_current, true)
  AND r.shareholder_relation IS NULL   // 사업보고서 최대주주현황이 아닌 것
RETURN elementId(r) AS eid, a.name AS a, b.name AS b, r.subtype AS st,
       coalesce(r.as_of, r.observed_at, r.last_seen) AS asof
"""

_END_ZERO = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.is_current = false,
    r.valid_until = coalesce(r.valid_until, $asof),
    r.ended_reason = '지분 전량 처분 — DART 보유비율 0.00'
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    changes: list[tuple[str, str, str, str, str, str]] = []   # eid,a,b,old,new,why
    held: list[tuple[str, str, float, str]] = []              # 보류 (사람 확인)
    with neo4j_session() as session:
        for r in session.run(_FIND_MAJOR):
            new = _stake_subtype(r["relate"])
            if new == r["cur"]:
                continue
            ratio = r["ratio"] or 0.0
            # ★과반을 가진 쪽을 특수관계인으로 내리지 않는다. relate 필드가 어떻든
            #   지분 50% 초과면 **사실상 지배주주**다. 실측: 「에스에프에이 →
            #   SFA반도체 54.95%」가 relate=특수괸계자(오타)로 적혀 있었다.
            #   공시 기재가 흔들리는 자리라 값을 믿고 라벨을 의심한다.
            if new == "특수관계인" and ratio > _MAJORITY_RATIO:
                held.append((r["a"], r["b"], ratio, r["relate"] or "(없음)"))
                continue
            changes.append((r["eid"], r["a"], r["b"], r["cur"], new,
                            f"relate={r['relate'] or '(없음)'} · 지분 {ratio:.2f}%"))
        for r in session.run(_FIND_INVEST):
            # 상법상 자회사 = 50% **초과**
            new = "자회사" if r["ratio"] > 50.0 else "출자"
            if new != r["cur"]:
                changes.append((r["eid"], r["a"], r["b"], r["cur"], new,
                                f"지분 {r['ratio']:.2f}% — 50% 초과여야 자회사"))

    # ── 전량 처분한 지분 관계를 끝난 것으로 ──────────────────
    with neo4j_session() as session:
        zeros = [dict(r) for r in session.run(_FIND_ZERO, subs=_RATIO_SUBTYPES)]
        if zeros:
            print(f"■ 지분 전량 처분 {len(zeros)}건 — 「지금도 주주」로 남아 있던 것")
            for z in zeros[:8]:
                print(f"   {str(z['a'])[:18]:20}→ {str(z['b'])[:16]:18}"
                      f"{z['st']:10}지분 0.0% · {str(z['asof'] or '')[:10]}")
            if len(zeros) > 8:
                print(f"   … 외 {len(zeros) - 8}건")
            if not args.dry_run:
                for z in zeros:
                    session.run(_END_ZERO, eid=z["eid"], asof=z["asof"])
                print(f"   ✅ is_current=false · ended_reason 기록 "
                      f"(삭제 아님 — 신선도가 expired로 잡습니다)")
            print()

    if held:
        print(f"■ 보류 {len(held)}건 — 지분이 과반이라 라벨을 내리지 않습니다 (사람 확인)")
        for a, b, ratio, rel in held:
            print(f"   {str(a)[:16]:18}→ {str(b)[:22]:24} 지분 {ratio:.2f}%  relate={rel}")
        print()

    if not changes:
        print("고칠 subtype이 없습니다.")
        return 0

    tally = Counter(f"{o} → {n}" for _, _, _, o, n, _ in changes)
    print(f"subtype 교정 대상 {len(changes)}건\n")
    for k, v in tally.most_common():
        print(f"   {k:24}{v:>5}건")
    print()
    for _, a, b, old, new, why in changes[:24]:
        print(f"   {str(a)[:16]:18}→ {str(b)[:22]:24} {old} → {new}   ({why})")
    if len(changes) > 24:
        print(f"   … 외 {len(changes) - 24}건")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0
    with neo4j_session() as session:
        for eid, _, _, old, new, _ in changes:
            session.run(_APPLY, eid=eid, new=new, old=old)
    print(f"\n✅ {len(changes)}건 교정 "
          f"(원래 값은 `subtype_corrected_from`에 남깁니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
