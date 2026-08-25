"""`anchor_source` 판정 — 설계서 §14-3 의 두 축을 **하나의 값**으로 못 박는다.

    ①a 질의가 대상을 **명시**했나   ×   ①b 그 대상이 **해소**됐나

★①a 판정 신호는 **실측으로 정했다**(2026-08-25 · 현황서 §8-5, 질의 41건).

    1차   워크스페이스 기업명을 질문 문자열과 직접 대조
    2차   Kiwi 고유명사 토큰(NNP·SL)
          └ 단, **Company 가 아닌 노드 이름과 정확히 일치**하는 토큰은 뺀다
    ①b    corp_code(PostgreSQL) → 실패하면 norm_name(Neo4j)

★**여기서 재료를 모으지 않는다.** 「무엇을 대상으로 답하는가」만 정한다 —
  재료 수집은 `retrieve_service` 의 몫이다(설계서 §10 의 ①b 와 ③ 이 다른 단계다).
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource
from app.services import query_understanding as qu
from pipeline.normalizer.resolver import Resolution

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_WS = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def _resolution(corp_code: str, corp_name: str, score: float = 1.0) -> Resolution:
    return Resolution(corp_code=corp_code, corp_name=corp_name, stock_code=None,
                      method="exact", score=score)


@pytest.fixture
def graph(monkeypatch):
    """그래프 조회 둘을 가짜로 세운다 — 판정 규칙만 본다."""
    state = {"companies": {}, "non_company": {}}

    monkeypatch.setattr(qu.company_service, "find_by_names",
                        lambda names: next(
                            (state["companies"][n] for n in names
                             if n in state["companies"]), None))
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda names: {n: state["non_company"][n] for n in names
                                       if n in state["non_company"]})
    return state


# ══════════════════════════════════════════════════════════════════════
#  query — 질문이 지정한 대상으로 답한다
# ══════════════════════════════════════════════════════════════════════

def test_resolved_entity_becomes_a_query_anchor(graph):
    decision = qu.decide_anchor("SK하이닉스 소송 상황",
                                [_resolution(_HYNIX, "SK하이닉스")], _WS)
    assert decision.source is AnchorSource.QUERY
    assert [(a.key, a.name, a.source) for a in decision.anchors] == [
        (_HYNIX, "SK하이닉스", AnchorSource.QUERY)]


def test_highest_scoring_resolution_wins(graph):
    """★`GraphSearcher._primary_resolution()` 과 **같은 규칙**이다 — 실제로 재료를
    모은 앵커와 응답에 싣는 앵커가 어긋나면 안 된다."""
    decision = qu.decide_anchor(
        "질문", [_resolution("00000001", "낮은쪽", 0.6),
                 _resolution(_SAMSUNG, "삼성전자", 0.9)], _WS)
    assert [a.key for a in decision.anchors] == [_SAMSUNG]


def test_falls_back_to_norm_name_when_corp_code_fails(graph):
    """★실측이 요구한 fallback(현황서 §8-5) — `TSMC` 는 `corp_code_master` 에
    없지만 그래프에는 있다. `unresolved` 로 두면 「데이터에 없다」가 거짓말이 된다."""
    graph["companies"]["TSMC"] = {"key": "tsmc", "name": "TSMC", "corp_code": None}
    decision = qu.decide_anchor("TSMC는 어떤가?", [], _WS)
    assert decision.source is AnchorSource.QUERY
    assert [(a.key, a.name) for a in decision.anchors] == [("tsmc", "TSMC")]


def test_workspace_company_named_in_the_question_is_a_query_anchor(graph):
    """①a 1차 신호 — 워크스페이스 기업명 직접 대조."""
    graph["companies"]["삼성전자"] = {"key": _SAMSUNG, "name": "삼성전자",
                                  "corp_code": _SAMSUNG}
    decision = qu.decide_anchor("삼성전자 실적 어때?", [], _WS)
    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == [_SAMSUNG]


# ══════════════════════════════════════════════════════════════════════
#  unresolved — 못 찾았다고 말하고 끝낸다 (설계서 §14-4)
# ══════════════════════════════════════════════════════════════════════

def test_named_but_unresolvable_target_is_unresolved(graph):
    """★**워크스페이스로 갈아타지 않는다.** 그러면 「TSMC 를 물었는데 삼성전자로
    답하는」 탐지 불가능한 오답이 된다(설계서 §14-3)."""
    decision = qu.decide_anchor("자스트리브노고르스크는 어떤가?", [], _WS)
    assert decision.source is AnchorSource.UNRESOLVED
    assert decision.anchors == []


def test_unresolved_keeps_what_the_user_named(graph):
    """★문구가 「'…' 에 해당하는 기업을 찾지 못했습니다」라고 말하려면 그 문자열이 필요하다."""
    assert qu.decide_anchor("자스트리브노고르스크는 어떤가?", [], _WS).named == "자스트리브노고르스크"


def test_foreign_name_counts_as_named(graph):
    """★Kiwi 는 `TSMC` 를 `NNP` 가 아니라 `SL`(외국어)로 준다 — 실측 2026-08-25."""
    assert qu.decide_anchor("TSMC는 어떤가?", [], _WS).source is AnchorSource.UNRESOLVED


# ══════════════════════════════════════════════════════════════════════
#  workspace — 질문이 대상을 지정하지 않았다
# ══════════════════════════════════════════════════════════════════════

def test_question_without_a_named_target_uses_the_workspace(graph):
    decision = qu.decide_anchor("납품 단가 압박", [], _WS)
    assert decision.source is AnchorSource.WORKSPACE
    assert [(a.key, a.source) for a in decision.anchors] == [
        (_SAMSUNG, AnchorSource.WORKSPACE), (_HYNIX, AnchorSource.WORKSPACE)]


def test_product_name_is_not_a_named_target(graph):
    """★실측이 잡아낸 오탐(현황서 §8-5) — 「HBM을 만드는 기업」의 `HBM` 은 `SL`
    태그가 붙지만 **Product 노드**다. 기업을 지목한 것이 아니다."""
    graph["non_company"]["HBM"] = "Product"
    decision = qu.decide_anchor("HBM을 만드는 기업", [], _WS)
    assert decision.source is AnchorSource.WORKSPACE


def test_non_company_filter_matches_exactly_not_by_substring(graph):
    """★`CONTAINS` 로 하면 「삼성전자」가 Event 이름에, 「엔비디아」가 Product
    이름에 걸려 **실존 기업이 통째로 억제**된다(실측 2026-08-25). 정확 일치만 본다."""
    graph["non_company"]["자스트리브노고르스크 서버랙"] = "Product"   # 부분 문자열로는 걸려선 안 된다
    assert qu.decide_anchor("자스트리브노고르스크는 어떤가?", [], _WS).source is AnchorSource.UNRESOLVED


def test_empty_workspace_still_reports_workspace_source(graph):
    """★빈 워크스페이스의 **거부**는 여기 책임이 아니다(설계서 §16-2) —
    `answer_service` 가 판단한다. 여기서는 「대상을 지정하지 않았다」만 말한다."""
    decision = qu.decide_anchor("납품 단가 압박", [], {})
    assert decision.source is AnchorSource.WORKSPACE
    assert decision.anchors == []


# ══════════════════════════════════════════════════════════════════════
#  비용 — 해소에 성공하면 그래프를 건드리지 않는다
# ══════════════════════════════════════════════════════════════════════

def test_resolved_question_makes_no_graph_call(monkeypatch):
    """★`resolved_entities` 가 있으면 fallback 도 비-Company 조회도 부르지 않는다 —
    실측상 41건 중 33건이 이 경로다(현황서 §8-5)."""
    calls = []
    monkeypatch.setattr(qu.company_service, "find_by_names",
                        lambda n: calls.append("find") or None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: calls.append("label") or {})
    qu.decide_anchor("삼성전자 실적", [_resolution(_SAMSUNG, "삼성전자")], _WS)
    assert calls == []


def test_question_without_name_tokens_skips_the_non_company_lookup(monkeypatch):
    """★고유명사가 하나도 없으면 걸러낼 것도 없다 — 조회하지 않는다."""
    called = []
    monkeypatch.setattr(qu.company_service, "find_by_names", lambda n: None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: called.append(n) or {})
    assert qu.decide_anchor("납품 단가 압박", [], _WS).source is AnchorSource.WORKSPACE
    assert called == []


# ══════════════════════════════════════════════════════════════════════
#  미해결 — 사명의 부분 토큰이 실존 기업으로 해소된다 (현황서 §5-15)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.xfail(strict=True, reason="현황서 §5-15 — 규칙을 얹기 전에 실측이 먼저다")
def test_partial_token_of_a_made_up_name_should_not_anchor(graph):
    """★실재하지 않는 이름인데 그 **조각**이 실존 기업이라 앵커가 붙는다.

        「TSMC반도체홀딩스코리아는 어떤가?」
          Kiwi → 'TSMC'(SL) · '반도체' …         ★합성 사명을 쪼갠다
          → find_by_names 가 실존 TSMC 를 문다   🔴 묻지 않은 기업이 대상이 된다

    ★**전보다 나쁘다.** 전에는 SEMANTIC 이라 헤지라도 걸렸는데 지금은
      `anchor_source=query` 로 헤지 없이 나간다.

    ★고치지 않고 표시만 한다 — 토큰 병합(`token_overlap._merge_adjacent()`)이
      후보지만, 합성 사명이 든 질의로 **오탐/미탐을 다시 재기 전에는** 규칙을
      얹지 않는다. 고쳐지면 이 테스트가 XPASS 로 뒤집혀 갱신하라고 알린다.
    """
    graph["companies"]["TSMC"] = {"key": "tsmc", "name": "TSMC", "corp_code": None}
    decision = qu.decide_anchor("TSMC반도체홀딩스코리아는 어떤가?", [], _WS)
    assert decision.source is AnchorSource.UNRESOLVED
