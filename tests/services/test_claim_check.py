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
