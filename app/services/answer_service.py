"""AnswerService — Retrieve Layer 의 재료로 LLM 답변을 쓴다.

★답변에 쓴 evidence_id 는 서버가 반드시 화이트리스트로 검증한다 — LLM 이
  준 id 라도 RetrieveResponse.evidence 에 없거나 missing=true 면 버린다.
  근거 원문(뉴스·공시)은 신뢰 안 된 텍스트라 인젝션이 섞일 수 있다.
  구조적 방어(델리미터 + 시스템 프롬프트)만 걸고, 이 화이트리스트 검증을
  실질적 2차 방어선으로 삼는다(설계서 §13-2).

★LLM 호출이 실패하면 503 이 아니라 200 + 고정 문구를 돌려주고
  `AskResponse.failed=True` 로 성공과 구별한다(설계서 §13-3).
"""

from __future__ import annotations

from typing import Optional

from app.api.schemas import AskRequest, AskResponse, Evidence, Relation, RetrieveResponse, Source
from app.services.retrieve_service import RetrieveService


def _edge_id_for(evidence_id: str, relations: list[Relation]) -> Optional[str]:
    """근거가 관계에서 왔으면 그 관계의 edge_id 를 돌려준다. 없으면 None."""
    for relation in relations:
        if relation.evidence_id == evidence_id:
            return relation.edge_id
    return None


def _source_from_evidence(evidence: Evidence, relations: list[Relation]) -> Source:
    return Source(
        evidence_id=evidence.evidence_id,
        edge_id=_edge_id_for(evidence.evidence_id, relations),
        text=evidence.text,
        source_doc=evidence.source_doc,
        source_type=evidence.source_type,
        published_at=evidence.published_at,
    )


def _sources_from(evidence_ids: list[str], retrieved: RetrieveResponse) -> list[Source]:
    """LLM 이 인용한 evidence_id 를 재료 안에서만 찾는다 — 화이트리스트 검증.

    ★없는 id(지어낸 것) · missing=true(원문을 못 찾은 것) 는 조용히 버린다.
    """
    by_id = {e.evidence_id: e for e in retrieved.evidence}
    out: list[Source] = []
    for eid in evidence_ids:
        evidence = by_id.get(eid)
        if evidence is None or evidence.missing:
            continue
        out.append(_source_from_evidence(evidence, retrieved.relations))
    return out


def _fallback_sources(retrieved: RetrieveResponse) -> list[Source]:
    """LLM 호출이 실패했을 때 — 필터링 근거가 없으니 missing 만 뺀 원본 전부."""
    return [_source_from_evidence(e, retrieved.relations)
            for e in retrieved.evidence if not e.missing]
