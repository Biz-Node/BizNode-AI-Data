"""파이프라인 전역 공유 DTO (BizNode v1.0 온톨로지).

노드 5종(Company/Person/Organization/Product/Event) + 엣지 표준 메타.
schemas는 순수 데이터 계층 — pipeline을 임포트하지 않는다(계층 역전 금지).
norm_name·person_key 등 계산은 normalizer(pipeline)에서 하고 값만 주입한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional

# 노드 5종. InvestmentVehicle(펀드·신탁·조합)은 Company로 흡수(market="펀드").
EntityType = str  # "Company" | "Person" | "Organization" | "Product" | "Event"


def make_entity_ref(entity_type: EntityType, key: str) -> str:
    """entities[]/relationships[]가 공유하는 "<Type>:<key>" 참조 문자열."""
    return f"{entity_type}:{key}"


def make_person_key(name: str, birth_year_month: Optional[str], corp_code: str) -> str:
    """Person MERGE 키. 겸직 통합을 위해 소속이 아니라 name+birth로 식별한다.
    birth_ym이 없으면 최초 소속(corp_code)으로 폴백(겸직 통합은 포기, 허용).
    """
    if birth_year_month:
        return f"{name}|{birth_year_month}"
    return f"{name}@{corp_code}"


# ---------------------------------------------------------------------------
# 엣지 표준 메타 (방법서 §4 · ERD §2-3) — 전 엣지 공통
# ---------------------------------------------------------------------------

# 소스별 예상 갱신 주기(일). "미갱신 = 종료"가 아니라 소스마다 주기가 다르다.
#   DART 정형(지분·임원)은 사업보고서가 연 1회라 11개월 미갱신도 정상이다.
#   방법서 §4의 "6개월 룰"을 일괄 적용하면 정상 관계를 대거 오판한다.
SOURCE_REFRESH_CYCLE_DAYS = {
    "dart": 365,          # 사업보고서 연 1회
    "dart_filing": 0,     # 개별 공시(공급계약 등) — valid_until이 진짜 종료일
    "news": 180,          # 뉴스는 6개월 미언급이면 신선도 하향 (P2)
}


def standard_edge_meta(
    *,
    source_doc: Optional[str],
    source_type: str = "dart",
    confidence: float = 1.0,
    valid_from: Optional[str] = None,       # 상태 엣지: 관계 시작일
    valid_until: Optional[str] = None,
    occurred_at: Optional[str] = None,      # 사건 엣지: 발생 시점
    last_seen: Optional[str] = None,        # 마지막 관측일(기록). None이면 관측 기준일로 채움
    observed_at: Optional[str] = None,      # 이 관계가 관측된 문서의 기준일
    is_current: bool = True,
    evidence_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """전 엣지가 공유하는 표준 메타 속성 딕셔너리.

    last_seen = "마지막으로 이 관계를 관측한 날"(사실 기록)이며 신선도 판정 자체가
    아니다. 판정은 `refresh_cycle_days`(소스별 주기)와 함께 조회 시점에 계산한다.
    valid_until이 있으면 그것이 우선한다(계약 종료일 = 명확한 사실).
    """
    # 관측일 우선순위: 명시 last_seen > 문서 기준일 > 관계 시작일/발생일
    resolved_last_seen = last_seen or observed_at or valid_from or occurred_at

    return {
        "valid_from": valid_from,
        "valid_until": valid_until,
        "occurred_at": occurred_at,
        "last_seen": resolved_last_seen,
        "refresh_cycle_days": SOURCE_REFRESH_CYCLE_DAYS.get(source_type, 365),
        "is_current": is_current,
        "confidence": confidence,
        "source_type": source_type,
        "source_doc": source_doc,
        "evidence_id": evidence_id,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# 공통 엔티티/관계 컨테이너
# ---------------------------------------------------------------------------

@dataclass
class EntityDTO:
    """entities[] 원소. `properties`는 각 노드 DTO의 `to_properties()` 결과."""

    type: EntityType
    key: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "key": self.key, "properties": self.properties}


@dataclass
class RelationshipDTO:
    """relationships[] 원소. `properties`는 관계 DTO의 `to_properties()` 결과."""

    type: str
    from_key: str
    to_key: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "from": self.from_key,
            "to": self.to_key,
            "properties": self.properties,
        }


@dataclass
class NormalizedDocument:
    """Normalizer가 산출하는 최상위 구조 (entities[] + relationships[])."""

    entities: list[EntityDTO] = field(default_factory=list)
    relationships: list[RelationshipDTO] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
        }


# ---------------------------------------------------------------------------
# 노드 DTO
# ---------------------------------------------------------------------------

@dataclass
class PersonDTO:
    name: str
    person_key: str                          # MERGE 키 (make_person_key)
    gender: Optional[str] = None
    birth_year_month: Optional[str] = None

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "person_key": self.person_key,
            "gender": self.gender,
            "birth_year_month": self.birth_year_month,
        }


@dataclass
class CompanyDTO:
    name: Optional[str] = None
    norm_name: Optional[str] = None          # stub 병합 키 (정규화 명칭)
    corp_code: Optional[str] = None
    stock_code: Optional[str] = None
    market: Optional[str] = None             # KOSPI/KOSDAQ/비상장/해외/펀드
    vehicle_type: Optional[str] = None       # 펀드·신탁·조합 세부(흡수분)
    is_stub: bool = False                    # 껍데기 노드 여부
    resolution_status: str = "resolved"      # resolved | unresolved
    # 출자현황에서 얻는 피투자사 재무 스냅샷(표시용)
    total_assets: Optional[float] = None
    net_profit: Optional[float] = None

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "norm_name": self.norm_name,
            "corp_code": self.corp_code,
            "stock_code": self.stock_code,
            "market": self.market,
            "vehicle_type": self.vehicle_type,
            "is_stub": self.is_stub,
            "resolution_status": self.resolution_status,
            "total_assets": self.total_assets,
            "net_profit": self.net_profit,
        }


@dataclass
class OrganizationDTO:
    """비기업 기관(규제기관·정부·협회). P1은 스키마만, 데이터는 P2."""

    name: str
    norm_name: Optional[str] = None
    org_type: Optional[str] = None           # 규제기관/정부/협회

    def to_properties(self) -> dict[str, Any]:
        return {"name": self.name, "norm_name": self.norm_name, "org_type": self.org_type}


@dataclass
class ProductDTO:
    """제품·기술·부품·소재. 데이터는 경로 C(Sprint 3)."""

    name: str
    norm_name: Optional[str] = None
    category: Optional[str] = None
    aliases: Optional[list[str]] = None

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "norm_name": self.norm_name,
            "category": self.category,
            "aliases": self.aliases,
        }


@dataclass
class EventDTO:
    """사건·이슈. 데이터는 경로 B(Sprint 2)."""

    event_id: str
    event_type: Optional[str] = None
    title: Optional[str] = None
    occurred_at: Optional[str] = None
    sign: Optional[str] = None               # positive/negative/neutral
    status: Optional[str] = None             # 진행중/종결
    source_doc: Optional[str] = None
    evidence_id: Optional[str] = None
    confidence: float = 1.0

    def to_properties(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": self.title,
            "occurred_at": self.occurred_at,
            "sign": self.sign,
            "status": self.status,
            "source_doc": self.source_doc,
            "evidence_id": self.evidence_id,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# 엣지 DTO — 지분 통합 (OWNS_STAKE_IN)
# ---------------------------------------------------------------------------

# OWNS_STAKE_IN subtype 값 (방법서 2-3)
STAKE_SUBTYPE_MAJOR = "최대주주"
# 「최대주주 및 특수관계인 현황」에서 relate가 「본인」이 아닌 행. 계열회사·친족·
# 재단·임원 등이 여기 들어간다. 최대주주와 **한 묶음으로 보고**되지만 최대주주는
# 아니다 — 라벨을 나누지 않으면 지분 0.7%짜리 계열사가 최대주주로 표시된다.
STAKE_SUBTYPE_RELATED = "특수관계인"
STAKE_SUBTYPE_5PCT = "5%이상주주"
STAKE_SUBTYPE_SUBSIDIARY = "자회사"
STAKE_SUBTYPE_INVEST = "출자"
STAKE_SUBTYPE_AFFILIATE = "계열사"


@dataclass
class OwnsStakeInRelationshipDTO:
    """지분 관계 통합 (구 OWNS_SHARE + INVESTS_IN).

    최대주주(17)·변동(18)·타법인출자(30)·대량보유(majorstock)·임원소유(elestock)를
    subtype으로 구분한다. 방향은 항상 소유주체 → 소유대상(outbound).
    """

    subtype: str                             # 최대주주/5%이상주주/자회사/출자/계열사
    meta: dict[str, Any]                      # standard_edge_meta(...)
    direction: str = "outbound"
    ratio: Optional[float] = None            # 지분율
    previous_ratio: Optional[float] = None
    ratio_change: Optional[float] = None
    settlement_date: Optional[str] = None    # 기준 결산일
    # ── 최대주주(17/18) 부가 ──
    shareholder_relation: Optional[str] = None
    share_count: Optional[int] = None
    previous_share_count: Optional[int] = None
    share_count_change: Optional[int] = None
    ownership_changed: Optional[bool] = None
    change_reason: Optional[str] = None
    share_type: Optional[str] = None
    remark: Optional[str] = None
    # ── 타법인출자(30) 부가 ──
    purpose: Optional[str] = None            # 출자목적 (지배의도 신호)
    first_acquired: Optional[str] = None
    first_acquired_amount: Optional[int] = None
    book_value: Optional[int] = None
    investment_increased: Optional[bool] = None
    investment_decreased: Optional[bool] = None

    type: ClassVar[str] = "OWNS_STAKE_IN"

    def to_properties(self) -> dict[str, Any]:
        domain = {
            "subtype": self.subtype,
            "direction": self.direction,
            "ratio": self.ratio,
            "previous_ratio": self.previous_ratio,
            "ratio_change": self.ratio_change,
            "settlement_date": self.settlement_date,
            "shareholder_relation": self.shareholder_relation,
            "share_count": self.share_count,
            "previous_share_count": self.previous_share_count,
            "share_count_change": self.share_count_change,
            "ownership_changed": self.ownership_changed,
            "change_reason": self.change_reason,
            "share_type": self.share_type,
            "remark": self.remark,
            "purpose": self.purpose,
            "first_acquired": self.first_acquired,
            "first_acquired_amount": self.first_acquired_amount,
            "book_value": self.book_value,
            "investment_increased": self.investment_increased,
            "investment_decreased": self.investment_decreased,
        }
        return {**domain, **self.meta}


# ---------------------------------------------------------------------------
# 엣지 DTO — 임원 (IS_EXECUTIVE_OF, 사외이사는 subtype)
# ---------------------------------------------------------------------------

@dataclass
class ExecutiveRelationshipDTO:
    """임원 관계 (구 IS_EXECUTIVE_OF + IS_OUTSIDE_DIRECTOR_OF 통합).
    사외이사는 subtype="사외이사"로 구분. Person → Company(outbound).
    """

    meta: dict[str, Any]
    subtype: Optional[str] = None            # 직위 또는 "사외이사"
    direction: str = "outbound"
    position: Optional[str] = None
    employment_type: Optional[str] = None
    duty: Optional[str] = None
    main_career: Optional[str] = None
    shareholder_relation: Optional[str] = None
    tenure_end: Optional[str] = None
    tenure_months: Optional[int] = None
    is_new_executive: Optional[bool] = None
    settlement_date: Optional[str] = None

    type: ClassVar[str] = "IS_EXECUTIVE_OF"

    def to_properties(self) -> dict[str, Any]:
        domain = {
            "subtype": self.subtype,
            "direction": self.direction,
            "position": self.position,
            "employment_type": self.employment_type,
            "duty": self.duty,
            "main_career": self.main_career,
            "shareholder_relation": self.shareholder_relation,
            "tenure_end": self.tenure_end,
            "tenure_months": self.tenure_months,
            "is_new_executive": self.is_new_executive,
            "settlement_date": self.settlement_date,
        }
        return {**domain, **self.meta}
