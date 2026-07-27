"""관계 신선도 판정 (방법서 §4 — 단, 소스별 갱신 주기를 반영해 정교화).

방법서는 "6개월 이상 last_seen 미갱신 → 과거 관계로 간주"라 했으나, 이를 일괄
적용하면 정상 관계를 대거 오판한다:
  · 삼성생명의 삼성전자 8.51% 지분은 10년 유지되는데 사업보고서는 연 1회
  · 임원 임기는 3년, 공시는 연 1회
  · 삼성-Google 특허 라이선스는 영구 계약

→ **"미갱신 = 종료"가 아니다.** 소스별 예상 갱신 주기를 넘겼을 때만 신선도를
   낮추고, `valid_until`(명시적 종료일)이 있으면 그것이 절대 우선한다.

GraphRAG 프롬프트·서빙 API가 이 판정을 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

# 주기를 넘겨도 바로 만료로 보지 않고 두는 여유(공시 지연 등)
_GRACE_RATIO = 1.5


@dataclass(frozen=True)
class Freshness:
    status: str          # current | stale | expired | unknown
    reason: str
    days_since: Optional[int]
    confidence_factor: float   # 신뢰도 가중치(0~1) — GraphRAG가 곱해서 사용


def _parse(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def assess(props: dict[str, Any], *, today: Optional[date] = None) -> Freshness:
    """엣지 속성 → 신선도 판정.

    우선순위:
      1) is_current=false 또는 valid_until 경과  → expired (명확한 사실)
      2) last_seen이 주기×여유 이내             → current
      3) 주기×여유 초과                         → stale (신뢰도 하향)
      4) 날짜 정보 없음                          → unknown
    """
    now = today or date.today()

    # 1) 명시적 종료가 최우선
    if props.get("is_current") is False:
        return Freshness("expired", "is_current=false", None, 0.3)

    valid_until = _parse(props.get("valid_until"))
    if valid_until and valid_until < now:
        days = (now - valid_until).days
        return Freshness("expired", f"계약 종료일 {valid_until} 경과", days, 0.3)

    last_seen = _parse(props.get("last_seen"))
    if last_seen is None:
        return Freshness("unknown", "관측일 정보 없음", None, 0.7)

    days_since = (now - last_seen).days
    cycle = props.get("refresh_cycle_days")
    # dart_filing(0) 등 주기가 없는 소스는 valid_until로만 판단 → 여기선 current
    if not cycle:
        return Freshness("current", "개별 공시(종료일 기준 유효)", days_since, 1.0)

    threshold = cycle * _GRACE_RATIO
    if days_since <= threshold:
        return Freshness("current", f"{days_since}일 경과 (주기 {cycle}일 이내)", days_since, 1.0)

    return Freshness(
        "stale",
        f"{days_since}일 미갱신 (예상 주기 {cycle}일 초과) — 재확인 필요",
        days_since,
        0.6,
    )


def effective_confidence(props: dict[str, Any], *, today: Optional[date] = None) -> float:
    """신선도를 반영한 실효 신뢰도 = confidence × 신선도 가중치."""
    base = props.get("confidence")
    base = 1.0 if base is None else float(base)
    return round(base * assess(props, today=today).confidence_factor, 3)
