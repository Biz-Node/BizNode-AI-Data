"""`app/graph/prompt.fact_lines` — **운영 경로의 `[사실]` 렌더러.**

★**이 파일이 없었다.** 모든 `/ask` 요청이 지나가는 렌더러(241줄)에 직접
  테스트가 0건이었고, 같은 규칙을 검사하는 테스트 ~89건은 전부 요청이 지나가지
  않는 `answer_service._fact_lines`(대조 기준선)에 붙어 있었다. 「760 passed」가
  보증하던 것은 **아무도 부르지 않는 사본**이었다.

★공용 부분(근거 블록·귀속·화이트리스트·파급 분배)은 `tests/llm/test_prompt.py`
  가 본다. 여기서 보는 것은 **1.5차가 더한 표기** — 도구 DTO 를 읽어야만 나오는
  줄들이다:

      관계   effective_confidence · direction_note · ratio_text · caution
      사건   role_note · sign · timeline_summary
      파급   stated_note

  전부 「LLM 이 오해하던 값에 뜻을 붙인 것」이라, 조용히 빠지면 오해가 되돌아온다.
"""

from __future__ import annotations

from datetime import date

from app.api.schemas import AnchorSource, Evidence, MatchType, RelationEndpoint
from app.core import clock
from app.graph import prompt as sut
from app.tools.dto import (CAUTION_NEWS_DEVELOPS, DIRECTION_NOTE, ROLE_NOTE,
                           SOURCE_NOTE, STATED_NOTE, EventDTO, EventPhaseDTO,
                           PropagationDTO, RelationDTO)

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_WS = {_SAMSUNG: "삼성전자"}


def _rel(**over):
    base = dict(edge_id="e1", source="삼성전자", target="SFA반도체",
                source_key=_SAMSUNG, target_key="00301246",
                edge_type="SUPPLIES_TO", subtype="반도체 PCB", evidence_id="ev_rel",
                source_type="news", source_note=SOURCE_NOTE["news"],
                direction="directed", direction_note=DIRECTION_NOTE["directed"],
                freshness="current", effective_confidence=0.9)
    base.update(over)
    return RelationDTO(**base)


def _evt(**over):
    base = dict(event_id="evt_1", name="이천 공장 질소 누출", event_type="사고재해",
                is_risk=True, occurred_at="2024-02-16", evidence_ids=["ev_evt"],
                role="subject", role_note=ROLE_NOTE["subject"])
    base.update(over)
    return EventDTO(**base)


def _ev(eid="ev_rel"):
    return Evidence(evidence_id=eid, text="원문", source_doc="doc", source_type="news")


def _lines(*, events=(), relations=(), propagation=(), evidence=(), companies=(),
           match_type=MatchType.EXACT, workspace_names=None):
    return sut.fact_lines(
        match_type=match_type, companies=list(companies), events=list(events),
        relations=list(relations), propagation=list(propagation),
        evidence=list(evidence),
        workspace_keys=set(workspace_names or _WS),
        workspace_names=workspace_names if workspace_names is not None else _WS)


# ══════════════════════════════════════════════════════════════════
#  관계 표기 — ★내부 점수를 그대로 주지 않는다
# ══════════════════════════════════════════════════════════════════

def test_relation_shows_effective_confidence_not_the_ranking_score():
    """★`score` 는 corroboration 보정·벌점이 섞여 1.0 에서 잘린 값이라
    「이 사실이 얼마나 확실한가」로 읽히면 안 된다."""
    got = _lines(relations=[_rel(effective_confidence=0.54, freshness="stale")])

    assert "신뢰도 0.54(신선도 stale 반영)" in got
    assert "score" not in got


def test_symmetric_relations_say_the_arrow_has_no_meaning():
    """★`PARTNERS_WITH` 화살표는 Neo4j 가 무방향을 저장 못 해 키 순서로 고정한
    **인공 방향**이다. 표기가 없으면 LLM 이 없는 방향을 만든다."""
    got = _lines(relations=[_rel(edge_type="PARTNERS_WITH", direction="symmetric",
                                 direction_note=DIRECTION_NOTE["symmetric"])])

    assert "「A 가 B 에게」로 읽지 말 것" in got


def test_news_develops_carries_the_caution():
    """★뉴스 추출 `DEVELOPS` 는 오추출률 47% — 단정하면 안 된다."""
    got = _lines(relations=[_rel(edge_type="DEVELOPS", caution=CAUTION_NEWS_DEVELOPS)])

    assert f"★{CAUTION_NEWS_DEVELOPS}" in got


def test_ratio_is_rendered_with_its_unit():
    """★`0.72` 는 0.72% 지 소수가 아니다 — 0~1 구간에 진짜 소액지분이 126건 산다."""
    got = _lines(relations=[_rel(ratio=0.72, ratio_unit="percent", ratio_text="0.72%")])

    assert "지분 0.72%" in got


def test_relation_endpoints_carry_workspace_membership():
    got = _lines(relations=[_rel()])

    assert "삼성전자=워크스페이스" in got and "SFA반도체=바깥" in got


def test_source_note_says_whether_it_is_confirmed():
    got = _lines(relations=[_rel(source_type="dart", source_note=SOURCE_NOTE["dart"])])

    assert "DART 정기공시 — 확정 사실" in got


# ══════════════════════════════════════════════════════════════════
#  사건 표기
# ══════════════════════════════════════════════════════════════════

def test_event_role_is_rendered_with_its_meaning():
    """★토큰만 주면 LLM 이 `mentioned` 를 연루로 읽는다 — 「이 기업에 난 일」은
    `subject` 뿐이다."""
    got = _lines(events=[_evt(role="mentioned", role_note=ROLE_NOTE["mentioned"])])

    assert "role=mentioned(기사에 함께 언급됐을 뿐" in got


def test_event_date_is_labelled_as_a_report_date():
    """★그냥 찍으면 LLM 이 **사건 발생일**로 읽는다. 실제로는 보도일이다."""
    got = _lines(events=[_evt(occurred_at="2024-02-16")])

    assert "보도 2024-02-16" in got


def test_event_without_a_date_says_so_instead_of_guessing():
    got = _lines(events=[_evt(occurred_at=None)])

    assert "보도일 미상" in got


def test_impacts_sign_is_rendered_when_present_and_omitted_when_not():
    """★`IMPACTS` 짝이 없으면 `None` 이다 — 0 이나 neutral 로 메우면 **모르는
    것을 아는 척**하는 것이 된다."""
    assert "영향=negative" in _lines(events=[_evt(sign="negative")])
    assert "영향=" not in _lines(events=[_evt(sign=None)])


def test_timeline_is_summarised_into_one_line():
    """★13국면짜리를 그대로 실으면 사건 하나가 재료를 다 먹는다."""
    got = _lines(events=[_evt(
        timeline=[EventPhaseDTO(period="2026-06", name="1국면"),
                  EventPhaseDTO(period="2026-07", name="2국면")],
        timeline_summary="2026-06 1국면 → 2026-07 2국면 (2국면)")])

    assert "국면: 2026-06 1국면 → 2026-07 2국면 (2국면)" in got


def test_risk_events_are_marked():
    assert "위험사건" in _lines(events=[_evt(is_risk=True)])
    assert "일반" in _lines(events=[_evt(is_risk=False)])


def test_event_lists_its_own_evidence_ids():
    got = _lines(events=[_evt(evidence_ids=["ev_a", "ev_b"])])

    assert "근거: ev_a, ev_b" in got


def test_event_without_evidence_says_none():
    got = _lines(events=[_evt(evidence_ids=[])])

    assert "근거: 없음" in got


# ══════════════════════════════════════════════════════════════════
#  파급 표기
# ══════════════════════════════════════════════════════════════════

def _prop(**over):
    base = dict(event_id="evt_1", target="현대오토에버", key=None, score=0.3,
                hops=2, stated=False, stated_note=STATED_NOTE[False],
                path=["이천 질소 누출", "현대오토에버"])
    base.update(over)
    return PropagationDTO(**base)


def test_computed_propagation_is_not_sold_as_a_reported_fact():
    """★섞어 말하면 **추론을 사실로 파는 것**이 된다(설계서 §12 4등급)."""
    got = _lines(propagation=[_prop(stated=False)])

    assert "공급망으로 계산한 파급 — 보도된 사실이 아니다" in got


def test_reported_propagation_says_the_article_said_it():
    got = _lines(propagation=[_prop(stated=True, stated_note=STATED_NOTE[True], hops=1)])

    assert "기사가 직접 말한 파급 — 보도된 사실" in got


def test_propagation_without_a_key_is_not_called_outside():
    """★이름만 있고 노드가 없는 대상을 「바깥」이라 적으면 안 된다."""
    got = _lines(propagation=[_prop(key=None)])

    assert "현대오토에버=바깥" not in got and "현대오토에버" in got


def test_dropped_propagation_is_disclosed_not_silently_cut():
    """★조용히 자르면 「그게 전부」로 읽힌다."""
    rows = [_prop(target=f"기업{i}", path=["사건A", f"기업{i}"]) for i in range(20)]
    got = _lines(propagation=rows)

    assert "지면상 생략했습니다" in got and "없는 것이 아닙니다" in got


# ══════════════════════════════════════════════════════════════════
#  머리말 · 빈 재료
# ══════════════════════════════════════════════════════════════════

def test_semantic_match_tells_the_model_not_to_be_certain():
    got = _lines(match_type=MatchType.SEMANTIC)

    assert "확정된 사실처럼 말하지 마세요" in got.split("\n")[0]


def test_workspace_is_listed_so_the_model_can_check_membership():
    """★집합 확인(설계서 §12)을 하려면 LLM 이 그 집합을 봐야 한다."""
    got = _lines(workspace_names={_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"})

    assert "워크스페이스: 삼성전자 · SK하이닉스" in got


def test_empty_material_says_so_explicitly():
    got = _lines(workspace_names={})

    assert "(찾은 사실 없음)" in got


def test_companies_are_listed_with_their_keys():
    got = _lines(companies=[RelationEndpoint(key=_SAMSUNG, name="삼성전자")])

    assert "기업: 삼성전자(00126380)" in got


# ══════════════════════════════════════════════════════════════════
#  ⑥.5 격리 — ★조용히 빼지 않는다
# ══════════════════════════════════════════════════════════════════

def test_build_user_prompt_puts_the_target_note_after_the_search_method():
    got = sut.build_user_prompt(
        "질문", match_type=MatchType.EXACT, companies=[], events=[], relations=[],
        propagation=[], evidence=[_ev()], anchor_source=AnchorSource.QUERY,
        workspace_names=_WS)

    facts = got.split("[사실]\n")[1].split("\n\n[근거]")[0]
    assert facts.split("\n")[1].startswith("답변 대상: 질문")


def test_build_user_prompt_has_the_three_sections(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 8, 30))
    got = sut.build_user_prompt(
        "질문내용", match_type=MatchType.EXACT, companies=[], events=[],
        relations=[_rel()], propagation=[], evidence=[_ev()],
        anchor_source=AnchorSource.QUERY, workspace_names=_WS)

    # ★`오늘:` 이 `질문:` 과 `[사실]` 사이에 있다(2026-08-30). 재료의 날짜를
    #   무엇과 견줄지 모델에 주는 유일한 자리다 — 이 경로도 같은 `assemble()` 을
    #   쓰므로 `answer_service` 쪽과 **바이트까지 같다**(`ask_graph_parity`).
    assert got.startswith("질문: 질문내용\n오늘: 2026-08-30\n\n[사실]\n")
    assert "\n\n[근거]\n<evidence id=\"ev_rel\"" in got
