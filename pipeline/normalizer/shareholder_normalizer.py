# 최대주주 현황 (17번) 정규화 → OWNS_STAKE_IN{subtype:"최대주주"}

from __future__ import annotations

from typing import Any

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    OwnsStakeInRelationshipDTO,
    RelationshipDTO,
    STAKE_SUBTYPE_MAJOR,
    standard_edge_meta,
)
from pipeline.normalizer.base import (
    clean_missing,
    clean_name,
    is_total_row,
    match_change_reason,
    parse_float,
    parse_int,
)
from pipeline.normalizer.entities import build_company, build_person, looks_like_company, master_company_ref


def normalize_shareholders(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """최대주주 현황을 주주(Person/Company) → 대상회사 OWNS_STAKE_IN 관계로 변환한다.
    방향: 주주(소유주체) → 회사(소유대상) [outbound].
    """
    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    to_ref = master_company_ref(corp_code)

    for row in rows:
        name = clean_name(row.get("nm"))
        if name is None or is_total_row(name):
            continue

        # 주주를 Company(펀드 포함) 또는 Person으로 분류
        if looks_like_company(name):
            entity, from_ref = build_company(name)
        else:
            entity, from_ref = build_person(name, None, None, corp_code)
        entities.setdefault(entity.key, entity)

        share_count = parse_int(row.get("trmend_posesn_stock_co"))
        previous_share_count = parse_int(row.get("bsis_posesn_stock_co"))
        ratio = parse_float(row.get("trmend_posesn_stock_qota_rt"))
        previous_ratio = parse_float(row.get("bsis_posesn_stock_qota_rt"))
        remark = clean_missing(row.get("rm"))
        settlement_date = clean_missing(row.get("stlm_dt"))

        share_count_change = (
            share_count - previous_share_count
            if share_count is not None and previous_share_count is not None else None
        )
        ratio_change = (
            round(ratio - previous_ratio, 2)
            if ratio is not None and previous_ratio is not None else None
        )

        rel = OwnsStakeInRelationshipDTO(
            subtype=STAKE_SUBTYPE_MAJOR,
            meta=standard_edge_meta(source_doc=None, valid_from=settlement_date),
            ratio=ratio,
            previous_ratio=previous_ratio,
            ratio_change=ratio_change,
            settlement_date=settlement_date,
            shareholder_relation=clean_missing(row.get("relate")),
            share_count=share_count,
            previous_share_count=previous_share_count,
            share_count_change=share_count_change,
            ownership_changed=bool(share_count_change),
            change_reason=match_change_reason(remark),
            share_type=clean_missing(row.get("stock_knd")),
            remark=remark,
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
