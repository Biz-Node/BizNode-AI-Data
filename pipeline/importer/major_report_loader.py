"""주요사항보고서 → ACQUIRES / SUES / Event+HAS_EVENT + evidence (경로 B, 2D).

 - 합병(cmpMgDecsn)·주식취득(otcprStkInvscrInhDecsn) → ACQUIRES (사건)
 - 소송(lwstLg) → 상대가 실명 회사면 SUES, 익명/개인이면 Event{소송}+HAS_EVENT
방향: 공시 주체(filer)가 대상을 인수(ACQUIRES) / 회사가 사건을 가짐(HAS_EVENT).
"""

from __future__ import annotations

import re
from typing import Optional

from schemas.dart_schemas import (
    EntityDTO,
    EventDTO,
    NormalizedDocument,
    RelationshipDTO,
    make_entity_ref,
    standard_edge_meta,
)
from pipeline.extractors.dart.document import register_document
from pipeline.extractors.dart.major_reports import fetch_major_report
from pipeline.importer.evidence import EvidenceRecord, make_evidence_id
from pipeline.text import eul_reul, i_ga
from pipeline.normalizer.base import clean_missing, clean_name, convert_korean_date
from pipeline.normalizer.entities import build_company, master_company_ref
from pipeline.normalizer.resolver import resolve

# 소송 상대 익명화 표기
_ANON_LAWSUIT = ("OO", "○○", "외 ", "개인", "비공개")


def _clean_corp(raw: Optional[str]) -> Optional[str]:
    """'두산에너빌리티 주식회사(DOOSAN...)' → '두산에너빌리티 주식회사'."""
    name = clean_name(raw)
    if name is None or name == "-":
        return None
    name = re.sub(r"\(.*?\)", "", name).strip()
    return name or None


def _to_int_date(iso: Optional[str]) -> int:
    if not iso:
        return 0
    d = iso.replace("-", "")
    return int(d) if d.isdigit() and len(d) == 8 else 0


def _acquire_edge(corp_code, corp_name, target_name, subtype, occurred_at, rcept_no,
                  extra_evidence, ratio=None):
    """ACQUIRES 엣지 1개 + Company 엔티티 + evidence 레코드 생성."""
    tgt_entity, tgt_ref = build_company(target_name)
    from_ref = master_company_ref(corp_code)
    if tgt_ref == from_ref:
        return None
    tgt_key = tgt_ref.split(":", 1)[1]

    ev_id = make_evidence_id(rcept_no, corp_code, tgt_key, "ACQUIRES", subtype)
    meta = standard_edge_meta(
        source_doc=rcept_no, source_type="dart_filing",
        occurred_at=occurred_at, observed_at=occurred_at, evidence_id=ev_id,
    )
    props = {"subtype": subtype, "direction": "outbound", "status": subtype,
             "ratio": ratio, **meta}
    rel = RelationshipDTO("ACQUIRES", from_ref, tgt_ref, props)

    text = f"{i_ga(corp_name)} {target_name}에 대해 {eul_reul(subtype)} 결정하였다."
    if occurred_at:
        text += f" 이사회 결의일은 {occurred_at}이다."
    text += extra_evidence
    ev = EvidenceRecord(ev_id, text, corp_code, rcept_no, {
        "edge_type": "ACQUIRES", "subtype": subtype, "source_corp": corp_code,
        "target_corp": tgt_key, "rcept_no": rcept_no, "occurred_at": _to_int_date(occurred_at),
    })
    return tgt_entity, rel, ev


def build_acquisition_document(conn, corp_code, corp_name, bgn_de, end_de):
    """합병 + 주식취득 → ACQUIRES 문서."""
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    stats = {"merger": 0, "acq": 0}

    # 합병
    for row in fetch_major_report(corp_code, "merger", bgn_de, end_de):
        target = _clean_corp(row.get("mgptncmp_cmpnm")) or _clean_corp(row.get("nmgcmp_cmpnm"))
        if not target:
            continue
        occurred = convert_korean_date(row.get("bddd"))
        register_document(conn, row["rcept_no"], corp_code, "합병",
                          f"회사합병결정: {target}", occurred)
        built = _acquire_edge(corp_code, corp_name, target, "합병", occurred, row["rcept_no"], "")
        if built:
            e, r, ev = built
            entities.setdefault(e.key, e); rels.append(r); evs.append(ev)
            stats["merger"] += 1

    # 주식취득
    for row in fetch_major_report(corp_code, "stock_acquisition", bgn_de, end_de):
        target = _clean_corp(row.get("iscmp_cmpnm"))
        if not target:
            continue
        occurred = convert_korean_date(row.get("bddd"))
        rate = clean_missing(row.get("atinh_eqrt"))
        purpose = clean_missing(row.get("inh_pp"))
        extra = ""
        if rate:
            extra += f" 취득 후 지분율은 {rate}%이다."
        if purpose:
            extra += f" 취득목적: {purpose}."
        register_document(conn, row["rcept_no"], corp_code, "주식취득",
                          f"타법인주식취득: {target}", occurred)
        built = _acquire_edge(corp_code, corp_name, target, "주식취득", occurred,
                              row["rcept_no"], extra)
        if built:
            e, r, ev = built
            entities.setdefault(e.key, e); rels.append(r); evs.append(ev)
            stats["acq"] += 1

    return NormalizedDocument(list(entities.values()), rels), evs, stats


def build_lawsuit_document(conn, corp_code, corp_name, bgn_de, end_de):
    """소송 → 상대 실명 회사면 SUES, 익명이면 Event{소송}+HAS_EVENT."""
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    stats = {"sues": 0, "event": 0}
    from_ref = master_company_ref(corp_code)

    for row in fetch_major_report(corp_code, "lawsuit", bgn_de, end_de):
        rcept_no = row["rcept_no"]
        case_nm = clean_missing(row.get("icnm")) or "소송"
        opponent = clean_missing(row.get("ac_ap"))
        occurred = convert_korean_date(row.get("lgd")) or convert_korean_date(row.get("cfd"))
        court = clean_missing(row.get("cpct"))
        register_document(conn, rcept_no, corp_code, "소송", f"소송제기: {case_nm}", occurred)

        # 상대가 실명 회사로 해소되면 SUES, 아니면 Event
        anon = (not opponent) or any(m in opponent for m in _ANON_LAWSUIT)
        resolved = resolve(opponent) if (opponent and not anon) else None

        text = f"{corp_name} 관련 소송 「{case_nm}」 제기."
        if court:
            text += f" 관할: {court}."
        if occurred:
            text += f" 제기일 {occurred}."

        if resolved is not None:
            # SUES: 상대(원고) → filer
            opp_entity, opp_ref = build_company(opponent)
            entities.setdefault(opp_entity.key, opp_entity)
            ev_id = make_evidence_id(rcept_no, opp_ref.split(":", 1)[1], corp_code, "SUES", case_nm)
            meta = standard_edge_meta(
                source_doc=rcept_no, source_type="dart_filing",
                occurred_at=occurred, observed_at=occurred, evidence_id=ev_id,
            )
            rels.append(RelationshipDTO("SUES", opp_ref, from_ref,
                                        {"subtype": case_nm, "direction": "outbound", **meta}))
            evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
                "edge_type": "SUES", "subtype": case_nm, "source_corp": opp_ref.split(":", 1)[1],
                "target_corp": corp_code, "rcept_no": rcept_no, "occurred_at": _to_int_date(occurred)}))
            stats["sues"] += 1
        else:
            # Event{소송} + HAS_EVENT (filer → Event)
            event_id = f"evt_{rcept_no}"
            ev_id = make_evidence_id(rcept_no, corp_code, event_id, "HAS_EVENT", "소송")
            event = EventDTO(event_id=event_id, event_type="소송", title=case_nm,
                             occurred_at=occurred, sign="negative", source_doc=rcept_no,
                             evidence_id=ev_id)
            entities.setdefault(event_id, EntityDTO("Event", event_id, event.to_properties()))
            meta = standard_edge_meta(
                source_doc=rcept_no, source_type="dart_filing",
                occurred_at=occurred, observed_at=occurred, evidence_id=ev_id,
            )
            rels.append(RelationshipDTO("HAS_EVENT", from_ref, make_entity_ref("Event", event_id),
                                        {"subtype": "소송", "direction": "outbound", **meta}))
            evs.append(EvidenceRecord(ev_id, text, corp_code, rcept_no, {
                "edge_type": "HAS_EVENT", "subtype": "소송", "source_corp": corp_code,
                "target_corp": event_id, "rcept_no": rcept_no, "occurred_at": _to_int_date(occurred)}))
            stats["event"] += 1

    return NormalizedDocument(list(entities.values()), rels), evs, stats
