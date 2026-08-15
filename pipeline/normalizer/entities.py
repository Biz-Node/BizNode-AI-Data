"""이름 → 노드 엔티티(+ref) 빌더 (정규화기 공통).

resolver(corp_code_master)로 corp_code를 해소하고, 실패 시 unresolved stub을
만든다. 펀드·신탁·조합은 Company로 흡수(market="펀드"). 지배 의도는 노드가 아니라
OWNS_STAKE_IN.subtype/ratio/purpose가 담는다.
"""

from __future__ import annotations

from typing import Optional

from schemas.dart_schemas import (
    CompanyDTO,
    EntityDTO,
    OrganizationDTO,
    PersonDTO,
    make_entity_ref,
    make_person_key,
)
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.common import is_investment_vehicle
from pipeline.normalizer.legal_forms import looks_like_company as _by_notation
from pipeline.normalizer.resolver import candidates, resolve

# 법인격·기관 표기는 `legal_forms.py` 한 곳에서만 정한다(2026-08-13).
# 전에는 이 파일이 자기 목록을 들고 있어 **노조를 기관으로 못 잡았다**
# (「전국금속노동조합」 → Company). 합친 목록이 그 빈칸을 메운다.
from pipeline.normalizer.legal_forms import looks_like_organization


def looks_like_company(name: str) -> bool:
    """주주명이 법인인지 판별 — 표기 마커 또는 corp_code 해소 성공."""
    if is_investment_vehicle(name):
        return True
    if _by_notation(name):
        return True
    # 모호해도 회사다 — 어느 회사인지 모를 뿐이다
    return resolve(name) is not None or bool(candidates(name))


def build_company(name: str) -> tuple[EntityDTO, str]:
    """이름 → (Company EntityDTO, ref). 엔드포인트 후보라 is_stub=True.

    P1 경로 A(정형)는 전부 Company로 둔다(펀드·연구소·재단 포함). Organization
    (규제기관·정부·협회) 타이핑은 P2(소송·규제 주체)에서 처리 — 정형 소스로는
    회사/기관을 안정적으로 구분할 수 없고(㈜연구원 등 실명 회사도 많음), 무리한
    분류가 ER 이중화를 유발한다.
    """
    norm = normalize_company_name(name)
    vehicle = is_investment_vehicle(name)

    if vehicle:
        dto = CompanyDTO(name=name, norm_name=norm, market="펀드", vehicle_type=vehicle,
                         is_stub=True, resolution_status="unresolved")
        return EntityDTO("Company", norm, dto.to_properties()), make_entity_ref("Company", norm)

    r = resolve(name)
    if r is not None:
        dto = CompanyDTO(
            name=r.corp_name or name, norm_name=norm, corp_code=r.corp_code,
            stock_code=r.stock_code, market=(None if r.stock_code else "비상장"),
            is_stub=True, resolution_status="resolved",
        )
        return EntityDTO("Company", r.corp_code, dto.to_properties()), make_entity_ref("Company", r.corp_code)

    # ★후보는 있는데 못 좁힌 경우와 후보가 아예 없는 경우를 가른다(2026-08-13).
    #   명부에 동명 법인이 13,452곳이라 「신우」·「태성산업」처럼 못 좁히는 이름이
    #   실제로 나온다. 전에는 둘 다 그냥 unresolved였는데, 그러면 화면에서
    #   「모르는 회사」와 「어느 회사인지 모르는 회사」가 구분되지 않는다.
    #   corp_code를 붙이지 않는 건 같지만 **후보를 남겨** 나중에 물어볼 수 있게 한다.
    cands = candidates(name)
    if len(cands) > 1:
        dto = CompanyDTO(name=name, norm_name=norm, is_stub=True,
                         resolution_status="ambiguous")
        props = dto.to_properties()
        props["candidate_corp_codes"] = [c.corp_code for c in cands[:10]]
        props["candidate_count"] = len(cands)
        return EntityDTO("Company", norm, props), make_entity_ref("Company", norm)

    dto = CompanyDTO(name=name, norm_name=norm, is_stub=True, resolution_status="unresolved")
    return EntityDTO("Company", norm, dto.to_properties()), make_entity_ref("Company", norm)


def build_person(name: str, birth_year_month: Optional[str], gender: Optional[str],
                 corp_code: str) -> tuple[EntityDTO, str]:
    """이름 → (Person EntityDTO, ref). person_key로 식별(겸직 통합)."""
    key = make_person_key(name, birth_year_month, corp_code)
    dto = PersonDTO(name=name, person_key=key, gender=gender, birth_year_month=birth_year_month)
    return EntityDTO("Person", key, dto.to_properties()), make_entity_ref("Person", key)


def master_company_ref(corp_code: str) -> str:
    """적재 대상(공시 주체) 회사 ref — 항상 corp_code 기준."""
    return make_entity_ref("Company", corp_code)
