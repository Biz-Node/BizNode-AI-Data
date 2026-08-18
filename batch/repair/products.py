"""이미 적재된 Product 노드를 대표 표기로 통일하고 병합한다.

`product_names`를 새로 만들기 전에 들어온 노드들이 남아 있다:
    HBM(8) · 고대역폭메모리(HBM)(4) · 고대역폭 메모리(2)
같은 제품이 3개 노드로 갈려 있으면 「이 제품에 누가 의존하나」가 셋으로 조각난다.

    python -m batch.repair.products --dry-run    # 무엇이 합쳐질지만
    python -m batch.repair.products              # 적용

★병합은 되돌릴 수 없으니 dry-run 결과를 먼저 본다. 특히 세대·세그먼트가
  섞이지 않았는지(HBM3 ≠ HBM4, 범용 DRAM ≠ DRAM) 눈으로 확인할 것.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from app.core.database import neo4j_session
from pipeline.normalizer.product_names import (
    canonical_product, is_descriptive, norm_key,
)

_FIND = """
MATCH (p:Product)
RETURN p.norm_name AS key, p.name AS name, size([(p)--() | 1]) AS deg
ORDER BY deg DESC
"""

_RENAME = """
MATCH (p:Product {norm_name:$old})
SET p.name = $name, p.norm_name = $new
RETURN 1 AS ok
"""

_MERGE = """
MATCH (a:Product {norm_name:$keep}), (b:Product {norm_name:$drop})
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.norm_name AS key
"""

_MARK_DESC = """
MATCH (p:Product {norm_name:$key}) SET p.name_suspect = true RETURN 1 AS ok
"""


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]
        print(f"Product {len(rows)}개\n")

        # 대표키별로 묶는다 — 같은 키로 모이는 것들이 병합 대상
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[norm_key(r["name"])].append(r)

        renamed = merged = descriptive = keyfix = 0
        survivors: list[str] = []          # 병합 후 남는 대표 이름
        for key, members in groups.items():
            if not key:
                continue
            # 대표 = 연결 많은 것 (근거가 가장 두터운 노드를 남긴다)
            ordered = sorted(members, key=lambda m: -m["deg"])
            keep = ordered[0]
            display = canonical_product(keep["name"])
            survivors.append(display)

            if len(ordered) > 1:
                names = " · ".join(f"{m['name']}({m['deg']})" for m in ordered)
                print(f"  ⊕ 「{display}」 ← {names}")
                for drop in ordered[1:]:
                    if not dry_run:
                        session.run(_MERGE, keep=keep["key"], drop=drop["key"])
                    merged += 1

            # 대표 노드의 표시명·키를 대표형으로 (병합 여부와 무관하게)
            if keep["name"] != display or keep["key"] != key:
                # 표시명이 실제로 바뀐 것만 찍는다. 키만 바뀌는 경우가 대부분인데
                # (옛 키는 `normalize_company_name`이 만든 것) 그건 눈에 띌 일이 아니다.
                if keep["name"] != display:
                    print(f"  ↻ 「{keep['name']}」 → 「{display}」")
                    renamed += 1
                else:
                    keyfix += 1
                if not dry_run:
                    session.run(_RENAME, old=keep["key"], name=display, new=key)

        # 설명형 이름은 **고치지 않고 표시만** — 추출 품질 문제라 사람이 본다.
        # ★병합으로 사라질 이름은 빼고 본다(대표로 남는 것만 대상).
        kept = set(survivors)
        suspects = [r for r in rows
                    if canonical_product(r["name"]) in kept
                    and is_descriptive(canonical_product(r["name"]))]
        if suspects:
            print(f"\n[설명형 이름 의심] {len(suspects)}건 — 삭제하지 않고 표시만")
            for r in suspects[:10]:
                print(f"    ⚠ {r['name'][:60]}  (연결 {r['deg']})")
            if len(suspects) > 10:
                print(f"    … 외 {len(suspects)-10}건")
            for r in suspects:
                if not dry_run:
                    session.run(_MARK_DESC, key=norm_key(r["name"]))
                descriptive += 1

    print(f"\n{'[dry-run] ' if dry_run else '✅ '}"
          f"병합 {merged}건 · 개명 {renamed}건 · 키만 정정 {keyfix}건 · "
          f"설명형 표시 {descriptive}건")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
