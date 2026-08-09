# 검색 처리 과정에서 쓰이는 내부 실행 상태값
"""SearchQuery — SearchOrchestrator가 조립하는 내부 실행 컨텍스트.

기술설계서 6-1, 6-3절. SearchRequest(API 계약, search_request.py)와 역할이 다르다:
SearchRequest는 HTTP 경계에서 오는 불변 입력이고, SearchQuery는 EntityResolver 실행
결과(resolved_entities)나 검색 모드 판별 결과처럼 실행 중에만 존재하는 상태를 담는다.
API 스키마가 아니므로 Pydantic이 아닌 일반 dataclass로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from pipeline.normalizer.resolver import Resolution
from search.model.enums import Direction, EntityType, SearchMode

# SearchRequest의 top_k 기본값과 맞춘다(search_request.py 참고, 잠정값).
_DEFAULT_TOP_K = 10


@dataclass
class SearchQuery:
    raw_query: str
    normalized_query: str
    mode: SearchMode
    today: date
    resolved_entities: list[Resolution] = field(default_factory=list)
    entity_types: Optional[list[EntityType]] = None
    edge_types: Optional[list[str]] = None
    direction: Optional[Direction] = None
    top_k: int = _DEFAULT_TOP_K
    include_evidence: bool = True
    filters: Optional[dict] = None
