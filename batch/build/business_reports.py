"""사업보고서 II-2 → Product 노드 + DEVELOPS + evidence (경로 C).

find 사업보고서 → 타겟 섹션 추출 → LLM 제품 추출 → 스테이징 → evidence → 적재.
쓰기 순서 §5-2. 실행: python -m batch.build.business_reports
"""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-only", action="store_true",
                        help="II-6 계약만 재추출(II-2 제품 LLM 호출 생략) — 재적재용")
    # ★건너뛰기가 없어서 **매번 64개사 전부에 LLM을 다시 돌리고 있었다**(2026-08-03).
    #   케이티·제이브이엠 두 곳만 필요했는데 15분이 걸렸다. `company_detail`에는
    #   같은 이유로 이미 건너뛰기가 있는데 여기만 빠져 있었다.
    #
    #   판단 기준은 **같은 접수번호가 이미 등록돼 있나**다. 사업보고서가 새로 나오면
    #   접수번호가 달라지므로 그때는 저절로 다시 돈다.
    parser.add_argument("--force", action="store_true",
                        help="이미 처리한 기업도 다시 (보고서가 갱신됐거나 프롬프트를 고친 뒤)")
    parser.add_argument("--only", nargs="+", metavar="기업명",
                        help="이 기업만")
    args = parser.parse_args()

    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    if args.only:
        want = set(args.only)
        seed = [c for c in seed if c["companyName"] in want]
        missing = want - {c["companyName"] for c in seed}
        if missing:
            print(f"⚠ 시드 목록에 없는 기업: {' · '.join(sorted(missing))}\n")

    # 이미 등록된 (기업, 접수번호) — 같으면 건너뛴다
    done: set[tuple[str, str]] = set()
    if not args.force:
        with postgres_connection() as _c:
            done = {(r[0], r[1]) for r in _c.execute(
                "SELECT corp_code, rcept_no FROM documents WHERE doc_type='사업보고서'"
            ).fetchall()}

    all_ev = []
    tot_products = tot_contracts = no_report = skipped = 0

    scope = "계약만" if args.contracts_only else "제품·계약"
    print(f"[1/3] 사업보고서 수집·LLM 추출({scope})·스테이징"
          + (f" · 대상 {len(seed)}개사" if args.only else "")
          + ("" if args.force else " · 이미 처리한 곳은 건너뜁니다"))
    with postgres_connection() as conn:
        for i, c in enumerate(seed, 1):
            corp_code, name = c["corpCode"], c["companyName"]
            rpt = find_business_report(corp_code, BGN_DE, END_DE)
            if not rpt:
                no_report += 1
                continue
            rcept_no = rpt["rcept_no"]
            if (corp_code, rcept_no) in done:
                skipped += 1
                continue
            try:
                sections = get_report_sections(rcept_no)
            except Exception as exc:
                print(f"  [{i}/{len(seed)}] {name}: 섹션 추출 실패 {exc!r}")
                continue

            report_date = _to_iso(rpt.get("rcept_dt"))
            register_document(conn, rcept_no, corp_code, "사업보고서",
                              rpt.get("report_nm", ""), report_date)
            # II-2 제품 → Product + DEVELOPS
            products: list[dict] = []
            n = 0
            if not args.contracts_only:
                products = extract_products(sections.get("products", ""), name)
                doc, evs = build_product_document(corp_code, name, rcept_no, products,
                                                  report_date)
                n, _ = stage_document(conn, f"report:{corp_code}", doc)
                all_ev.extend(evs)
                tot_products += n

            # II-6 계약 → SUPPLIES_TO / PARTNERS_WITH / DEPENDS_ON
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
              f"계약관계 {tot_contracts}건, 보고서 없음 {no_report}개사"
              + (f", **건너뜀 {skipped}개사**" if skipped else ""))

        print(f"\n[2/3] evidence 임베딩 → ChromaDB + vector_chunks ({len(all_ev)}건)")
        upsert_evidence(conn, all_ev)

    print("\n[3/3] staged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    resolver.close()
    print("\n✅ Sprint 3 Product·DEVELOPS 구축 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
