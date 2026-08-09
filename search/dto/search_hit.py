# 검색된 개별 항목 하나하나의 데이터
# 예: "삼성전자"를 검색했을 때 나오는 결과 리스트가 10개라면, 10개의 SearchHit 반환 
"""SearchHit — 검색 결과 단위. 기술설계서 6-4절 정의.

여러 저장소(PostgreSQL/Neo4j/ChromaDB)에서 나온 결과를 ResultRanker가 병합한 뒤
공통 형태로 노출하는 DTO. freshness/verdict는 app.services.graph_service가 이미
계산한 값을 그대로 옮겨 담을 뿐, 이 DTO 자체는 재계산하지 않는다(기술설계서 9-3절).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from search.model.enums import EntityType

SourceName = Literal["postgres", "neo4j", "chroma"]


class SearchHit(BaseModel):
    entity_type: EntityType
    entity_id: str
    name: str
    score: float
    sources: list[SourceName]
    freshness: Optional[dict] = None
    verdict: Optional[str] = None
    relations: Optional[list[dict]] = None
    evidence: list[dict] = Field(default_factory=list)
