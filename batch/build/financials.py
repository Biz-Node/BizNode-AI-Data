"""[Sprint 1D] 시드 재무 → PostgreSQL financials + Neo4j Company revenue_snapshot.

실행: python -m batch.import_financials
"""

from __future__ import annotations

import json
import sys
import time

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.dart.financials import fetch_financials

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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

_SNAPSHOT_CYPHER = """
MATCH (c:Company {corp_code: $corp_code})
SET c.revenue_snapshot = $revenue, c.fin_year_snapshot = $year
"""


def main() -> int:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    loaded = no_data = 0
    with postgres_connection() as pg, neo4j_session() as session:
        for i, company in enumerate(seed, 1):
            corp_code = company["corpCode"]
            name = company["companyName"]
            rows = fetch_financials(corp_code)
            time.sleep(0.25)

            if not rows:
                no_data += 1
                print(f"  [{i}/{len(seed)}] {name}: 재무 없음")
                continue

            with pg.cursor() as cur:
                for row in rows:
                    cur.execute(_UPSERT_SQL, row)

            # 최신 연도 매출 → 노드 스냅샷
            latest = max(rows, key=lambda r: r["bsns_year"])
            session.run(_SNAPSHOT_CYPHER, corp_code=corp_code,
                        revenue=latest["revenue"], year=latest["bsns_year"])
            loaded += 1
            rev = latest["revenue"]
            rev_str = f"{rev/1e12:.1f}조" if rev else "None"
            print(f"  [{i}/{len(seed)}] {name}: {len(rows)}년치 (최신 {latest['bsns_year']} 매출 {rev_str})")

    print(f"\n재무 적재 완료: {loaded}개사, 재무없음 {no_data}개사")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
