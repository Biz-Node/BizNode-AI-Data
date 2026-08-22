# API 입구에서 받는 유저의 원문 요청

"""SearchRequest — Search API의 입력 계약(HTTP body).

기술설계서 6-2절 정의. 내부 실행 컨텍스트인 SearchQuery(search_query.py)와는
역할이 다르다 — SearchRequest는 사용자가 보낸 원문 그대로만 담고, 검색이
실행되기 전의 불변 입력이다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pipeline.ontology import EDGE_TYPES

# top_k 기본값/상한 — 실측 근거 없는 잠정값이다(기술설계서 §15 G9, §10-5 "미확정").
# 트래픽·성능 실측 후 조정한다.
_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50


class SearchRequest(BaseModel):
    """★`entity_types`·`filters`를 계약에서 뺐다(D3, 2026-08-19).

    둘 다 SearchQuery에 담기기만 하고 읽는 코드가 0곳이었다. `entity_types`는
    pg_trgm·Neo4j·벡터 인덱스가 전부 Company에만 있어 지원할 방법이 없고,
    `filters`(sector 2단계 조합)가 쓰던 선필터는 `companies` 표와 함께 사라졌다.
    보낸 값이 조용히 무시되느니 422로 되돌려 주는 편이 낫다 — `extra="forbid"`가
    폐기된 필드와 오타를 함께 걸러낸다.

    `edge_types`는 반대로 **배선했다.** 값이 오면 QueryRouter의 키워드 추론보다
    우선한다 — 챗봇 탐색 프로파일이 엣지를 직접 지정해야 하기 때문이다.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    workspace_keys: list[str] = Field(
        default_factory=list,
        description="검색 범위. 관계는 **양끝 모두** 이 안에 있어야 하고(라벨 무관), "
                    "의미검색은 이 기업들로 선필터한다. 비면 범위 제한이 없다")
    edge_types: Optional[list[str]] = Field(
        default=None,
        description="지정하면 QueryRouter의 질의문 추론 대신 이 값을 쓴다")
    top_k: int = Field(default=_DEFAULT_TOP_K, ge=1, le=_MAX_TOP_K)
    include_evidence: bool = Field(
        default=True, description="false면 응답의 evidence를 비운다")

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
