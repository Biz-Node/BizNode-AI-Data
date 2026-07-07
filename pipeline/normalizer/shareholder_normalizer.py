# 최대주주 현황 (17번) 정규화를 담당하는 모듈.

from __future__ import annotations
from typing import Any
from schemas.dart_schemas import (
    CompanyDTO,
    EntityDTO,
    EntityType,
    InvestmentVehicleDTO,
    NormalizedDocument,
    OwnsShareRelationshipDTO,
    PersonDTO,
    RelationshipDTO,
    make_entity_ref,
)
from pipeline.normalizer.base import (
    clean_missing,
    clean_name,
    is_company_name,
    is_total_row,
    match_change_reason,
    parse_float,
    parse_int,
)
from pipeline.normalizer.common import is_investment_vehicle


def normalize_shareholders(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """최대주주 현황을 Person/Company/InvestmentVehicle 엔티티와 OWNS_SHARE 관계로 변환한다."""

    entities: dict[tuple[EntityType, str], EntityDTO] = {}
    relationships: list[RelationshipDTO] = []

    for row in rows:
        name = clean_name(row.get("nm"))
        if name is None or is_total_row(name):
            continue
        
        # 주주를 Person, Company 또는 InvestmentVehicle로 구분한다.
        vehicle_type = is_investment_vehicle(name)
        entity_type: EntityType = (
            "InvestmentVehicle"
            if vehicle_type
            else "Company"
            if is_company_name(name)
            else "Person"
        )

        # 동일 주주는 한 번만 엔티티로 생성한다.
        entity_key = (entity_type, name)
        if entity_key not in entities:
            if vehicle_type:
                properties = InvestmentVehicleDTO(name=name, vehicle_type=vehicle_type).to_properties()
            elif entity_type == "Company":
                properties = CompanyDTO(name=name).to_properties()
            else:
                properties = PersonDTO(name=name).to_properties()
            entities[entity_key] = EntityDTO(type=entity_type, key=name, properties=properties)

        share_count = parse_int(row.get("trmend_posesn_stock_co"))
        previous_share_count = parse_int(row.get("bsis_posesn_stock_co"))
        share_ratio = parse_float(row.get("trmend_posesn_stock_qota_rt"))
        previous_share_ratio = parse_float(row.get("bsis_posesn_stock_qota_rt"))
        remark = clean_missing(row.get("rm"))

        # 기초·기말 보유량과 지분율을 비교해 증감 정보를 계산한다.
        share_count_change = (
            share_count - previous_share_count
            if share_count is not None and previous_share_count is not None
            else None
        )
        share_ratio_change = (
            round(share_ratio - previous_share_ratio, 2)
            if share_ratio is not None and previous_share_ratio is not None
            else None
        )

        # OWNS_SHARE 관계를 생성한다.
        relationship_dto = OwnsShareRelationshipDTO(
            share_type=clean_missing(row.get("stock_knd")),
            shareholder_relation=clean_missing(row.get("relate")),
            share_count=share_count,
            previous_share_count=previous_share_count,
            share_ratio=share_ratio,
            previous_share_ratio=previous_share_ratio,
            remark=remark,
            change_reason=match_change_reason(remark),
            share_count_change=share_count_change,
            share_ratio_change=share_ratio_change,
            ownership_changed=bool(share_count_change),
            settlement_date=clean_missing(row.get("stlm_dt")),
        )

        # OWNS_SHARE 관계를 추가한다.
        relationships.append(
            RelationshipDTO(
                type=OwnsShareRelationshipDTO.type,
                from_key=make_entity_ref(entity_type, name),
                to_key=make_entity_ref("Company", corp_code),
                properties=relationship_dto.to_properties(),
            )
        )

    return NormalizedDocument(entities=list(entities.values()), relationships=relationships)
