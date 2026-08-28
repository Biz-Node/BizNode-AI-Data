"""`source_type` 이 세 스키마에서 **같은 값 집합**을 받는지 고정한다.

Neo4j 엣지에 `dart_filing` 이 113건 실재한다(실측 2026-08-27:
news 8,384 · dart 2,563 · dart_filing 113). `Relation` 만 3값이고
`Evidence`·`Source` 가 2값이면, 근거가 관계에서 답변으로 넘어오는 길목에서
그 113건이 `ValidationError` 로 튕긴다.

★지금 튕기지 않는 이유는 스키마가 맞아서가 아니라 **중간에 눌러서**다 —
  `relation_service.evidence_for_ids()` 가 `"dart" if st.startswith("dart")`
  로 `dart_filing` 을 `dart` 로 접는다. 스키마가 좁으면 그 누르기를 걷어낼
  수 없으므로, 세 곳을 먼저 맞춰 둔다.
"""

import pytest
from pydantic import ValidationError

from app.api.schemas import Evidence, Relation, RelationEndpoint, Source

_ALLOWED = ("dart", "dart_filing", "news")


@pytest.mark.parametrize("source_type", _ALLOWED)
def test_evidence_accepts_all_three_source_types(source_type):
    assert Evidence(evidence_id="ev_1", text="t", source_doc="d",
                    source_type=source_type).source_type == source_type


@pytest.mark.parametrize("source_type", _ALLOWED)
def test_source_accepts_all_three_source_types(source_type):
    assert Source(evidence_id="ev_1", text="t", source_doc="d",
                  source_type=source_type).source_type == source_type


@pytest.mark.parametrize("source_type", _ALLOWED)
def test_relation_accepts_all_three_source_types(source_type):
    end = RelationEndpoint(key="00126380", name="삼성전자")
    assert Relation(edge_id="e1", type="SUPPLIES_TO", source=end, target=end,
                    source_type=source_type).source_type == source_type


@pytest.mark.parametrize("model,kwargs", [
    (Evidence, {"evidence_id": "ev_1", "text": "t", "source_doc": "d"}),
    (Source, {"evidence_id": "ev_1", "text": "t", "source_doc": "d"}),
])
def test_unknown_source_type_is_still_rejected(model, kwargs):
    """넓힌 것이지 연 것이 아니다 — 모르는 값은 그대로 튕겨야 한다."""
    with pytest.raises(ValidationError):
        model(**kwargs, source_type="blog")
