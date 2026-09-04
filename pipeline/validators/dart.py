# DART 정형 API 문서(최대주주·임원·타법인출자)의 도메인 검증.
#
# 세 API가 서로 다른 파일이었지만 하는 일이 같다 — 값의 범위·형식을 보고
# 어긋나는 관계를 떨어뜨리거나 경고를 단다. 공통 헬퍼(`base.py`)도 공유한다.
# 파일을 나눠 두면 「어느 파일에 있더라」를 매번 찾게 되므로 한곳에 모은다.
#
#   validate_shareholders  최대주주 현황 (17번)
#   validate_executives    임원 현황 (20번)
#   validate_investments   타법인 출자현황 (30번)

from __future__ import annotations

from schemas.dart_schemas import EntityDTO, NormalizedDocument, RelationshipDTO
from pipeline.validators.base import (
    ValidationReport,
    add_warning,
    is_in_range,
    is_iso_date,
    is_non_negative,
    is_numeric_or_none,
    is_plausible_birth_year_month,
)


# ── 최대주주 현황 (17번) ──────────────────────────────────

def validate_shareholders(document: NormalizedDocument) -> tuple[NormalizedDocument, ValidationReport]:
    report = ValidationReport()
    kept: list[RelationshipDTO] = []

    for rel in document.relationships:
        props = rel.properties
        reasons: list[str] = []

        if not is_in_range(props.get("ratio"), 0, 100):
            reasons.append(f"ratio={props.get('ratio')!r} (0~100 벗어남)")
        if not is_in_range(props.get("previous_ratio"), 0, 100):
            reasons.append(f"previous_ratio={props.get('previous_ratio')!r} (0~100 벗어남)")
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


# ── 임원 현황 (20번) ──────────────────────────────────────

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


# ── 타법인 출자현황 (30번) ────────────────────────────────

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

        if not is_in_range(props.get("ratio"), 0, 100):
            reasons.append(f"ratio={props.get('ratio')!r} (0~100 벗어남)")
        if not is_in_range(props.get("previous_ratio"), 0, 100):
            reasons.append(f"previous_ratio={props.get('previous_ratio')!r} (0~100 벗어남)")
        if not is_iso_date(props.get("settlement_date")):
            reasons.append(f"settlement_date={props.get('settlement_date')!r} (ISO 8601 아님)")

        if reasons:
            report.dropped.append(f"{rel.from_key} -> {rel.to_key}: " + "; ".join(reasons))
            continue

        kept_relationships.append(rel)

    return NormalizedDocument(entities=kept_entities, relationships=kept_relationships), report
