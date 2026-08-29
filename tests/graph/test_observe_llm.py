"""관측 — **LLM 사용량과 주장(claim)이 버킷에 담기는가.**

★왜 필요한가. 계획서의 `~$0.3` 은 **검증할 방법이 없던 추정치**였다. 평가셋이
  토큰을 한 번도 세지 않았기 때문이다. 모델을 올리면(특히 gpt-5 계열은 추론
  토큰이 출력으로 청구된다) 비용이 어디서 얼마나 늘었는지 짚을 수 없다.

★`uncited` 도 같다 — `check_claims` 가 계산은 하는데 **로그 한 줄에만** 남겼다.
  20 케이스를 모아 비율로 읽으려면 구조화된 값이 있어야 한다.

★`tests/graph/test_observe_rings.py` 와 **같은 규약**이다: LLM 을 부르지 않고
  기록 함수만 직접 두들긴다.
"""

from __future__ import annotations

from app.core import observe


class _Reply:
    """`AIMessage` 흉내 — 관측이 읽는 두 속성만 있으면 된다."""

    def __init__(self, model, usage):
        self.response_metadata = {"model_name": model} if model else {}
        self.usage_metadata = usage


def _usage(input_tokens, output_tokens, reasoning=0):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "output_token_details": {"reasoning": reasoning}}


# ══════════════════════════════════════════════════════════════════
#  ① 사용량 — 모델별로 갈려 담기는가
# ══════════════════════════════════════════════════════════════════


def test_usage_is_split_by_model():
    """★Agent 와 답변이 다른 모델을 쓸 수 있게 됐다. 합쳐 담으면 「Agent 만
    올렸을 때 얼마가 더 나가나」를 못 잰다."""
    with observe.observing() as seen:
        observe.record_llm_message(_Reply("모델-A", _usage(100, 10)))
        observe.record_llm_message(_Reply("모델-A", _usage(50, 5)))
        observe.record_llm_message(_Reply("모델-B", _usage(200, 20)))

    assert dict(seen.llm_calls) == {"모델-A": 2, "모델-B": 1}
    assert dict(seen.llm_input_tokens) == {"모델-A": 150, "모델-B": 200}
    assert dict(seen.llm_output_tokens) == {"모델-A": 15, "모델-B": 20}


def test_reasoning_tokens_are_counted_separately():
    """★추론 토큰은 **출력 안에 이미 포함돼** 청구된다. 따로 세어 두지 않으면
    gpt-5 계열로 바꿨을 때 늘어난 비용의 출처를 못 짚는다."""
    with observe.observing() as seen:
        observe.record_llm_message(_Reply("추론모델", _usage(100, 500, reasoning=400)))

    assert seen.llm_output_tokens["추론모델"] == 500
    assert seen.llm_reasoning_tokens["추론모델"] == 400


def test_a_call_without_usage_is_not_counted_as_zero_tokens():
    """★사용량이 안 실려 오면 **호출은 세되 따로 표시한다.** 0 으로 더하면
    그 실행이 공짜로 돌았던 것처럼 보인다."""
    with observe.observing() as seen:
        observe.record_llm_message(_Reply("모델-A", None))

    assert seen.llm_calls["모델-A"] == 1
    assert seen.llm_calls_without_usage == 1
    assert seen.llm_input_tokens["모델-A"] == 0


def test_a_reply_without_a_model_name_still_gets_counted():
    """이름을 못 읽어도 **삼키지 않는다** — 세되 「알수없음」으로 남긴다."""
    with observe.observing() as seen:
        observe.record_llm_message(_Reply(None, _usage(10, 1)))

    assert seen.llm_calls["알수없음"] == 1
    assert seen.llm_input_tokens["알수없음"] == 10


def test_recording_outside_a_bucket_is_a_no_op():
    """★버킷이 안 열려 있으면 아무 일도 없다 — 운영 `/ask` 에 비용이 없다."""
    observe.record_llm_message(_Reply("모델-A", _usage(1, 1)))
    observe.record_claims({"claims": 1, "uncited": 1})
    assert observe.current() is None


# ══════════════════════════════════════════════════════════════════
#  ② 주장(claim) — 0건과 「안 지났다」를 가르는가
# ══════════════════════════════════════════════════════════════════


def test_claims_are_accumulated_across_cases():
    with observe.observing() as seen:
        observe.record_claims({"claims": 4, "uncited": 1, "no_text": 2,
                               "unlinked": 3})
        observe.record_claims({"claims": 6, "uncited": 2, "no_text": 0,
                               "unlinked": 1})

    assert (seen.claims_total, seen.claims_uncited) == (10, 3)
    assert (seen.claims_no_text, seen.claims_unlinked) == (2, 4)


def test_zero_claims_is_different_from_never_checking():
    """★이 파일의 핵심. 둘을 섞으면 uncited **비율의 분모**를 만들 수 없다."""
    with observe.observing() as never:
        pass
    with observe.observing() as empty:
        observe.record_claims({"claims": 0, "uncited": 0})

    assert never.claims_checked is False
    assert empty.claims_checked is True
    assert never.claims_total == empty.claims_total == 0


def test_summary_carries_the_new_values():
    """보고서가 읽는 납작한 dict 에 실려야 문서에 나온다."""
    with observe.observing() as seen:
        observe.record_llm_message(_Reply("모델-A", _usage(7, 3, reasoning=2)))
        observe.record_claims({"claims": 2, "uncited": 1})

    got = seen.summary()
    assert got["llm_input_tokens"] == {"모델-A": 7}
    assert got["llm_reasoning_tokens"] == {"모델-A": 2}
    assert got["claims_uncited"] == 1
    assert got["claims_checked"] is True
