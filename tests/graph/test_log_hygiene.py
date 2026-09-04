"""`/ask` 로그 — **무엇이 남고 무엇은 절대 안 남는가.**

★한 번 새면 로그 파일에 영구히 남는다. 근거 원문은 뉴스·공시 본문이고, 프롬프트
  에는 그 본문이 통째로 들어 있으며, 답변 본문은 그것을 되풀이한 것이다. 셋 다
  **id 와 길이만** 남긴다.

★반대로 **남아야 하는 것**도 못 박는다. 「최종 근거가 어디서 만들어졌는가」는
  `llm.response` 한 줄이 답한다 — LLM 이 든 id · 화이트리스트를 통과한 id ·
  버려진 id(환각이거나 원문 없음)를 갈라 놓는다. 본문을 뺐다고 id 까지 빠지면
  추적이 안 된다.

★**운영 경로에서 본다**(2026-09-04). 이 검사들은 폐기된 1차(`AnswerService`)에
  붙어 있었다 — 요청이 지나가지 않는 코드의 로그 위생을 재고 있었던 셈이다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import Evidence

_SECRET_TEXT = "이 문장이 로그에 새면 안 된다"


@pytest.fixture
def evidence():
    """★`wired` 가 쓰는 근거를 **비밀 문장으로** 덮어쓴다."""
    return Evidence(evidence_id="ev_rel", text=_SECRET_TEXT,
                    source_doc="20260101000001", source_type="dart")


@pytest.fixture
def llm(monkeypatch):
    """페이로드를 시험마다 갈아끼울 수 있는 어댑터 대역."""
    from app.graph.nodes import answer

    from tests.graph.conftest import FakeLLM

    def _install(payload):
        fake = FakeLLM(payload)
        monkeypatch.setattr(answer, "_llm", fake)
        return fake
    return _install


# ══════════════════════════════════════════════════════════════════════
#  남아야 하는 것
# ══════════════════════════════════════════════════════════════════════


def test_llm_request_log_carries_material_counts(caplog, wired, llm, request_):
    """★재료 규모가 없으면 「무엇을 주고 이 답을 받았나」를 되짚을 수 없다."""
    graph, _ = wired
    llm({"answer": "답", "evidence_ids": [], "claims": []})

    with caplog.at_level("INFO"):
        graph.invoke({"request": request_})

    assert "llm.request" in caplog.text
    assert "match_type=" in caplog.text
    assert "evidence=1" in caplog.text
    assert "prompt_chars=" in caplog.text


def test_llm_response_log_separates_cited_accepted_and_dropped(caplog, wired, llm,
                                                               request_):
    """★이 한 줄이 「최종 근거가 어디서 만들어졌는가」에 답한다."""
    graph, _ = wired
    llm({"answer": "답", "evidence_ids": ["ev_rel", "ev_ghost"], "claims": []})

    with caplog.at_level("INFO"):
        graph.invoke({"request": request_})

    assert "llm.response" in caplog.text
    assert "accepted=['ev_rel']" in caplog.text
    assert "ev_ghost" in caplog.text     # 환각 id 가 버려졌다는 사실이 남아야 한다


def test_llm_failure_is_visible_in_the_log(caplog, wired, llm, request_):
    graph, _ = wired
    llm({"answer": "", "evidence_ids": [], "claims": [], "failed": True})

    with caplog.at_level("INFO"):
        graph.invoke({"request": request_})

    assert "failed=True" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  절대 남으면 안 되는 것
# ══════════════════════════════════════════════════════════════════════


def test_evidence_text_never_reaches_the_log(caplog, wired, llm, request_):
    """★근거 원문은 뉴스·공시 본문이다. id 만 남기고 본문은 절대 찍지 않는다."""
    graph, _ = wired
    llm({"answer": "답", "evidence_ids": ["ev_rel"], "claims": []})

    with caplog.at_level("DEBUG"):
        graph.invoke({"request": request_})

    assert _SECRET_TEXT not in caplog.text
    assert "ev_rel" in caplog.text, "본문을 뺐다고 id 까지 빠지면 추적이 안 된다"


def test_the_prompt_itself_never_reaches_the_log(caplog, wired, llm, request_):
    """★전체 프롬프트에는 시스템 지시문과 근거 본문이 통째로 들어 있다 —
    길이만 남긴다."""
    graph, _ = wired
    llm({"answer": "답", "evidence_ids": [], "claims": []})

    with caplog.at_level("DEBUG"):
        graph.invoke({"request": request_})

    assert "당신은 BizNode" not in caplog.text     # 시스템 프롬프트 첫 구절
    assert "<evidence id=" not in caplog.text      # 근거 블록 델리미터


def test_the_generated_answer_body_never_reaches_the_log(caplog, wired, llm, request_):
    """★답변 본문은 근거 원문을 되풀이한 것이라 같은 위험을 진다."""
    graph, _ = wired
    llm({"answer": f"근거에 따르면 {_SECRET_TEXT}", "evidence_ids": ["ev_rel"],
         "claims": []})

    with caplog.at_level("DEBUG"):
        graph.invoke({"request": request_})

    assert _SECRET_TEXT not in caplog.text
