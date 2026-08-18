"""사업보고서 II-2 → Product 노드 + DEVELOPS 엣지 + evidence (경로 C).

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
from pipeline.text import i_ga


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

        desc = (p.get("description") or "").strip()
        text = f"{corp_name} 개발·생산 — {name} ({category})"
        if desc:
            text += f"\n설명: {desc}"
        text += "\n출처: 사업보고서 「주요 제품 및 서비스」"
        evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
            "edge_type": "DEVELOPS", "subtype": category, "source_corp": corp_code,
            "target_corp": norm, "rcept_no": rcept_no, "occurred_at": 0,
        }))

    return NormalizedDocument(list(entities.values()), rels), evs


_DATE_RE = re.compile(r"(\d{4})[.\-/년]\s*(\d{1,2})?[.\-/월]?\s*(\d{1,2})?")


def _iso_date(raw: str | None) -> str | None:
    """'2022.07.06'·'2022-07-06'·'2022년 7월' → ISO. 파싱 실패 시 None."""
    if not raw:
        return None
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    if day and month:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def _contract_evidence_text(*, corp_name: str, counterparty: str, edge_type: str,
                            subtype: str, direction: str | None, signed: str | None,
                            purpose: str, quote: str) -> str:
    """근거 스니펫 — **관계가 성립한 이유**를 담는다.

    이전에는 "…계약으로 협력 관계를 맺고 있다. 사업보고서에 기재되어 있다"처럼
    엣지 내용을 되풀이하고 **위치만** 알려줬다. 그건 엣지를 보면 이미 아는 정보라
    근거로서 값이 없다. 보고서에는 '목적 및 내용'·'체결시기'가 실려 있으므로
    그것을 그대로 옮긴다 — 무엇을·왜·언제가 드러나야 팩트체크가 가능하다.

    외국 회사명 뒤에는 조사를 붙이지 않는 구조를 쓴다(발음을 알 수 없어
    '와(과)' 같은 표기가 되는 것을 피한다).
    """
    if edge_type == "SUPPLIES_TO":
        supplier, customer = ((corp_name, counterparty) if direction != "they_supply"
                              else (counterparty, corp_name))
        head = f"공급 관계 — 공급자: {supplier} / 수요자: {customer}"
    elif edge_type == "DEPENDS_ON":
        head = f"{i_ga(corp_name)} 의존하는 기술·제품: {counterparty}"
    else:
        head = f"협력 관계 — {corp_name} ↔ {counterparty}"

    parts = [head]
    detail = f"계약유형: {subtype}" if subtype else ""
    if signed:
        detail += f" / 체결: {signed}" if detail else f"체결: {signed}"
    if detail:
        parts.append(detail)
    if purpose:
        parts.append(f"목적·내용: {purpose}")
    if quote and quote not in purpose:
        parts.append(f"원문: {quote}")
    parts.append("출처: 사업보고서 「주요계약 및 연구개발활동」")
    return "\n".join(parts)


def build_contract_relation_document(corp_code: str, corp_name: str, rcept_no: str,
                                     relations: list[dict], report_date: str | None = None):
    """II-6 계약 관계 → SUPPLIES_TO / PARTNERS_WITH(Company) / DEPENDS_ON(Product).

    PARTNERS_WITH는 대칭 엣지 — 방법서 §11대로 키가 작은 쪽 → 큰 쪽 단방향 저장
    (A→B, B→A 중복 방지). 조회는 방향 무시 패턴 사용.
    SUPPLIES_TO는 **방향 엣지** — 추출기가 판단한 공급자 → 수요자로 저장한다.
    """
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    self_ref = master_company_ref(corp_code)

    for r in relations:
        edge_type = r.get("edge_type")
        counterparty = (r.get("counterparty") or "").strip()
        subtype = r.get("subtype") or ""
        if not counterparty or edge_type not in ("SUPPLIES_TO", "PARTNERS_WITH", "DEPENDS_ON"):
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

            if edge_type == "SUPPLIES_TO":
                # 공급자 → 수요자. we_supply면 이 기업이 공급자, they_supply면 반대.
                if r.get("direction") == "they_supply":
                    src_ref, tgt_ref = other_ref, self_ref
                else:
                    src_ref, tgt_ref = self_ref, other_ref
                direction = "outbound"
            else:
                # 대칭 엣지: 키 사전순으로 단방향 고정
                src_ref, tgt_ref = sorted([self_ref, other_ref])
                direction = "symmetric"

        src_key, tgt_key = src_ref.split(":", 1)[1], tgt_ref.split(":", 1)[1]
        ev_id = make_evidence_id(rcept_no, src_key, tgt_key, edge_type, subtype)

        # 체결시기를 살린다 — 지금까지 버려온 실제 시점 정보
        signed = _iso_date(r.get("signed_at"))
        meta = standard_edge_meta(
            source_doc=rcept_no, evidence_id=ev_id, observed_at=report_date,
            valid_from=signed,
        )
        rels.append(RelationshipDTO(edge_type, src_ref, tgt_ref,
                                    {"subtype": subtype, "direction": direction, **meta}))

        text = _contract_evidence_text(
            corp_name=corp_name, counterparty=counterparty, edge_type=edge_type,
            subtype=subtype, direction=r.get("direction"), signed=signed,
            purpose=(r.get("purpose") or "").strip(),
            quote=(r.get("evidence") or "").strip(),
        )
        evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
            "edge_type": edge_type, "subtype": subtype, "source_corp": src_key,
            "target_corp": tgt_key, "rcept_no": rcept_no,
            "occurred_at": int(signed.replace("-", "")) if signed and len(signed) == 10 else 0,
        }))

    return NormalizedDocument(list(entities.values()), rels), evs
