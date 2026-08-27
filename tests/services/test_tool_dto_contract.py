"""`app/tools/dto.py` — 도구 반환 계약을 고정한다.

이 파일이 지키는 것은 **구현이 나중에 말을 바꾸지 못하게** 하는 것이다.
계약이 주석으로만 있으면 문서일 뿐이라, 어긋나도 아무도 모른다.

★도구 구현은 아직 없다. 여기서 검사하는 것은 모델의 모양뿐이다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools import dto
from app.tools.dto import (
    BusinessOverviewDTO,
    EventDTO,
    EventPhaseDTO,
    MarketDTO,
    RelationDTO,
)


# ══════════════════════════════════════════════════════════════════
#  MarketDTO — 계산값에 근거 id 를 발급하지 않는다
# ══════════════════════════════════════════════════════════════════

def test_market_dto_has_no_evidence_id():
    """★시총·PER·PBR·PSR 은 `market_metrics` 뷰의 **계산값**이다. 근거 id 를
    붙이면 원본 갱신 때 어긋난다 — `ratio_change` 를 제거했던 것과 같은 실수."""
    assert "evidence_id" not in MarketDTO.model_fields


def test_market_dto_carries_the_calculation_coordinates():
    """근거 id 대신 **좌표**로 되짚는다. 뷰가 `fin_year`·`fs_div` 를 값과 함께
    내보내는 이유가 이것이다."""
    for field in ("corp_code", "trade_date", "fin_year", "fs_div"):
        assert field in MarketDTO.model_fields, field


def test_per_null_reasons_are_two_and_distinct():
    """★`per` 가 `null` 인 이유가 둘이다(실측: 적자 16,340행 · 재무 미수집 500행).
    한 문구로 뭉뚱그리면 「재무가 없다」를 「적자다」로 읽게 된다."""
    assert dto.PER_NOTE_LOSS != dto.PER_NOTE_NO_FINANCIALS
    assert "적자" in dto.PER_NOTE_LOSS
    assert "적자" not in dto.PER_NOTE_NO_FINANCIALS

    m = MarketDTO(corp_code="00126380", trade_date="2026-08-26",
                  fin_year=None, per=None, per_note=dto.PER_NOTE_NO_FINANCIALS)
    assert m.per is None and m.per_note == dto.PER_NOTE_NO_FINANCIALS


# ══════════════════════════════════════════════════════════════════
#  EventDTO — 배열을 문자열로 펴지 않는다
# ══════════════════════════════════════════════════════════════════

def test_timeline_stays_a_list_of_phases():
    """★과거에 국면 배열이 문자열이 되어 `size()` 가 **글자 수**를 센 사고가
    있었다(28건). 배열은 배열로 둔다."""
    e = EventDTO(event_id="evt_1", name="압수수색", event_type="규제수사",
                 role="subject", role_note=dto.ROLE_NOTE["subject"],
                 timeline=[EventPhaseDTO(period="2026-06", name="압수수색")])

    assert isinstance(e.timeline, list)
    assert isinstance(e.timeline[0], EventPhaseDTO)
    assert len(e.timeline) == 1          # 글자 수가 아니라 국면 수


def test_timeline_summary_is_a_separate_field():
    """요약은 **배열을 대체하지 않는다.** 최대 13국면짜리를 그대로 프롬프트에
    실으면 사건 하나가 재료를 다 먹으므로 요약을 따로 둔다."""
    assert "timeline" in EventDTO.model_fields
    assert "timeline_summary" in EventDTO.model_fields
    assert EventDTO.model_fields["timeline_summary"].annotation is not list


def test_role_note_separates_subject_from_the_rest():
    """★「이 기업에 난 일」은 `subject` 만이다."""
    assert dto.ROLE_NOTE["subject"] == "이 기업에 난 일"
    for role in ("counterparty", "mentioned"):
        assert "이 기업에 난 일이 아니다" in dto.ROLE_NOTE[role]


@pytest.mark.parametrize("sign", ["negative", "positive", "neutral"])
def test_impacts_sign_accepts_the_three_measured_values(sign):
    e = EventDTO(event_id="e", name="n", event_type="t", sign=sign,
                 role="subject", role_note=dto.ROLE_NOTE["subject"])
    assert e.sign == sign


def test_occurred_at_is_optional_because_it_comes_from_the_edge():
    """★Event 노드에는 날짜가 없다(실측 1,058건 전부). `HAS_EVENT`/`IMPACTS`
    엣지에서 가져오므로 엣지를 못 붙이면 비어야 한다 — 0 이나 ''로 메우지 않는다."""
    e = EventDTO(event_id="e", name="n", event_type="t",
                 role="subject", role_note=dto.ROLE_NOTE["subject"])
    assert e.occurred_at is None


# ══════════════════════════════════════════════════════════════════
#  RelationDTO — 오해할 수 있는 값에 표기를 붙인다
# ══════════════════════════════════════════════════════════════════

def _rel(**kw):
    # ★`edge_id`·`source_key`·`target_key` 는 1.5차에서 더해진 **식별** 필드다
    #   (표기가 아니다). 없으면 근거를 관계에 되짚을 수 없어 `Source.edge_id` 가
    #   비고, 워크스페이스 소속 표기도 못 붙인다 — 둘 다 이미 나가 있는 계약이다.
    base = dict(edge_id="e1", source="심텍", target="SK하이닉스",
                source_key="00152127", target_key="00164779",
                edge_type="SUPPLIES_TO",
                source_type="news", source_note=dto.SOURCE_NOTE["news"],
                direction="directed", direction_note=dto.DIRECTION_NOTE["directed"],
                effective_confidence=0.9)
    return RelationDTO(**{**base, **kw})


def test_source_note_exists_for_every_allowed_source_type():
    """`Relation`·`Evidence`·`Source` 와 **같은 3값**이다."""
    assert set(dto.SOURCE_NOTE) == {"dart", "dart_filing", "news"}
    assert "확정 사실" in dto.SOURCE_NOTE["dart"]
    assert "확정되지 않은" in dto.SOURCE_NOTE["news"]


def test_symmetric_edge_types_are_the_measured_two():
    """★`PARTNERS_WITH`·`COMPETES_WITH` 는 화살표에 뜻이 없다."""
    assert dto.SYMMETRIC_EDGE_TYPES == {"PARTNERS_WITH", "COMPETES_WITH"}
    assert "「A 가 B 에게」로 읽지 말 것" in dto.DIRECTION_NOTE["symmetric"]


def test_news_develops_caution_is_a_fixed_string():
    """★구현이 문구를 고쳐 쓰지 못하게 상수로 못 박는다."""
    assert dto.CAUTION_NEWS_DEVELOPS == "뉴스 추출 DEVELOPS 는 오추출률 47% — 단정 불가"
    assert _rel(edge_type="DEVELOPS", caution=dto.CAUTION_NEWS_DEVELOPS).caution


def test_ratio_carries_its_unit():
    """★`0.72` 는 0.72% 다. 0~1 구간에 진짜 소액지분 126건이 실재해서
    값만으로는 백분율인지 소수인지 구별이 안 된다."""
    r = _rel(ratio=0.72, ratio_unit="percent", ratio_text="0.72%")
    assert r.ratio_unit == "percent"
    assert r.ratio_text.endswith("%")


def test_ratio_above_100_is_rejected():
    """퍼센트 구간을 벗어난 값은 단위를 잘못 읽었다는 뜻이다."""
    with pytest.raises(ValidationError):
        _rel(ratio=101.0, ratio_unit="percent", ratio_text="101%")


def test_freshness_weights_match_pipeline_freshness():
    """★가중치를 두 벌 두지 않는다. 여기 적힌 값은 `pipeline/freshness.py` 가
    실제로 내는 값과 **같아야** 한다."""
    from datetime import date

    from pipeline import freshness

    today = date(2026, 8, 27)
    cases = {
        "expired": {"is_current": False},
        "current": {"last_seen": "2026-08-01", "refresh_cycle_days": 180},
        "stale": {"last_seen": "2024-01-01", "refresh_cycle_days": 180},
        "unknown": {},
    }
    for status, props in cases.items():
        got = freshness.assess(props, today=today)
        assert got.status == status, (status, got)
        assert got.confidence_factor == dto.FRESHNESS_WEIGHT[status], status


def test_effective_confidence_is_bounded():
    """`confidence × 가중치` 라 0~1 을 벗어날 수 없다."""
    with pytest.raises(ValidationError):
        _rel(effective_confidence=1.4)


# ══════════════════════════════════════════════════════════════════
#  BusinessOverviewDTO
# ══════════════════════════════════════════════════════════════════

def test_relation_dto_keeps_the_identity_fields():
    """★1.5차 추가 — 되짚을 좌표가 DTO 에 남아 있어야 한다.

    `edge_id` 가 없으면 `Source.edge_id` 가 전부 `null` 이 되고,
    `source_key`·`target_key` 가 없으면 워크스페이스 소속 표기(설계서 §12)가
    사라진다. 둘 다 표기가 아니라 **식별**이다.
    """
    for field in ("edge_id", "source_key", "target_key"):
        assert field in RelationDTO.model_fields, field
    assert "evidence_ids" in EventDTO.model_fields


def test_business_overview_dto_keeps_source_doc():
    """★`source_doc` 이 rcept_no 라, 나중에 `Evidence` 로 승격할 때 필요하다."""
    assert set(BusinessOverviewDTO.model_fields) == {
        "corp_code", "bsns_year", "overview_text", "products_text", "source_doc"}
    b = BusinessOverviewDTO(corp_code="00126380", bsns_year=2025,
                            source_doc="20260310002820")
    assert b.source_doc == "20260310002820"
    assert b.products_text is None       # 없는 행이 실재한다 — ''로 바꾸지 않는다
