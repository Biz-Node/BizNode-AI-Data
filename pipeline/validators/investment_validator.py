# 타법인 출자현황 (30번) 검증을 수행하는 Validator 모듈.

from __future__ import annotations
from schemas.dart_schemas import EntityDTO, NormalizedDocument, RelationshipDTO
from pipeline.validators.base import ValidationReport, is_in_range, is_iso_date, is_numeric_or_none

def validate_investments(document: NormalizedDocument) -> tuple[NormalizedDocument, ValidationReport]:
    report = ValidationReport()

    kept_entities: list[EntityDTO] = []
    for entity in document.entities:
        if entity.type != "Company":
            kept_entities.append(entity)
            continue

        props = entity.properties
        reasons: list[str] = []
        if not is_numeric_or_none(props.get("total_assets")):
            reasons.append(f"total_assets={props.get('total_assets')!r} (숫자 파싱 실패)")
        if not is_numeric_or_none(props.get("net_profit")):
            reasons.append(f"net_profit={props.get('net_profit')!r} (숫자 파싱 실패)")

        if reasons:
            report.dropped.append(f"Company:{entity.key}: " + "; ".join(reasons))
            continue

        kept_entities.append(entity)

    kept_relationships: list[RelationshipDTO] = []
    for rel in document.relationships:
        props = rel.properties
        reasons = []

        if not is_in_range(props.get("share_ratio"), 0, 100):
            reasons.append(f"share_ratio={props.get('share_ratio')!r} (0~100 벗어남)")
        if not is_in_range(props.get("previous_share_ratio"), 0, 100):
            reasons.append(f"previous_share_ratio={props.get('previous_share_ratio')!r} (0~100 벗어남)")
        if not is_iso_date(props.get("settlement_date")):
            reasons.append(f"settlement_date={props.get('settlement_date')!r} (ISO 8601 아님)")

        if reasons:
            report.dropped.append(f"{rel.from_key} -> {rel.to_key}: " + "; ".join(reasons))
            continue

        kept_relationships.append(rel)

    return NormalizedDocument(entities=kept_entities, relationships=kept_relationships), report
