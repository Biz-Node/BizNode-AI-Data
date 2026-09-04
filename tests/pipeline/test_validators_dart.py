"""`validate_executives` — 임원 현황(20번)의 도메인 검증.

★**이 파일은 버그 하나 때문에 생겼다**(2026-09-04). `is_plausible_birth_year_month`
  를 import 없이 부르고 있어서, `staging.py` 의 executives 트랙이 **Person 을
  만나는 순간 `NameError` 로 죽었다.** 검증기 세 개 중 이 함수만 그랬고,
  `pipeline/validators/` 에 테스트가 **한 건도 없어** 아무도 몰랐다.

★그래서 여기서 보는 첫째는 「생년월 판정이 실제로 불리는가」다. import 를
  되돌리면 아래 두 번째 시험이 `NameError` 로 깨진다 — 경고가 안 나오는 것이
  아니라 함수가 통째로 죽는다.

★두 번째로 보는 것은 **드롭과 경고의 경계**다. 생년월은 **경고만** 남기고
  엔티티를 지우지 않는다(사람은 실재한다). 관계 속성은 범위를 벗어나면
  **드롭한다**. 이 둘을 섞으면 「값이 이상한 임원」이 통째로 사라진다.
"""

from __future__ import annotations

from schemas.dart_schemas import EntityDTO, NormalizedDocument, RelationshipDTO

from pipeline.validators.dart import validate_executives


def _person(key="p_1", **props) -> EntityDTO:
    return EntityDTO(type="Person", key=key, properties=dict(props))


def _is_executive_of(**props) -> RelationshipDTO:
    return RelationshipDTO(type="IS_EXECUTIVE_OF", from_key="p_1",
                           to_key="00126380", properties=dict(props))


def _doc(entities=(), relationships=()) -> NormalizedDocument:
    return NormalizedDocument(entities=list(entities),
                              relationships=list(relationships))


# ══════════════════════════════════════════════════════════════════════
#  생년월 — ★판정이 **불리는가** (import 회귀)
# ══════════════════════════════════════════════════════════════════════


def test_a_person_entity_does_not_blow_up_the_validator():
    """★회귀 그물. 판정 함수 import 가 빠지면 여기서 `NameError` 로 죽는다."""
    doc, report = validate_executives(_doc([_person(birth_year_month="1970-05")]))

    assert report.warned == []
    assert len(doc.entities) == 1


def test_an_implausible_birth_year_is_warned():
    doc, report = validate_executives(_doc([_person(birth_year_month="1780-05")]))

    assert len(report.warned) == 1
    assert "1780-05" in report.warned[0]
    assert doc.entities[0].properties["_validation_warnings"] == report.warned


def test_a_malformed_birth_year_month_is_warned():
    """★`YYYY-MM` 이 아니면 값의 범위를 볼 수도 없다."""
    _, report = validate_executives(_doc([_person(birth_year_month="1970")]))

    assert len(report.warned) == 1


def test_a_missing_birth_year_month_passes():
    """★없는 것은 틀린 것이 아니다 — DART 가 안 주는 경우가 있다."""
    _, report = validate_executives(_doc([_person()]))

    assert report.warned == []


def test_the_person_survives_the_warning():
    """★**경고이지 드롭이 아니다.** 생년월이 이상하다고 사람을 지우면
    관계가 끊겨 임원이 통째로 사라진다."""
    doc, _ = validate_executives(_doc([_person(birth_year_month="3000-01")]))

    assert [e.key for e in doc.entities] == ["p_1"]


def test_non_person_entities_are_left_alone():
    company = EntityDTO(type="Company", key="00126380",
                        properties={"birth_year_month": "0000-99"})
    _, report = validate_executives(_doc([company]))

    assert report.warned == []


# ══════════════════════════════════════════════════════════════════════
#  관계 속성 — ★이쪽은 **드롭한다**
# ══════════════════════════════════════════════════════════════════════


def test_a_tenure_out_of_range_is_dropped():
    doc, report = validate_executives(
        _doc(relationships=[_is_executive_of(tenure_months=2000)]))

    assert doc.relationships == []
    assert "tenure_months" in report.dropped[0]


def test_a_non_iso_settlement_date_is_dropped():
    doc, report = validate_executives(
        _doc(relationships=[_is_executive_of(settlement_date="2026/03/31")]))

    assert doc.relationships == []
    assert "settlement_date" in report.dropped[0]


def test_both_reasons_are_reported_together():
    """★하나만 알려 주면 고치고 다시 돌렸을 때 또 걸린다."""
    _, report = validate_executives(_doc(relationships=[
        _is_executive_of(tenure_months=-1, settlement_date="어제")]))

    assert len(report.dropped) == 1
    assert "tenure_months" in report.dropped[0]
    assert "settlement_date" in report.dropped[0]


def test_a_clean_relationship_survives():
    doc, report = validate_executives(_doc(relationships=[
        _is_executive_of(tenure_months=36, settlement_date="2026-03-31")]))

    assert len(doc.relationships) == 1
    assert report.dropped == []


def test_missing_properties_are_not_a_failure():
    """★검사할 값이 없는 것과 값이 틀린 것은 다르다."""
    doc, report = validate_executives(_doc(relationships=[_is_executive_of()]))

    assert len(doc.relationships) == 1
    assert report.dropped == []
