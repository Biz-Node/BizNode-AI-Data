"""주요사항보고서 → ACQUIRES / SUES / Event+HAS_EVENT + evidence.

쓰기 순서(§5-2): staged_edges → ChromaDB evidence + vector_chunks → Neo4j + loaded_at.
실행: python -m batch.build.major_reports
"""

from __future__ import annotations

import json
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import postgres_connection
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.major_report_loader import (
    build_acquisition_document,
    build_lawsuit_document,
)
from pipeline.importer.staging import stage_document
from pipeline.normalizer import resolver

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BGN_DE = "20230101"
END_DE = "20260726"


def main() -> int:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    all_evidence = []
    tot = {"merger": 0, "acq": 0, "sues": 0, "event": 0}

    print(f"[1/3] 주요사항보고 수집·스테이징 (기간 {BGN_DE}~{END_DE})")
    with postgres_connection() as conn:
        for i, c in enumerate(seed, 1):
            corp_code, name = c["corpCode"], c["companyName"]

            acq_doc, acq_ev, acq_st = build_acquisition_document(conn, corp_code, name, BGN_DE, END_DE)
            law_doc, law_ev, law_st = build_lawsuit_document(conn, corp_code, name, BGN_DE, END_DE)

            n1, _ = stage_document(conn, f"major_acq:{corp_code}", acq_doc)
            n2, _ = stage_document(conn, f"major_law:{corp_code}", law_doc)
            all_evidence.extend(acq_ev)
            all_evidence.extend(law_ev)
            for k in tot:
                tot[k] += acq_st.get(k, 0) + law_st.get(k, 0)

            if n1 + n2:
                print(f"  [{i}/{len(seed)}] {name}: 합병 {acq_st['merger']} "
                      f"취득 {acq_st['acq']} 소송SUES {law_st['sues']} 소송Event {law_st['event']}")

        print(f"  → ACQUIRES {tot['merger']+tot['acq']}(합병 {tot['merger']}·취득 {tot['acq']}) "
              f"· SUES {tot['sues']} · Event {tot['event']}")

        print(f"\n[2/3] evidence 임베딩 → ChromaDB + vector_chunks ({len(all_evidence)}건)")
        upsert_evidence(conn, all_evidence)

    print("\n[3/3] staged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    resolver.close()
    print("\n✅ Sprint 2D 사건 엣지(ACQUIRES/SUES/Event) 구축 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
