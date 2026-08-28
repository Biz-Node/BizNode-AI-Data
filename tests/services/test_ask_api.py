"""POST /ask — HTTP 경계.

라우트는 어댑터다. 조립·화이트리스트 로직은 tests/services/test_answer_service.py 가 본다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import main as main_module
from app.api.main import app
from app.api.schemas import AskResponse, Source

_PATH = "/ask"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _stub_graph(payload: AskResponse):
    """`run_ask` 자리에 끼울 대역.

    ★2026-08-27 — 라우트가 `AnswerService.ask_async()` 대신 **LangGraph** 를
      부르게 바뀌었다(Phase 1). 이 파일이 보는 것은 예나 지금이나 **「라우트가
      로직을 갖지 않고 위임하는가」** 하나라, 위임 대상 이름만 갈아 끼운다.
      검사하는 것은 그대로다.
    """
    def _run_ask(body):
        return payload

    return _run_ask


def test_route_delegates_to_the_graph(client, monkeypatch):
    payload = AskResponse(answer="바꿔치기 답변", sources=[
        Source(evidence_id="ev_1", text="t", source_doc="d", source_type="news")])
    monkeypatch.setattr(main_module, "run_ask", _stub_graph(payload))

    resp = client.post(_PATH, json={"question": "원래질문"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "바꿔치기 답변"
    assert body["sources"][0]["evidence_id"] == "ev_1"
    assert body["failed"] is False


def test_missing_question_is_422(client):
    assert client.post(_PATH, json={}).status_code == 422


def test_blank_question_is_422_not_500(client):
    assert client.post(_PATH, json={"question": "   "}).status_code == 422


def test_workspace_keys_are_accepted(client, monkeypatch):
    payload = AskResponse(answer="답")
    monkeypatch.setattr(main_module, "run_ask", _stub_graph(payload))

    resp = client.post(_PATH, json={"question": "q", "workspace_keys": ["00126380"]})

    assert resp.status_code == 200
