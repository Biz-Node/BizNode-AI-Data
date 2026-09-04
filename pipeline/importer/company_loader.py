"""시드 기업 → PostgreSQL company_attributes + Neo4j Company 노드 (경로 A, 1B-8).

기업개황 + 시드 JSON(sector·etf_list·market) 병합.
Neo4j 노드는 SET으로 항상 full 속성(is_stub=false) — graph_loader가 만든
stub이 있어도 시드로 승격된다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.dart.company_info import CORP_CLS_MARKET, fetch_company_info, to_iso_date
from pipeline.normalizer.base import normalize_company_name

# ★대상이 `companies` 였다. 그 표는 2026-08-15 에 지웠는데(`batch/repair/pg_tidy.py`
#   2번 — 64행이 전부 `company_attributes` 에 있고 값도 같았다) **이 SQL 만 따라가지
#   않아 경로 A 2단계가 그날 이후 죽어 있었다.** 데이터 이관은 그때 끝났고,
#   여기는 코드가 옮겨간 표를 가리키게 하는 것뿐이다.
#
# ★`stock_code`·`market` 은 싣지 않는다. `company_attributes` 에 그 칸이 없고,
#   값은 아래 Cypher 가 Neo4j 노드에 쓴다 — `pg_tidy` 가 표를 지워도 되는 근거로
#   확인한 것이 바로 그 자리다.
#
# ★**여기 적은 칸만 덮어쓴다.** `sector_label`·`revenue_snapshot` 등은 다른 배치가
#   채우므로 `DO UPDATE SET` 에 넣지 않는다 — 넣으면 시드를 돌릴 때마다 지워진다.
_UPSERT_COMPANY_SQL = """
INSERT INTO company_attributes (node_key, corp_code, name, norm_name, name_en,
                                sector, etf_list, ceo_nm, induty, est_dt, is_seed)
VALUES (%(node_key)s, %(corp_code)s, %(name)s, %(norm_name)s, %(name_en)s,
        %(sector)s, %(etf_list)s, %(ceo_nm)s, %(induty)s, %(est_dt)s, true)
ON CONFLICT (node_key) DO UPDATE SET
    corp_code=EXCLUDED.corp_code, name=EXCLUDED.name, norm_name=EXCLUDED.norm_name,
    name_en=EXCLUDED.name_en, sector=EXCLUDED.sector, etf_list=EXCLUDED.etf_list,
    ceo_nm=EXCLUDED.ceo_nm, induty=EXCLUDED.induty, est_dt=EXCLUDED.est_dt,
    is_seed=true, updated_at=now()
"""

_MERGE_NODE_CYPHER = """
MERGE (c:Company {corp_code: $corp_code})
SET c.name=$name, c.norm_name=$norm_name, c.name_en=$name_en,
    c.stock_code=$stock_code, c.market=$market, c.sector=$sector, c.etf_list=$etf_list,
    c.ceo_nm=$ceo_nm, c.induty=$induty, c.est_dt=$est_dt,
    c.is_seed=true, c.is_stub=false, c.resolution_status='resolved'
"""


def load_seed_companies(delay: float = 0.25) -> int:
    """시드 리스트 전체를 company_attributes + Neo4j Company 노드로 적재."""
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    count = 0
    with postgres_connection() as pg, neo4j_session() as session:
        for company in seed:
            corp_code = company["corpCode"]
            name = company["companyName"]
            info = fetch_company_info(corp_code) or {}
            time.sleep(delay)

            market = company.get("market") or CORP_CLS_MARKET.get(info.get("corp_cls"))
            norm_name = normalize_company_name(name)
            est_dt = to_iso_date(info.get("est_dt"))

            row: dict[str, Any] = {
                # ★`node_key` 를 따로 넘긴다. 같은 값이지만 `node_key` 는 text,
                #   `corp_code` 는 character 라 한 파라미터를 두 자리에 쓰면
                #   psycopg 가 타입을 못 정한다(AmbiguousParameter).
                "node_key": corp_code,
                "corp_code": corp_code,
                "name": name,
                "norm_name": norm_name,
                "name_en": info.get("corp_name_eng"),
                "sector": json.dumps(company.get("sector"), ensure_ascii=False),
                "etf_list": json.dumps(company.get("etfList"), ensure_ascii=False),
                "ceo_nm": info.get("ceo_nm"),
                "induty": info.get("induty_code"),
                "est_dt": est_dt,
            }
            with pg.cursor() as cur:
                cur.execute(_UPSERT_COMPANY_SQL, row)

            session.run(
                _MERGE_NODE_CYPHER,
                corp_code=corp_code, name=name, norm_name=norm_name,
                name_en=info.get("corp_name_eng"), stock_code=company.get("stockCode"),
                market=market, sector=company.get("sector"), etf_list=company.get("etfList"),
                ceo_nm=info.get("ceo_nm"), induty=info.get("induty_code"), est_dt=est_dt,
            )
            count += 1
            print(f"  [{count}/{len(seed)}] {name} ({market})")

    print(f"시드 기업 {count}개 적재 완료 (companies + Neo4j)")
    return count
