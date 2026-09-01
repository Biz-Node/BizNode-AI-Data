"""C-3 — **답변의 종류가 다르다.** `anchor_source` 로 가른다(설계서 §14-6).

    query      「이 기업에 대해 **답하라**」        → 서술
    workspace  「이 문맥에 해당하는 게 있으면 **찾아 보여라**」 → 목록 + 「대상을 저희가 골랐다」

★**헤지의 이유가 다르다.** `match_type` 헤지는 「의미가 비슷해서 찾은 것이라 틀린
  기업일 수 있다」이고, 워크스페이스 앵커는 **키가 정확하다.** 부정확한 것은
  **대상을 누가 골랐나**이지 그 기업이 맞나가 아니다(설계서 §14-6).

★가장 나쁜 것은 「답이 틀린 것」이 아니라 **「재료가 약하다는 사실이 사용자에게
  안 가는 것」**이다.

그리고 설계서 §12 — **워크스페이스 소속을 프롬프트에 싣는다.**

    ④ Insight 등급에는 걸 고리가 하나도 없었다. 「이 기업이 사용자의 워크스페이스
    안인가」는 **우리가 확실히 아는 사실**이라 처음으로 결정론적 고리가 된다.
    ★여전히 **판정하지 않는다** — 관측만 한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.schemas import (Anchor, AnchorSource, AskRequest, Event, MatchType,
                             Relation, RelationEndpoint, RetrieveResponse)
from app.services import answer_service as as_module
from app.services.query_understanding import AnchorDecision

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_WS = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def _relation(source_key, source_name, target_key, target_name):
    return Relation(
        edge_id="5:a:1", evidence_id="ev_a", type="SUPPLIES_TO",
        source=RelationEndpoint(key=source_key, name=source_name),
        target=RelationEndpoint(key=target_key, name=target_name),
        freshness="current")


def _retrieved(*, relations=(), match_type=MatchType.EXACT):
    return RetrieveResponse(question="q", match_type=match_type,
                            relations=list(relations))


def _decision(source, anchors=()):
    return AnchorDecision(source=source, workspace_names=_WS, anchors=list(anchors))


# ══════════════════════════════════════════════════════════════════════
#  §12 — 워크스페이스 소속을 사실 블록에 싣는다
# ══════════════════════════════════════════════════════════════════════

def test_fact_lines_marks_which_side_is_inside_the_workspace():
    """★설계서 §12 의 「심텍=워크스페이스 · 마이크론=바깥」 표기."""
    retrieved = _retrieved(relations=[_relation("09999999", "마이크론", _HYNIX, "SK하이닉스")])
    line = next(l for l in as_module._fact_lines(retrieved, set(_WS)).splitlines()
                if l.startswith("관계"))
    assert "SK하이닉스=워크스페이스" in line
    assert "마이크론=바깥" in line


def test_fact_lines_marks_both_sides_when_both_are_inside():
    retrieved = _retrieved(relations=[_relation(_SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스")])
    line = next(l for l in as_module._fact_lines(retrieved, set(_WS)).splitlines()
                if l.startswith("관계"))
    assert "삼성전자=워크스페이스" in line and "SK하이닉스=워크스페이스" in line


def test_fact_lines_lists_the_workspace_set():
    """★「집합 확인」을 하려면 LLM 이 그 집합을 봐야 한다(설계서 §12)."""
    lines = as_module._fact_lines(_retrieved(), set(_WS), workspace_names=_WS)
    assert "워크스페이스: 삼성전자 · SK하이닉스" in lines


def test_fact_lines_omits_membership_when_there_is_no_workspace():
    """★워크스페이스가 없으면 붙일 사실이 없다 — 빈 표기를 만들지 않는다."""
    retrieved = _retrieved(relations=[_relation("09999999", "마이크론", _HYNIX, "SK하이닉스")])
    line = next(l for l in as_module._fact_lines(retrieved, set()).splitlines()
                if l.startswith("관계"))
    assert "워크스페이스" not in line


def test_fact_lines_membership_is_deterministic():
    """★우리가 확실히 아는 사실이다 — 같은 입력에 같은 문자열이 나와야 한다."""
    retrieved = _retrieved(relations=[_relation(_SAMSUNG, "삼성전자", "09999999", "마이크론")])
    assert (as_module._fact_lines(retrieved, set(_WS))
            == as_module._fact_lines(retrieved, set(_WS)))


# ══════════════════════════════════════════════════════════════════════
#  §14-6 — 답변 형태를 anchor_source 로 가른다
# ══════════════════════════════════════════════════════════════════════

def test_prompt_states_the_answer_target_for_a_query_anchor():
    prompt = as_module._build_user_prompt(
        "q", _retrieved(), _decision(AnchorSource.QUERY, [
            Anchor(key=_HYNIX, name="SK하이닉스", source=AnchorSource.QUERY)]))
    assert "답변 대상: 질문" in prompt


def test_prompt_states_the_answer_target_when_there_is_no_anchor():
    prompt = as_module._build_user_prompt("q", _retrieved(),
                                          _decision(AnchorSource.ANCHORLESS))
    assert "답변 대상: 지정 없음" in prompt


def test_anchorless_prompt_does_not_name_the_workspace_as_the_target():
    """★헤지의 이유가 정확해진다(설계서 §14-6). 전에는 「워크스페이스 기업들을
    대상으로 삼았습니다」였는데, 그것이 §17-3 이 폐기한 앵커 승격의 선언이었다.
    지금은 **대상이 없다**고 말한다."""
    prompt = as_module._build_user_prompt("q", _retrieved(),
                                          _decision(AnchorSource.ANCHORLESS))
    assert "특정 대상을 지정하지 않았습니다" in prompt
    assert "워크스페이스 기업들을 대상으로" not in prompt


def test_query_anchor_prompt_does_not_add_the_anchorless_hedge():
    """★질문이 대상을 지정했으면 그 헤지는 거짓이다 — 붙이면 안 된다."""
    prompt = as_module._build_user_prompt("q", _retrieved(),
                                          _decision(AnchorSource.QUERY))
    assert "지정하지 않" not in prompt


def test_prompt_survives_without_a_decision():
    """★`/retrieve` 쪽 헬퍼 호출과 기존 테스트가 안 깨진다 — 판정이 없으면
    「답변 대상」 줄을 붙이지 않는다."""
    assert "답변 대상" not in as_module._build_user_prompt("q", _retrieved())


def test_system_prompt_tells_the_model_how_to_shape_each_answer():
    """★목록형 지시는 남는다 — 앵커가 없을 때 기업들을 하나의 서사로 엮는 것이
    가장 나쁜 실패다. 절 이름만 [워크스페이스] → [대상 지정 없음] 으로 바뀌었다."""
    assert "답변 대상" in as_module._SYSTEM_PROMPT
    assert "독립적으로 설명" in as_module._SYSTEM_PROMPT
    assert "하나의 이야기로" in as_module._SYSTEM_PROMPT


def test_system_prompt_does_not_make_the_workspace_the_subject(monkeypatch):
    """★**뒤집힌 규칙이다**(최종 설계 §6-2·§19-3).

    전에는 「인사이트는 워크스페이스 기업이 직접 주체이거나 영향 대상이어야
    한다」고 지시했다. 그건 프롬프트 층의 hard filter다 — 워크스페이스 밖 사실을
    답변에서 지우게 만든다. 지금은 **닿으면 밝히고, 밖이라고 빼지 않는다.**
    """
    assert "워크스페이스 밖이라는 이유로 재료를 빼지 않습니다" in as_module._SYSTEM_PROMPT
    assert "직접 주체이거나 직접적인 영향 대상이어야" not in as_module._SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════
#  ask() 가 실제로 그 프롬프트를 보내는가
# ══════════════════════════════════════════════════════════════════════

def _service(decision, retrieved):
    service = MagicMock()
    service.retrieve_for_ask.return_value = (decision, retrieved)
    return service


def test_ask_sends_the_anchorless_shape_to_the_llm(monkeypatch):
    captured = {}

    def _ask_json(system, user, **kw):
        captured["user"] = user
        return {"answer": "답", "evidence_ids": []}

    monkeypatch.setattr(as_module, "ask_json", _ask_json)
    as_module.AnswerService(
        _service(_decision(AnchorSource.ANCHORLESS), _retrieved())).ask(
        AskRequest(question="납품 단가 압박", workspace_keys=list(_WS)))
    assert "답변 대상: 지정 없음" in captured["user"]


def test_ask_sends_workspace_membership_to_the_llm(monkeypatch):
    captured = {}

    def _ask_json(system, user, **kw):
        captured["user"] = user
        return {"answer": "답", "evidence_ids": []}

    monkeypatch.setattr(as_module, "ask_json", _ask_json)
    retrieved = _retrieved(relations=[_relation(_SAMSUNG, "삼성전자", "09999999", "마이크론")])
    as_module.AnswerService(
        _service(_decision(AnchorSource.QUERY), retrieved)).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert "삼성전자=워크스페이스" in captured["user"]
    assert "마이크론=바깥" in captured["user"]


def test_ask_still_hedges_semantic_match_type(monkeypatch):
    """★`match_type` 헤지는 **별개 축**이라 그대로 남는다(설계서 §14-6)."""
    captured = {}

    def _ask_json(system, user, **kw):
        captured["user"] = user
        return {"answer": "답", "evidence_ids": []}

    monkeypatch.setattr(as_module, "ask_json", _ask_json)
    as_module.AnswerService(
        _service(_decision(AnchorSource.ANCHORLESS),
                 _retrieved(match_type=MatchType.SEMANTIC))).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert "SEMANTIC" in captured["user"]
    assert "답변 대상: 지정 없음" in captured["user"]
