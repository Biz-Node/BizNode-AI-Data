# 최대주주 현황 (17번) 정규화 → OWNS_STAKE_IN{subtype:"최대주주" 또는 "특수관계인"}
#
# API가 **최대주주 그룹 전원**을 주므로 relate 필드로 갈라야 한다.
# 「본인」인 행만 최대주주, 나머지(계열회사·친족·재단·임원)는 특수관계인이다.
# 같은 주주가 보통주·우선주 행으로 나뉘어 오는 것도 여기서 하나로 고른다.

from __future__ import annotations

from typing import Any

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    OwnsStakeInRelationshipDTO,
    RelationshipDTO,
    STAKE_SUBTYPE_MAJOR,
    STAKE_SUBTYPE_RELATED,
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

# 개인 주주 폭발 방지: 개인은 5% 이상만 노드화.
# <5% 특수관계인(친인척 0.x%)은 N차 리스크 추론에 무용 → 제외. 법인·펀드는 무관.
_PERSON_MIN_RATIO = 5.0

# 지분이 낮아도 '지배구조 핵심'인 개인은 유지한다.
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

_SELF_MARKERS = ("본인",)


def _stake_subtype(relate: str | None) -> str:
    """relate → subtype. 「본인」만 최대주주, 나머지는 특수관계인."""
    normalized = (relate or "").replace(" ", "").replace("\n", "")
    if not normalized:
        # relate가 비면 판정 근거가 없다. 최대주주 API의 행이므로 특수관계인보다는
        # 최대주주 쪽이 맞을 확률이 높지만, 단정하지 않고 기존 값을 유지한다.
        return STAKE_SUBTYPE_MAJOR
    if any(m in normalized for m in _SELF_MARKERS) or normalized == "최대주주":
        return STAKE_SUBTYPE_MAJOR
    return STAKE_SUBTYPE_RELATED


# ── 주식 종류별 분리 행 ──────────────────────────────────────
# ★DART는 **같은 주주를 주식 종류마다 따로** 준다(보통주 행 + 우선주 행).
#   적재는 (주주, 회사, subtype)로 MERGE하므로 뒤 행이 앞 행을 **덮어쓴다**.
#   보통주 행을 남기고 우선주는 별도 속성으로 붙인다.
_COMMON_STOCK = ("보통", "의결권")


def _is_common_stock(share_type: str | None) -> bool:
    return any(m in (share_type or "") for m in _COMMON_STOCK)


def _pick_primary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """주주별로 **보통주 행 하나**만 남긴다. 없으면 지분이 가장 큰 행."""
    by_holder: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_holder.setdefault((clean_name(row.get("nm")) or ""), []).append(row)

    out: list[dict[str, Any]] = []
    for name, group in by_holder.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        common = [g for g in group if _is_common_stock(g.get("stock_knd"))]
        pick = max(common or group,
                   key=lambda g: parse_float(g.get("trmend_posesn_stock_qota_rt")) or 0.0)
        # 버린 행도 흔적을 남긴다 — 우선주 지분이 궁금할 때 되짚을 수 있게
        others = [f"{g.get('stock_knd')} "
                  f"{parse_float(g.get('trmend_posesn_stock_qota_rt')) or 0}%"
                  for g in group if g is not pick]
        pick = dict(pick)
        pick["_other_share_types"] = " · ".join(others)
        out.append(pick)
    return out


def normalize_shareholders(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """최대주주 현황을 주주(Person/Company) → 대상회사 OWNS_STAKE_IN 관계로 변환한다.
    방향: 주주(소유주체) → 회사(소유대상) [outbound].
    """
    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    to_ref = master_company_ref(corp_code)

    for row in _pick_primary_rows(rows):
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
            subtype=_stake_subtype(shareholder_relation),
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
