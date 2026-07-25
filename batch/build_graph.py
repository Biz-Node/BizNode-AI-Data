"""[Sprint 1] 정형 골격 그래프 구축 오케스트레이터 (경로 A).

순서: Neo4j 스키마 → 시드 Company 노드 → staged_edges 스테이징 → Neo4j 적재.
raw_dart(기존 수집분) 재사용. 관계는 staged_edges에서 재생성되므로 --reset 후
재실행해도 DART 재호출은 기업개황(company.json)만 발생한다.

실행:
  python -m batch.build_graph            # 증분(loaded_at NULL만 적재)
  python -m batch.build_graph --reset    # 그래프 초기화 후 전체 재구축
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.company_loader import load_seed_companies
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.neo4j_schema import setup_schema
from pipeline.importer.staging import stage_corp
from pipeline.normalizer import resolver

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _seed_corp_codes() -> list[str]:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        return [c["corpCode"] for c in json.load(f)["companies"]]


def _reset_graph() -> None:
    print("그래프 초기화 (DETACH DELETE) ...")
    with neo4j_session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("UPDATE staged_edges SET loaded_at = NULL")
    print("✓ 초기화 완료")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="그래프 초기화 후 전체 재구축")
    args = parser.parse_args()

    if args.reset:
        _reset_graph()

    print("\n[1/4] Neo4j 스키마 셋업")
    setup_schema()

    print("\n[2/4] 시드 Company 노드 적재 (기업개황 + 시드 병합)")
    load_seed_companies()

    print("\n[3/4] staged_edges 스테이징 (최대주주·임원·출자)")
    corp_codes = _seed_corp_codes()
    total_edges = total_invalid = 0
    with postgres_connection() as conn:
        for i, corp in enumerate(corp_codes, 1):
            n, invalid = stage_corp(conn, corp)
            total_edges += n
            total_invalid += invalid
            print(f"  [{i}/{len(corp_codes)}] {corp}: {n}건 스테이징 (위반 {invalid})")
    print(f"  → 총 {total_edges}건 스테이징 (매트릭스 위반 {total_invalid})")

    print("\n[4/4] staged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    resolver.close()
    print("\n✅ Sprint 1 골격 그래프 구축 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
