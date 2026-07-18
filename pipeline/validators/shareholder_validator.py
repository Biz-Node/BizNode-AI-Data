# 최대주주 현황 (17번) 검증을 수행하는 Validator 모듈.

from __future__ import annotations
from schemas.dart_schemas import NormalizedDocument, RelationshipDTO
from pipeline.validators.base import ValidationReport, is_in_range, is_iso_date, is_non_negative

def validate_shareholders(document: NormalizedDocument) -> tuple[NormalizedDocument, ValidationReport]:
    report = ValidationReport()
    kept: list[RelationshipDTO] = []

    for rel in document.relationships:
        props = rel.properties
        reasons: list[str] = []

        if not is_in_range(props.get("share_ratio"), 0, 100):
            reasons.append(f"share_ratio={props.get('share_ratio')!r} (0~100 벗어남)")
        if not is_in_range(props.get("previous_share_ratio"), 0, 100):
            reasons.append(f"previous_share_ratio={props.get('previous_share_ratio')!r} (0~100 벗어남)")
        if not is_non_negative(props.get("share_count")):
            reasons.append(f"share_count={props.get('share_count')!r} (음수)")
        if not is_non_negative(props.get("previous_share_count")):
            reasons.append(f"previous_share_count={props.get('previous_share_count')!r} (음수)")
        if not is_iso_date(props.get("settlement_date")):
            reasons.append(f"settlement_date={props.get('settlement_date')!r} (ISO 8601 아님)")

        if reasons:
            report.dropped.append(f"{rel.from_key} -> {rel.to_key}: " + "; ".join(reasons))
            continue

        kept.append(rel)

    return NormalizedDocument(entities=document.entities, relationships=kept), report
