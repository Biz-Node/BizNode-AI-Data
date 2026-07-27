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
from pipeline.normalizer.person_index import lookup_birth

# 개인 주주 폭발 방지(방법서 §10[2]): 개인은 5% 이상만 노드화.
# <5% 특수관계인(친인척 0.x%)은 N차 리스크 추론에 무용 → 제외. 법인·펀드는 무관.
_PERSON_MIN_RATIO = 5.0

# ★예외: 지분이 낮아도 '지배구조 핵심'인 개인은 유지한다.
# 한국 재벌은 총수가 낮은 지분(이재용 1.65%)으로 순환출자를 통해 지배하므로
# 지분율만으로 거르면 총수를 놓친다. DART relate(관계) 필드가 신분을 알려준다.
#   유지: "최대주주 본인"/"본인"/"최대주주"/"최대주주의 특수관계인"/"최대주주의 자"
#   제외: 일반 "특수관계인"·"미등기임원"·"계열회사 임원" (5% 미만이면)
_CONTROLLING_RELATE_MARKER = "최대주주"
_SELF_RELATE_VALUES = {"본인"}


def _is_controlling_person(relate: str | None) -> bool:
    """relate가 최대주주 본인·총수 일가를 가리키면 True (지분 무관 유지)."""
    if not relate:
        return False
    normalized = relate.replace(" ", "").replace("\n", "")
    return _CONTROLLING_RELATE_MARKER in normalized or normalized in _SELF_RELATE_VALUES


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

        ratio = parse_float(row.get("trmend_posesn_stock_qota_rt"))
        shareholder_relation = clean_missing(row.get("relate"))
        is_company = looks_like_company(name)

        # 개인은 5% 이상 또는 지배구조 핵심(최대주주 본인·총수 일가)만 (§10[2]).
        # 법인·펀드는 지분 무관 유지.
        if not is_company:
            has_stake = ratio is not None and ratio >= _PERSON_MIN_RATIO
            if not has_stake and not _is_controlling_person(shareholder_relation):
                continue

        # 주주를 Company(펀드 포함) 또는 Person으로 분류
        # 개인은 전역 임원 인덱스에서 생년월을 찾아 person_key를 안정화한다
        # (없으면 name@corp 폴백 → 회사 간 분열은 P2 ER이 처리)
        if is_company:
            entity, from_ref = build_company(name)
        else:
            entity, from_ref = build_person(name, lookup_birth(name), None, corp_code)
        entities.setdefault(entity.key, entity)

        share_count = parse_int(row.get("trmend_posesn_stock_co"))
        previous_share_count = parse_int(row.get("bsis_posesn_stock_co"))
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
            # 근거: 공시 접수번호 / 관측일: 결산기준일(사업보고서 연 1회)
            meta=standard_edge_meta(
                source_doc=clean_missing(row.get("rcept_no")),
                valid_from=settlement_date,
                observed_at=settlement_date,
            ),
            ratio=ratio,
            previous_ratio=previous_ratio,
            ratio_change=ratio_change,
            settlement_date=settlement_date,
            shareholder_relation=shareholder_relation,
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
