# 타법인 출자현황 (30번) 정규화 → OWNS_STAKE_IN{subtype:"자회사"/"출자"}

from __future__ import annotations

from typing import Any

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    OwnsStakeInRelationshipDTO,
    RelationshipDTO,
    STAKE_SUBTYPE_INVEST,
    STAKE_SUBTYPE_SUBSIDIARY,
    standard_edge_meta,
)
from pipeline.normalizer.base import (
    clean_missing,
    clean_name,
    convert_dotted_date,
    is_total_row,
    parse_float,
    parse_int,
)
from pipeline.normalizer.entities import build_company, master_company_ref

# 지분율이 이 값을 **넘으면** "자회사", 아니면 "출자"로 subtype 판정.
_SUBSIDIARY_RATIO_THRESHOLD = 50.0


def normalize_investments(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """타법인 출자현황을 회사 → 피투자사 OWNS_STAKE_IN 관계로 변환한다.
    방향: 출자회사(소유주체) → 피투자사(소유대상) [outbound].
    """
    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    from_ref = master_company_ref(corp_code)

    for row in rows:
        name = clean_name(row.get("inv_prm"))
        if name is None or is_total_row(name):
            continue

        entity, to_ref = build_company(name)
        # 피투자사 재무 스냅샷 보강 (표시용)
        total_assets = parse_float(row.get("recent_bsns_year_fnnr_sttus_tot_assets"))
        net_profit = parse_float(row.get("recent_bsns_year_fnnr_sttus_thstrm_ntpf"))
        if total_assets is not None:
            entity.properties["total_assets"] = total_assets
        if net_profit is not None:
            entity.properties["net_profit"] = net_profit
        entities.setdefault(entity.key, entity)

        ratio = parse_float(row.get("trmend_blce_qota_rt"))
        previous_ratio = parse_float(row.get("bsis_blce_qota_rt"))
        ratio_change = (
            round(ratio - previous_ratio, 2)
            if ratio is not None and previous_ratio is not None else None
        )
        subtype = (
            STAKE_SUBTYPE_SUBSIDIARY
            if ratio is not None and ratio > _SUBSIDIARY_RATIO_THRESHOLD
            else STAKE_SUBTYPE_INVEST
        )
        first_acquired = convert_dotted_date(row.get("frst_acqs_de"))

        rel = OwnsStakeInRelationshipDTO(
            subtype=subtype,
            # 근거: 공시 접수번호 / 관측일: 결산기준일(valid_from은 최초취득일이라 별개)
            meta=standard_edge_meta(
                source_doc=clean_missing(row.get("rcept_no")),
                valid_from=first_acquired,
                observed_at=clean_missing(row.get("stlm_dt")),
            ),
            ratio=ratio,
            previous_ratio=previous_ratio,
            ratio_change=ratio_change,
            settlement_date=clean_missing(row.get("stlm_dt")),
            purpose=clean_missing(row.get("invstmnt_purps")),
            first_acquired=first_acquired,
            first_acquired_amount=parse_int(row.get("frst_acqs_amount")),
            book_value=parse_int(row.get("trmend_blce_acntbk_amount")),
            investment_increased=(
                ratio > previous_ratio if ratio is not None and previous_ratio is not None else None
            ),
            investment_decreased=(
                ratio < previous_ratio if ratio is not None and previous_ratio is not None else None
            ),
        )
        relationships.append(
            RelationshipDTO(
                type=OwnsStakeInRelationshipDTO.type,
                from_key=from_ref,
                to_key=to_ref,
                properties=rel.to_properties(),
            )
        )

    return NormalizedDocument(entities=list(entities.values()), relationships=relationships)
