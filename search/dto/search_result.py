# 최종적으로 유저에게 돌려주는 검색 결과
"""SearchResult — Search API의 최종 응답 객체(HTTP response body).

기술설계서 7.4절 정의. SearchOrchestrator가 검색을 마친 후 생성하며,
클라이언트(웹 UI 또는 AI Agent)에 반환되는 통합 검색 결과 데이터 구조다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from search.dto.search_hit import SearchHit


class SearchResult(BaseModel):
    query: str = Field(
        ...,
        description="사용자가 요청했던 원문 검색어"
    )
    hits: list[SearchHit] = Field(
        ...,
        description="랭킹 및 중복 제거가 완료된 최종 검색 결과 항목 목록"
    )
    total: int = Field(
        ...,
        ge=0,
        description="조건에 일치하는 전체 검색 결과 개수 (페이징/총 개수 표기용)"
    )
    took_ms: int = Field(
        ...,
        ge=0,
        description="검색 처리 완료까지 소요된 총 시간 (밀리초 단위)"
    )
    cache_hit: bool = Field(
        ...,
        description="캐시(Redis)에서 결과를 즉시 반환했는지 여부 (True: 캐시 사용, False: DB 직접 조회)"
    )
    used_semantic_fallback: bool = Field(
        ...,
        description="정확한 매칭 결과가 없어 의미 기반 검색(Vector Search)으로 우회(Fallback)하여 결과를 찾았는지 여부"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="검색 수행 중 발생한 비치명적 경고 메시지 목록 (예: 일부 DB 타임아웃, 파라미터 보정 안내 등)"
    )