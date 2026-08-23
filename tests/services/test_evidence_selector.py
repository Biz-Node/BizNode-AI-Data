"""evidence_selector — 질문 의도로 사건을 골라내는 순수 함수들.

★왜 이 파일이 있나 (2026-08-23)

Step1 에서 기업별 evidence scope 를 분리했지만, 그 뒤로도 **질문이 무엇이든
같은 재료**가 나갔다. 실측: 「SK하이닉스」와 「SK하이닉스 노조 관련 리스크
알려줘」가 바이트 단위로 동일한 82건(13,916자)을 반환했고, 「삼성전자와
SK하이닉스의 담합 소송」은 205건(34,430자)까지 갔다. 사건 수에 상한이 없었다.

설계 실험 3회로 확정한 순위 규칙:

  ① 근거 **원문**을 질문과 비교 → 실패. 「안전사고」 질의에서 실제 사고 근거
     (TMAH·인산 노출)가 82건 중 최하위(0.155)로 밀렸다. 근거 단편에 기업명이
     없어 임베딩이 빗나간다.
  ② 사건 **라벨**(name + event_type)로 비교 → 나아졌지만 여전히 기업명이 든
     라벨이 상위를 먹었다.
  ③ 질문과 라벨 **양쪽에서 앵커 기업명을 제거** → 정확해졌다.
     '안전사고' → 사고재해 5건 / '소송 상황' → 분쟁소송 5건.

규칙 매칭은 **hard filter 가 아니라 티어**다 — 규칙에 없는 표현은 임베딩이
받는다. 그리고 **다른 기업이라는 이유로는 아무것도 버리지 않는다**: 선택은
기업 scope 안에서만 일어나고, scope 를 정하는 것은 이 모듈의 일이 아니다.
"""

from __future__ import annotations

from app.api.schemas import Event
from app.services import evidence_selector as sel


def _event(event_id, name, event_type, *, is_risk=False, occurred_at="2026-06-01",
           evidence_ids=("ev_x",)):
    return Event(event_id=event_id, name=name, event_type=event_type,
                 is_risk=is_risk, role="subject", occurred_at=occurred_at,
                 article_count=1, timeline=[], evidence_ids=list(evidence_ids))


# ── 의도 추출 ────────────────────────────────────────────────────────────

def test_anchor_name_is_stripped_from_the_question():
    """★기업명이 남으면 임베딩을 지배한다 — 실험 ②의 실패 원인."""
    assert sel.intent_of("SK하이닉스 노조 관련 리스크 알려줘", ["SK하이닉스"]) \
        == "노조 관련 리스크 알려줘"


def test_every_resolved_name_is_stripped():
    got = sel.intent_of("삼성전자와 SK하이닉스의 담합 소송",
                        ["삼성전자", "SK하이닉스"])
    assert "삼성전자" not in got and "SK하이닉스" not in got
    assert "담합 소송" in got


def test_question_with_only_a_company_name_has_no_intent():
    """★「SK하이닉스」만 물으면 **의도가 없다.** 원문을 그대로 쓰면 「SK하이닉스」
    와의 유사도로 줄을 세우게 되는데 그건 잡음이다 — 실측으로 「행복 도시락
    사업」이 상위에 올라왔다. 빈 문자열을 돌려주고 유사도를 아예 건너뛴다."""
    assert sel.intent_of("SK하이닉스", ["SK하이닉스"]) == ""


def test_no_intent_skips_the_embedder_entirely():
    """의도가 없으면 임베딩을 부르지 않는다 — 잡음일 뿐인데 돈과 시간을 쓴다."""
    called = []

    def fake_embed(texts):
        called.append(texts)
        return [[1.0, 0.0] for _ in texts]

    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="", embed=fake_embed, anchor_names=[])

    assert got == {} and called == []


def test_no_intent_falls_back_to_risk_then_recency():
    """의도가 없으면 위험사건·최신순이 답이다 — 임의 유사도보다 낫다."""
    events = [_event("e1", "옛 일반", "기타", is_risk=False, occurred_at="2020-01-01"),
              _event("e2", "최근 위험", "기타", is_risk=True, occurred_at="2026-08-01")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=1)
    assert [e.event_id for e in kept] == ["e2"]


def test_no_anchor_leaves_the_question_alone():
    assert sel.intent_of("반도체 업계 소송", []) == "반도체 업계 소송"


# ── 규칙 신호 ────────────────────────────────────────────────────────────

def test_rule_matches_labour_event_type():
    assert "노무" in sel.matched_event_types("노조 관련 리스크 알려줘")


def test_rule_matches_accident_event_type():
    assert "사고재해" in sel.matched_event_types("안전사고 있었어?")


def test_rule_matches_litigation_event_type():
    assert "분쟁소송" in sel.matched_event_types("소송 상황")


def test_unmatched_wording_yields_no_rule_signal():
    """★규칙이 못 잡는 표현이 있다는 걸 인정한다 — 그건 임베딩이 받는다."""
    assert sel.matched_event_types("요즘 어때?") == frozenset()


# ── 선택 ────────────────────────────────────────────────────────────────

def test_rule_matched_types_come_first():
    events = [_event("e1", "HBM 증산", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    kept, _ = sel.select(events, matched=frozenset({"노무"}), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_rule_is_a_boost_not_a_hard_filter():
    """★규칙에 안 걸린 사건도 자리가 남으면 살아남는다."""
    events = [_event("e1", "HBM 증산", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    kept, dropped = sel.select(events, matched=frozenset({"노무"}), sims={}, limit=2)
    assert len(kept) == 2 and dropped == []


def test_similarity_orders_within_a_tier():
    events = [_event("e1", "낮은 유사도", "사업확장"),
              _event("e2", "높은 유사도", "사업확장")]
    sims = {"e1": 0.10, "e2": 0.90}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_rule_tier_outranks_a_higher_similarity():
    """규칙이 **우선** 신호다 — 유사도가 높아도 티어를 못 넘는다."""
    events = [_event("e1", "무관하지만 유사도 높음", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    sims = {"e1": 0.95, "e2": 0.10}
    kept, _ = sel.select(events, matched=frozenset({"노무"}), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_limit_caps_and_reports_what_was_dropped():
    """★조용히 자르면 「그게 전부」로 읽힌다 — 잘린 것을 돌려준다."""
    events = [_event(f"e{i}", f"사건{i}", "사업확장") for i in range(5)]
    kept, dropped = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert len(kept) == 2 and len(dropped) == 3


def test_risk_events_win_ties_when_no_similarity_is_available():
    """★임베딩이 없거나 실패했을 때의 폴백 — 위험사건이 앞에 온다."""
    events = [_event("e1", "일반", "사업확장", is_risk=False),
              _event("e2", "위험", "사업확장", is_risk=True)]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_recent_events_win_when_risk_and_similarity_tie():
    events = [_event("e1", "옛날", "사업확장", occurred_at="2020-01-01"),
              _event("e2", "최근", "사업확장", occurred_at="2026-08-01")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_selection_is_stable_for_identical_events():
    """동점이면 입력 순서를 지킨다 — 같은 질문에 매번 다른 답이 나오면 안 된다."""
    events = [_event(f"e{i}", "같은 사건", "사업확장") for i in range(4)]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e0", "e1"]


# ── 유사도 계산 ──────────────────────────────────────────────────────────

def test_similarities_uses_the_injected_embedder():
    events = [_event("e1", "노조 설립", "노무")]
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    got = sel.similarities(events, intent="노조", embed=fake_embed, anchor_names=[])

    assert got["e1"] == 1.0
    assert calls, "임베더가 호출돼야 한다"


def test_anchor_name_is_stripped_from_event_labels_too():
    """★질문에서만 빼면 소용없다 — 라벨에서도 같은 항을 지워야 비교가 된다."""
    events = [_event("e1", "SK하이닉스 압수수색", "규제수사")]
    seen = []

    def fake_embed(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    sel.similarities(events, intent="압수수색", embed=fake_embed,
                     anchor_names=["SK하이닉스"])

    assert not any("SK하이닉스" in t for t in seen), seen


def test_similarities_returns_empty_when_the_embedder_fails():
    """★OpenAI 가 죽어도 /ask 는 죽지 않는다 — 유사도만 포기하고 규칙으로 간다."""
    def broken_embed(texts):
        raise RuntimeError("openai down")

    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="노조", embed=broken_embed, anchor_names=[])

    assert got == {}


def test_similarities_without_an_embedder_is_empty():
    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="노조", embed=None, anchor_names=[])
    assert got == {}
