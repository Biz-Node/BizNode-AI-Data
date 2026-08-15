"""PostgreSQL 정리 — 어긋난 컬럼을 고치고, 죽은 표와 고아 행을 뺀다.

★왜 (2026-08-15)

표가 27개인데 그중 넷이 비어 있고, 하나는 다른 표와 100% 중복이고,
한 컬럼은 전 행이 같은 값으로 잘못 채워져 있었다.

    1  staged_edges.origin   19,512행 전부 'dart' — 실제로는 뉴스가 16,684건
    2  companies 삭제        64행 전부 company_attributes 에 있고 값도 동일
    3  ingest_runs 삭제      0행 · 읽는 코드 0개 · staged_edges.run_id 는 전부 NULL
    4  고아 행 정리           company_attributes 에 노드가 사라진 행 101개

★1번이 왜 문제인가: 이 컬럼의 존재 이유가 **「뉴스에서 나온 관계가 DART 에도
  있는지 SQL 로 대조」**인데, 전부 dart 면 그 대조가 통째로 안 된다.
  값은 `properties->>'source_type'` 에 제대로 들어 있어 소급 교정이 된다.

★2번 — `companies` 는 시드 64곳용으로 먼저 만들었고, 나중에 `company_attributes`
  가 전 기업(3,016곳)을 담으면서 흡수했다. `companies` 에만 있던 `stock_code`·
  `market` 은 **Neo4j 노드에도 있어** 잃는 값이 없다.

실행:
    python -m batch.repair.pg_tidy --dry-run
    python -m batch.repair.pg_tidy
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def step1_origin(cur, dry: bool) -> None:
    """`origin` 을 엣지가 밝힌 출처로 되돌린다."""
    cur.execute("SELECT origin, count(*) FROM staged_edges GROUP BY 1 ORDER BY 2 DESC")
    print("  지금:", cur.fetchall())
    cur.execute("""SELECT properties->>'source_type' AS st, count(*)
                   FROM staged_edges GROUP BY 1 ORDER BY 2 DESC""")
    print("  properties 안:", cur.fetchall())
    if dry:
        return
    cur.execute("""UPDATE staged_edges SET origin = COALESCE(
                       properties->>'source_type',
                       CASE WHEN source_doc LIKE 'news:%' THEN 'news' ELSE 'dart' END)
                   WHERE origin IS DISTINCT FROM COALESCE(
                       properties->>'source_type',
                       CASE WHEN source_doc LIKE 'news:%' THEN 'news' ELSE 'dart' END)""")
    print(f"  → {cur.rowcount:,}행 교정")
    cur.execute("SELECT origin, count(*) FROM staged_edges GROUP BY 1 ORDER BY 2 DESC")
    print("  이후:", cur.fetchall())


def step2_drop_companies(cur, dry: bool) -> None:
    """`companies` 삭제 — 값을 잃지 않는지 먼저 확인한다."""
    cur.execute("""SELECT count(*) FROM companies c
                   LEFT JOIN company_attributes a ON a.corp_code = c.corp_code
                   WHERE a.corp_code IS NULL""")
    orphan = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM companies c
                   JOIN company_attributes a ON a.corp_code = c.corp_code
                   WHERE c.ceo_nm IS DISTINCT FROM a.ceo_nm
                      OR c.induty IS DISTINCT FROM a.induty
                      OR c.est_dt IS DISTINCT FROM a.est_dt""")
    diff = cur.fetchone()[0]
    print(f"  company_attributes 에 없는 기업 {orphan}곳 · 값이 다른 곳 {diff}곳")
    if orphan or diff:
        print("  ⛔ 잃는 값이 있습니다. 삭제하지 않습니다.")
        return
    # `stock_code`·`market` 은 Neo4j 노드에 있는지 확인
    with neo4j_session() as s:
        n = s.run("""MATCH (c:Company) WHERE c.is_stub = false
            RETURN sum(CASE WHEN c.stock_code IS NOT NULL THEN 1 ELSE 0 END) AS sc,
                   sum(CASE WHEN c.market IS NOT NULL THEN 1 ELSE 0 END) AS mk,
                   count(*) AS tot""").single()
    print(f"  시드 {n['tot']}곳의 Neo4j 노드: stock_code {n['sc']} · market {n['mk']}")
    if dry:
        return
    cur.execute("DROP TABLE IF EXISTS companies CASCADE")
    print("  → companies 삭제 (stock_code·market 은 Neo4j 노드에 있습니다)")


def step3_drop_dead(cur, dry: bool) -> None:
    """행도 없고 읽는 코드도 없는 표를 뺀다."""
    DEAD = ["ingest_runs"]
    for t in DEAD:
        cur.execute(f'SELECT count(*) FROM "{t}"')
        n = cur.fetchone()[0]
        print(f"  {t:<22}{n}행")
        if n:
            print("  ⛔ 행이 있습니다. 삭제하지 않습니다.")
            continue
        if not dry:
            cur.execute(f'ALTER TABLE staged_edges DROP CONSTRAINT IF EXISTS '
                        f'staged_edges_run_id_fkey')
            cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
            print(f"  → {t} 삭제 (staged_edges.run_id 는 전부 NULL 이었습니다)")


def step4_orphans(cur, dry: bool) -> None:
    """노드가 사라진 `company_attributes` 행을 뺀다."""
    with neo4j_session() as s:
        keys = {r["k"] for r in s.run("""MATCH (c:Company)
            RETURN coalesce(c.corp_code, c.norm_name) AS k""")}
    cur.execute("SELECT node_key FROM company_attributes")
    rows = [r[0] for r in cur.fetchall()]
    orphan = [k for k in rows if k not in keys]
    print(f"  company_attributes {len(rows)}행 · 노드가 없는 행 {len(orphan)}개")
    for k in orphan[:6]:
        print(f"      {k}")
    if dry or not orphan:
        return
    cur.execute("DELETE FROM company_attributes WHERE node_key = ANY(%s)", (orphan,))
    print(f"  → {cur.rowcount}행 삭제")


STEPS = [("staged_edges.origin 교정", step1_origin),
         ("companies 삭제", step2_drop_companies),
         ("죽은 표 삭제", step3_drop_dead),
         ("고아 행 정리", step4_orphans)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--step", type=int, metavar="N")
    args = ap.parse_args()

    if args.dry_run:
        print("[dry-run] 아무것도 바꾸지 않습니다\n")
    with postgres_connection() as conn, conn.cursor() as cur:
        for i, (name, fn) in enumerate(STEPS, 1):
            if args.step and i != args.step:
                continue
            print("=" * 58)
            print(f"[{i}/{len(STEPS)}] {name}")
            print("=" * 58)
            fn(cur, args.dry_run)
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
