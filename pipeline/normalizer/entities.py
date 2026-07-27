"""이름 → 노드 엔티티(+ref) 빌더 (정규화기 공통).

resolver(corp_code_master)로 corp_code를 해소하고, 실패 시 unresolved stub을
만든다. 펀드·신탁·조합은 Company로 흡수(market="펀드"). 지배 의도는 노드가 아니라
OWNS_STAKE_IN.subtype/ratio/purpose가 담는다(ERD §3-1).
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
from pipeline.normalizer.resolver import resolve

# 개인이 아닌 법인/단체를 시사하는 표기
_COMPANY_MARKERS = ("㈜", "(주)", "(유)", "주식회사", "유한회사", "회사", "재단", "조합", "Inc", "Ltd", "Corp")

# 비기업 기관(Organization) 표기 — 연구기관·재단·협회 등 (기업 아님)
_ORG_MARKERS = ("연구원", "연구소", "재단법인", "협회", "진흥원", "위원회", "학회", "진흥회")


def looks_like_organization(name: str) -> bool:
    """비기업 기관 판별. 단 ㈜·주식회사 등 법인격 표기가 있으면 회사다
    (예: ㈜인공지능연구원, 디엠비마케팅연구소㈜는 '연구원/연구소'가 들어가도 주식회사).
    """
    if any(m in name for m in _COMPANY_MARKERS):
        return False
    return any(m in name for m in _ORG_MARKERS)


def looks_like_company(name: str) -> bool:
    """주주명이 법인인지 판별 — 표기 마커 또는 corp_code 해소 성공."""
    if is_investment_vehicle(name):
        return True
    if any(marker in name for marker in _COMPANY_MARKERS):
        return True
    return resolve(name) is not None


def build_company(name: str) -> tuple[EntityDTO, str]:
    """이름 → (Company EntityDTO, ref). 엔드포인트 후보라 is_stub=True.

    P1 경로 A(정형)는 전부 Company로 둔다(펀드·연구소·재단 포함). Organization
    (규제기관·정부·협회) 타이핑은 P2(소송·규제 주체)에서 처리 — 정형 소스로는
    회사/기관을 안정적으로 구분할 수 없고(㈜연구원 등 실명 회사도 많음), 무리한
    분류가 ER 이중화를 유발한다. looks_like_organization은 P2용으로 보존.
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
