"""재무 → PostgreSQL `financials` + `company_attributes` 매출 스냅샷.

대상이 시드 64곳에서 **corp_code 가 있는 전 기업**으로 넓어졌다(2026-08-15).

  그래프에 기업이 3,453곳인데 재무는 64곳(1.9%)뿐이었다. 「못 받는 것」이
  아니라 **아직 안 받은 것**이다 — DART 재무 API 는 `corp_code` 만 있으면 되고,
  지금 1,169곳이 그 번호를 갖고 있다.

    시드 64곳          ETF 목록에서 고른 수집 대상
    corp_code 보유     1,169곳 ← 여기까지 받을 수 있다
    나머지 2,284곳     해외·미등록이라 DART 에 없다

★이미 받은 곳은 건너뛴다(증분). DART 는 일 20,000건 한도라 아껴야 한다.
  `--refresh` 로 전건을 다시 받는다(연간 실적이 갱신됐을 때).

★매출 스냅샷은 **PostgreSQL 로** 간다. 그래프 노드는 탐색·표시에 쓰는 것만
  들고, 재무는 상세 화면에서 한 건씩 보는 값이다.

실행:
    python -m batch.build.financials --dry-run
    python -m batch.build.financials              # 시드 + 아직 안 받은 곳
    python -m batch.build.financials --limit 200  # 오늘은 200곳만
"""

from __future__ import annotations

import argparse
import sys
import time

from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.dart.financials import fetch_financials

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_SLEEP = 0.25       # DART 초당 호출 제한 여유

_UPSERT_SQL = """
INSERT INTO financials (corp_code, bsns_year, reprt_code, fs_div, revenue,
                        operating_profit, net_profit, total_assets,
                        total_liabilities, total_equity)
VALUES (%(corp_code)s, %(bsns_year)s, %(reprt_code)s, %(fs_div)s, %(revenue)s,
        %(operating_profit)s, %(net_profit)s, %(total_assets)s,
        %(total_liabilities)s, %(total_equity)s)
ON CONFLICT (corp_code, bsns_year, reprt_code) DO UPDATE SET
    fs_div=EXCLUDED.fs_div, revenue=EXCLUDED.revenue,
    operating_profit=EXCLUDED.operating_profit, net_profit=EXCLUDED.net_profit,
    total_assets=EXCLUDED.total_assets, total_liabilities=EXCLUDED.total_liabilities,
    total_equity=EXCLUDED.total_equity, updated_at=now()
"""

# 매출 스냅샷 — 노드가 아니라 상세 표로
_SNAPSHOT_SQL = """
INSERT INTO company_attributes (node_key, corp_code, name, revenue_snapshot, revenue_year)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (node_key) DO UPDATE SET
    revenue_snapshot = EXCLUDED.revenue_snapshot,
    revenue_year = EXCLUDED.revenue_year, updated_at = now()
"""

# 시드를 먼저, 그다음 연결이 많은 순서 — 중간에 끊겨도 중요한 곳부터 채워진다
_TARGETS = """
MATCH (c:Company) WHERE c.corp_code IS NOT NULL
OPTIONAL MATCH (c)-[r]-()
RETURN c.corp_code AS corp_code, c.name AS name, c.norm_name AS norm,
       coalesce(c.is_stub, true) AS stub, count(r) AS deg
ORDER BY stub ASC, deg DESC
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="대상만 보고 끝")
    ap.add_argument("--limit", type=int, metavar="N", help="N곳까지만 (한도 아끼기)")
    ap.add_argument("--refresh", action="store_true",
                    help="이미 받은 곳도 다시 (연간 실적 갱신 시)")
    args = ap.parse_args()

    with neo4j_session() as s:
        targets = [dict(r) for r in s.run(_TARGETS)]
    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("SELECT DISTINCT corp_code FROM financials")
        done = {r[0].strip() for r in cur.fetchall()}

    total = len(targets)
    if not args.refresh:
        targets = [t for t in targets if t["corp_code"] not in done]
    if args.limit:
        targets = targets[:args.limit]

    print(f"■ 재무 수집 — corp_code 보유 {total:,}곳 · 이미 받음 {len(done)}곳 "
          f"· 이번 대상 {len(targets):,}곳")
    print(f"   예상 {len(targets) * _SLEEP / 60:.0f}분 · DART 호출 약 {len(targets):,}건 "
          f"(일 한도 20,000)")
    if args.dry_run or not targets:
        for t in targets[:10]:
            print(f"     {t['name'][:20]:<22}{t['corp_code']}  연결 {t['deg']}")
        if args.dry_run:
            print("\n[dry-run] 받지 않았습니다.")
        return 0

    loaded = no_data = failed = 0
    with postgres_connection() as pg:
        for i, t in enumerate(targets, 1):
            try:
                rows = fetch_financials(t["corp_code"])
            except Exception as exc:
                failed += 1
                if failed <= 3:
                    print(f"  ! {t['name'][:16]} 실패: {exc!r}"[:100])
                continue
            time.sleep(_SLEEP)

            if not rows:
                no_data += 1
                continue

            latest = max(rows, key=lambda r: r["bsns_year"])
            with pg.cursor() as cur:
                for row in rows:
                    cur.execute(_UPSERT_SQL, row)
                cur.execute(_SNAPSHOT_SQL, (t["corp_code"], t["corp_code"], t["name"],
                                            latest["revenue"], latest["bsns_year"]))
            loaded += 1
            if i % 50 == 0 or loaded <= 5:
                rev = latest["revenue"]
                rev_str = f"{rev / 1e12:.1f}조" if rev else "-"
                print(f"  [{i}/{len(targets)}] {t['name'][:18]:<20}"
                      f"{len(rows)}년치 · 최신 {latest['bsns_year']} 매출 {rev_str}")

    print(f"\n적재 {loaded:,}개사 · 재무없음 {no_data:,} · 실패 {failed}")
    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("SELECT count(DISTINCT corp_code), count(*) FROM financials")
        n, rows = cur.fetchone()
        print(f"financials 누적 {n:,}개사 · {rows:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
