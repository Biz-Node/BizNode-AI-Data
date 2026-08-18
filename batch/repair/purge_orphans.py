"""관계가 하나도 없는 노드를 **기록하고** 치운다.

★왜 생기나

고아는 수집 실패가 아니라 **검사가 일한 결과**다. 근거 검증·병렬언급 검사가
엣지를 지우면, 그 엣지 하나만 붙들고 있던 노드가 홀로 남는다.

    실측(2026-08-12): 근거 없는 엣지 485건을 지웠더니 고아 노드 273건이 생겼다
    실측(2026-08-15): 감사를 돌린 뒤 37곳이 새로 생겼다

**엣지를 지운 건 맞으니 이건 고칠 게 아니라 치울 것**이다. 관계가 없는 노드는
그래프에서 할 일이 없다 — 경로에 못 오르고 화면에도 안 뜬다.

★그런데 **그냥 지우면 안 된다.** 왜 사라졌는지가 같이 사라진다. 그래서
  `purged_nodes` 표에 **속성 전체와 함께** 옮겨 두고 그래프에서만 뺀다.
  엣지 삭제가 `purged_edges` 를 쓰는 것과 같은 방식이다.

★사용자가 담아 둔 노드가 여기 걸릴 수 있다. 그때 조회는 404가 아니라
  **「검증 결과 제외됐습니다」 + 사유**로 답해야 한다 — 조용히 사라지는 것만
  막으면 된다.

실행:
    python -m batch.repair.purge_orphans --dry-run
    python -m batch.repair.purge_orphans
    python -m batch.repair.purge_orphans --keep Event    # 특정 라벨은 남긴다
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LABELS = ["Company", "Person", "Organization", "Product", "Event"]

_TABLE = """
CREATE TABLE IF NOT EXISTS purged_nodes (
    id         BIGSERIAL PRIMARY KEY,
    purged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    label      TEXT NOT NULL,
    node_key   TEXT,
    name       TEXT,
    reason     TEXT,
    props      JSONB
)
"""

_FIND = """
MATCH (n:{label}) WHERE NOT (n)--()
RETURN coalesce(n.corp_code, n.norm_name, n.person_key, n.event_id) AS key,
       coalesce(n.name, '') AS name, properties(n) AS props
"""

_REASON = "관계 0 — 검사가 마지막 엣지를 지운 자리"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", nargs="*", default=[], metavar="LABEL",
                    help="이 라벨은 지우지 않는다")
    args = ap.parse_args()

    targets = [l for l in LABELS if l not in args.keep]
    found: dict[str, list[dict]] = {}
    with neo4j_session() as s:
        for lb in targets:
            rows = [dict(r) for r in s.run(_FIND.format(label=lb))]
            if rows:
                found[lb] = rows

    total = sum(len(v) for v in found.values())
    print(f"■ 관계가 없는 노드 {total}곳")
    for lb, rows in found.items():
        names = " · ".join((r["name"] or r["key"] or "?")[:16] for r in rows[:5])
        print(f"   {lb:<14}{len(rows):>4}곳   {names}")
    if args.dry_run or not total:
        if args.dry_run:
            print("\n[dry-run] 치우지 않았습니다.")
        return 0

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_TABLE)
        for lb, rows in found.items():
            for r in rows:
                props = {k: str(v) for k, v in (r["props"] or {}).items()}
                cur.execute("""INSERT INTO purged_nodes
                    (label, node_key, name, reason, props) VALUES (%s,%s,%s,%s,%s)""",
                    (lb, r["key"], r["name"], _REASON,
                     json.dumps(props, ensure_ascii=False)))
    with neo4j_session() as s:
        for lb in found:
            s.run(f"MATCH (n:{lb}) WHERE NOT (n)--() DELETE n")

    print(f"\n✅ {total}곳을 `purged_nodes` 에 기록하고 그래프에서 뺐습니다")
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM purged_nodes")
        print(f"   purged_nodes 누적 {cur.fetchone()[0]:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
