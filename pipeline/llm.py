"""LLM 호출 한 곳 — 스키마 강제 + **실패를 통과와 구별**.

★왜 모듈로 뺐나 (2026-08-01)

같은 20줄이 배치 8곳에 복사돼 있었다. 줄 수보다 나쁜 건 **실패 처리가 제각각**이
된 것이다. 방법서 §16 ④에 「조용한 실패를 막는다」를 원칙으로 적어 놨는데
복사할 때마다 빠졌다 — 7곳 중 3곳만 지키고 있었다:

    freshness · grounding_fulltext · event_types      `failed: True` 표시 ✓
    relations · event_names · misclassified_edges     표시 없음 ✗
    subtype_taxonomy                                  표시 없음 ✗

표시가 없으면 **LLM 호출 실패가 「검사 통과」와 똑같이 보인다.** 그러면
`*_checked_at`이 찍혀 다음 실행이 건너뛰고, 그 엣지는 영영 검사되지 않는다.
커버율은 100%로 뜨는데 실제로는 안 본 것이다 — 가장 나쁜 종류의 거짓 보고다.

여기서는 예외를 잡으면 **무조건** `failed=True`를 넣는다. 호출부가 잊을 수 없다.

    from pipeline.llm import ask_json

    v = ask_json(_SYSTEM, user, schema=_SCHEMA, name="grounding",
                 fallback={"verdict": "supported", "actual": "", "reason": ""})
    if v.get("failed"):
        ...   # 기록하지 않고 다음 실행에서 재시도
"""

from __future__ import annotations

import json
from typing import Any, Optional

import openai

from app.core.config import OPENAI_API_KEY

# 판정·분류·요약의 기본 모델. 추출(gpt-4o)은 품질이 그래프를 좌우해 따로 쓴다.
DEFAULT_MODEL = "gpt-4o-mini"

_client: Optional[openai.OpenAI] = None


def get_client() -> openai.OpenAI:
    """지연 생성 — import 시점에 키가 없어도 모듈이 뜬다."""
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def ask_json(
    system: str,
    user: str,
    *,
    schema: dict[str, Any],
    name: str,
    fallback: dict[str, Any],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """구조화 응답을 받는다. 실패하면 `fallback | {"failed": True, "reason": ...}`.

    · `schema`는 OpenAI json_schema(strict). 모델이 형식을 어기면 스스로 재시도한다.
    · `fallback`은 **호출부가 안전한 쪽으로** 정한다 — 검사라면 「통과」쪽,
      삭제 판정이라면 「지우지 않음」쪽. 어느 쪽이든 `failed`가 함께 붙는다.
    · temperature 기본 0 — 판정은 재현 가능해야 한다.
    """
    try:
        resp = get_client().chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": name, "schema": schema,
                                             "strict": True}},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        # ★여기가 이 모듈의 존재 이유다. 실패는 **반드시** 표시가 붙는다.
        return {**fallback, "failed": True, "reason": f"LLM 호출 실패 {exc!r}"}
