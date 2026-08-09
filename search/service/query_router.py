# 검색 결과 합치기, 중복 제거 및 순위 정렬(Ranking)
"""QueryRouter — normalized_query에서 관계(edge) 키워드와 방향을 감지하는 독립 컴포넌트.

기술설계서 §10-2·§18이 "미확정"으로 남겨둔 "자연어 질의 → edge_type 자동 판별 규칙"의
1차 구현이다. mode 판별과 Orchestrator 편입은 이번 Task의 범위가 아니다(Task 8이 이
결과를 소비한다).

입력은 항상 `SearchQuery.normalized_query` 전체 문자열이다 — EntityResolver가 어떤
Entity를 추출·해소했는지와 무관하게 원문을 그대로 본다(EntityResolver를 호출하지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from search.model.enums import Direction


@dataclass(frozen=True)
class RoutingResult:
    edge_types: list[str]
    direction: Optional[Direction]


@dataclass(frozen=True)
class _DeepRule:
    edge_type: str
    keyword: re.Pattern
    # 주체 조사(가/이) + 키워드 — 질의에 언급된 대상이 관계를 일으키는 쪽(source) → outgoing.
    outgoing: re.Pattern
    # 대상 조사(에/에게/를/을) + 키워드 — 질의에 언급된 대상이 관계를 받는 쪽(target) → incoming.
    incoming: re.Pattern


# ── 방향까지 판단하는 3종(§4-1) ──────────────────────────────────────────
_DEEP_RULES: list[_DeepRule] = [
    _DeepRule(
        edge_type="SUPPLIES_TO",
        keyword=re.compile("납품|공급"),
        outgoing=re.compile(r"[가이]\s*(?:납품|공급)"),        # "~가 납품하는" → 공급사(source)
        incoming=re.compile(r"(?:에|에게)\s*(?:납품|공급)"),   # "~에 납품하는" → 고객(target)
    ),
    _DeepRule(
        edge_type="OWNS_STAKE_IN",
        keyword=re.compile("투자|지분|출자|최대주주"),
        outgoing=re.compile(r"[가이]\s*(?:투자|출자)"),        # "~가 투자한" → 투자자(source)
        incoming=re.compile(r"(?:에|에게)\s*(?:투자|출자)"),   # "~에 투자한" → 피투자사(target)
    ),
    _DeepRule(
        edge_type="SUES",
        keyword=re.compile("소송|제소|피소"),
        outgoing=re.compile(r"[가이]\s*제소"),                 # "~가 제소한" → 원고(source)
        incoming=re.compile(r"(?:를|을)\s*제소|피소"),         # "~를 제소한"/"피소" → 피고(target)
    ),
]

# ── 대표 키워드 1개만 등록한 나머지 9종(§5) — 방향 판단 없음 ───────────────
# 각 값은 [미확정/저신뢰] — ontology.py EDGE_DEFINITIONS를 참고해 고른 대표
# 키워드 1개씩이다. 동의어 확장·정확도 검증은 이번 Task 범위 밖.
_SHALLOW_KEYWORDS: dict[str, str] = {
    "PARTNERS_WITH": "협력",     # [미확정/저신뢰]
    "COMPETES_WITH": "경쟁",     # [미확정/저신뢰]
    "ACQUIRES": "인수",          # [미확정/저신뢰]
    "REGULATES": "규제",         # [미확정/저신뢰]
    "DEVELOPS": "개발",          # [미확정/저신뢰]
    "DEPENDS_ON": "의존",        # [미확정/저신뢰]
    "IS_EXECUTIVE_OF": "임원",   # [미확정/저신뢰]
    "HAS_EVENT": "사건",         # [미확정/저신뢰]
    "IMPACTS": "영향",           # [미확정/저신뢰]
}


def _detect_direction(query: str, rule: _DeepRule) -> Optional[Direction]:
    is_outgoing = bool(rule.outgoing.search(query))
    is_incoming = bool(rule.incoming.search(query))
    if is_outgoing and not is_incoming:
        return Direction.OUTGOING
    if is_incoming and not is_outgoing:
        return Direction.INCOMING
    return None  # 조사 패턴이 없거나 양쪽 다 걸리면 방향을 강제하지 않는다(§4-1)


class QueryRouter:
    def route(self, normalized_query: str) -> RoutingResult:
        edge_types: list[str] = []
        direction: Optional[Direction] = None

        for rule in _DEEP_RULES:
            if rule.keyword.search(normalized_query):
                if rule.edge_type not in edge_types:
                    edge_types.append(rule.edge_type)
                if direction is None:
                    direction = _detect_direction(normalized_query, rule)

        for edge_type, keyword in _SHALLOW_KEYWORDS.items():
            if keyword in normalized_query and edge_type not in edge_types:
                edge_types.append(edge_type)

        return RoutingResult(edge_types=edge_types, direction=direction)
