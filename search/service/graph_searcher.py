"""GraphSearcher — resolved entity + edge_types(+direction)를 Neo4j 관계로 조회해
SearchHit으로 변환한다.

기술설계서 §4-4·§5-2·§9-3 결정을 그대로 따른다: `app.services.graph_service.
relations_of()`를 **그대로 함수 호출**한다. freshness·grounding_suspect·
grounding_verdict·confidence 필터링은 이미 그 안에서 끝나 있으므로 다시 구현하지
않는다. `Neo4jRepository`를 거치지 않는다(graph_service 우회 금지).

`Relation.score`/`freshness`/`verdict`는 재계산하지 않고 그대로 옮긴다.

Task6: `Relation`이 이제 source/target의 안정 식별자(`source_id`/`target_id`,
graph_loader.py·batch/repair/first_seen.py와 같은 우선순위의 coalesce 결과)와
Neo4j label(`source_entity_type`/`target_entity_type`)을 함께 반환하므로, 상대
엔티티의 `entity_id`/`entity_type`을 이름 기반 추측 없이 그대로 옮긴다.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from app.services.graph_service import Relation, relations_of
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit, SearchRelation
from search.model.enums import Direction, EntityType

# resolved_entities가 비었을 때(norm_name=None) relations_of()가 그래프 전체를
# 스캔하지 않도록 강제하는 안전 상한(§6). 실측 근거 없는 잠정치.
_HARD_LIMIT = 100

# anchor 없는 검색(§8)에서 source/target 슬롯을 5개씩 채우기 위해 필요한 최소
# 원천 후보 수. top_k를 그대로 relations_of(limit=...)에 넘기면(예: top_k=3)
# dedup 도중 슬롯을 채우기도 전에 후보가 바닥날 수 있다 — top_k의 "최종 결과 개수
# 상한"이라는 기존 의미는 건드리지 않는다. 원천 조회량과 최종 반환량을 분리해,
# 조회는 넉넉히 하고 최종 반환에서만 top_k를 적용한다(아래 _search_anchorless).
# 실측 근거 없는 잠정치.
_ANCHORLESS_MIN_FETCH = 50
_ANCHORLESS_SLOT_SIZE = 5

# 워크스페이스가 주어지면 점수순으로 미리 자르지 않는다(2026-08-20).
#
# ★`relations_of(limit=...)`는 **점수순 파이썬 슬라이스**다. 워크스페이스 랭킹은
#   점수가 아닌 기준으로 다시 줄 세우므로, 여기서 top_k로 잘라 버리면 랭커가
#   볼 수 없는 후보가 생긴다. 실측(2026-08-20) — 삼성전자 관계 998건에서
#   SK하이닉스는 271번째, 현대차는 418번째다. top_k=10으로 자르면 워크스페이스
#   기업이 후보에 아예 없어 「우선 노출」이 성립하지 않는다.
#
# Cypher에 LIMIT이 없어 **조회량은 어차피 같다** — 자르지 않아도 DB는 더 일하지
# 않는다. 다만 무한정 SearchHit을 만들지 않도록 천장은 둔다(잠정치).
_WORKSPACE_FETCH_CEILING = 2000


def _primary_resolution(resolved_entities: list[Resolution]) -> Optional[Resolution]:
    """가장 점수가 높은 Resolution 하나만 쓴다(§2, §9) — 동점이면 먼저 나온 것."""
    if not resolved_entities:
        return None
    return max(resolved_entities, key=lambda r: r.score)


def _resolve_limit(top_k: Optional[int]) -> int:
    if top_k is None or top_k <= 0:
        return _HARD_LIMIT
    return min(top_k, _HARD_LIMIT)


def _fetch_limit(top_k: Optional[int], workspace_keys: Optional[list[str]]) -> int:
    """워크스페이스가 있으면 넉넉히, 없으면 지금까지대로."""
    if workspace_keys:
        return _WORKSPACE_FETCH_CEILING
    return _resolve_limit(top_k)


def _resolve_anchorless_fetch_limit(top_k: Optional[int]) -> int:
    return max(_resolve_limit(top_k), _ANCHORLESS_MIN_FETCH)


def _safe_entity_type(label: str) -> Optional[EntityType]:
    """Neo4j label -> EntityType. 모르는 label이면(2026-08-09 실측: 5종 라벨만
    존재, 이론상 없어야 함) 검색 전체를 죽이지 않고 None을 돌려준다 — EntityType에
    UNKNOWN 멤버가 없어(확인 완료) 임의로 추가하지 않았다. 호출측은 해당 엔티티를
    조용히 건너뛴다(완료 보고서 [설계 결정 필요] 참고)."""
    try:
        return EntityType(label)
    except ValueError:
        return None


def _relation_direction(relation: Relation, anchor_norm_name: str) -> Optional[Direction]:
    """anchor(해소된 기업) 기준 이 Relation의 방향. 정규화 후에도 source/target
    어느 쪽도 anchor와 안 맞으면(이론상 드묾) 판별 불가로 None."""
    is_source = normalize_company_name(relation.source) == anchor_norm_name
    is_target = normalize_company_name(relation.target) == anchor_norm_name
    if is_source and not is_target:
        return Direction.OUTGOING
    if is_target and not is_source:
        return Direction.INCOMING
    return None


def _relation_info(relation: Relation, direction: Optional[Direction]) -> SearchRelation:
    """★`edge_id`를 반드시 싣는다(설계서 Rule 7). `RetrieveResponse.relations`의
    `Relation.edge_id`가 필수라, 여기서 흘리면 관계를 통째로 재조회해야 한다."""
    return SearchRelation(
        edge_id=relation.edge_id,
        edge_type=relation.edge_type,
        source=relation.source, source_id=relation.source_id,
        source_entity_type=relation.source_entity_type,
        target=relation.target, target_id=relation.target_id,
        target_entity_type=relation.target_entity_type,
        direction=direction.value if direction is not None else None,
    )


def _evidence_refs(relation: Relation) -> list[dict]:
    """★`evidence_id`(단수)와 `evidence_ids`(복수)를 **둘 다** 모은다.

    전에는 단수만 옮겨 근거가 조용히 빠졌다 — 실측(2026-08-20)으로 삼성전자 관계
    200건 중 54건이 복수 근거를 들고 있었다. `relation_service._evidence()`도 둘 다
    모으므로 같은 규약을 쓴다. 순서는 보존하고 중복만 제거한다.
    """
    ids: list[str] = []
    for value in (relation.props.get("evidence_id"), *(relation.props.get("evidence_ids") or [])):
        if value and value not in ids:
            ids.append(value)
    return [{"evidence_id": i} for i in ids]


def _to_search_hit(
    *, entity_id: str, name: str, entity_type: EntityType,
    relation: Relation, direction: Optional[Direction],
) -> SearchHit:
    return SearchHit(
        entity_type=entity_type,
        entity_id=entity_id,
        name=name,
        source_score=relation.score,
        sources=["neo4j"],
        freshness=dataclasses.asdict(relation.freshness),
        verdict=relation.verdict,
        relations=[_relation_info(relation, direction)],
        evidence=_evidence_refs(relation),
    )


class GraphSearcher:
    def search(
        self,
        resolved_entities: list[Resolution],
        edge_types: list[str],
        direction: Optional[Direction] = None,
        *,
        top_k: Optional[int] = None,
        workspace_keys: Optional[list[str]] = None,
    ) -> list[SearchHit]:
        """★`workspace_keys`는 **거르는 데 쓰지 않는다.** 후보를 얼마나 넉넉히
        뽑을지만 정한다 — 워크스페이스 관련도로 다시 줄 세울 것이므로 점수순
        절단을 늦춘다. 관련도 계산과 최종 top_k는 ResultRanker의 몫이다.
        """
        primary = _primary_resolution(resolved_entities)
        anchor_norm_name = normalize_company_name(primary.corp_name) if primary else None

        if anchor_norm_name is not None:
            return self._search_anchored(
                anchor_norm_name, edge_types, direction, top_k, workspace_keys)
        return self._search_anchorless(edge_types, top_k, workspace_keys)

    def _search_anchored(
        self, anchor_norm_name: str, edge_types: list[str],
        direction: Optional[Direction], top_k: Optional[int],
        workspace_keys: Optional[list[str]] = None,
    ) -> list[SearchHit]:
        limit = _fetch_limit(top_k, workspace_keys)
        relations = relations_of(norm_name=anchor_norm_name, edge_types=edge_types,
                                 limit=limit)

        hits: list[SearchHit] = []
        for relation in relations:
            rel_direction = _relation_direction(relation, anchor_norm_name)
            if rel_direction is None:
                continue  # 정규화 후에도 양쪽 다 anchor와 안 맞음 — 상대를 정의할 수 없다
            if direction is not None and rel_direction != direction:
                continue

            if rel_direction == Direction.OUTGOING:
                entity_id, name, label = (
                    relation.target_id, relation.target, relation.target_entity_type)
            else:
                entity_id, name, label = (
                    relation.source_id, relation.source, relation.source_entity_type)

            entity_type = _safe_entity_type(label)
            if entity_type is None:
                continue

            hits.append(_to_search_hit(
                entity_id=entity_id, name=name, entity_type=entity_type,
                relation=relation, direction=rel_direction))
        return hits

    # ★anchorless 슬롯 채우기는 아래 그대로 둔다 — 그쪽은 「source/target 어느
    #   한쪽만 임의로 고르지 않는다」는 별개 규칙(§8)이라 워크스페이스와 무관하다.

    def _search_anchorless(
        self, edge_types: list[str], top_k: Optional[int],
        workspace_keys: Optional[list[str]] = None,
    ) -> list[SearchHit]:
        """§8: anchor가 없으면 source/target 어느 한쪽만 임의로 고르지 않는다 —
        각 슬롯을 최대 5개씩, 서로 독립적으로(같은 엔티티가 양쪽에 다 나와도 됨)
        `relations_of()`가 이미 정렬해 준 score 내림차순 순서 그대로 채운다."""
        fetch_limit = (_WORKSPACE_FETCH_CEILING if workspace_keys
                       else _resolve_anchorless_fetch_limit(top_k))
        relations = relations_of(norm_name=None, edge_types=edge_types, limit=fetch_limit)

        source_hits: list[SearchHit] = []
        target_hits: list[SearchHit] = []
        seen_source_ids: set[str] = set()
        seen_target_ids: set[str] = set()

        for relation in relations:
            if len(source_hits) < _ANCHORLESS_SLOT_SIZE and relation.source_id not in seen_source_ids:
                entity_type = _safe_entity_type(relation.source_entity_type)
                if entity_type is not None:
                    seen_source_ids.add(relation.source_id)
                    source_hits.append(_to_search_hit(
                        entity_id=relation.source_id, name=relation.source,
                        entity_type=entity_type, relation=relation, direction=None))

            if len(target_hits) < _ANCHORLESS_SLOT_SIZE and relation.target_id not in seen_target_ids:
                entity_type = _safe_entity_type(relation.target_entity_type)
                if entity_type is not None:
                    seen_target_ids.add(relation.target_id)
                    target_hits.append(_to_search_hit(
                        entity_id=relation.target_id, name=relation.target,
                        entity_type=entity_type, relation=relation, direction=None))

            if len(source_hits) >= _ANCHORLESS_SLOT_SIZE and len(target_hits) >= _ANCHORLESS_SLOT_SIZE:
                break

        hits = source_hits + target_hits
        # top_k의 기존 의미("최종 결과 개수 상한")는 여기서 지킨다 — 5+5 슬롯을
        # 채우려고 원천 조회는 넉넉히 했지만, 호출자가 그보다 적게 원하면 자른다.
        return hits[:top_k] if top_k is not None else hits
