"""`/ask` 가 앵커 판정에 따라 갈리는가 — **여기서 동작이 실제로 바뀐다.**

단계 0~2 는 부품만 만들었다(스키마 · `names_of` · `find_by_names` ·
`decide_anchor`). 이 파일이 보는 것은 그 부품이 `/ask` 경로에 붙어서

    B-2   해소 실패는 **워크스페이스로 폴백하지 않는다**(설계서 §14-4)
          · LLM 을 부르지 않는다 · `sources=[]` · `failed=false` · HTTP 200
    §16-2 빈 `workspace_keys` 는 **거부한다** — 역시 LLM 미호출

를 지키는가다.

★**`failed` 와 `unresolved` 는 다르다.** `failed` 는 「LLM 호출이 실패했다」이고
  (설계서 §15-4), 여기서는 **애초에 안 불렀다.** 둘을 섞으면 화면이 「서버가
  고장났다」와 「그 기업을 못 찾았다」를 구별하지 못한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.schemas import (Anchor, AnchorSource, AskRequest, MatchType,
                             RetrieveResponse)
from app.services import answer_service as as_module
from app.services.query_understanding import AnchorDecision

_WS = {"00126380": "삼성전자", "00164779": "SK하이닉스"}


def _decision(source, **kw):
    return AnchorDecision(source=source, workspace_names=kw.pop("workspace_names", _WS), **kw)


def _service(decision, retrieved=None):
    """`retrieve_for_ask()` 를 세운다 — `/ask` 는 이 입구만 쓴다."""
    service = MagicMock()
    service.retrieve_for_ask.return_value = (decision, retrieved)
    return service


def _retrieved(**kw):
    return RetrieveResponse(question="q", match_type=MatchType.EXACT, **kw)


@pytest.fixture
def no_llm(monkeypatch):
    """LLM 이 불리면 즉시 터지게 한다 — 「안 불렀다」를 증명하는 방법이다."""
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("LLM 을 부르면 안 된다")

    monkeypatch.setattr(as_module, "ask_json", _boom)
    return calls


# ══════════════════════════════════════════════════════════════════════
#  unresolved — 못 찾았다고 말하고 끝낸다 (설계서 §14-4)
# ══════════════════════════════════════════════════════════════════════

def test_unresolved_does_not_call_the_llm(no_llm):
    decision = _decision(AnchorSource.UNRESOLVED, named="TSMC")
    got = as_module.AnswerService(_service(decision)).ask(AskRequest(
        question="TSMC는 어떤가?", workspace_keys=list(_WS)))
    assert no_llm == []
    assert got.anchor_source is AnchorSource.UNRESOLVED


def test_unresolved_is_not_a_failure(no_llm):
    """★`failed=false` 다 — `failed` 는 「LLM 호출이 실패했다」는 뜻이고
    여기서는 애초에 안 불렀다(설계서 §14-4)."""
    got = as_module.AnswerService(
        _service(_decision(AnchorSource.UNRESOLVED, named="TSMC"))).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert got.failed is False
    assert got.sources == []


def test_unresolved_says_what_it_could_not_find(no_llm):
    got = as_module.AnswerService(
        _service(_decision(AnchorSource.UNRESOLVED, named="TSMC"))).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert "TSMC" in got.answer


def test_unresolved_suggests_the_workspace_but_does_not_answer_about_it(no_llm):
    """★대안은 **「제안」까지만**이다(설계서 §14-4) — 이름을 보여줄 뿐 그
    기업들에 대해 답하지 않는다. 답하면 그게 곧 조용한 오답이다."""
    got = as_module.AnswerService(
        _service(_decision(AnchorSource.UNRESOLVED, named="TSMC"))).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert "삼성전자" in got.answer and "SK하이닉스" in got.answer
    assert got.sources == []


def test_unresolved_message_is_deterministic(no_llm):
    """★문구는 이름만으로 조립된다 — 같은 입력에 같은 문장이 나와야 한다."""
    service = as_module.AnswerService(
        _service(_decision(AnchorSource.UNRESOLVED, named="TSMC")))
    request = AskRequest(question="q", workspace_keys=list(_WS))
    assert service.ask(request).answer == service.ask(request).answer


# ══════════════════════════════════════════════════════════════════════
#  빈 workspace_keys — 거부한다 (설계서 §16-2)
# ══════════════════════════════════════════════════════════════════════

def test_empty_workspace_is_rejected_without_calling_the_llm(no_llm):
    """★워크스페이스가 비면 「무엇에 대한 인사이트인가」가 정해지지 않는다."""
    service = MagicMock()
    got = as_module.AnswerService(service).ask(
        AskRequest(question="납품 단가 압박", workspace_keys=[]))
    assert no_llm == []
    assert got.failed is False
    assert got.sources == []
    assert got.anchor_source is AnchorSource.UNRESOLVED


def test_empty_workspace_does_not_even_search():
    """★검색도 하지 않는다 — 재료를 모을 출발점이 없다."""
    service = MagicMock()
    as_module.AnswerService(service).ask(AskRequest(question="q", workspace_keys=[]))
    service.retrieve_for_ask.assert_not_called()


def test_empty_workspace_message_differs_from_unresolved(no_llm):
    """★사용자가 할 일이 다르다 — 하나는 기업 추가, 하나는 다른 이름으로 질문."""
    empty = as_module.AnswerService(MagicMock()).ask(
        AskRequest(question="q", workspace_keys=[]))
    unresolved = as_module.AnswerService(
        _service(_decision(AnchorSource.UNRESOLVED, named="TSMC"))).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert empty.answer != unresolved.answer


# ══════════════════════════════════════════════════════════════════════
#  query · workspace — 평소대로 답하되 anchor_source 를 싣는다
# ══════════════════════════════════════════════════════════════════════

def test_query_anchor_answers_normally(monkeypatch):
    monkeypatch.setattr(as_module, "ask_json",
                        lambda *a, **k: {"answer": "답변", "evidence_ids": []})
    decision = _decision(AnchorSource.QUERY, anchors=[
        Anchor(key="00164779", name="SK하이닉스", source=AnchorSource.QUERY)])
    got = as_module.AnswerService(_service(decision, _retrieved())).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert got.answer == "답변"
    assert got.anchor_source is AnchorSource.QUERY


def test_workspace_anchor_answers_normally(monkeypatch):
    monkeypatch.setattr(as_module, "ask_json",
                        lambda *a, **k: {"answer": "답변", "evidence_ids": []})
    got = as_module.AnswerService(
        _service(_decision(AnchorSource.WORKSPACE), _retrieved())).ask(
        AskRequest(question="납품 단가 압박", workspace_keys=list(_WS)))
    assert got.anchor_source is AnchorSource.WORKSPACE


def test_anchor_source_survives_an_llm_failure(monkeypatch):
    """★LLM 이 죽어도 「무엇을 대상으로 답하려 했나」는 남는다 — 그건 서버가
    아는 결정론적 값이라 LLM 과 무관하다(설계서 §14-3)."""
    monkeypatch.setattr(as_module, "ask_json", lambda *a, **k: as_module._SAFE_FALLBACK)
    got = as_module.AnswerService(
        _service(_decision(AnchorSource.WORKSPACE), _retrieved())).ask(
        AskRequest(question="q", workspace_keys=list(_WS)))
    assert got.failed is True
    assert got.anchor_source is AnchorSource.WORKSPACE
