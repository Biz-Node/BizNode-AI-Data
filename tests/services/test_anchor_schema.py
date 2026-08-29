"""`anchor_source`·`anchors[]` 계약 — 스키마만 본다(설계서 §5 계약 변경).

★**판정 로직은 여기 없다.** 값을 누가 채우는가는 `query_understanding` 이
  생긴 뒤의 일이고([현황서 §6-2](../../docs/BizNode_Search_Layer_현황서.md) ② 단계),
  이 파일은 「계약이 그 값을 담을 수 있는가」와 「기존 호출이 안 깨지는가」만 본다 —
  `tests/search/test_example_queries.py` 가 DTO 에 대해 하는 것과 같은 성격이다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (Anchor, AnchorSource, AskResponse, MatchType,
                             RelationEndpoint, RetrieveResponse)


# ── AnchorSource — 설계서 §14-3 의 세 값 + `context` ────────────────────
def test_anchor_source_has_exactly_four_values():
    """★값이 늘면 답변 형태 분기(§14-6)와 `unresolved` 처리(§14-4)가 함께
    바뀌어야 한다 — 조용히 늘어나지 않게 못 박는다.

    ★**`context` 가 늘어난 것은 2026-08-29 이고, 이 테스트가 그것을 잡았다.**
      함께 바뀐 자리를 여기 적어 둔다 — 다음에 값이 늘 때 무엇을 따라 고쳐야
      하는지가 이 목록이다:

          Anchor.source              Literal 에 추가 (안 하면 pydantic 이 막는다)
          TARGET_NOTE_BY_SOURCE      표기 문구 (전수 분기 dict — 빠뜨리면 KeyError)
          _SYSTEM_PROMPT             그 대상일 때의 지시. ★[워크스페이스] 절의
                                     범위와 최종검증 12번을 함께 좁혀야 한다
          decide_anchor()            판정 분기와 **순서**
          _has_starting_point()      게이트가 그 출발점을 통과시키나
          halt_no_material()         문구 분기
    """
    assert {s.value for s in AnchorSource} == {
        "query", "context", "workspace", "unresolved"}


# ── Anchor — 재료 앵커 한 건 ─────────────────────────────────────────────
def test_anchor_carries_key_name_and_source():
    anchor = Anchor(key="00164779", name="SK하이닉스", source=AnchorSource.QUERY)
    assert (anchor.key, anchor.name, anchor.source) == (
        "00164779", "SK하이닉스", AnchorSource.QUERY)


def test_anchor_key_may_be_norm_name():
    """★식별은 `corp_code` → `norm_name` 순이다(설계서 §16-1). `corp_code` 가
    없는 기업(TSMC·마이크론)은 `norm_name` 이 key 다 — 8자리 숫자를 강제하지 않는다."""
    assert Anchor(key="tsmc", name="TSMC", source=AnchorSource.QUERY).key == "tsmc"


def test_anchor_rejects_unresolved_as_source():
    """★`anchors[]` 는 **재료가 된 앵커**다. 해소에 실패하면 앵커 자체가 없으므로
    `unresolved` 는 여기 올 수 없다 — 그 값은 `AskResponse.anchor_source` 의 것이다."""
    with pytest.raises(ValidationError):
        Anchor(key="tsmc", name="TSMC", source="unresolved")


# ── RetrieveResponse.anchors ────────────────────────────────────────────
def _retrieved(**kw) -> RetrieveResponse:
    return RetrieveResponse(question="질문", match_type=MatchType.EXACT, **kw)


def test_retrieve_response_anchors_defaults_to_empty():
    """기존 호출이 안 깨진다 — `anchors` 를 안 주면 빈 목록이다."""
    assert _retrieved().anchors == []


def test_retrieve_response_carries_anchors():
    anchors = [Anchor(key="00126380", name="삼성전자", source=AnchorSource.WORKSPACE)]
    assert _retrieved(anchors=anchors).anchors == anchors


def test_companies_and_anchors_are_separate_fields():
    """★`companies` 는 「재료가 된 기업」이고 `anchors` 는 「그 재료를 모은 출발점」이다
    (설계서 §5·현황서 §5-7). 둘이 달라질 수 있어야 한다."""
    retrieved = _retrieved(
        anchors=[Anchor(key="00126380", name="삼성전자", source=AnchorSource.WORKSPACE)],
        companies=[RelationEndpoint(key="01095722", name="심텍")])
    assert [a.key for a in retrieved.anchors] != [c.key for c in retrieved.companies]


# ── AskResponse.anchor_source ───────────────────────────────────────────
def test_ask_response_anchor_source_is_unset_until_judged():
    """★아직 판정기가 없다 — ② 단계 전까지는 `None` 이다. **네 번째 앵커 상태가
    아니라 「아직 안 정했다」**이고, 그래서 `AnchorSource` 에 멤버를 늘리지 않았다."""
    assert AskResponse(answer="…").anchor_source is None


def test_ask_response_carries_anchor_source():
    for source in AnchorSource:
        assert AskResponse(answer="…", anchor_source=source).anchor_source is source


def test_ask_response_has_no_match_type():
    """★`match_type` 은 `AskResponse` 에 싣지 않는다(설계서 부록 A) — 프론트가
    필요한 것은 「무엇을 대상으로 답했나」이고 그건 별개 축이다."""
    assert "match_type" not in AskResponse.model_fields
