"""`pipeline/llm.py` — **실패를 통과와 구별한다.**

★이 모듈의 존재 이유는 「예외를 잡으면 **무조건** `failed=True` 를 붙인다」 하나다.
  표시가 없으면 LLM 호출 실패가 「검사 통과」와 똑같이 보이고, `*_checked_at` 이
  찍혀 다음 실행이 건너뛴다 — 커버율은 100% 로 뜨는데 실제로는 안 본 것이다.

★배치 22곳이 이 함수 위에 있다. 전에는 이 규약을 **다른 모듈의 테스트가 우연히**
  지나가며 밟고 있었는데, 그 모듈이 폐기되면서 실패 경로가 통째로 무방비가 됐다
  (2026-09-04). 여기서 직접 못 박는다.

★**진짜 API 를 부르지 않는다.** 클라이언트 자리에 대역을 세운다.
"""

from __future__ import annotations

import json

import pytest

from pipeline import llm as sut

_SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}},
           "required": ["verdict"], "additionalProperties": False}
_FALLBACK = {"verdict": "supported"}


class _FakeClient:
    """`chat.completions.create` 하나만 흉내 낸다."""

    def __init__(self, *, content=None, raises=None):
        self.content, self.raises = content, raises
        self.seen: dict = {}
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.seen = kwargs
                if outer.raises is not None:
                    raise outer.raises
                message = type("M", (), {"content": outer.content})()
                choice = type("C", (), {"message": message})()
                return type("R", (), {"choices": [choice]})()

        self.chat = type("Chat", (), {"completions": _Completions()})()


@pytest.fixture
def client(monkeypatch):
    def _install(**kw):
        fake = _FakeClient(**kw)
        monkeypatch.setattr(sut, "get_client", lambda: fake)
        return fake
    return _install


# ══════════════════════════════════════════════════════════════════════
#  실패 — ★표시가 반드시 붙는다
# ══════════════════════════════════════════════════════════════════════


def test_a_failed_call_is_marked_instead_of_looking_like_a_pass(client):
    """★이 파일의 이유. 표시가 없으면 실패가 「검사 통과」로 읽힌다."""
    client(raises=RuntimeError("연결 끊김"))

    got = sut.ask_json("s", "u", schema=_SCHEMA, name="n", fallback=_FALLBACK)

    assert got["failed"] is True
    assert got["verdict"] == "supported", "fallback 값은 그대로 실려야 한다"
    assert "연결 끊김" in got["reason"], "무엇 때문에 실패했는지 남아야 한다"


def test_malformed_json_is_a_failure_not_a_crash(client):
    """★모델이 JSON 이 아닌 것을 뱉어도 **호출부는 죽지 않는다.**"""
    client(content="이건 JSON 이 아니다")

    got = sut.ask_json("s", "u", schema=_SCHEMA, name="n", fallback=_FALLBACK)

    assert got["failed"] is True


def test_the_caller_chooses_which_way_is_safe(client):
    """★`fallback` 은 호출부가 정한다 — 검사면 「통과」쪽, 삭제 판정이면
    「지우지 않음」쪽. 어느 쪽이든 `failed` 가 함께 붙는다."""
    client(raises=RuntimeError("x"))

    got = sut.ask_json("s", "u", schema=_SCHEMA, name="n",
                       fallback={"verdict": "unfounded", "delete": False})

    assert got["delete"] is False and got["failed"] is True


# ══════════════════════════════════════════════════════════════════════
#  통과 — 표시를 붙이지 않는다
# ══════════════════════════════════════════════════════════════════════


def test_a_good_response_carries_no_failure_mark(client):
    client(content=json.dumps({"verdict": "unfounded"}))

    got = sut.ask_json("s", "u", schema=_SCHEMA, name="n", fallback=_FALLBACK)

    assert got == {"verdict": "unfounded"}
    assert "failed" not in got


def test_the_schema_goes_out_in_strict_mode(client):
    """★`strict` 가 빠지면 모델이 형식을 어겨도 그대로 돌아온다."""
    fake = client(content=json.dumps({"verdict": "supported"}))

    sut.ask_json("s", "u", schema=_SCHEMA, name="grounding", fallback=_FALLBACK)

    sent = fake.seen["response_format"]["json_schema"]
    assert sent["strict"] is True
    assert sent["name"] == "grounding"
    assert sent["schema"] is _SCHEMA


def test_judgements_are_reproducible_by_default(client):
    """★temperature 기본 0 — 판정이 실행마다 흔들리면 대조가 성립하지 않는다."""
    fake = client(content=json.dumps({"verdict": "supported"}))

    sut.ask_json("s", "u", schema=_SCHEMA, name="n", fallback=_FALLBACK)

    assert fake.seen["temperature"] == 0.0
    assert fake.seen["model"] == sut.DEFAULT_MODEL


# ══════════════════════════════════════════════════════════════════════
#  클라이언트 — 지연 생성 · 프로세스당 하나
# ══════════════════════════════════════════════════════════════════════


def test_the_client_is_made_once_and_shared(monkeypatch):
    """★파이프라인 여섯 곳이 각자 사본을 들고 있던 자리다(2026-09-04 통합).
    두 번째 호출부터는 같은 것을 준다."""
    made = []

    class _OpenAI:
        def __init__(self, **kw):
            made.append(kw)

    monkeypatch.setattr(sut, "_client", None)
    monkeypatch.setattr(sut.openai, "OpenAI", _OpenAI)

    first, second = sut.get_client(), sut.get_client()

    assert first is second
    assert len(made) == 1
