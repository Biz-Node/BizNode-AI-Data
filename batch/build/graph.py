"""정형 골격 그래프 구축 오케스트레이터 (경로 A).

순서: Neo4j 스키마 → 시드 Company 노드 → staged_edges 스테이징 → Neo4j 적재.
raw_dart(기존 수집분) 재사용. 관계는 staged_edges에서 재생성되므로 --reset 후
재실행해도 DART 재호출은 기업개황(company.json)만 발생한다.

실행:
  python -m batch.build.graph            # 증분(loaded_at NULL만 적재)
  python -m batch.build.graph --reset    # 그래프 초기화 후 전체 재구축
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.company_loader import load_seed_companies
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.neo4j_schema import setup_schema
from pipeline.importer.person_er import resolve_persons
from pipeline.importer.staging import build_corp_evidence, stage_corp
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


def run_path_a(reset: bool = False) -> int:
    """경로 A 골격 그래프 구축 (스키마 → Company → 지분·임원 스테이징 → 적재)."""
    if reset:
        _reset_graph()

    print("\n[1/4] Neo4j 스키마 셋업")
    setup_schema()

    print("\n[2/4] 시드 Company 노드 적재 (기업개황 + 시드 병합)")
    load_seed_companies()

    print("\n[3/5] staged_edges 스테이징 + evidence 생성 (최대주주·임원·출자)")
    corp_codes = _seed_corp_codes()
    total_edges = total_invalid = 0
    all_evidence = []
    with postgres_connection() as conn:
        for i, corp in enumerate(corp_codes, 1):
            n, invalid = stage_corp(conn, corp)
            all_evidence.extend(build_corp_evidence(corp))
            total_edges += n
            total_invalid += invalid
            print(f"  [{i}/{len(corp_codes)}] {corp}: {n}건 스테이징 (위반 {invalid})")
        print(f"  → 총 {total_edges}건 스테이징 (매트릭스 위반 {total_invalid})")

        print(f"\n  evidence 임베딩 → ChromaDB + vector_chunks ({len(all_evidence)}건)")
        upsert_evidence(conn, all_evidence)

    print("\n[4/5] staged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    print("\n[5/5] 개체해소(ER) — 확실한 Person 분열만 병합")
    er_stats = resolve_persons()
    print(f"  → 병합 {er_stats['merged']}건, 보류 {er_stats['skipped']}건(P2 ER 대상)")

    resolver.close()
    print("\n✅ 경로 A 골격 그래프 구축 완료")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="그래프 초기화 후 전체 재구축")
    args = parser.parse_args()
    return run_path_a(reset=args.reset)


if __name__ == "__main__":
    raise SystemExit(main())
