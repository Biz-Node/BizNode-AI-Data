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
