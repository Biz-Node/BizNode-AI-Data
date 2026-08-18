"""L3 subtype 레지스트리 구축·정리.

subtype은 방법서상 **개방형**이다. 그런데 개방형이 자유형이 되면 같은 뜻이 표현만
달리해 무한히 늘어난다. 실측(2026-07-28) PARTNERS_WITH 29종 중 20여 종이 1건짜리
꼬리였다 — 「전략적 협력」·「협업」·「기술 라이선스」 …

이 스크립트는 개방형을 **관리되는 개방형**으로 바꾼다:
  1. 현재 그래프의 subtype으로 레지스트리를 만든다
  2. 레지스트리 안에서 꼬리를 몸통에 붙인다 (빈도 높은 쪽이 몸통)
  3. Neo4j 엣지의 subtype을 그에 맞춰 갱신한다
  4. 이후 새 추출은 `SubtypeRegistry.resolve()`가 기존 표현에 붙이거나 신규 등록

이후 실행하면 새로 들어온 표현만 정리된다(멱등).

실행:
  python -m batch.repair.subtypes --dry-run
  python -m batch.repair.subtypes
"""

from __future__ import annotations

import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.subtype_registry import (
    apply_consolidation,
    consolidate,
    seed_from_graph,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_UPDATE_EDGES = """
MATCH ()-[r]->()
WHERE type(r) = $edge_type AND r.subtype = $drop
SET r.subtype = $keep
RETURN count(r) AS n
"""


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    with postgres_connection() as conn, neo4j_session() as session:
        n_seed = seed_from_graph(conn, session)
        print(f"[1/3] 레지스트리 시드: {n_seed}개 (엣지타입, subtype)")

        mapping = consolidate(conn)
        if not mapping:
            print("\n정리할 subtype이 없습니다 (이미 정돈됨).")
            return 0

        grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for edge_type, drop, keep in mapping:
            grouped[edge_type].append((drop, keep))

        print(f"\n[2/3] 꼬리 표현 {len(mapping)}건을 몸통에 붙입니다")
        for edge_type, pairs in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(f"  {edge_type}")
            for drop, keep in pairs[:8]:
                print(f"     {drop:28} → {keep}")
            if len(pairs) > 8:
                print(f"     … 외 {len(pairs) - 8}건")

        if dry_run:
            print(f"\n[dry-run] {len(mapping)}건 병합 예정 "
                  f"(Neo4j 엣지 subtype도 함께 갱신)")
            return 0

        # ③ Neo4j 엣지 갱신 — 레지스트리와 그래프를 같은 값으로 맞춘다
        total = 0
        for edge_type, drop, keep in mapping:
            rec = session.run(_UPDATE_EDGES, edge_type=edge_type,
                              drop=drop, keep=keep).single()
            total += rec["n"] if rec else 0
        apply_consolidation(conn, mapping)

        print(f"\n[3/3] ✅ 레지스트리 {len(mapping)}건 정리 · Neo4j 엣지 {total}건 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
