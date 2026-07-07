# 회사명을 투자조합/신탁/펀드 여부로 분류하는 유틸리티
from __future__ import annotations

from typing import Optional

VehicleType = str

VEHICLE_PATTERNS: dict[VehicleType, tuple[str, ...]] = {
    "VC_PARTNERSHIP": (
        "신기술투자조합",
        "벤처투자조합",
        "창업투자조합",
        "투자조합",
    ),
    "TRUST": (
        "투자신탁",
        "증권투자신탁",
        "자투자신탁",
        "trust",
        "신탁",
    ),
    "FUND": (
        "펀드",
        "fund",
        "pef",
        "사모투자전문회사",
    ),
}

def is_investment_vehicle(name: Optional[str]) -> Optional[str]:

    if not name:
        return None

    normalized = name.casefold()
    for vehicle_type, patterns in VEHICLE_PATTERNS.items():
        if any(pattern.casefold() in normalized for pattern in patterns):
            return vehicle_type

    return None
