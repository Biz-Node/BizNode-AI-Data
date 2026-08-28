"""그래프 경로의 프롬프트 조립 — **DTO 를 읽는다.**

★`answer_service._fact_lines()` 를 **고치지 않고 여기 따로 둔다.** 저쪽은
  `AnswerService.ask()` 가 쓰는 대조 기준선이라, 표기를 붙이려고 손대면 기준선이
  같이 움직여 「무엇 때문에 달라졌나」를 못 가린다(현황서 §5-28).

★1차와 달라지는 것은 **표기뿐**이다. 재료 집합(기업·사건·관계·근거)은 그대로다
  (`batch/audit/ask_graph_parity.py --materials`). 달라지는 줄:

    관계   score=0.9              → 신뢰도 0.54(confidence × 신선도) · 방향 표기 · 단위 표기
    사건   role=mentioned         → role=mentioned(기사에 함께 언급됐을 뿐 …)
    파급   stated=False           → stated=False(공급망으로 계산한 파급 …)

  전부 **LLM 이 오해하던 값에 뜻을 붙인 것**이고 새 재료가 아니다.

★판정 로직은 그대로 가져다 쓴다 — `_select_propagation`(사건별 공평 분배)·
  `material_consistency`(⑥.5 극성·시간 격리)·`_neutralize_delimiters`(델리미터
  무결성)는 DTO 와 **덕 타이핑이 맞아** 그대로 돈다. 규칙을 두 벌 두지 않는다.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.api.schemas import AnchorSource, Evidence, MatchType, Source
from app.llm import prompt as shared
from app.llm.prompt import EventRef, RelationRef
from app.services import material_consistency
from app.tools.dto import EventDTO, PropagationDTO, RelationDTO


# ── 도구 DTO → 공용 참조 타입 ─────────────────────────────────
# ★공용 모듈은 **자기가 쓰는 필드만** 받는다. 덕 타이핑으로 두 모양을 동시에
#   받게 하면 어느 쪽이 왔는지 모르는 채로 필드를 더듬게 된다.

def _relation_refs(relations: Sequence[RelationDTO]) -> list[RelationRef]:
    return [RelationRef(evidence_id=r.evidence_id, edge_id=r.edge_id,
                        source_key=r.source_key, source_name=r.source,
                        target_key=r.target_key, target_name=r.target)
            for r in relations]


def _event_refs(events: Sequence[EventDTO]) -> list[EventRef]:
    return [EventRef(event_id=e.event_id, event_type=e.event_type,
                     evidence_ids=e.evidence_ids) for e in events]


# ══════════════════════════════════════════════════════════════════
#  [사실]
# ══════════════════════════════════════════════════════════════════


def fact_lines(*, match_type: MatchType, companies, events: Sequence[EventDTO],
               relations: Sequence[RelationDTO],
               propagation: Sequence[PropagationDTO],
               evidence: Sequence[Evidence], workspace_keys: set[str],
               workspace_names: Optional[dict[str, str]] = None) -> str:
    lines: list[str] = []
    # ★집합 확인(설계서 §12)을 하려면 LLM 이 그 집합을 봐야 한다.
    if workspace_names:
        lines.append("워크스페이스: " + " · ".join(workspace_names.values()))
    if companies:
        lines.append("기업: " + ", ".join(f"{c.name}({c.key})" for c in companies))

    # ★⑥.5 가 낸 flag 로 **[확인된 사실] 안에서만** 격리한다(설계서 §10).
    #   근거 블록·`sources[]`·응답은 그대로다.
    polarity = material_consistency.check_polarity(events, evidence)
    temporal = material_consistency.check_temporal(events, evidence)

    for event in events:
        flag = polarity.get(event.event_id)
        if flag is not None:
            # 사건 줄 자체를 빼되 **조용히 빼지 않는다.**
            lines.append(
                f"(사건 {event.event_id} 은 라벨과 근거가 어긋나 사실에서 뺐습니다 — "
                f"라벨의 '{'·'.join(flag.label_words)}' 이 근거에 없고 "
                f"'{'·'.join(flag.evidence_words)}' 이 있습니다. "
                f"근거 원문은 [근거] 블록에 그대로 있습니다)")
            continue
        risk = "위험사건" if event.is_risk else "일반"
        # ★날짜를 그냥 찍으면 LLM 이 **사건 발생일**로 읽는다. 실제로는 보도일이다.
        when = f"보도 {event.occurred_at}" if event.occurred_at else "보도일 미상"
        when_flag = temporal.get(event.event_id)
        if when_flag is not None:
            # 줄은 남기고 **날짜만** 격리한다 — 실패한 것은 날짜 귀속이지 사건의 존재가 아니다.
            years = "·".join(str(y) for y in when_flag.evidence_years)
            when = (f"발생 시점 불명확 — 보도는 {event.occurred_at} 인데 "
                    f"근거 원문은 {years}년을 말합니다")
        # ★`role` 에 **뜻을 붙인다**(1.5차). 토큰만 주면 LLM 이 `mentioned` 를
        #   당사자로 읽는다 — 「이 기업에 난 일」은 `subject` 만이다.
        sign = f", 영향={event.sign}" if event.sign else ""
        summary = f", 국면: {event.timeline_summary}" if event.timeline_summary else ""
        lines.append(f"사건 {event.event_id}: {event.name} ({event.event_type}, "
                     f"{when}, {risk}, role={event.role}({event.role_note})"
                     f"{sign}{summary}) "
                     f"근거: {', '.join(event.evidence_ids) or '없음'}")

    for relation in relations:
        # ★방향에 **뜻이 있는지**를 적는다. PARTNERS_WITH·COMPETES_WITH 는 Neo4j 가
        #   무방향을 저장 못 해 키 순서로 고정한 인공 방향인데, 화살표만 찍으면
        #   LLM 이 없는 방향을 만든다.
        ends = (f"{shared.membership(relation.source_key, relation.source, workspace_keys)} "
                f"--{relation.edge_type}({relation.subtype or '-'})--> "
                f"{shared.membership(relation.target_key, relation.target, workspace_keys)}")
        # ★`score` 대신 `effective_confidence` 를 준다 — 내부 랭킹 점수를 그대로
        #   주지 않는다(dto.py). corroboration 보정·벌점이 섞인 값이라 「이 사실이
        #   얼마나 확실한가」로 읽히면 안 된다.
        marks = [f"{relation.source_note}",
                 f"신뢰도 {relation.effective_confidence}(신선도 {relation.freshness} 반영)",
                 relation.direction_note]
        if relation.ratio_text:
            marks.append(f"지분 {relation.ratio_text}")
        if relation.caution:
            marks.append(f"★{relation.caution}")
        lines.append(f"관계 {relation.edge_id}: {ends} ({' / '.join(marks)}) "
                     f"근거: {relation.evidence_id or '없음'}")

    # ★파급은 **프롬프트를 먹는다.** 사건별로 공평하게 나눠 담고, 뺀 몫을 적는다 —
    #   조용히 자르면 「그게 전부」로 읽힌다.
    kept, dropped = shared.select_propagation(list(propagation))
    for prop in kept:
        target = shared.membership(prop.key, prop.target, workspace_keys)
        lines.append(f"파급: {target} ({prop.hops}홉, {prop.stated_note}, "
                     f"경로: {' → '.join(prop.path)})")
    if dropped:
        detail = " · ".join(f"{origin} {n}곳" for origin, n in dropped.items())
        lines.append(f"(파급 {sum(dropped.values())}곳은 지면상 생략했습니다 — "
                     f"{detail}. 없는 것이 아닙니다)")

    body = "\n".join(lines) if lines else "(찾은 사실 없음)"
    return f"{shared.match_type_note(match_type)}\n{body}"


# ══════════════════════════════════════════════════════════════════
#  [근거]
# ══════════════════════════════════════════════════════════════════


def evidence_about(relations: Sequence[RelationDTO], events: Sequence[EventDTO],
                   evidence: Sequence[Evidence],
                   workspace_keys: set[str]) -> dict[str, str]:
    """근거 귀속 — **공용 모듈이 한다**(`app/llm/prompt.py`). 여기서는 도구 DTO 를
    공용 참조 타입으로 옮겨 넘기기만 한다."""
    return shared.evidence_about(_relation_refs(relations), _event_refs(events),
                                 evidence, workspace_keys)


def evidence_block(evidence: Sequence[Evidence], about: dict[str, str]) -> str:
    """`<evidence>` 블록 — **공용 모듈이 한다.** 델리미터 중화가 여기 있었는데,
    보안에 직접 걸리는 규칙을 두 벌로 두면 한쪽만 고쳐진다."""
    return shared.evidence_block(evidence, about)


def build_user_prompt(question: str, *, match_type: MatchType, companies,
                      events: Sequence[EventDTO], relations: Sequence[RelationDTO],
                      propagation: Sequence[PropagationDTO],
                      evidence: Sequence[Evidence],
                      anchor_source: AnchorSource,
                      workspace_names: dict[str, str]) -> str:
    workspace_keys = set(workspace_names or ())
    facts = fact_lines(match_type=match_type, companies=companies, events=events,
                       relations=relations, propagation=propagation,
                       evidence=evidence, workspace_keys=workspace_keys,
                       workspace_names=workspace_names)
    facts = shared.with_target_note(facts, anchor_source)
    about = evidence_about(relations, events, evidence, workspace_keys)
    return shared.assemble(question, facts, evidence, about)


# ══════════════════════════════════════════════════════════════════
#  근거 → 인용 (화이트리스트 검증)
# ══════════════════════════════════════════════════════════════════


def sources_from(evidence_ids: Sequence[str], evidence: Sequence[Evidence],
                 relations: Sequence[RelationDTO]) -> list[Source]:
    """화이트리스트 검증 — **공용 모듈이 한다.** 인용 가능한 id 를 정하는 자리라
    두 벌로 두면 한쪽만 느슨해져도 아무도 모른다."""
    return shared.sources_from(evidence_ids, evidence, _relation_refs(relations))


def fallback_sources(evidence: Sequence[Evidence],
                     relations: Sequence[RelationDTO]) -> list[Source]:
    """LLM 호출이 실패했을 때 — `missing` 만 뺀 원본 전부."""
    return shared.fallback_sources(evidence, _relation_refs(relations))


def event_types_by_evidence(events: Sequence[EventDTO]) -> dict[str, frozenset[str]]:
    """근거 id → 그 근거가 달린 사건들의 event_type. 연결성 판정의 재료다."""
    return shared.event_types_by_evidence(_event_refs(events))
