"""공급계약 공시 → SUPPLIES_TO + evidence (경로 B).

쓰기 순서(ERD §5-2): staged_edges(권위) → ChromaDB evidence + vector_chunks
→ Neo4j(파생) + loaded_at(커밋 마커 마지막).

실행: python -m batch.build.disclosures
"""

from __future__ import annotations

import json
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import postgres_connection
from pipeline.importer.disclosure_loader import build_contract_document
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.staging import stage_document
from pipeline.normalizer import resolver

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BGN_DE = "20230725"   # 최근 3년
END_DE = "20260725"


def main() -> int:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    all_evidence = []
    total_edges = total_filings = total_anon = 0

    print(f"[1/3] 공시 수집·파싱·스테이징 (기간 {BGN_DE}~{END_DE})")
    with postgres_connection() as conn:
        for i, company in enumerate(seed, 1):
            corp_code, name = company["corpCode"], company["companyName"]
            doc, evidence, stats = build_contract_document(conn, corp_code, name, BGN_DE, END_DE)
            n, _invalid = stage_document(conn, f"contract:{corp_code}", doc)
            all_evidence.extend(evidence)
            total_edges += n
            total_filings += stats["filings"]
            total_anon += stats["anonymous"]
            if stats["filings"]:
                print(f"  [{i}/{len(seed)}] {name}: 공시 {stats['filings']} "
                      f"→ 엣지 {n} (익명 {stats['anonymous']})")

        print(f"  → 공시 {total_filings}건, SUPPLIES_TO {total_edges}건, 공시유보 {total_anon}건")

        print(f"\n[2/3] evidence 임베딩 → ChromaDB + vector_chunks ({len(all_evidence)}건)")
        upsert_evidence(conn, all_evidence)

    print("\n[3/3] staged_edges → Neo4j 적재 (커밋 마커)")
    load_staged_to_neo4j()

    resolver.close()
    print("\n✅ Sprint 2C 공급망 엣지 + 근거 구축 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
