"""`/ask` LLM 응답의 모양 — **pydantic 판과 손으로 쓴 JSON Schema 두 벌.**

★두 벌인 이유는 **호출 경로가 둘**이기 때문이다. 운영(`app/graph`)은
  `LLMAdapter.structured()` 로 pydantic 모델을 넘기고, 배치
  (`batch/audit/claim_grounding.py`)는 `pipeline.llm.ask_json` 으로 JSON Schema 를
  넘긴다. 같은 모양이어야 하므로 **한 파일에 나란히 둔다** — 떨어뜨려 두면
  한쪽만 필드가 늘어난다.

★pydantic 을 기준으로 삼는다. OpenAI structured output 은
  `additionalProperties: false` 와 **모든 property 가 required** 일 것을 요구하는데,
  손으로 쓴 쪽에는 그 사실이 주석으로만 적혀 있어 필드를 하나 더할 때
  `required` 에 넣는 것을 잊으면 조용히 깨진다.
  `ConfigDict(extra="forbid")` 를 쓰면 pydantic 이 둘 다 자동으로 만든다.
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


# ★`claims` 는 **내부 관측용**이다(Step4a). `AskResponse` 에는 나가지 않는다 —
#   외부 계약을 바꾸기 전에 먼저 분포를 봐야 한다.
#
#   답변이 통짜 문자열이면 「어떤 주장이 어떤 근거에 기대는가」가 데이터로
#   존재하지 않는다. 그래서 화이트리스트(`_sources_from`)가 「지어낸 id」밖에
#   못 잡는다 — 실제로 있는 id 를 **엉뚱한 주장에** 달아도 그대로 통과한다
#   (실측 2026-08-23: 질소 누출 답변이 HBM3E 양산 근거를 인용했다).
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    # strict 모드는 모든 property 가 required 여야 한다.
    "required": ["answer", "evidence_ids", "claims"],
    "additionalProperties": False,
}

# 위 스키마와 **같은 모양의 빈 값**. `pipeline.llm.ask_json` 이 실패했을 때
# 돌려주는 것이라 키가 어긋나면 호출부가 없는 키를 읽는다.
SAFE_FALLBACK = {"answer": "", "evidence_ids": [], "claims": []}
