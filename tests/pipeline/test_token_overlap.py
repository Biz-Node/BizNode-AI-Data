"""token_overlap — 주장의 낱말이 근거 안에 있는가.

★두 호출자가 **다른 것**을 넣는다. 그래서 토크나이저를 갈아 끼울 수 있어야 한다.

    batch/audit/grounding.py   노드 **이름**("평택 공장 화재")   토큰 2~3개
    app/services/claim_check   답변 **문장**("삼성전자에 …")     토큰 10개 이상

  이름에는 조사가 안 붙지만 문장에는 붙는다. Step4a 실측(2026-08-23)에서
  낮은 점수의 지배적 원인이 근거 부실이 아니라 **조사**였다:

      "삼성전자에 납품하는 기업으로 SFA반도체가 있다"        score 0.00
      없는 토큰 ['삼성전자에','납품하는','기업으로','SFA반도체가','있다']
      ↑ 삼성전자도 SFA반도체도 근거에 **있다.** 조사가 붙어 못 맞춘 것뿐이다.

  그래서 문장용 토크나이저를 따로 두되, **기본 동작은 건드리지 않는다** —
  batch 쪽 `_GROUND_THRESHOLD=0.34` 가 지금 동작에 맞춰 잡힌 값이라서다.
"""

from __future__ import annotations

from pipeline import token_overlap as to


# ── 기존 동작 보존 (batch 회귀 방어) ─────────────────────────────────────

def test_default_tokenizer_is_unchanged():
    """★`batch/audit/grounding.py` 가 이 동작에 맞춰 임계값을 잡았다.
    갈아 끼우기를 도입하면서 기본값이 바뀌면 그쪽이 조용히 틀어진다."""
    assert to.tokens("평택 공장 화재") == ["평택", "공장", "화재"]


def test_default_overlap_is_unchanged():
    assert to.overlap("평택 공장에서 화재가 났다", "평택 공장 화재") == (1.0, [])
    score, missing = to.overlap("한미반도체는 TC본더 가격을 인상했다", "평택 공장 화재")
    assert score == 0.0
    assert missing == ["평택", "공장", "화재"]


def test_default_overlap_still_keeps_josa_attached():
    """기본 토크나이저는 조사를 떼지 않는다 — 그게 지금 batch 의 동작이다."""
    score, _ = to.overlap("삼성전자가 공시했다", "삼성전자에")
    assert score == 0.0


# ── 날짜 정규화 ─────────────────────────────────────────────────────────

def test_korean_date_becomes_iso():
    assert to.normalize_dates("2026년 3월 18일에") == "2026-03-18에"


def test_dotted_date_becomes_iso():
    assert to.normalize_dates("2026.03.18") == "2026-03-18"


def test_year_month_only_is_normalized():
    assert to.normalize_dates("2026년 3월") == "2026-03"


def test_text_without_a_date_is_untouched():
    assert to.normalize_dates("질소 누출 사고") == "질소 누출 사고"


def test_date_normalization_makes_the_two_sides_meet():
    """★Step4a 실측: 주장은 「2026년 3월 18일」, 근거는 「2026-03-18」이었다."""
    claim = to.normalize_dates("2026년 3월 18일에 압수수색")
    evidence = to.normalize_dates("2026-03-18 삼성전자 수원사업장 압수수색")
    score, _ = to.overlap(evidence, claim, tokenizer=to.sentence_tokens)
    assert score == 1.0


# ── 문장용 토크나이저 ────────────────────────────────────────────────────

def test_sentence_tokenizer_strips_josa():
    got = to.sentence_tokens("삼성전자에 납품하는 기업으로 SFA반도체가 있다")
    assert "삼성전자" in got
    assert "SFA반도체" in got
    assert not any(t.endswith("에") and t != "에" and t == "삼성전자에" for t in got)


def test_sentence_tokenizer_drops_endings_and_generic_verbs():
    """「있다」·「하고」 같은 서술 꼬리는 낱말 대조에 의미가 없다."""
    got = to.sentence_tokens("공급하고 있다")
    assert "공급" in got
    assert "있다" not in got and "있" not in got


def test_sentence_tokenizer_keeps_the_content_noun_of_a_conjugated_verb():
    assert "발주" in to.sentence_tokens("97억 원 규모의 장비를 발주하였다")


def test_sentence_tokenizer_drops_single_character_tokens():
    """1글자는 아무 데나 걸린다 — 기본 토크나이저와 같은 규칙을 지킨다."""
    assert all(len(t) >= 2 for t in to.sentence_tokens("3월 8일에 승소하였다"))


def test_sentence_tokenizer_keeps_latin_and_digits():
    got = to.sentence_tokens("HBM4 양산을 2026년에 시작했다")
    assert "HBM4" in got


def test_overlap_accepts_an_injected_tokenizer():
    score, _ = to.overlap("삼성전자가 공시했다", "삼성전자에",
                          tokenizer=to.sentence_tokens)
    assert score == 1.0, "조사를 뗀 토크나이저를 주면 맞아야 한다"


def test_sentence_tokenizer_on_empty_text():
    assert to.sentence_tokens("") == []
