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


class SearchRelation(BaseModel):
    """검색 히트에 딸린 관계 한 건. 전에는 자유 dict였다.

    ★타입을 준 이유는 `edge_id`를 **빠뜨릴 수 없게** 하기 위해서다(설계서 Rule 7).
      dict면 키 하나가 조용히 비어도 조립 단계에 가서야 터진다. `RetrieveResponse.
      relations`의 `Relation.edge_id`가 필수 필드라 여기서 못 채우면 관계를 통째로
      다시 조회해야 한다.

    ★`evidence_id`로 대신하면 안 된다 — 한 근거가 여러 엣지를 뒷받침해서 유일하지
      않다(엣지 11,060 : 근거 9,228).

    설계서 §8은 source/target을 `EntityRef`로 감싼 모양을 보였으나, `EntityRef`의
    필드가 설계서 어디에도 정의돼 있지 않고 지금 평평한 모양으로 이미 상대측
    id·라벨을 다 싣고 있어 **평평한 채로 둔다**(설계서 §30-6).
    """

    edge_id: str = Field(description="엣지 자체의 유일한 id(Neo4j elementId)")
    edge_type: str
    source: str
    source_id: str
    source_entity_type: str
    target: str
    target_id: str
    target_entity_type: str
    direction: Optional[str] = Field(
        default=None,
        description="해소된 앵커 기준 방향. 판별 불가면 null — 지어내지 않는다")


class SearchHit(BaseModel):
    """★점수를 하나로 뭉뚱그리지 않는다(D2, 2026-08-19).

    전에는 `score` 하나였는데 단계마다 뜻이 달랐다 — GraphSearcher는 Neo4j 관계
    점수를, VectorSearcher는 코사인 유사도를, ResultRanker는 RRF 순위값을 같은
    필드에 덮어썼다. RRF 1위가 0.0164라 그대로 노출하면 프론트가 「신뢰도 1.6%」로
    읽는다(현황서 §5-1). 셋을 이름으로 가른다.
    """

    entity_type: EntityType
    entity_id: str
    name: str
    source_score: float = Field(
        description="생산자가 매긴 원점수 — Neo4j 관계 점수 / 코사인 유사도 / "
                    "이름 해소 확신도. 소스마다 스케일이 달라 서로 비교하면 안 된다")
    rrf_score: Optional[float] = Field(
        default=None,
        description="ResultRanker가 채운 Reciprocal Rank Fusion 값(1위 ≈ 0.0164). "
                    "**순위 신호일 뿐 확률·confidence가 아니다.** ResultRanker를 "
                    "거치지 않는 NAME 분기는 null")
    rank: Optional[int] = Field(
        default=None, ge=1,
        description="최종 결과 안에서의 1부터 시작하는 순위. Orchestrator가 채운다")
    sources: list[SourceName]
    kind: Optional[str] = None
    freshness: Optional[dict] = None
    verdict: Optional[str] = None
    relations: Optional[list[SearchRelation]] = None
    evidence: list[dict] = Field(default_factory=list)
