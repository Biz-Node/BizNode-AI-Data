"""공급계약 공시 → SUPPLIES_TO 엣지 + evidence 빌더 (경로 B, 2C).

corp별로: 공급계약 공시 수집 → 원문 다운로드·파싱 → 계약상대 ER →
계약상대별 최신 1건으로 SUPPLIES_TO 엣지 + evidence 스니펫 생성.
방향: 공시 주체(공급자) → 계약상대방(고객).
"""

from __future__ import annotations

import time
from typing import Optional

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    RelationshipDTO,
    standard_edge_meta,
)
from pipeline.extractors.dart.disclosure_list import latest_supply_contracts
from pipeline.extractors.dart.document import download_document_xml, register_document
from pipeline.importer.evidence import EvidenceRecord, make_evidence_id
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.entities import build_company, master_company_ref
from pipeline.parsers.supply_contract import build_evidence_text, parse_supply_contract

SUPPLY_SUBTYPE = "공급계약"


def _to_iso(yyyymmdd: Optional[str]) -> Optional[str]:
    if not yyyymmdd:
        return None
    s = yyyymmdd.strip()
    if "-" in s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def _to_int_date(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    digits = iso.replace("-", "")
    return int(digits) if digits.isdigit() and len(digits) == 8 else None


def build_contract_document(
    conn, corp_code: str, corp_name: str, bgn_de: str, end_de: str
) -> tuple[NormalizedDocument, list[EvidenceRecord], dict[str, int]]:
    """공급계약 공시 → (SUPPLIES_TO 문서, evidence 레코드, 통계)."""
    stats = {"filings": 0, "anonymous": 0, "parsed": 0}
    from_ref = master_company_ref(corp_code)

    contracts = latest_supply_contracts(corp_code, bgn_de, end_de)
    stats["filings"] = len(contracts)

    # 계약상대별 최신 1건만 유지 (동일 고객 반복 계약 dedup)
    latest: dict[str, tuple] = {}  # counterparty_norm -> (info, rcept_no, rcept_dt)
    for f in contracts:
        rcept_no = f["rcept_no"]
        rcept_dt = f.get("rcept_dt", "")
        xml = download_document_xml(rcept_no)
        time.sleep(0.2)
        register_document(conn, rcept_no, corp_code, "공급계약",
                          f.get("report_nm", ""), _to_iso(rcept_dt))
        if not xml:
            continue
        info = parse_supply_contract(xml)
        if info.is_anonymous or not info.counterparty:
            stats["anonymous"] += 1
            continue
        stats["parsed"] += 1
        key = normalize_company_name(info.counterparty)
        if key not in latest or rcept_dt > latest[key][2]:
            latest[key] = (info, rcept_no, rcept_dt)

    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    evidence_records: list[EvidenceRecord] = []

    for _, (info, rcept_no, _dt) in latest.items():
        tgt_entity, tgt_ref = build_company(info.counterparty)
        if tgt_ref == from_ref:  # 자기공급 스킵
            continue
        entities.setdefault(tgt_entity.key, tgt_entity)
        tgt_key = tgt_ref.split(":", 1)[1]

        evidence_id = make_evidence_id(rcept_no, corp_code, tgt_key, "SUPPLIES_TO", SUPPLY_SUBTYPE)
        meta = standard_edge_meta(
            source_doc=rcept_no,
            source_type="dart_filing",   # 개별 공시 — valid_until이 명확한 종료일
            valid_from=info.valid_from,
            valid_until=info.valid_until,
            occurred_at=info.occurred_at,
            observed_at=info.occurred_at or info.valid_from,
            evidence_id=evidence_id,
        )
        props = {
            "subtype": SUPPLY_SUBTYPE,
            "direction": "outbound",
            "revenue_ratio": info.revenue_ratio,
            "contract_amount": info.contract_amount,
            **meta,
        }
        relationships.append(RelationshipDTO("SUPPLIES_TO", from_ref, tgt_ref, props))

        evidence_records.append(EvidenceRecord(
            evidence_id=evidence_id,
            text=build_evidence_text(corp_name, info),
            corp_code=corp_code,
            source_doc=rcept_no,
            metadata={
                "edge_type": "SUPPLIES_TO",
                "subtype": SUPPLY_SUBTYPE,
                "source_corp": corp_code,
                "target_corp": tgt_key,
                "rcept_no": rcept_no,
                "occurred_at": _to_int_date(info.occurred_at or info.valid_from) or 0,
            },
        ))

    return NormalizedDocument(list(entities.values()), relationships), evidence_records, stats
