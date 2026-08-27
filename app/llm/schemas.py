"""LLM 구조화 응답 스키마 — `answer_service._ANSWER_SCHEMA` 의 pydantic 판.

★손으로 쓴 JSON Schema 를 그대로 두지 않는 이유는 **strict 모드의 요구사항을
  사람이 계속 기억해야 하기 때문**이다. OpenAI structured output 은
  `additionalProperties: false` 와 **모든 property 가 required** 일 것을 요구하는데,
  `_ANSWER_SCHEMA` 에는 그 사실이 주석으로만 적혀 있어 필드를 하나 더할 때
  `required` 에 넣는 것을 잊으면 조용히 깨진다.

  `ConfigDict(extra="forbid")` 를 쓰면 pydantic 이 둘 다 자동으로 만든다 —
  `Optional` 이 아닌 필드는 전부 `required` 로 나가고 `additionalProperties: false`
  가 붙는다. 실측으로 `_ANSWER_SCHEMA` 와 같은 모양임을 확인했다.

★**필드를 늘리지 않았다.** Phase 1 은 실행 담당만 옮기는 작업이고, 스키마가
  바뀌면 LLM 출력이 바뀐다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnswerClaim(BaseModel):
    """답변 문장 하나와 그것이 든 근거 id — `claim_check` 가 받는 모양."""

    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class AskAnswer(BaseModel):
    """`/ask` 의 LLM 응답."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence_ids: list[str]
    claims: list[AnswerClaim]
