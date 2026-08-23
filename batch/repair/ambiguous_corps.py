"""근거 없이 붙은 `corp_code`를 찾아 **모호하다고 표시**한다. 비용 0.

왜 필요한가 (2026-08-13)

법인 명부 118,535건 중 이름이 겹치는 법인이 **13,452곳(11.3%)**이다.
「신우」 11곳 · 「에스엠」 11곳 · 「세원」 10곳 …

그런데 `resolver._exact_index()`가 정규화명당 후보를 하나만 들고 있어서,
어느 회사인지 모르는 상태에서도 **말없이 하나를 골라** corp_code를 붙였다.
실측으로 우리 그래프의 동명 96곳 중:

    상장사가 후보에 하나뿐 → 그걸 고름     57곳  ✓ 근거가 있다
    후보에 상장사가 없음 → 그냥 하나 고름   39곳  ✗ 근거가 없다
        「태성산업」 후보 7곳 중 하나 · 「스페이스」 3곳 중 하나

`resolver`는 고쳤지만(이제 못 좁히면 None), **이미 붙은 값은 그대로다.**
이 스크립트가 그것들을 찾아 표시한다.

★지우지 않는다. corp_code를 떼면 그 노드가 붙들고 있던 엣지의 근거가 흐려지고,
  실제로 맞았을 수도 있다. **틀렸다고 단정하지 말고 모른다고 적는다** —
  이 프로젝트의 「삭제보다 표시」 원칙 그대로다.

    python -m batch.repair.ambiguous_corps --dry-run
    python -m batch.repair.ambiguous_corps
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session
from pipeline.normalizer.resolver import candidates, close, resolve

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_FIND = """
MATCH (c:Company) WHERE c.corp_code IS NOT NULL AND c.norm_name IS NOT NULL
RETURN c.norm_name AS norm, c.name AS name, c.corp_code AS cc,
       coalesce(c.resolution_status,'') AS status,
       size([(c)-[]-() | 1]) AS deg
ORDER BY deg DESC
"""

_MARK = """
UNWIND $rows AS row
MATCH (c:Company {norm_name: row.norm})
SET c.resolution_status = 'ambiguous',
    c.candidate_corp_codes = row.cands,
    c.candidate_count = row.n,
    c.resolution_note = row.note
RETURN count(*) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as s:
        nodes = [dict(r) for r in s.run(_FIND)]

    todo = []
    for n in nodes:
        cands = candidates(n["name"])
        if len(cands) < 2:
            continue                      # 동명이 아니다
        if resolve(n["name"]) is not None:
            continue                      # 근거로 좁혀진다(상장사 하나)
        todo.append({
            "norm": n["norm"], "name": n["name"], "deg": n["deg"],
            "cands": [c.corp_code for c in cands[:10]],
            "n": len(cands),
            "note": f"동명 {len(cands)}곳 중 근거 없이 {n['cc']}가 붙어 있었음",
        })

    print("=" * 70)
    print(f"  근거 없이 corp_code가 붙은 노드 — {len(todo)}곳 / 전체 {len(nodes):,}곳")
    print("=" * 70)
    for t in sorted(todo, key=lambda x: -x["deg"])[:15]:
        print(f"   {t['name'][:20]:<22}후보 {t['n']:>2}곳   연결 {t['deg']:>3}")
    if len(todo) > 15:
        print(f"   … 외 {len(todo) - 15}곳")

    if args.dry_run or not todo:
        print("\n[dry-run] 표시하지 않았습니다." if args.dry_run else "\n· 대상이 없습니다.")
        close()
        return 0

    with neo4j_session() as s:
        n = s.run(_MARK, rows=todo).single()["n"]
    close()
    print(f"\n✅ {n}곳에 `resolution_status='ambiguous'` + 후보 목록 기록")
    print("   corp_code는 그대로 둡니다 — 지우지 않고 **모른다고 표시만** 합니다.")
    print("   조회 계층은 이 표시를 보고 화면에서 「확인 필요」로 그리면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
