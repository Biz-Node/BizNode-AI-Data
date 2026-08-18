"""DART `induty`(5자리 세분류)에서 KSIC 중분류를 채운다. 비용 0.

★왜 DART가 우선인가

`sector_label`은 LLM이 지은 자유 문자열이라 같은 업종이 여러 이름으로 갈렸다
(「국내 IT 서비스 기업」 vs 「국내 IT 서비스 제공사」). 그런데 **DART가 이미
표준 업종코드를 주고 있었다** — `stub_profiles`의 1단계가 `induty`를 노드에
넣는데, 아무도 그걸 업종 축으로 쓰지 않았다.

    Company 2,890개 · induty 997개 · sector_label 2,802개

`induty`가 있으면 그게 정답이다. LLM 추론은 **없는 곳만** 채운다.

★중분류(앞 2자리)를 쓰는 이유는 `normalizer/ksic.py` 참고.

    python -m batch.repair.ksic_backfill --dry-run
    python -m batch.repair.ksic_backfill
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.core.database import neo4j_session
from pipeline.normalizer.ksic import division_of, label_of

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_FIND = """
MATCH (c:Company) WHERE c.induty IS NOT NULL
RETURN c.name AS name, toString(c.induty) AS induty,
       coalesce(c.ksic,'') AS cur, coalesce(c.sector_label,'') AS label
"""

_SET = """
UNWIND $rows AS row
MATCH (c:Company {name: row.name})
SET c.ksic = row.ksic, c.ksic_source = 'dart'
RETURN count(*) AS n
"""

# ★정체(`entity_kind`)만으로 업종이 정해지는 것들 — LLM에 다시 물을 이유가 없다.
#
#   실측(2026-08-12): `stub_profiles`가 `99 기타`로 흘린 510곳 중 **127곳이
#   펀드·조합**이었다. 「하나에스앤비 소부장2호신기술조합」·「에이티유컬쳐테크5호
#   사모투자합자회사」 같은 것들이다. 이건 모르는 게 아니라 **정의상 금융업**이다.
#   LLM이 「무슨 산업의 펀드인가」를 고민하다 99로 간 것으로 보인다.
#
#   ★대학·연구소는 넣지 않는다 — 대학은 85(교육), 연구소는 70(연구개발)이라
#     한 코드로 못 묶는다. 애매한 건 99로 두고 표시한다.
_KIND_TO_KSIC = {
    "펀드·조합": "64",     # 신탁업·집합투자업
    "공공기관": "84",      # 공공행정·국방 (국민연금공단·공정거래위원회 등)
}

_SET_BY_KIND = """
MATCH (c:Company)
WHERE c.entity_kind = $kind AND coalesce(c.ksic, '99') = '99'
SET c.ksic = $code, c.ksic_source = 'kind'
RETURN count(*) AS n
"""

# LLM이 라벨은 제대로 썼는데 코드만 99로 흘린 것 — 다시 묻게 비운다.
# (`stub_profiles`가 `ksic IS NULL`을 대상으로 잡는다.)
_CLEAR_LAZY = """
MATCH (c:Company)
WHERE c.ksic = '99' AND c.ksic_source = 'llm' AND c.entity_kind = '기업'
  AND coalesce(c.sector_label, '') <> '' AND c.sector_label <> '불명'
SET c.ksic = NULL
RETURN count(*) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND)]

    todo = []
    tally: Counter = Counter()
    for r in rows:
        code = division_of(r["induty"])
        if not code:
            continue
        tally[code] += 1
        if r["cur"] != code:
            todo.append({"name": r["name"], "ksic": code})

    print("=" * 66)
    print(f"  DART induty → KSIC 중분류 — 대상 {len(rows)}곳 · 채울 것 {len(todo)}곳")
    print("=" * 66)
    for code, n in tally.most_common(12):
        print(f"   {code}  {label_of(code):<32}{n:>4}곳")
    if len(tally) > 12:
        print(f"   … 외 {len(tally) - 12}종")

    print("\n  예시 — 코드가 같은데 label 은 갈려 있던 것")
    seen: dict[str, list[str]] = {}
    for r in rows:
        c = division_of(r["induty"])
        if c and r["label"]:
            seen.setdefault(c, []).append(r["label"])
    for code, labels in sorted(seen.items(), key=lambda x: -len(set(x[1])))[:3]:
        uniq = sorted(set(labels))[:4]
        print(f"   {code} {label_of(code)}  →  " + " · ".join(u[:22] for u in uniq))

    if args.dry_run:
        print("\n[dry-run] 실제로 바뀐 것은 없습니다.")
        return 0

    if todo:
        with neo4j_session() as s:
            n = s.run(_SET, rows=todo).single()["n"]
        print(f"\n✅ {n}곳에 `ksic` 기록 (`ksic_source='dart'`)")
    else:
        print("\n· 이미 전부 채워져 있습니다.")

    # ── 정체만으로 정해지는 것 + LLM이 게을렀던 것 ──────────
    with neo4j_session() as s:
        for kind, code in _KIND_TO_KSIC.items():
            n = s.run(_SET_BY_KIND, kind=kind, code=code).single()["n"]
            if n:
                print(f"   · {kind} {n}곳 → {code} {label_of(code)} "
                      f"(`ksic_source='kind'`)")
        lazy = s.run(_CLEAR_LAZY).single()["n"]
    if lazy:
        print(f"   · 라벨은 있는데 코드만 99였던 기업 {lazy}곳 → 비움")
        print("     → `python -m batch.build.stub_profiles`로 다시 물으세요")
    print("   나머지(해외·비상장)는 `batch.build.stub_profiles`가 "
          "KSIC 목록에서 골라 채웁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
