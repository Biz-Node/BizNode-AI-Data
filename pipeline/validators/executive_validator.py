# 임원 현황 (20번) 검증을 수행하는 Validator 모듈.

from __future__ import annotations
from schemas.dart_schemas import NormalizedDocument, RelationshipDTO
from pipeline.validators.base import (
    ValidationReport,
    add_warning,
    is_in_range,
    is_iso_date,
    is_plausible_birth_year_month,
)

def validate_executives(document: NormalizedDocument) -> tuple[NormalizedDocument, ValidationReport]:
    report = ValidationReport()

    # birth_year_month는 경고만 추가한다 — 엔티티는 드롭하지 않고 그대로 유지한다.
    for entity in document.entities:
        if entity.type != "Person":
            continue
        birth_year_month = entity.properties.get("birth_year_month")
        if not is_plausible_birth_year_month(birth_year_month):
            message = f"Person:{entity.key}: birth_year_month={birth_year_month!r} (1900~올해 벗어남)"
            add_warning(entity.properties, message)
            report.warned.append(message)

    kept: list[RelationshipDTO] = []
    for rel in document.relationships:
        props = rel.properties
        reasons: list[str] = []

        if not is_in_range(props.get("tenure_months"), 0, 1200):
            reasons.append(f"tenure_months={props.get('tenure_months')!r} (0~1200 벗어남)")
        if not is_iso_date(props.get("settlement_date")):
            reasons.append(f"settlement_date={props.get('settlement_date')!r} (ISO 8601 아님)")

        if reasons:
            report.dropped.append(f"{rel.from_key} -> {rel.to_key}: " + "; ".join(reasons))
            continue

        kept.append(rel)

    return NormalizedDocument(entities=document.entities, relationships=kept), report
