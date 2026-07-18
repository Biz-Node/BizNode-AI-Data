# NormalizedDocument의 도메인 검증을 수행하는 Validator 모듈.

from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-\d{2}$")

@dataclass
class ValidationReport:
    """Validator가 `NormalizedDocument`를 검사한 결과를 담는다."""
    dropped: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)

    def extend(self, other: "ValidationReport") -> None:
        self.dropped.extend(other.dropped)
        self.warned.extend(other.warned)

def add_warning(properties: dict[str, Any], message: str) -> None:
    """드롭하지 않고 통과시키되 `properties["_validation_warnings"]`에 경고를 남긴다."""
    properties.setdefault("_validation_warnings", []).append(message)

def is_in_range(value: Optional[float], low: float, high: float) -> bool:
    """`value`가 `None`이면 통과(검사 생략), 아니면 `low <= value <= high`."""
    return value is None or low <= value <= high

def is_non_negative(value: Optional[float]) -> bool:
    """`value`가 `None`이면 통과, 아니면 `value >= 0`."""
    return value is None or value >= 0

def is_iso_date(value: Optional[str]) -> bool:
    """`value`가 `None`이면 통과, 아니면 `YYYY-MM-DD` 형식인지 확인한다."""
    return value is None or bool(_ISO_DATE_RE.match(value))

def is_plausible_birth_year_month(value: Optional[str], *, today: Optional[date] = None) -> bool:
    """`value`가 `None`이면 통과, 아니면 `YYYY-MM` 형식이고 1900~현재년도 사이인지 확인한다."""
    if value is None:
        return True
    match = _YEAR_MONTH_RE.match(value)
    if not match:
        return False
    year = int(match.group(1))
    current_year = (today or date.today()).year
    return 1900 <= year <= current_year

def is_numeric_or_none(value: Any) -> bool:
    """`value`가 `None`이면 통과, 아니면 int/float인지 확인한다."""
    if value is None:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool)
