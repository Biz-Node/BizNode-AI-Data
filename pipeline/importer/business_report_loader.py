"""사업보고서 II-2 → Product 노드 + DEVELOPS 엣지 + evidence (경로 C, Sprint 3).

LLM 추출 제품을 Product 노드로. 같은 제품명은 공유 노드(삼성·SK가 DRAM 공유
→ 경쟁·공급 추론). DEVELOPS는 상태 엣지, subtype=category.
"""

from __future__ import annotations

import re

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    ProductDTO,
    RelationshipDTO,
    make_entity_ref,
    standard_edge_meta,
)
from pipeline.importer.evidence import EvidenceRecord, make_evidence_id
from pipeline.normalizer.entities import build_company, master_company_ref


def _norm_product(name: str) -> str:
    """제품 MERGE 키 — 공백 제거 + 소문자('NAND Flash'/'nand flash' → 'nandflash')."""
    return re.sub(r"\s+", "", name).lower()


def build_product_document(corp_code: str, corp_name: str, rcept_no: str, products: list[dict],
                           report_date: str | None = None):
    """LLM 추출 제품 목록 → (Product+DEVELOPS 문서, evidence)."""
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    from_ref = master_company_ref(corp_code)

    for p in products:
        name = (p.get("name") or "").strip()
        category = p.get("category") or "제품"
        norm = _norm_product(name)
        if len(norm) < 2:
            continue

        dto = ProductDTO(name=name, norm_name=norm, category=category)
        entities.setdefault(norm, EntityDTO("Product", norm, dto.to_properties()))
        tgt_ref = make_entity_ref("Product", norm)

        ev_id = make_evidence_id(rcept_no, corp_code, norm, "DEVELOPS", category)
        # 사업보고서 = 연 1회 → 관측일은 보고서 접수일
        meta = standard_edge_meta(
            source_doc=rcept_no, evidence_id=ev_id, observed_at=report_date
        )
        rels.append(RelationshipDTO("DEVELOPS", from_ref, tgt_ref,
                                    {"subtype": category, "direction": "outbound", **meta}))

        text = (f"{corp_name}의 주요 제품·기술: {name} ({category}). "
                f"사업보고서 '주요 제품 및 서비스'에 기재되어 있다.")
        evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
            "edge_type": "DEVELOPS", "subtype": category, "source_corp": corp_code,
            "target_corp": norm, "rcept_no": rcept_no, "occurred_at": 0,
        }))

    return NormalizedDocument(list(entities.values()), rels), evs


def build_contract_relation_document(corp_code: str, corp_name: str, rcept_no: str,
                                     relations: list[dict], report_date: str | None = None):
    """II-6 계약 관계 → PARTNERS_WITH(Company) / DEPENDS_ON(Product) + evidence.

    PARTNERS_WITH는 대칭 엣지 — 방법서 §11대로 키가 작은 쪽 → 큰 쪽 단방향 저장
    (A→B, B→A 중복 방지). 조회는 방향 무시 패턴 사용.
    """
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    self_ref = master_company_ref(corp_code)

    for r in relations:
        edge_type = r.get("edge_type")
        counterparty = (r.get("counterparty") or "").strip()
        subtype = r.get("subtype") or ""
        if not counterparty or edge_type not in ("PARTNERS_WITH", "DEPENDS_ON"):
            continue

        if edge_type == "DEPENDS_ON":
            # 대상은 기술/제품 → Product 노드(제품과 공유)
            norm = _norm_product(counterparty)
            if len(norm) < 2:
                continue
            dto = ProductDTO(name=counterparty, norm_name=norm, category="기술")
            entities.setdefault(norm, EntityDTO("Product", norm, dto.to_properties()))
            src_ref, tgt_ref = self_ref, make_entity_ref("Product", norm)
            direction = "outbound"
        else:
            # 상대는 회사 → Company(미매칭 시 stub)
            entity, other_ref = build_company(counterparty)
            if other_ref == self_ref:
                continue
            entities.setdefault(entity.key, entity)
            # 대칭 엣지: 키 사전순으로 단방향 고정
            src_ref, tgt_ref = sorted([self_ref, other_ref])
            direction = "symmetric"

        src_key, tgt_key = src_ref.split(":", 1)[1], tgt_ref.split(":", 1)[1]
        ev_id = make_evidence_id(rcept_no, src_key, tgt_key, edge_type, subtype)
        meta = standard_edge_meta(
            source_doc=rcept_no, evidence_id=ev_id, observed_at=report_date
        )
        rels.append(RelationshipDTO(edge_type, src_ref, tgt_ref,
                                    {"subtype": subtype, "direction": direction, **meta}))

        verb = "협력 관계를 맺고 있다" if edge_type == "PARTNERS_WITH" else "기술에 의존한다"
        text = (f"{corp_name}은(는) {counterparty}와(과) {subtype} 계약으로 {verb}. "
                f"사업보고서 '주요계약 및 연구개발활동'에 기재되어 있다.")
        evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
            "edge_type": edge_type, "subtype": subtype, "source_corp": src_key,
            "target_corp": tgt_key, "rcept_no": rcept_no, "occurred_at": 0,
        }))

    return NormalizedDocument(list(entities.values()), rels), evs
