"""모델 노브 — Agent 와 답변이 **다른 모델을 받는가.**

★이 파일이 잡는 회귀는 하나다. 전에는 `app/llm/adapter.DEFAULT_MODEL` **하나**를
  Agent 루프와 답변 생성이 같이 썼다. 그래서 「Agent 만 바꿔 도구 선택 분산을
  본다」가 구조적으로 불가능했다 — 노브가 하나면 바꾸는 순간 답변 모델도 같이
  움직이고, 평가셋 점수 차이를 **어느 쪽에 귀속시킬지 못 가른다.** 임베딩
  드리프트·링 계측기와 **같은 종류의 문제**라 같은 방식으로 묶어 둔다.

★**LLM 을 부르지 않는다.** `ChatOpenAI` 를 가짜로 물려 **받은 인자만** 본다.
  두 호출부 다 함수 안에서 지연 import 하므로 모듈 속성 교체가 그대로 먹는다.
"""

from __future__ import annotations

import langchain_openai
import pytest

from app.core import config
from app.graph.nodes import agent_loop
from app.llm.adapter import LangChainAdapter


class _FakeChat:
    """받은 인자를 그대로 들고 있는다. `bind_tools` 는 자기를 돌려준다."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def bind_tools(self, tools):
        return self


@pytest.fixture
def made(monkeypatch) -> list[dict]:
    """만들어진 `ChatOpenAI` 의 인자들. 순서대로 쌓인다."""
    seen: list[dict] = []

    def _factory(**kwargs):
        seen.append(kwargs)
        return _FakeChat(**kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _factory)
    # ★Agent 쪽 전역을 비운다 — 앞선 테스트가 만들어 둔 것을 재사용하면
    #   이 테스트는 아무것도 안 보게 된다.
    agent_loop.bind_chat(None)
    return seen


# ══════════════════════════════════════════════════════════════════
#  ① 두 노브가 갈려 있는가 — 이 파일의 이유
# ══════════════════════════════════════════════════════════════════


def test_agent_and_answer_take_different_models(monkeypatch, made):
    monkeypatch.setattr(config, "AGENT_MODEL", "모델-에이전트")
    monkeypatch.setattr(config, "ANSWER_MODEL", "모델-답변")

    agent_loop._model()
    LangChainAdapter()._chat()

    assert [k["model"] for k in made] == ["모델-에이전트", "모델-답변"], \
        "Agent 와 답변이 같은 모델을 받았다 — 노브가 다시 하나로 붙었다"


def test_answer_model_change_does_not_move_the_agent(monkeypatch, made):
    """★답변만 바꿨는데 Agent 가 따라 움직이면 2단계 측정이 오염된다."""
    monkeypatch.setattr(config, "AGENT_MODEL", "고정")
    monkeypatch.setattr(config, "ANSWER_MODEL", "바뀐-답변-모델")

    agent_loop._model()

    assert made[0]["model"] == "고정"


# ══════════════════════════════════════════════════════════════════
#  ② temperature — 0 을 지키되, 비울 수 있어야 한다
# ══════════════════════════════════════════════════════════════════


def test_temperature_is_sent_when_set(monkeypatch, made):
    monkeypatch.setattr(config, "AGENT_TEMPERATURE", "0.0")
    agent_loop._model()
    assert made[0]["temperature"] == 0.0


def test_blank_temperature_is_not_sent_at_all(monkeypatch, made):
    """★gpt-5.6 계열은 `temperature=0.0` 을 **거부한다**(400). 빈 값이면 인자
    자체가 나가지 않아야 그 모델로 돌아간다."""
    monkeypatch.setattr(config, "AGENT_TEMPERATURE", "")
    monkeypatch.setattr(config, "ANSWER_TEMPERATURE", "   ")

    agent_loop._model()
    LangChainAdapter()._chat()

    assert "temperature" not in made[0], "Agent 에 temperature 가 실려 나갔다"
    assert "temperature" not in made[1], "답변에 temperature 가 실려 나갔다"


@pytest.mark.parametrize("value", ["0.0", "0", 0.0, 0])
def test_zero_is_never_mistaken_for_blank(value):
    """★파이썬에서 `0.0` 은 거짓이다. `if not value` 로 쓰면 **0 을 지정한
    실행이 조용히 모델 기본값(1)으로** 돈다 — 재현성을 지키려고 둔 값이 정확히
    반대로 뒤집힌다. 그 함정을 여기서 묶는다."""
    assert config.temperature_kwargs(value) == {"temperature": 0.0}


@pytest.mark.parametrize("value", ["", "  ", None])
def test_blank_forms_all_mean_do_not_send(value):
    assert config.temperature_kwargs(value) == {}


# ══════════════════════════════════════════════════════════════════
#  ②-2 추론 세기 — ★전송 경로와 함께 움직인다
# ══════════════════════════════════════════════════════════════════


def test_none_effort_stays_on_chat_completions():
    """`none` 이면 추론을 끄고 **기본 경로 그대로** 간다."""
    assert config.reasoning_kwargs("none") == {"reasoning_effort": "none"}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_real_effort_forces_the_responses_api(effort):
    """★chat.completions 는 function tools 와 추론을 **같이 못 쓴다.** 노브를
    둘로 두면 한쪽만 바꿔 400 을 맞는 조합이 생기므로 함께 낸다."""
    assert config.reasoning_kwargs(effort) == {
        "reasoning_effort": effort, "use_responses_api": True}


@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_effort_sends_nothing(value):
    """★`gpt-4o-mini` 같은 비추론 모델은 `reasoning_effort` 자체를 거부한다 —
    모델을 되돌릴 때 이 값도 비울 수 있어야 한다."""
    assert config.reasoning_kwargs(value) == {}


def test_the_agent_gets_the_effort_knob(monkeypatch, made):
    monkeypatch.setattr(config, "AGENT_REASONING_EFFORT", "none")
    agent_loop._model()
    assert made[0]["reasoning_effort"] == "none"
    assert "use_responses_api" not in made[0]


def test_turning_reasoning_on_switches_transport_in_one_move(monkeypatch, made):
    monkeypatch.setattr(config, "AGENT_REASONING_EFFORT", "low")
    agent_loop._model()
    assert made[0]["reasoning_effort"] == "low"
    assert made[0]["use_responses_api"] is True,         "추론만 켜고 경로를 안 바꾸면 도구 호출이 400 으로 죽는다"


# ══════════════════════════════════════════════════════════════════
#  ②-3 기본값 — ★모델과 temperature 는 **함께** 움직여야 한다
# ══════════════════════════════════════════════════════════════════


def test_defaults_are_a_consistent_combination():
    """★기본 모델이 gpt-5.6 계열이면 temperature 는 **비어 있어야** 한다.
    한쪽만 바꾸면 `/ask` 가 첫 호출에서 400 으로 죽는다 — 이 조합이 어긋난 채
    커밋되는 것을 막는다."""
    if config.AGENT_MODEL.startswith("gpt-5"):
        assert config.temperature_kwargs(config.AGENT_TEMPERATURE) == {},             f"{config.AGENT_MODEL} 은 temperature 를 거부한다"
    if config.ANSWER_MODEL.startswith("gpt-5"):
        assert config.temperature_kwargs(config.ANSWER_TEMPERATURE) == {},             f"{config.ANSWER_MODEL} 은 temperature 를 거부한다"
    if not config.AGENT_MODEL.startswith("gpt-5"):
        assert config.reasoning_kwargs(config.AGENT_REASONING_EFFORT) == {},             f"{config.AGENT_MODEL} 은 reasoning_effort 를 거부한다"


# ══════════════════════════════════════════════════════════════════
#  ③ 명시 인자가 노브를 이긴다 — 테스트·배치가 고정할 수 있어야 한다
# ══════════════════════════════════════════════════════════════════


def test_explicit_argument_beats_the_knob(monkeypatch, made):
    monkeypatch.setattr(config, "ANSWER_MODEL", "노브")
    LangChainAdapter(model="명시", temperature=0.25)._chat()
    assert made[0]["model"] == "명시"
    assert made[0]["temperature"] == 0.25
