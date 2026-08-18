# 대량보유 상황보고 (majorstock, 5%룰) 정규화 → OWNS_STAKE_IN{subtype:"5%이상주주"}

from __future__ import annotations

from typing import Any

from schemas.dart_schemas import (
    EntityDTO,
    NormalizedDocument,
    OwnsStakeInRelationshipDTO,
    RelationshipDTO,
    STAKE_SUBTYPE_5PCT,
    standard_edge_meta,
)
from pipeline.normalizer.base import clean_missing, clean_name, parse_float
from pipeline.normalizer.entities import build_company, build_person, looks_like_company, master_company_ref


def _to_iso(raw: str | None) -> str | None:
    """rcept_dt 정규화. 'YYYY-MM-DD' 그대로, 'YYYYMMDD'는 변환."""
    s = clean_missing(raw)
    if s is None:
        return None
    if "-" in s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def normalize_majorstock(rows: list[dict[str, Any]], corp_code: str) -> NormalizedDocument:
    """대량보유(5%) 보고를 보고자 → 회사 OWNS_STAKE_IN 관계로 변환.
    시계열 보고라 보고자별 최신 1건만 취한다.
    """
    # 보고자별 최신(rcept_dt 최대)만 유지
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        repror = clean_name(row.get("repror"))
        if not repror:
            continue
        dt = row.get("rcept_dt") or ""
        if repror not in latest or dt > (latest[repror].get("rcept_dt") or ""):
            latest[repror] = row

    entities: dict[str, EntityDTO] = {}
    relationships: list[RelationshipDTO] = []
    to_ref = master_company_ref(corp_code)

    for repror, row in latest.items():
        if looks_like_company(repror):
            entity, from_ref = build_company(repror)
        else:
            entity, from_ref = build_person(repror, None, None, corp_code)
        entities.setdefault(entity.key, entity)

        valid_from = _to_iso(row.get("rcept_dt"))
        rel = OwnsStakeInRelationshipDTO(
            subtype=STAKE_SUBTYPE_5PCT,
            # 근거: 대량보유 보고서 접수번호(rcept_no)
            meta=standard_edge_meta(
                source_doc=clean_missing(row.get("rcept_no")), valid_from=valid_from
            ),
            ratio=parse_float(row.get("stkrt")),
            change_reason=clean_missing(row.get("report_resn")),
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
