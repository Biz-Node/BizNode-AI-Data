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


def _stub_service(payload: AskResponse):
    from unittest.mock import MagicMock

    service = MagicMock()

    async def _ask_async(body):
        return payload

    service.ask_async = _ask_async
    return service


def test_route_delegates_to_the_service(client, monkeypatch):
    payload = AskResponse(answer="바꿔치기 답변", sources=[
        Source(evidence_id="ev_1", text="t", source_doc="d", source_type="news")])
    monkeypatch.setattr(main_module, "_answer_service", _stub_service(payload))

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
    monkeypatch.setattr(main_module, "_answer_service", _stub_service(payload))

    resp = client.post(_PATH, json={"question": "q", "workspace_keys": ["00126380"]})

    assert resp.status_code == 200
