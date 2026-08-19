# 기본 규칙 및 상태값 (Enum 정의)
# 검색 모드(이름/관계/의미/하이브리드) 및 방향 정의
"""Search Layer 공용 Enum.

값은 새로 만들지 않고 기존 코드의 상수를 그대로 재사용한다.
EntityType 값 = Neo4j 노드 라벨(pipeline/validators/matrix.py의 NODE_TYPES).
"""

from __future__ import annotations

from enum import Enum

from pipeline.validators.matrix import NODE_TYPES


class EntityType(str, Enum):
    COMPANY = "Company"
    PERSON = "Person"
    ORGANIZATION = "Organization"
    PRODUCT = "Product"
    EVENT = "Event"


assert {e.value for e in EntityType} == set(NODE_TYPES), (
    "EntityType이 matrix.NODE_TYPES와 어긋난다 — 기존 상수가 바뀌었는지 확인할 것"
)


class SearchMode(str, Enum):
    """기술설계서 6-3절 정의. 기존 코드에 대응 상수 없음(신규 개념).

    ★`HYBRID`를 제거했다(A4, 2026-08-19). 분기는 `edge_types` 유무로만 갈리므로
      (있으면 RELATIONSHIP, 없으면 NAME 또는 SEMANTIC) 어떤 경로로도 생성되지
      않는 값이었다. 관계 질의에 의미검색을 섞지 않는다는 설계 결정과도 맞물려
      있어 되살리려면 그 결정부터 다시 봐야 한다.
    """

    NAME = "NAME"
    RELATIONSHIP = "RELATIONSHIP"
    SEMANTIC = "SEMANTIC"


class Direction(str, Enum):
    """resolved entity 기준 관계의 방향. 기술설계서 §7-2 시퀀스(`direction=incoming`)가
    이미 쓰는 값을 그대로 가져온다 — 6-3절 DTO 표에서 누락돼 있던 것을 보완(Task 3)."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"
