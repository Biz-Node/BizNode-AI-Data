"""LLM 어댑터 — 구조화 응답 하나를 받는다. **실패를 통과와 구별한다.**

★이 모듈의 존재 이유는 `pipeline/llm.py` 와 같다 — **실패에 반드시 표시가
  붙게 하는 것**이다. 저쪽 docstring 이 적어 둔 사고가 그 근거다: 같은 20줄이
  배치 8곳에 복사되면서 `failed: True` 표시가 **3곳에서만** 지켜졌고, 표시가
  없는 5곳에서는 **LLM 호출 실패가 「검사 통과」와 똑같이 보였다.**

★LangChain 의 `with_structured_output()` 은 **실패하면 예외를 던진다**(실측
  2026-08-27: 잘못된 키로 부르면 `AuthenticationError` 가 그대로 올라온다).
  그대로 쓰면 `/ask` 가 500 을 내는데, 설계서 §13-3 은 **200 + 고정 문구 +
  `failed=True`** 를 요구한다. 그 갭을 여기서 메운다 — 예외를 잡으면 **무조건**
  `fallback | {"failed": True, "reason": ...}` 를 돌려준다. 호출부가 잊을 수 없다.

    adapter.structured(system, user, schema=AskAnswer, name="ask_answer",
                       fallback={"answer": "", "evidence_ids": [], "claims": []})

반환은 **`dict`** 다. `pipeline.llm.ask_json()` 과 같은 모양이라 호출부가
`result.get("answer")` 를 그대로 쓸 수 있다 — 교체하면서 호출부를 고치지 않는다.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, Type

from pydantic import BaseModel

from app.core import observe

# ★**모델 상수를 여기 두지 않는다**(2026-08-29). 전에는 `DEFAULT_MODEL` 이
#   여기 있었고 `agent_loop._model()` 이 그걸 집어 가, Agent 와 답변이 **한
#   노브**였다 — 그게 「Agent 만 바꿔 도구 선택 분산을 본다」를 막고 있었다.
#   이제 `app/core/config.py` 의 `ANSWER_MODEL` 을 **호출 시점에** 읽는다.
#
# ★`pipeline.llm.DEFAULT_MODEL` 과는 이제 **다른 노브**다. 저쪽은 배치의
#   판정·분류용이고 여기는 `/ask` 의 답변용이라, 같이 움직일 이유가 없다.


class LLMAdapter(Protocol):
    """구조화 응답 하나를 받는 계약. **예외를 던지지 않는다.**"""

    def structured(
        self,
        system: str,
        user: str,
        *,
        schema: Type[BaseModel],
        name: str,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        """`schema` 모양의 dict. 실패하면 `fallback | {"failed": True, "reason": ...}`.

        ★`fallback` 은 **호출부가 안전한 쪽으로** 정한다 — 어느 쪽이든 `failed` 가
          함께 붙어서, 호출부가 「실패인데 통과로 읽는」 일이 없다.
        """
        ...


class LangChainAdapter(LLMAdapter):
    """`ChatOpenAI.with_structured_output()` 구현체.

    ★클라이언트를 **지연 생성**한다 — `pipeline/llm.py` 와 같은 이유로, import
      시점에 키가 없어도 모듈이 떠야 한다(테스트·배치가 키 없이 import 한다).
    """

    def __init__(self, *, model: Optional[str] = None,
                 temperature: Optional[Any] = None) -> None:
        # ★`None` 은 **「config 를 따른다」**다. 값을 주면 그 값이 이긴다 —
        #   테스트가 노브와 무관하게 한 모델을 고정할 수 있어야 한다.
        self._model = model
        # ★0 이 규약이다. 판정은 재현 가능해야 한다(`pipeline.llm` 과 같은 말).
        #   기본값은 `config.ANSWER_TEMPERATURE` 이고 그 기본이 `"0.0"` 이다 —
        #   0 을 거부하는 모델이 있어 **비울 수 있게** 열어 둔 것뿐이다.
        self._temperature = temperature
        self._client: Optional[Any] = None

    def _chat(self):
        if self._client is None:
            from langchain_openai import ChatOpenAI

            # ★**호출 시점에** 읽는다. import 시점에 묶으면 테스트가 노브를
            #   갈아끼울 수 없고, 「어느 모델로 돌았나」가 프로세스 시작
            #   시각에 고정돼 버린다.
            from app.core import config

            temperature = (self._temperature if self._temperature is not None
                           else config.ANSWER_TEMPERATURE)
            self._client = ChatOpenAI(
                model=self._model or config.ANSWER_MODEL,
                api_key=config.OPENAI_API_KEY,
                **config.temperature_kwargs(temperature),
            )
        return self._client

    def structured(
        self,
        system: str,
        user: str,
        *,
        schema: Type[BaseModel],
        name: str,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            # `method="json_schema"` + `strict=True` 가 OpenAI structured output 이다
            # — `pipeline.llm.ask_json()` 이 `response_format={"type":"json_schema",
            # ..., "strict": True}` 로 부르던 것과 같은 경로다.
            #
            # ★스키마 이름은 pydantic 클래스 이름(`AskAnswer`)에서 나온다. 예전
            #   `name="ask_answer"` 와 문자열이 다르지만 **요청 메타데이터일 뿐**
            #   이라 응답에는 영향이 없다. `name` 은 실패 사유에만 남긴다.
            # ★`include_raw=True` — **토큰 사용량을 꺼내려고** 켰다. 끄면
            #   파싱된 객체만 와서 이 호출이 얼마를 썼는지 알 방법이 없다.
            runnable = self._chat().with_structured_output(
                schema, method="json_schema", strict=True, include_raw=True)
            got = runnable.invoke([("system", system), ("user", user)])
        except Exception as exc:
            # ★여기가 이 어댑터의 존재 이유다. 실패는 **반드시** 표시가 붙는다.
            #   문구는 `pipeline.llm.ask_json()` 과 맞춰 둔다 — 로그를 같이 읽는다.
            return {**fallback, "failed": True,
                    "reason": f"LLM 호출 실패({name}) {exc!r}"}

        observe.record_llm_message(got.get("raw"))

        # ★**`include_raw=True` 는 파싱 실패를 예외가 아니라 값으로 돌려준다.**
        #   여기서 접지 않으면 이 모듈의 존재 이유가 그대로 깨진다 — 형식이
        #   깨진 응답이 「통과」로 보이고, 호출부는 `result.get("answer")` 로
        #   빈 값을 받아 **실패를 성공과 구별하지 못한다.** 그게 정확히 이
        #   파일이 막으려고 생긴 사고다.
        parsing_error, parsed = got.get("parsing_error"), got.get("parsed")
        if parsing_error is not None or parsed is None:
            return {**fallback, "failed": True,
                    "reason": f"LLM 응답 파싱 실패({name}) {parsing_error!r}"}

        # ★dict 로 되돌린다. 호출부는 `pipeline.llm.ask_json()` 시절 그대로
        #   `result.get("answer")` 를 쓴다 — 교체가 호출부에 보이지 않아야 한다.
        if isinstance(parsed, BaseModel):
            return parsed.model_dump()
        # 스키마를 dict 로 준 경우(계약상 허용) — 이미 dict 다.
        return dict(parsed or {})
