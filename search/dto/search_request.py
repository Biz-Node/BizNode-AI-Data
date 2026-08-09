# API 입구에서 받는 유저의 원문 요청

"""SearchRequest — Search API의 입력 계약(HTTP body).

기술설계서 6-2절 정의. 내부 실행 컨텍스트인 SearchQuery(search_query.py)와는
역할이 다르다 — SearchRequest는 사용자가 보낸 원문 그대로만 담고, 검색이
실행되기 전의 불변 입력이다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from pipeline.ontology import EDGE_TYPES
from search.model.enums import EntityType

# top_k 기본값/상한 — 실측 근거 없는 잠정값이다(기술설계서 §15 G9, §10-5 "미확정").
# 트래픽·성능 실측 후 조정한다.
_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    entity_types: Optional[list[EntityType]] = None
    edge_types: Optional[list[str]] = None
    top_k: int = Field(default=_DEFAULT_TOP_K, ge=1, le=_MAX_TOP_K)
    include_evidence: bool = True
    filters: Optional[dict] = None

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("edge_types")
    @classmethod
    def _edge_types_must_be_known(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return value
        unknown = [e for e in value if e not in EDGE_TYPES]
        if unknown:
            raise ValueError(f"unknown edge_types: {unknown}")
        return value
