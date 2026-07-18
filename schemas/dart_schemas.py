from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

EntityType = Literal["Person", "Company", "InvestmentVehicle"]

def make_entity_ref(entity_type: EntityType, key: str) -> str:
    """entities[]/relationships[]가 공유하는 "<Type>:<key>" 참조 문자열을 만든다."""
    return f"{entity_type}:{key}"

# ---------------------------------------------------------------------------
# 공통 엔티티/관계 
# ---------------------------------------------------------------------------

@dataclass
class EntityDTO:
    """entities[] 원소 하나. `properties`는 Person/CompanyDTO의 `to_properties()` 결과."""

    type: EntityType
    key: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "key": self.key, "properties": self.properties}

@dataclass
class RelationshipDTO:
    """relationships[] 원소 하나. `properties`는 OwnsShare/Executive/InvestsInDTO의 `to_properties()` 결과."""

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
    """Normalizer가 `data/normalized/{corp_code}_{api_name}.json`에 쓰는 최상위 구조."""

    entities: list[EntityDTO] = field(default_factory=list)
    relationships: list[RelationshipDTO] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
        }

# ---------------------------------------------------------------------------
# Entity DTOs
# ---------------------------------------------------------------------------

@dataclass
class PersonDTO:

    name: str
    gender: Optional[str] = None  
    birth_year_month: Optional[str] = None  

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gender": self.gender,
            "birth_year_month": self.birth_year_month,
        }


@dataclass
class CompanyDTO:

    name: Optional[str] = None
    corp_code: Optional[str] = None
    stock_code: Optional[str] = None
    market: Optional[str] = None
    total_assets: Optional[float] = None  
    net_profit: Optional[float] = None  

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "corp_code": self.corp_code,
            "stock_code": self.stock_code,
            "market": self.market,
            "total_assets": self.total_assets,
            "net_profit": self.net_profit,
        }


@dataclass
class InvestmentVehicleDTO:
    name: str
    vehicle_type: Literal["TRUST", "FUND", "VC_PARTNERSHIP"]

    def to_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vehicle_type": self.vehicle_type,
        }

# ---------------------------------------------------------------------------
# 최대주주 — OWNS_SHARE
# ---------------------------------------------------------------------------

@dataclass
class OwnsShareRelationshipDTO:
    share_type: str
    shareholder_relation: Optional[str]
    share_count: Optional[int]
    previous_share_count: Optional[int]
    share_ratio: Optional[float]
    previous_share_ratio: Optional[float]
    remark: Optional[str]
    change_reason: Optional[str]
    share_count_change: Optional[int]
    share_ratio_change: Optional[float]
    ownership_changed: bool
    settlement_date: str

    type: ClassVar[str] = "OWNS_SHARE"

    def to_properties(self) -> dict[str, Any]:
        return {
            "share_type": self.share_type,
            "shareholder_relation": self.shareholder_relation,
            "share_count": self.share_count,
            "previous_share_count": self.previous_share_count,
            "share_ratio": self.share_ratio,
            "previous_share_ratio": self.previous_share_ratio,
            "remark": self.remark,
            "change_reason": self.change_reason,
            "share_count_change": self.share_count_change,
            "share_ratio_change": self.share_ratio_change,
            "ownership_changed": self.ownership_changed,
            "settlement_date": self.settlement_date,
        }

# ---------------------------------------------------------------------------
# 임원현황 — IS_EXECUTIVE_OF / IS_OUTSIDE_DIRECTOR_OF
# ---------------------------------------------------------------------------

@dataclass
class ExecutiveRelationshipDTO:

    type: Literal["IS_EXECUTIVE_OF", "IS_OUTSIDE_DIRECTOR_OF"]
    position: Optional[str]
    employment_type: Optional[str]
    duty: Optional[str]
    main_career: Optional[str]
    shareholder_relation: Optional[str]
    tenure_end: Optional[str]
    tenure_months: Optional[int]
    is_new_executive: Optional[bool]
    settlement_date: str

    def to_properties(self) -> dict[str, Any]:
        return {
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

# ---------------------------------------------------------------------------
# 타법인출자 — INVESTS_IN
# ---------------------------------------------------------------------------

@dataclass
class InvestsInRelationshipDTO:
    purpose: Optional[str]
    first_acquired: Optional[str]
    first_acquired_amount: Optional[int]
    share_ratio: Optional[float]
    previous_share_ratio: Optional[float]
    share_ratio_change: Optional[float]
    investment_increased: bool
    investment_decreased: bool
    book_value: Optional[int]
    settlement_date: str

    type: ClassVar[str] = "INVESTS_IN"

    def to_properties(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "first_acquired": self.first_acquired,
            "first_acquired_amount": self.first_acquired_amount,
            "share_ratio": self.share_ratio,
            "previous_share_ratio": self.previous_share_ratio,
            "share_ratio_change": self.share_ratio_change,
            "investment_increased": self.investment_increased,
            "investment_decreased": self.investment_decreased,
            "book_value": self.book_value,
            "settlement_date": self.settlement_date,
        }
