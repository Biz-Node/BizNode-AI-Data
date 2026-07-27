"""[Sprint 3] 사업보고서 II-2 → Product 노드 + DEVELOPS + evidence (경로 C).

find 사업보고서 → 타겟 섹션 추출 → LLM 제품 추출 → 스테이징 → evidence → 적재.
쓰기 순서 §5-2. 실행: python -m batch.build_business_reports
"""

from __future__ import annotations

import json
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import postgres_connection
from pipeline.extractors.dart.business_report import find_business_report, get_report_sections
from pipeline.extractors.dart.document import register_document
from pipeline.importer.business_report_loader import (
    build_contract_relation_document,
    build_product_document,
)
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.staging import stage_document
from pipeline.normalizer import resolver
from pipeline.parsers.contract_extractor import extract_contract_relations
from pipeline.parsers.product_extractor import extract_products

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BGN_DE = "20240101"
END_DE = "20260726"


def _to_iso(yyyymmdd):
    s = (yyyymmdd or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else None


def main() -> int:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    all_ev = []
    tot_products = tot_contracts = no_report = 0

    print(f"[1/3] 사업보고서 수집·LLM 추출(제품·계약)·스테이징")
    with postgres_connection() as conn:
        for i, c in enumerate(seed, 1):
            corp_code, name = c["corpCode"], c["companyName"]
            rpt = find_business_report(corp_code, BGN_DE, END_DE)
            if not rpt:
                no_report += 1
                continue
            rcept_no = rpt["rcept_no"]
            try:
                sections = get_report_sections(rcept_no)
            except Exception as exc:
                print(f"  [{i}/{len(seed)}] {name}: 섹션 추출 실패 {exc!r}")
                continue

            report_date = _to_iso(rpt.get("rcept_dt"))
            register_document(conn, rcept_no, corp_code, "사업보고서",
                              rpt.get("report_nm", ""), report_date)
            # II-2 제품 → Product + DEVELOPS
            products = extract_products(sections.get("products", ""), name)
            doc, evs = build_product_document(corp_code, name, rcept_no, products, report_date)
            n, _ = stage_document(conn, f"report:{corp_code}", doc)
            all_ev.extend(evs)
            tot_products += n

            # II-6 계약 → PARTNERS_WITH / DEPENDS_ON
            relations = extract_contract_relations(sections.get("contracts", ""), name)
            cdoc, cevs = build_contract_relation_document(
                corp_code, name, rcept_no, relations, report_date
            )
            cn, _ = stage_document(conn, f"contract_rel:{corp_code}", cdoc)
            all_ev.extend(cevs)
            tot_contracts += cn

            if n or cn:
                names = ", ".join(p["name"] for p in products[:3])
                partners = ", ".join(r["counterparty"] for r in relations[:3])
                extra = f" | 계약 {cn} ({partners})" if cn else ""
                print(f"  [{i}/{len(seed)}] {name}: 제품 {n} ({names}){extra}")

        print(f"  → Product·DEVELOPS {tot_products}건, "
              f"PARTNERS/DEPENDS_ON {tot_contracts}건, 보고서 없음 {no_report}개사")

        print(f"\n[2/3] evidence 임베딩 → ChromaDB + vector_chunks ({len(all_ev)}건)")
        upsert_evidence(conn, all_ev)

    print("\n[3/3] staged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    resolver.close()
    print("\n✅ Sprint 3 Product·DEVELOPS 구축 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
