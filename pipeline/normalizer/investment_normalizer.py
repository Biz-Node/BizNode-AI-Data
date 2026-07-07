# 타법인 출자현황 (30번) 정규화를 담당하는 모듈.

from __future__ import annotations
from typing import Any
from schemas.dart_schemas import (
    CompanyDTO,
    EntityDTO,
    EntityType,
    InvestmentVehicleDTO,
    InvestsInRelationshipDTO,
    NormalizedDocument,
    RelationshipDTO,
    make_entity_ref,
)
from pipeline.normalizer.base import (
    clean_missing,
    clean_name,
    convert_dotted_date,
    is_total_row,
    parse_float,
    parse_int,
    resolve_investee_corp_code,
)
from pipeline.normalizer.common import is_investment_vehicle

def normalize_investments(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """타법인 출자현황을 Company/InvestmentVehicle 엔티티와 INVESTS_IN 관계로 변환한다."""

    entities: dict[tuple[EntityType, str], EntityDTO] = {}
    relationships: list[RelationshipDTO] = []

    for row in rows:
        name = clean_name(row.get("inv_prm"))
        if name is None or is_total_row(name):
            continue

        total_assets = parse_float(row.get("recent_bsns_year_fnnr_sttus_tot_assets"))
        net_profit = parse_float(row.get("recent_bsns_year_fnnr_sttus_thstrm_ntpf"))
        
        # 피투자 회사를 Company 또는 InvestmentVehicle로 구분한다.
        vehicle_type = is_investment_vehicle(name)
        entity_type: EntityType = "InvestmentVehicle" if vehicle_type else "Company"
        entity_key = (entity_type, name)
        
        if entity_key not in entities:
            if vehicle_type:
                vehicle = InvestmentVehicleDTO(name=name, vehicle_type=vehicle_type)
                properties = vehicle.to_properties()
            else:
                matched_corp_code, matched_stock_code, matched_market = resolve_investee_corp_code(name)
                company = CompanyDTO(
                    name=name,
                    corp_code=matched_corp_code,
                    stock_code=matched_stock_code,
                    market=matched_market,
                    total_assets=total_assets,
                    net_profit=net_profit,
                )
                properties = company.to_properties()

            entities[entity_key] = EntityDTO(type=entity_type, key=name, properties=properties)

        # 기초·기말 지분율을 비교해 증감 정보를 계산한다.
        share_ratio = parse_float(row.get("trmend_blce_qota_rt"))
        previous_share_ratio = parse_float(row.get("bsis_blce_qota_rt"))
        share_ratio_change = (
            round(share_ratio - previous_share_ratio, 2)
            if share_ratio is not None and previous_share_ratio is not None
            else None
        )
        investment_increased = (
            share_ratio > previous_share_ratio
            if share_ratio is not None and previous_share_ratio is not None
            else False
        )
        investment_decreased = (
            share_ratio < previous_share_ratio
            if share_ratio is not None and previous_share_ratio is not None
            else False
        )

        # INVESTS_IN 관계를 생성한다.
        relationship_dto = InvestsInRelationshipDTO(
            purpose=clean_missing(row.get("invstmnt_purps")),
            first_acquired=convert_dotted_date(row.get("frst_acqs_de")),
            first_acquired_amount=parse_int(row.get("frst_acqs_amount")),
            share_ratio=share_ratio,
            previous_share_ratio=previous_share_ratio,
            share_ratio_change=share_ratio_change,
            investment_increased=investment_increased,
            investment_decreased=investment_decreased,
            book_value=parse_int(row.get("trmend_blce_acntbk_amount")),
            settlement_date=clean_missing(row.get("stlm_dt")),
        )

        # INVESTS_IN 관계를 추가한다.
        relationships.append(
            RelationshipDTO(
                type=InvestsInRelationshipDTO.type,
                from_key=make_entity_ref("Company", corp_code),
                to_key=make_entity_ref(entity_type, name),
                properties=relationship_dto.to_properties(),
            )
        )

    return NormalizedDocument(entities=list(entities.values()), relationships=relationships)
