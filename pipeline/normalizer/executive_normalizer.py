# 임원 현황 (20번) 정규화를 담당하는 모듈.

from __future__ import annotations

import re
from typing import Any, Optional

from schemas.dart_schemas import (
    EntityDTO,
    ExecutiveRelationshipDTO,
    NormalizedDocument,
    RelationshipDTO,
    standard_edge_meta,
)
from pipeline.normalizer.base import (
    clean_bullet_text,
    clean_missing,
    clean_name,
    clean_position,
    collapse_whitespace,
    convert_korean_date,
    convert_korean_year_month,
    parse_tenure_months,
)
from pipeline.normalizer.entities import build_person, master_company_ref

# 사외이사 subtype
_OUTSIDE_DIRECTOR_SUBTYPE = "사외이사"

# Person 노드 폭발 방지 — 미등기 실무임원(상무·전무 등)은 제외하고
# 이사회 구성원(등기)만 노드화. 단 대표/회장/사장은 미등기라도 유지.
_TOP_POSITION_MARKERS = ("대표", "회장", "사장")


def _is_governance_relevant(rgist_at: str | None, ofcps_raw: str | None) -> bool:
    """등기임원이거나 최고경영진(대표/회장/사장)이면 True."""
    if rgist_at and rgist_at.strip() != "미등기":
        return True
    position = ofcps_raw or ""
    return any(m in position for m in _TOP_POSITION_MARKERS)

# 신규 선임 여부를 판단하는 재직기간 기준(12개월).
_NEW_EXECUTIVE_THRESHOLD_MONTHS = 12

# main_career에서 "(현)/(전)/(겸)" 기준으로 항목을 분리하는 패턴.
_MAIN_CAREER_MARKER_SPLIT_RE = re.compile(r"(?=\((?:현|전|겸)\))")
_SUSPICIOUS_MARKER_TOKEN_RE = re.compile(r"현\)|전\)|겸\)|現|前|兼|\d{2}년")

# "쪼갤 항목이 없는 정상 케이스"로 간주하는 최대 문자열 길이.
_TRIVIAL_SINGLE_ITEM_MAX_LENGTH = 15


def _has_repeated_unmatched_marker(text: str) -> bool:
    """미처리 마커가 2회 이상 반복되는지 확인한다."""
    return len(_SUSPICIOUS_MARKER_TOKEN_RE.findall(text)) >= 2


def _is_trivial_single_item(text: str) -> bool:
    """분리 대상이 아닌 단일 항목인지 확인한다."""
    return len(text) <= _TRIVIAL_SINGLE_ITEM_MAX_LENGTH and "," not in text


def _merge_wrapped_lines(text: str) -> list[str]:
    """들여쓰기된 줄을 이전 항목과 병합한다."""

    items: list[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and items:
            items[-1] = f"{items[-1]} {line.strip()}"
        else:
            stripped = line.strip()
            if stripped:
                items.append(stripped)
    return items


# LLM 후처리 대상으로 분류되는 main_career 태그.
MAIN_CAREER_LLM_ELIGIBLE_TAGS = frozenset({"구분자 없음(실제 정보 손실)", "미매칭 마커"})

def _classify_main_career(cleaned: str) -> tuple[list[str], Optional[str]]:
    """main_career를 분리하고 분류 태그를 반환한다."""

    items: list[str] = []
    for line_item in _merge_wrapped_lines(cleaned):
        for bullet_part in line_item.split("ㆍ"):
            for marker_part in _MAIN_CAREER_MARKER_SPLIT_RE.split(bullet_part):
                part = collapse_whitespace(marker_part)
                if part:
                    items.append(part)

    tag: Optional[str] = None
    if len(items) == 1:
        if _is_trivial_single_item(items[0]):
            tag = "쪼갤 항목 없음(정상)"
        elif _has_repeated_unmatched_marker(items[0]):
            tag = "미매칭 마커"
        else:
            tag = "구분자 없음(실제 정보 손실)"
    return items, tag


def _parse_main_career(raw: Optional[str], corp_code: str, nm: Optional[str]) -> Optional[str]:
    """main_career를 정제하여 "|" 구분 문자열로 변환한다."""
    
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None

    items, tag = _classify_main_career(cleaned)
    if not items:
        return None

    if tag is not None:
        print(f"[main_career_parse] {tag}: corp_code={corp_code!r} nm={nm!r} text={items[0]!r}")

    return " | ".join(items)


def _parse_duty(raw: Optional[str], corp_code: str, nm: Optional[str]) -> Optional[str]:
    """`chrg_job`(duty) 정제 + `[duty_parse]` 로그 출력."""

    cleaned = clean_bullet_text(raw)
    if cleaned is None:
        return None

    stripped_raw = (raw or "").strip()
    if "\n" in stripped_raw and not stripped_raw.startswith("ㆍ"):
        print(f"[duty_parse] 개행 미처리: corp_code={corp_code!r} nm={nm!r} text={cleaned!r}")

    return cleaned


def normalize_executives(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """20번 임원 현황 → Person + IS_EXECUTIVE_OF{subtype}. 사외이사는 subtype으로 구분.
    방향: 임원(Person) → 회사(Company) [outbound].
    """
    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    to_ref = master_company_ref(corp_code)

    for row in rows:
        name = clean_name(row.get("nm"))
        if name is None:
            continue

        # 미등기 실무임원 제외 (Person 폭발 방지) — 등기임원·최고경영진만
        if not _is_governance_relevant(clean_missing(row.get("rgist_exctv_at")), row.get("ofcps")):
            continue

        birth_ym = convert_korean_year_month(row.get("birth_ym"))
        gender = clean_missing(row.get("sexdstn"))
        entity, from_ref = build_person(name, birth_ym, gender, corp_code)
        entities.setdefault(entity.key, entity)

        # 직위 또는 사외이사로 subtype 결정
        position = clean_position(row.get("ofcps"))
        subtype = (
            _OUTSIDE_DIRECTOR_SUBTYPE
            if row.get("rgist_exctv_at") == "사외이사"
            else position
        )

        tenure_months = parse_tenure_months(row.get("hffc_pd"))
        is_new_executive = (
            tenure_months < _NEW_EXECUTIVE_THRESHOLD_MONTHS if tenure_months is not None else None
        )

        relationship_dto = ExecutiveRelationshipDTO(
            # 근거: 공시 접수번호 / 관측일: 결산기준일(사업보고서 연 1회)
            meta=standard_edge_meta(
                source_doc=clean_missing(row.get("rcept_no")),
                valid_from=clean_missing(row.get("stlm_dt")),
                observed_at=clean_missing(row.get("stlm_dt")),
            ),
            subtype=subtype,
            position=position,
            employment_type=clean_missing(row.get("fte_at")),
            duty=_parse_duty(row.get("chrg_job"), corp_code, name),
            main_career=_parse_main_career(row.get("main_career"), corp_code, name),
            shareholder_relation=clean_missing(row.get("mxmm_shrholdr_relate")),
            tenure_end=convert_korean_date(row.get("tenure_end_on")),
            tenure_months=tenure_months,
            is_new_executive=is_new_executive,
            settlement_date=clean_missing(row.get("stlm_dt")),
        )

        relationships.append(
            RelationshipDTO(
                type=ExecutiveRelationshipDTO.type,
                from_key=from_ref,
                to_key=to_ref,
                properties=relationship_dto.to_properties(),
            )
        )

    return NormalizedDocument(entities=list(entities.values()), relationships=relationships)
