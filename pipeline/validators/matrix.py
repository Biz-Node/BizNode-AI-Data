"""노드-엣지 허용 매트릭스 Validator (방법서 2-2 / ERD §2-1).

적재 전 `(source_type, edge_type, target_type)`이 허용 조합인지 검증한다.
스키마 견고함의 핵심 — 파서·LLM이 'Product -SUPPLIES_TO-> Person' 같은
불가능한 관계를 만들지 못하게 하는 기준.

대칭 엣지(⇄)는 양방향 모두 허용한다.
"""

from __future__ import annotations

from typing import NamedTuple

# 노드 5종
NODE_TYPES = frozenset({"Company", "Person", "Organization", "Product", "Event"})

# L2 엣지 → L1 카테고리 (직렬화 시 주입용 — 엣지에 저장 안 함)
EDGE_TO_L1_CATEGORY: dict[str, str] = {
    "OWNS_STAKE_IN": "소유·지배",
    "IS_EXECUTIVE_OF": "소유·지배",
    "SUPPLIES_TO": "거래·협력",
    "PARTNERS_WITH": "거래·협력",
    "ACQUIRES": "거래·협력",
    "SUES": "리스크·분쟁",
    "COMPETES_WITH": "리스크·분쟁",
    "REGULATES": "리스크·분쟁",
    "DEVELOPS": "제품·기술",
    "DEPENDS_ON": "제품·기술",
    "HAS_EVENT": "이벤트·영향",
    "IMPACTS": "이벤트·영향",
}


class EdgeRule(NamedTuple):
    sources: frozenset[str]      # 허용 source 노드 타입
    targets: frozenset[str]      # 허용 target 노드 타입
    symmetric: bool              # 대칭(⇄) 여부


def _s(*types: str) -> frozenset[str]:
    return frozenset(types)


# 방법서 2-2 허용 매트릭스 (12종)
EDGE_MATRIX: dict[str, EdgeRule] = {
    "OWNS_STAKE_IN":  EdgeRule(_s("Company", "Person", "Organization"), _s("Company"), False),
    "IS_EXECUTIVE_OF": EdgeRule(_s("Person"), _s("Company", "Organization"), False),
    "SUPPLIES_TO":    EdgeRule(_s("Company"), _s("Company"), False),
    "PARTNERS_WITH":  EdgeRule(_s("Company", "Organization"), _s("Company", "Organization"), True),
    "ACQUIRES":       EdgeRule(_s("Company"), _s("Company"), False),
    "SUES":           EdgeRule(_s("Company", "Person", "Organization"),
                               _s("Company", "Person", "Organization"), False),
    "COMPETES_WITH":  EdgeRule(_s("Company", "Product"), _s("Company", "Product"), True),
    "REGULATES":      EdgeRule(_s("Organization"), _s("Company", "Product"), False),
    "DEVELOPS":       EdgeRule(_s("Company"), _s("Product"), False),
    "DEPENDS_ON":     EdgeRule(_s("Company", "Product"), _s("Product"), False),
    "HAS_EVENT":      EdgeRule(_s("Company", "Product"), _s("Event"), False),
    "IMPACTS":        EdgeRule(_s("Event"), _s("Company", "Product"), False),
}


def validate_edge(source_type: str, edge_type: str, target_type: str) -> tuple[bool, str]:
    """(허용여부, 사유)를 반환한다. 허용이면 사유는 빈 문자열."""
    if edge_type not in EDGE_MATRIX:
        return False, f"미정의 엣지 타입: {edge_type!r}"
    if source_type not in NODE_TYPES:
        return False, f"미정의 source 노드 타입: {source_type!r}"
    if target_type not in NODE_TYPES:
        return False, f"미정의 target 노드 타입: {target_type!r}"

    rule = EDGE_MATRIX[edge_type]
    if source_type in rule.sources and target_type in rule.targets:
        return True, ""
    # 대칭 엣지는 방향을 바꿔서도 허용
    if rule.symmetric and source_type in rule.targets and target_type in rule.sources:
        return True, ""

    return False, (
        f"매트릭스 위반: {source_type} -{edge_type}-> {target_type} "
        f"(허용 source={sorted(rule.sources)}, target={sorted(rule.targets)}"
        f"{', 대칭' if rule.symmetric else ''})"
    )


def level_1_category(edge_type: str) -> str:
    """엣지 타입의 L1 카테고리. 직렬화 시 주입용(엣지에 저장하지 않음)."""
    return EDGE_TO_L1_CATEGORY.get(edge_type, "기타")
