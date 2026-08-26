"""claim_check — 답변 주장과 인용 근거의 **토큰 겹침**을 잰다.

★이것은 검증기가 아니다. **의심 탐지기(cheap suspicion detector)** 다.

  점수가 낮다고 「거짓」이 아니고, 높다고 「참」이 아니다. 재는 것은 오직
  「주장에 쓴 낱말이 인용한 근거 안에 실제로 있는가」뿐이다. 의역·동의어·
  한국어 조사에 그대로 걸린다 — 실측(2026-08-23)으로 「SK하이닉스**의**」가
  「SK하이닉스」를 담은 근거에서 *없는 토큰*으로 잡혔다.

  그래서 Step4a 에서는 **판정하지 않는다.** 분포만 모은다. `batch/audit/
  grounding.py` 의 `_GROUND_THRESHOLD=0.34` 를 그대로 쓰지 않는 이유도 같다 —
  그 값은 노드 **이름**(토큰 2~3개) 기준이고 여기는 **문장**(토큰 10개 이상)이다.

무엇을 잡고 싶은가(Step3 실측에서 실제로 나온 것):
  · 오인용   — 질소 누출 답변에 HBM3E 양산 근거를 달았다(겹침 0.00)
  · 무인용   — 인용 0건인데 실질 주장을 여럿 했다
"""

from __future__ import annotations

from app.api.schemas import Evidence
from app.services import claim_check


def _evidence(eid, text):
    return Evidence(evidence_id=eid, text=text, source_doc="d", source_type="news")


# ── 상태 구분 ────────────────────────────────────────────────────────────

def test_claim_without_any_evidence_id_is_uncited():
    """★프롬프트로는 못 막은 실패 — 결정론적으로 잡을 수 있는 유일한 종류다."""
    got = claim_check.check([{"text": "생산에 차질이 생겼다", "evidence_ids": []}], {})
    assert got[0].status == "uncited"
    assert got[0].score is None


def test_claim_citing_an_unknown_id_is_marked_not_scored():
    """재료에 없는 id — 화이트리스트가 이미 버리지만, 여기서도 점수는 못 낸다."""
    got = claim_check.check([{"text": "무언가", "evidence_ids": ["ev_nope"]}], {})
    assert got[0].status == "no_text"
    assert got[0].score is None


def test_claim_with_evidence_text_gets_a_score():
    ev = {"ev_a": _evidence("ev_a", "SK하이닉스 이천 공장에서 질소 누출 사고가 났다")}
    got = claim_check.check(
        [{"text": "이천 공장에서 질소 누출 사고", "evidence_ids": ["ev_a"]}], ev)
    assert got[0].status == "scored"
    assert got[0].score == 1.0


# ── 실제로 잡고 싶은 것 ──────────────────────────────────────────────────

def test_mis_citation_scores_far_lower_than_a_good_citation():
    """★Step3 에서 실제로 나온 오인용 — 질소 답변에 HBM3E 근거를 달았다."""
    good = _evidence("ev_good", "2015년 SK하이닉스 이천 공장에서 발생한 질소가스 "
                                "누출 사고로 근로자 3명이 사망했다")
    bad = _evidence("ev_bad", "2025년 HBM3E 12단 중심의 재편이 유력시되는 시장 "
                              "수요 변화에 발맞춰 양산을 시작했다")
    claim = "이천 공장에서 질소 누출 사고가 발생했습니다"

    scored = claim_check.check([{"text": claim, "evidence_ids": ["ev_good"]},
                                {"text": claim, "evidence_ids": ["ev_bad"]}],
                               {"ev_good": good, "ev_bad": bad})

    assert scored[0].score > scored[1].score
    assert scored[1].score == 0.0


def test_multiple_evidence_ids_are_pooled():
    """한 주장이 근거 둘을 들면 **합쳐서** 본다 — 나눠 보면 둘 다 낮게 나온다."""
    ev = {"ev_a": _evidence("ev_a", "이천 공장에서"),
          "ev_b": _evidence("ev_b", "질소 누출 사고가 났다")}
    got = claim_check.check(
        [{"text": "이천 공장 질소 누출", "evidence_ids": ["ev_a", "ev_b"]}], ev)
    assert got[0].score == 1.0


def test_missing_tokens_are_reported_for_diagnosis():
    """분포만 모으는 단계라 **왜 낮은지**를 볼 수 있어야 한다."""
    ev = {"ev_a": _evidence("ev_a", "질소 누출 사고")}
    got = claim_check.check(
        [{"text": "평택 공장 화재 발생", "evidence_ids": ["ev_a"]}], ev)
    assert "평택" in got[0].missing


def test_missing_evidence_text_is_not_scored():
    """원문을 못 꺼낸 근거(missing=true)로는 점수를 낼 수 없다."""
    gone = Evidence(evidence_id="ev_gone", text="", source_doc="",
                    source_type="news", missing=True)
    got = claim_check.check([{"text": "무언가", "evidence_ids": ["ev_gone"]}],
                            {"ev_gone": gone})
    assert got[0].status == "no_text"


# ── 이 단계에서 **하지 않는** 것 ─────────────────────────────────────────

def test_check_makes_no_pass_fail_judgement():
    """★Step4a 는 관측만 한다 — 임계값도 판정도 없다. 실측 전에 값을 박으면
    거짓 양성이 정상 답변을 훼손한다."""
    result = claim_check.check(
        [{"text": "무관한 주장", "evidence_ids": ["ev_a"]}],
        {"ev_a": _evidence("ev_a", "전혀 다른 내용")})[0]
    assert not hasattr(result, "supported")
    assert not hasattr(result, "verdict")


def test_empty_claims_yields_empty_result():
    assert claim_check.check([], {}) == []


# ── 분포 요약 ────────────────────────────────────────────────────────────

# ── Step4b: 형태소 잡음 제거 (2026-08-23) ────────────────────────────────

def test_josa_no_longer_sinks_a_correct_claim():
    """★Step4a 실측에서 0.00 이 나온 바로 그 문장. 삼성전자도 SFA반도체도
    근거에 **있었다** — 조사 「에」·「가」가 붙어 못 맞춘 것뿐이다."""
    ev = {"ev_a": _evidence("ev_a", "SFA반도체는 삼성전자에 반도체 패키징을 "
                                    "납품하는 기업이다")}
    got = claim_check.check(
        [{"text": "삼성전자에 납품하는 기업으로 SFA반도체가 있다",
          "evidence_ids": ["ev_a"]}], ev)
    assert got[0].score == 1.0, got[0].missing


def test_date_format_difference_no_longer_sinks_a_correct_claim():
    """★주장은 「2026년 3월 18일」, 근거는 「2026-03-18」이었다."""
    ev = {"ev_a": _evidence("ev_a", "2026-03-18 삼성전자 수원사업장 압수수색이 "
                                    "진행됐다")}
    got = claim_check.check(
        [{"text": "2026년 3월 18일에 삼성전자 수원사업장 압수수색이 진행되었다",
          "evidence_ids": ["ev_a"]}], ev)
    assert got[0].score == 1.0, got[0].missing


def test_conjugated_verb_matches_its_content_noun():
    """「발주하였다」→「발주」. 근거에 없는 낱말(「규모」)은 그대로 잡히는 것이
    맞다 — 여기서 보는 것은 **활용 꼬리 때문에 놓치지 않는가**뿐이다."""
    ev = {"ev_a": _evidence("ev_a", "SK하이닉스가 한미반도체에 97억 원 장비 발주")}
    got = claim_check.check(
        [{"text": "SK하이닉스는 한미반도체에 97억 원 규모의 장비를 발주하였다",
          "evidence_ids": ["ev_a"]}], ev)
    assert "발주하였다" not in got[0].missing
    assert "발주" not in got[0].missing
    assert "SK하이닉스는" not in got[0].missing
    assert got[0].score > 0.8, got[0].missing


def test_report_date_is_grounded_by_the_evidence_published_at():
    """★근거 원문에 날짜가 없어도 `published_at` 으로 보여 줬다면 근거가 있는 것이다.

    실측: 못 맞춘 토큰 123개 중 26개(21.1%)가 날짜였다. 프롬프트의
    `<evidence id=... published_at="2026-06-12">` 가 모델이 본 그 값인데,
    채점은 본문만 봐서 「없는 근거를 지어냈다」로 잘못 읽혔다.
    """
    ev = {"ev_a": Evidence(evidence_id="ev_a", text="불소 누출 사고가 났다",
                           source_doc="d", source_type="news",
                           published_at="2026-06-12")}
    got = claim_check.check(
        [{"text": "2026년 6월 12일에 불소 누출 사고가 났다",
          "evidence_ids": ["ev_a"]}], ev)
    assert got[0].score == 1.0, got[0].missing


def test_a_date_we_never_showed_is_still_missing():
    """★반대는 성립하면 안 된다 — 보여 준 적 없는 날짜는 여전히 못 맞춘다."""
    ev = {"ev_a": Evidence(evidence_id="ev_a", text="불소 누출 사고가 났다",
                           source_doc="d", source_type="news",
                           published_at="2026-06-12")}
    got = claim_check.check(
        [{"text": "1999년 1월 1일에 불소 누출 사고가 났다",
          "evidence_ids": ["ev_a"]}], ev)
    assert "1999-01-01" in got[0].missing


def test_real_mis_citation_still_scores_zero():
    """★잡음을 걷어내도 **진짜 오인용은 그대로 0 이어야 한다.** 이게 무너지면
    개선이 아니라 검출력을 버린 것이다."""
    ev = {"ev_bad": _evidence("ev_bad", "2025년 HBM3E 12단 중심의 재편이 "
                                        "유력시되는 시장 수요 변화에 발맞춰 "
                                        "양산을 시작했다")}
    got = claim_check.check(
        [{"text": "이천 공장에서 질소 누출 사고가 발생했습니다",
          "evidence_ids": ["ev_bad"]}], ev)
    assert got[0].score == 0.0


def test_invented_causation_still_scores_low():
    """Step4a 가 잡아낸 진짜 문제 — 파급 줄에서 만들어낸 인과."""
    ev = {"ev_a": _evidence("ev_a", "현대오토에버 내부에서 노동조합 설립 움직임이 "
                                    "확산되고 있다")}
    got = claim_check.check(
        [{"text": "노동조합 설립이 기아, 현대자동차 등에도 공급 차질을 초래할 수 있다",
          "evidence_ids": ["ev_a"]}], ev)
    assert got[0].score < 0.5, got[0].missing


# ── claim 6번째 유형 — 미분류/자유결합 Insight (2026-08-26) ──────────────
# ★설계서 §13-1 의 claim 5종에 **분류되지 않는** 주장이 있다.
#
#     ⑤ Insight/파급 의 검증 원천은 `propagation[]`(target·path·stated)다.
#     그런데 실측 사례1의 「이 사고로 인해 생산에 영향을 미쳤을 가능성이
#     있습니다」는 `propagation[]` 근거 없이 LLM 이 두 사실을 **자유 결합**한
#     것이라 ⑤ 에도 안 들어간다 — 4등급 ④(Insight)에는 속하는데 claim 5종
#     어디에도 안 걸린다. **검증 원천이 없는 게 아니라 분류가 안 된다.**
#
# ★**이번 단계에서는 관측·분류만 한다** — strip 하지 않는다. 발생률과 오탐률을
#   재기 전에 문장을 지우면 정상 답변을 훼손한다(Step4a 와 같은 규율).

def test_a_causal_claim_unsupported_by_propagation_is_typed_as_free_combination():
    """★실측 사례1 그 자체 — 근거는 손해배상 판결을 말하는데 답변이
    「이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다」로 끝났다."""
    ev = {"ev_a": _evidence("ev_a", "2015년 SK하이닉스 이천 공장에서 발생한 "
                                    "질소가스 누출 사고로 인해 근로자 3명이 "
                                    "사망한 사건과 관련, 손해배상 소송을 제기했다")}

    got = claim_check.check(
        [{"text": "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다",
          "evidence_ids": ["ev_a"]}], ev)

    assert got[0].claim_type == claim_check.TYPE_FREE_COMBINATION


def test_a_claim_without_a_causal_marker_is_not_a_free_combination():
    """★인과를 주장하지 않은 문장은 대상이 아니다 — 대다수가 여기다."""
    ev = {"ev_a": _evidence("ev_a", "이천 공장에서 질소 누출 사고가 났다")}

    got = claim_check.check(
        [{"text": "이천 공장에서 질소 누출 사고가 발생했습니다",
          "evidence_ids": ["ev_a"]}], ev)

    assert got[0].claim_type is None


def test_a_causal_claim_whose_effect_is_in_the_evidence_is_not_flagged():
    """★근거가 그 인과를 **실제로 말하고 있으면** ② 관측된 인과다(설계서 §12).
    자유 결합이 아니다."""
    ev = {"ev_a": _evidence("ev_a", "질소 누출 사고로 인해 근로자 3명이 사망했다")}

    got = claim_check.check(
        [{"text": "질소 누출 사고로 인해 근로자 3명이 사망했습니다",
          "evidence_ids": ["ev_a"]}], ev)

    assert got[0].claim_type is None


def test_a_causal_claim_backed_by_propagation_is_typed_as_propagation():
    """★`propagation[]` 이 뒷받침하면 claim ⑤ 다 — 자유 결합이 아니라
    **우리가 계산한 것**이고, 검증 원천이 있다(설계서 §13-2)."""
    ev = {"ev_a": _evidence("ev_a", "SK하이닉스에 공급 차질이 발생했다")}

    got = claim_check.check(
        [{"text": "SK하이닉스의 차질로 인해 엔비디아에 매출 상실 리스크가 있습니다",
          "evidence_ids": ["ev_a"]}], ev,
        propagation_targets=["엔비디아"])

    assert got[0].claim_type == claim_check.TYPE_PROPAGATION


def test_an_uncited_causal_claim_is_still_typed():
    """★인용조차 없는 인과 주장이 가장 위험하다 — 화이트리스트가 원리적으로
    못 잡는 자리다. 점수를 못 내도 **유형은 남긴다.**"""
    got = claim_check.check(
        [{"text": "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다",
          "evidence_ids": []}], {})

    assert got[0].status == "uncited"
    assert got[0].claim_type == claim_check.TYPE_FREE_COMBINATION


def test_free_combination_claims_are_counted_in_the_summary():
    """★분포를 로그로 남길 수 있어야 한다 — strip 여부를 정하려면 발생률이 먼저다."""
    ev = {"ev_a": _evidence("ev_a", "이천 공장에서 질소 누출 사고가 났다")}
    checked = claim_check.check(
        [{"text": "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다",
          "evidence_ids": ["ev_a"]},
         {"text": "이천 공장에서 질소 누출 사고가 발생했습니다",
          "evidence_ids": ["ev_a"]}], ev)

    got = claim_check.summarize(checked)

    assert got["free_combination"] == 1


def test_check_still_does_not_drop_or_judge_a_free_combination_claim():
    """★**관측만 한다** — 유형을 붙였다고 문장을 버리거나 판정하지 않는다.
    strip 여부는 발생률·오탐률 실측 뒤에 정한다."""
    ev = {"ev_a": _evidence("ev_a", "이천 공장에서 질소 누출 사고가 났다")}

    got = claim_check.check(
        [{"text": "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다",
          "evidence_ids": ["ev_a"]}], ev)

    assert len(got) == 1
    assert got[0].text == "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다"
    assert not hasattr(got[0], "supported")   # 판정 필드를 두지 않는다
    assert not hasattr(got[0], "verdict")


def test_summary_reports_counts_and_scores_for_logging():
    """로그 한 줄로 분포를 남길 수 있어야 한다 — 20개 질문을 모을 도구다."""
    ev = {"ev_a": _evidence("ev_a", "질소 누출 사고")}
    checked = claim_check.check(
        [{"text": "질소 누출", "evidence_ids": ["ev_a"]},
         {"text": "근거 없는 말", "evidence_ids": []}], ev)

    got = claim_check.summarize(checked)

    assert got["claims"] == 2
    assert got["uncited"] == 1
    assert got["scored"] == 1
    assert got["min"] == 1.0
