"""엣지·노드에 **언제 처음 생겼나**를 기록한다. 비용 0.

왜 필요한가 (2026-08-04)

홈 화면의 「알림」과 AI 인사이트의 「변화」 축이 전부 여기 걸려 있다.
「지난번 본 이후 새로 생긴 관계」를 계산하려면 각 엣지의 생성 시각이 있어야
하는데, 실측해 보니 **하나도 없었다**:

    엣지.first_seen       0/7,130   (0%)
    엣지.last_seen    7,130/7,130   (100%)   ← 「마지막으로 본 시점」이라 못 씀
    노드.first_seen       0/5,273   (0%)

`last_seen`은 신선도용이라 신규 판별에 쓸 수 없다. 매 적재마다 갱신되므로
어제 생긴 엣지와 3개월 전 엣지가 같은 값을 갖는다.

오늘 날짜로 일괄 채우면 안 된다

「이전 것들은 오늘 날짜로 하면 되잖아」가 자연스러운 생각이지만, 그러면
**7,130개가 전부 「오늘 새로 생김」이 된다.** 알림 화면이 첫날부터 7,130건으로
뒤덮이고, 「변화」 축은 영구히 못 쓰게 된다. NULL보다 나쁘다 —
NULL은 「모른다」라서 알림이 알아서 빼지만, 오늘 날짜는 **틀린 사실**이다.

다행히 진짜 생성일이 남아 있다

`staged_edges.created_at`이 PostgreSQL에 그대로 있다:

    2026-07-27  2,849행     2026-07-30  2,162행     2026-08-03  1,697행
    2026-07-28    841행     2026-07-31  3,123행     2026-08-04      3행
    2026-07-29    309행

(출발키, 엣지유형, 도착키)로 그래프와 맞추면 실제 날짜를 되살릴 수 있다.
맞지 않는 것만 「그 이전부터 있었음」으로 표시한다 — 오늘로 위장하지 않는다.

    python -m batch.repair.first_seen --dry-run
    python -m batch.repair.first_seen
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 그래프 엣지의 양끝을 staged_edges와 같은 키로 뽑는다.
# ★키 우선순위가 `graph_loader`와 같아야 한다 — 8자리 숫자면 corp_code, 아니면 norm_name.
_EDGE_KEYS = """
MATCH (a)-[r]->(b)
RETURN elementId(r) AS eid,
       coalesce(a.corp_code, a.person_key, a.norm_name, a.event_id, a.name) AS sk,
       type(r) AS t,
       coalesce(b.corp_code, b.person_key, b.norm_name, b.event_id, b.name) AS tk,
       r.first_seen IS NOT NULL AS done
"""

_SET_EDGE = """
UNWIND $rows AS row
MATCH ()-[r]->() WHERE elementId(r) = row.eid
SET r.first_seen = date(row.d),
    r.first_seen_estimated = row.est
"""

# 노드는 **붙어 있는 엣지 중 가장 이른 것**을 쓴다. 노드는 엣지 없이 안 생긴다.
_NODE_FROM_EDGES = """
MATCH (n) WHERE n.first_seen IS NULL
OPTIONAL MATCH (n)-[r]-() WHERE r.first_seen IS NOT NULL
WITH n, min(r.first_seen) AS d WHERE d IS NOT NULL
SET n.first_seen = d, n.first_seen_estimated = true
RETURN count(*) AS n
"""

# 되살릴 수 없는 것 — 「오늘」이 아니라 **관측 시작 이전**으로 표시한다.
# 이 날짜는 staged_edges의 첫 기록(2026-07-27)보다 하루 앞이다.
_BEFORE = "2026-07-26"

_MARK_REST = """
MATCH ()-[r]->() WHERE r.first_seen IS NULL
SET r.first_seen = date($d), r.first_seen_estimated = true,
    r.first_seen_note = '수집 이력이 없어 관측 시작 이전으로 표시'
RETURN count(*) AS n
"""


def _staged_first() -> dict[tuple[str, str, str], str]:
    """(출발키, 유형, 도착키) → 가장 이른 created_at."""
    with postgres_connection() as conn:
        rows = conn.execute("""
            SELECT src_key, edge_type, tgt_key, min(created_at)::date::text
            FROM staged_edges WHERE loaded_at IS NOT NULL
            GROUP BY 1, 2, 3
        """).fetchall()
    return {(r[0], r[1], r[2]): r[3] for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    staged = _staged_first()
    print(f"staged_edges에서 (출발,유형,도착) 조합 {len(staged):,}개를 읽었습니다\n")

    with neo4j_session() as session:
        edges = [dict(r) for r in session.run(_EDGE_KEYS)]
        todo = [e for e in edges if not e["done"]]
        matched = [(e, staged[(e["sk"], e["t"], e["tk"])])
                   for e in todo if (e["sk"], e["t"], e["tk"]) in staged]
        print(f"■ 엣지 {len(edges):,}개 · 이미 기록됨 {len(edges) - len(todo):,} · "
              f"이번에 채울 것 {len(todo):,}")
        print(f"   staged와 맞은 것 {len(matched):,} "
              f"({len(matched) * 100 // max(len(todo), 1)}%) — **실제 생성일**을 씁니다")
        print(f"   못 맞춘 것 {len(todo) - len(matched):,} — "
              f"「{_BEFORE} 이전」으로 표시합니다(오늘로 위장하지 않습니다)\n")

        tally = Counter(d for _, d in matched)
        for d, n in sorted(tally.items()):
            print(f"   {d}   {n:>6,}개")

        if args.dry_run:
            print("\n[dry-run] 변경하지 않았습니다.")
            return 0

        rows = [{"eid": e["eid"], "d": d, "est": False} for e, d in matched]
        for i in range(0, len(rows), 1000):
            session.run(_SET_EDGE, rows=rows[i:i + 1000])
        rest = session.run(_MARK_REST, d=_BEFORE).single()["n"]
        nodes = session.run(_NODE_FROM_EDGES).single()["n"]

    print(f"\n✅ 엣지 {len(rows):,}개에 실제 생성일 · {rest:,}개에 「이전」 표시")
    print(f"   노드 {nodes:,}개는 **붙어 있는 가장 이른 엣지**의 날짜를 물려받았습니다")
    print(f"   추정치는 `first_seen_estimated=true`로 구분됩니다 — "
          f"알림에서 신규로 셀지 말지 가릴 수 있습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
