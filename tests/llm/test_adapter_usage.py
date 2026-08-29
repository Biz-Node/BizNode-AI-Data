"""어댑터 — `include_raw=True` 를 켜고도 **실패가 통과와 구별되는가.**

★이 파일이 막는 회귀가 이 작업에서 가장 위험한 것이다.

  토큰 사용량을 꺼내려면 `with_structured_output(..., include_raw=True)` 가
  필요하다. 그런데 그 플래그는 동작을 하나 바꾼다 — **파싱 실패가 예외가 아니라
  값으로** 돌아온다(`{"raw": ..., "parsed": None, "parsing_error": <예외>}`).

  기존 코드는 `try/except` 로 예외를 잡아 `failed=True` 를 붙였다. 플래그만 켜고
  그 값을 안 접으면, **형식이 깨진 응답이 「통과」로 보인다.** 호출부는
  `result.get("answer")` 로 빈 문자열을 받고, `verify_sources` 는 그걸 실패로
  읽지만 `reason` 이 없어 **왜 비었는지 되짚을 수 없다.**

  그게 정확히 `app/llm/adapter.py` 와 `pipeline/llm.py` 가 존재하는 이유다 —
  「같은 20줄이 복사되며 실패 표시가 5곳에서 빠졌고, 그때 LLM 호출 실패가
  검사 통과와 똑같이 보였다」. 그러니 여기서 묶는다.
"""

from __future__ import annotations

import langchain_openai
import pytest
from pydantic import BaseModel

from app.core import observe
from app.llm.adapter import LangChainAdapter


class _Schema(BaseModel):
    answer: str


class _Reply:
    def __init__(self, model="모델-A", usage=None):
        self.response_metadata = {"model_name": model}
        self.usage_metadata = usage or {
            "input_tokens": 120, "output_tokens": 8,
            "output_token_details": {"reasoning": 0}}


class _Runnable:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeChat:
    """`with_structured_output` 이 무엇으로 불렸는지도 함께 붙잡는다."""

    def __init__(self, result, **kwargs):
        self._result = result
        self.kwargs = kwargs
        self.structured_kwargs: dict = {}

    def with_structured_output(self, schema, **kwargs):
        self.structured_kwargs = kwargs
        return _Runnable(self._result)


@pytest.fixture
def adapter(monkeypatch):
    """결과를 정해 주면 그걸 돌려주는 어댑터를 만든다."""

    def _build(result):
        made: list[_FakeChat] = []

        def _factory(**kwargs):
            chat = _FakeChat(result, **kwargs)
            made.append(chat)
            return chat

        monkeypatch.setattr(langchain_openai, "ChatOpenAI", _factory)
        got = LangChainAdapter()
        return got, made

    return _build


_FALLBACK = {"answer": "", "evidence_ids": [], "claims": []}


def _call(got: LangChainAdapter) -> dict:
    return got.structured("시스템", "사용자", schema=_Schema,
                          name="ask_answer", fallback=_FALLBACK)


# ══════════════════════════════════════════════════════════════════
#  ① 정상 — 파싱된 값이 그대로 dict 로 나온다
# ══════════════════════════════════════════════════════════════════


def test_a_parsed_reply_comes_back_as_a_plain_dict(adapter):
    got, _ = adapter({"raw": _Reply(), "parsed": _Schema(answer="답"),
                      "parsing_error": None})
    result = _call(got)
    assert result == {"answer": "답"}
    assert "failed" not in result


def test_include_raw_is_actually_on(adapter):
    """★꺼지면 사용량을 꺼낼 방법이 사라진다. 플래그 자체를 묶어 둔다."""
    got, made = adapter({"raw": _Reply(), "parsed": _Schema(answer="답"),
                         "parsing_error": None})
    _call(got)
    assert made[0].structured_kwargs["include_raw"] is True
    assert made[0].structured_kwargs["strict"] is True
    assert made[0].structured_kwargs["method"] == "json_schema"


# ══════════════════════════════════════════════════════════════════
#  ② ★파싱 실패 — 예외가 아니라 값으로 온다. 반드시 접혀야 한다
# ══════════════════════════════════════════════════════════════════


def test_a_parsing_error_becomes_a_marked_failure(adapter):
    got, _ = adapter({"raw": _Reply(), "parsed": None,
                      "parsing_error": ValueError("형식이 깨졌다")})
    result = _call(got)

    assert result["failed"] is True, \
        "파싱 실패가 통과로 보인다 — 이 어댑터의 존재 이유가 깨졌다"
    assert result["answer"] == ""
    assert "형식이 깨졌다" in result["reason"]


def test_a_missing_parsed_value_is_also_a_failure(adapter):
    """`parsing_error` 가 비어 있어도 `parsed` 가 없으면 실패다 — 둘 중 하나만
    보면 라이브러리가 모양을 바꿀 때 조용히 새는 길이 남는다."""
    got, _ = adapter({"raw": _Reply(), "parsed": None, "parsing_error": None})
    assert _call(got)["failed"] is True


def test_an_api_exception_is_still_marked(adapter):
    """기존 규약 그대로 — 호출 자체가 죽어도 `failed` 가 붙는다."""
    got, _ = adapter(RuntimeError("401"))
    result = _call(got)
    assert result["failed"] is True
    assert "LLM 호출 실패" in result["reason"]


# ══════════════════════════════════════════════════════════════════
#  ③ 사용량 — 실패한 호출도 토큰은 썼다
# ══════════════════════════════════════════════════════════════════


def test_usage_is_recorded_on_success(adapter):
    got, _ = adapter({"raw": _Reply(), "parsed": _Schema(answer="답"),
                      "parsing_error": None})
    with observe.observing() as seen:
        _call(got)
    assert seen.llm_input_tokens["모델-A"] == 120
    assert seen.llm_output_tokens["모델-A"] == 8


def test_usage_is_recorded_even_when_parsing_failed(adapter):
    """★파싱이 깨졌어도 **토큰은 이미 나갔다.** 안 세면 비용이 실제보다 작게
    잡히고, 하필 실패가 잦은 실행일수록 더 작게 잡힌다."""
    got, _ = adapter({"raw": _Reply(), "parsed": None,
                      "parsing_error": ValueError("깨짐")})
    with observe.observing() as seen:
        _call(got)
    assert seen.llm_input_tokens["모델-A"] == 120
