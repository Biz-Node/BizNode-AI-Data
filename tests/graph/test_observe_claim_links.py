"""주장 연결성을 **버킷이 받는가** — `STRIP_UNLINKED_CLAIMS` 를 켜기 위한 계측.

★이 계측이 필요한 이유(2026-08-29). `claim_check.unlinked()` 도 `_strip_claims()`
  도 이미 완성돼 있고 플래그만 `False` 다. 켜려면 **오탐률**을 알아야 하는데 —
  「연결 없음」으로 판정된 주장이 정말 질문과 무관했나 — 그 값이 여태
  `log.info("claim.unlinked ...")` 한 줄에만 있었다. 로그는 운영에서 되짚는
  통로이고, **평가셋이 구조화된 값으로 읽을 통로가 없었다.**

★**개수만으로는 오탐률이 안 나온다.** 「이 문장이 정말 무관했나」는 문장을 읽어야
  정해진다. 그래서 본문과 근거 id 를 담는다.

★`None`(판정 불가)을 `False`(연결 없음)와 **섞지 않는다.** 섞으면 관계 질의
  (「삼성전자에 납품하는 기업」)가 통째로 차단된 것처럼 보인다 —
  `claim_check.summarize` 가 두 값을 가른 것과 같은 이유다.

★**개수의 주인은 `record_claims` 하나다**(2026-08-29 · 머지에서 정했다). 전에는
  `record_claim_links` 도 `claims_total`·`claims_unlinked` 를 늘렸는데, `check_claims`
  가 둘을 연달아 부르므로 값이 **두 배**가 됐다. `summarize()` 가 `claims`·
  `unlinked`·`link_unknown` 을 이미 같은 규칙으로 세므로(`claim_check.py:380·409·410`
  = `unlinked()` 와 같은 술어) 개수는 거기서만 읽고, 여기는 **본문만** 담는다.

DB 도 LLM 도 안 쓴다. `record_claim_links` 에 ClaimCheck 모양을 직접 먹인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core import observe


@dataclass
class _Claim:
    """`claim_check.ClaimCheck` 중 이 계측이 읽는 세 자리만."""

    text: str
    evidence_ids: list[str]
    intent_linked: Optional[bool]


def test_counts_are_split_three_ways():
    """★`True` · `False` · `None` 이 각자 제 통으로 간다."""
    checked = [_Claim("이어짐", ["e1"], True),
               _Claim("안 이어짐", ["e2"], False),
               _Claim("판정 불가", [], None)]

    with observe.observing() as seen:
        # ★노드가 부르는 **순서 그대로** 부른다 — `check_claims` 가 둘을 연달아
        #   부르므로, 하나만 부르는 테스트는 이중 계수를 못 잡는다(머지에서 실제로
        #   났던 결함이다).
        observe.record_claims({"claims": 3, "unlinked": 1, "link_unknown": 1})
        observe.record_claim_links(checked, [checked[1]])

    assert seen.claims_total == 3
    assert seen.claims_unlinked == 1
    assert seen.claims_link_unknown == 1


def test_unknown_is_not_counted_as_unlinked():
    """★**판정 불가는 차단 대상이 아니다.** 섞으면 「연결 없음 3건」으로 읽혀
    플래그를 켜면 안 되는 것으로 잘못 판단한다."""
    checked = [_Claim(f"판정 불가 {i}", [], None) for i in range(3)]

    with observe.observing() as seen:
        observe.record_claims({"claims": 3, "unlinked": 0, "link_unknown": 3})
        observe.record_claim_links(checked, [])

    assert seen.claims_unlinked == 0
    assert seen.claims_link_unknown == 3
    assert seen.unlinked_claims == []


def test_text_and_evidence_are_kept():
    """★**개수가 아니라 문장을 담는다** — 오탐은 숫자로 안 보인다."""
    cut = _Claim("근거와 무관한 문장", ["ev_a", "ev_b"], False)

    with observe.observing() as seen:
        observe.record_claim_links([cut], [cut])

    assert seen.unlinked_claims == [("근거와 무관한 문장", ("ev_a", "ev_b"))]


def test_summary_carries_counts_but_not_texts():
    """★`summary()` 는 **로그 한 줄로 찍히는 값**이다. 주장 본문을 실으면
    로그가 답변 사본이 된다 — 설계서 §13-2 가 프롬프트에 대해 못 박은 것과
    같은 이유다. 문장은 버킷에서 직접 읽는다."""
    cut = _Claim("답변에 실린 문장", ["ev_a"], False)

    with observe.observing() as seen:
        observe.record_claims({"claims": 1, "unlinked": 1, "link_unknown": 0})
        observe.record_claim_links([cut], [cut])
    summary = seen.summary()

    assert summary["claims_total"] == 1
    assert summary["claims_unlinked"] == 1
    assert "unlinked_claims" not in summary
    assert "답변에 실린 문장" not in str(summary)


def test_no_bucket_is_a_no_op():
    """★버킷이 안 열려 있으면 **아무 일도 안 난다.** 운영 `/ask` 는 안 연다 —
    `agent_tools._COLLECTED` 와 같은 규약이고, 그래서 운영에 비용이 없다."""
    cut = _Claim("문장", ["e1"], False)

    observe.record_claim_links([cut], [cut])          # 터지지 않아야 한다

    assert observe.current() is None


def test_missing_attributes_do_not_break_the_bucket():
    """★계측이 **본체를 죽이지 않는다.** `ClaimCheck` 모양이 바뀌어도 관측이
    예외를 올리면 답변이 통째로 안 나간다 — 재는 코드가 재는 대상을 무너뜨리면
    안 된다."""

    class _Bare:
        intent_linked = False

    with observe.observing() as seen:
        observe.record_claim_links([_Bare()], [_Bare()])

    # ★개수는 여기서 안 센다(아래 이중 계수 테스트) — 안 죽는 것만 본다.
    assert seen.unlinked_claims == [("", ())]


def test_check_claims_node_records_into_the_bucket(monkeypatch, wired, fake_llm,
                                                   request_):
    """★노드가 **실제로 부르는가** — 함수만 있고 배선이 없으면 평가셋은 늘 0 이다.

    `check_claims` 는 여전히 State 를 안 바꾼다(`STRIP_UNLINKED_CLAIMS` 는
    `False`). 늘어난 것은 관측뿐이다.
    """
    from app.graph.nodes import answer

    fake_llm.payload = {"answer": "답변 문장", "evidence_ids": ["ev_rel"],
                        "claims": [{"text": "답변 문장",
                                    "evidence_ids": ["ev_rel"]}]}
    graph, _ = wired
    state = graph.invoke({"request": request_})

    with observe.observing() as seen:
        got = answer.check_claims(state)

    assert got == {}, "관측 전용 노드가 State 조각을 돌려주면 안 된다"
    assert seen.claims_total == 1, "노드가 버킷에 담지 않았다 — 배선이 없다"


def test_the_two_recorders_do_not_double_count():
    """★**개수를 세는 주인은 하나다.** 이 테스트가 있는 이유(2026-08-29).

    브랜치 둘이 각자 claim 계측기를 만들어 `claims_total`·`claims_unlinked` 를
    **둘 다** 늘리고 있었다. `check_claims` 는 `record_claims(summary)` 와
    `record_claim_links(checked, cut)` 를 **연달아** 부르므로, 합치는 순간 모든
    값이 두 배가 된다 — 충돌도 안 나고 테스트도 각자 통과한다. 같은 이름을 두
    주인이 쓰면 합칠 때 조용히 어긋난다는 것을 여기 남긴다.
    """
    checked = [_Claim("이어짐", ["e1"], True),
               _Claim("안 이어짐", ["e2"], False)]

    with observe.observing() as seen:
        observe.record_claims({"claims": 2, "unlinked": 1, "link_unknown": 0})
        observe.record_claim_links(checked, [checked[1]])

    assert seen.claims_total == 2, f"두 번 세었다: {seen.claims_total}"
    assert seen.claims_unlinked == 1, f"두 번 세었다: {seen.claims_unlinked}"
    assert seen.claims_link_unknown == 0
    # 본문은 `record_claim_links` 만 담는다 — 이쪽은 주인이 하나다
    assert seen.unlinked_claims == [("안 이어짐", ("e2",))]
