"""POST /retrieve — HTTP 경계.

이 라우트는 **어댑터**다. 여기서 보는 것은 「RetrieveService 를 부르고 그 결과를
그대로 내보내는가」와 「스텁 표시가 사라졌는가」뿐이다. 조립 로직은
tests/services/test_retrieve_service.py 가 본다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import main as main_module
from app.api.main import app
from app.api.schemas import RetrieveResponse

_PATH = "/retrieve"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _stub_service(payload: RetrieveResponse):
    service = MagicMock()

    async def _retrieve_async(body):
        return payload

    service.retrieve_async = _retrieve_async
    return service


def test_returns_200_with_real_material(client):
    resp = client.post(_PATH, json={"question": "삼성전자에 납품하는 기업"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "삼성전자에 납품하는 기업"
    assert body["companies"]


def test_stub_header_is_gone(client):
    """★`X-Stub: true` 가 사라지는 것이 실물화의 완료 신호다."""
    resp = client.post(_PATH, json={"question": "삼성전자에 납품하는 기업"})

    assert "X-Stub" not in resp.headers


def test_stub_header_still_marks_other_stubs(client):
    """스텁 표시 자체를 없앤 게 아니다 — `/news` 는 아직 고정값이다."""
    assert client.get("/news").headers.get("X-Stub") == "true"


def test_missing_question_is_422(client):
    assert client.post(_PATH, json={}).status_code == 422


def test_workspace_keys_are_accepted(client):
    resp = client.post(_PATH, json={"question": "삼성전자에 납품하는 기업",
                                    "workspace_keys": ["00126380", "00301246"]})
    assert resp.status_code == 200


def test_route_delegates_to_the_service(client, monkeypatch):
    """라우트에 로직이 없다 — 서비스가 준 것을 그대로 내보낸다(설계서 Rule 5)."""
    payload = RetrieveResponse(question="바꿔치기")
    monkeypatch.setattr(main_module, "_retrieve_service", _stub_service(payload))

    body = client.post(_PATH, json={"question": "원래질문"}).json()

    assert body["question"] == "바꿔치기"


def test_blank_question_is_422_not_500(client):
    """★사용자 입력 오류를 서버 오류로 보고하지 않는다.

    `AskRequest.question` 에 공백 검증이 없던 동안에는 `SearchRequest.query`
    (min_length=1 + 공백 검증)까지 내려가 ValidationError 가 500 으로 나갔다.
    검증을 경계로 올려 두 계약을 맞췄다.
    """
    assert client.post(_PATH, json={"question": "   "}).status_code == 422
