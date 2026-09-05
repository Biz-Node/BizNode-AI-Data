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
    """그래프 조회 셋을 가짜로 세운다 — 판정 규칙만 본다.

    ★`missing_keys` 는 **「해소는 됐는데 그래프엔 없다」**를 만드는 손잡이다
      (§6-0 A-2). 비워 두면 모든 key 가 그래프에 있는 것으로 본다 — 그래야
      이 파일의 기존 시험들이 뜻을 그대로 유지한다.
    """
    state = {"companies": {}, "non_company": {}, "missing_keys": set()}

    monkeypatch.setattr(qu.company_service, "names_by_keys",
                        lambda keys: {k: k for k in keys
                                      if k and k not in state["missing_keys"]})
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
#  anchorless — 질문이 대상을 지정하지 않았다
# ══════════════════════════════════════════════════════════════════════

def test_question_without_a_named_target_is_anchorless(graph):
    """★**워크스페이스를 앵커로 승격시키지 않는다**(최종 설계 §17-3).

    전에는 여기서 담아 둔 두 기업이 `source=workspace` 앵커가 됐다. 그러면
    「납품 단가 압박」이 「삼성전자·SK하이닉스의 납품 단가 압박」으로 조용히
    바뀐다 — 질문이 묻지 않은 대상이다.
    """
    decision = qu.decide_anchor("납품 단가 압박", [], _WS)
    assert decision.source is AnchorSource.ANCHORLESS
    assert decision.anchors == []
    # 랭킹·표기용으로는 그대로 따라간다.
    assert decision.workspace_names == _WS


def test_product_name_is_not_a_named_target(graph):
    """★실측이 잡아낸 오탐(현황서 §8-5) — 「HBM을 만드는 기업」의 `HBM` 은 `SL`
    태그가 붙지만 **Product 노드**다. 기업을 지목한 것이 아니다."""
    graph["non_company"]["HBM"] = "Product"
    decision = qu.decide_anchor("HBM을 만드는 기업", [], _WS)
    assert decision.source is AnchorSource.ANCHORLESS


def test_non_company_filter_matches_exactly_not_by_substring(graph):
    """★`CONTAINS` 로 하면 「삼성전자」가 Event 이름에, 「엔비디아」가 Product
    이름에 걸려 **실존 기업이 통째로 억제**된다(실측 2026-08-25). 정확 일치만 본다."""
    graph["non_company"]["자스트리브노고르스크 서버랙"] = "Product"   # 부분 문자열로는 걸려선 안 된다
    assert qu.decide_anchor("자스트리브노고르스크는 어떤가?", [], _WS).source is AnchorSource.UNRESOLVED


def test_empty_workspace_is_just_anchorless_too(graph):
    """★워크스페이스 유무가 **판정을 바꾸지 않는다**(최종 설계 §17-3).

    있든 없든 「질문이 대상을 지정하지 않았다」는 같은 사실이다. 전에는 있으면
    `workspace`, 없으면 거절이었는데 지금은 둘 다 `anchorless` 다 — 거부는
    아예 사라졌다(§17-1).
    """
    with_ws = qu.decide_anchor("납품 단가 압박", [], _WS)
    without_ws = qu.decide_anchor("납품 단가 압박", [], {})

    assert with_ws.source is without_ws.source is AnchorSource.ANCHORLESS
    assert with_ws.anchors == without_ws.anchors == []


# ══════════════════════════════════════════════════════════════════════
#  비용 — 해소에 성공하면 **존재 확인 한 번**으로 끝낸다
# ══════════════════════════════════════════════════════════════════════

def test_a_resolved_question_costs_one_graph_lookup(monkeypatch):
    """★계약이 바뀌었다(2026-09-05 · §6-0 A-2). 전에는 **그래프를 아예 안 건드리는
    것**이 계약이었고, 그래서 「해소됐다 ≠ 그래프에 있다」를 아무도 안 봤다 —
    죽은 앵커가 그대로 통과해 재료가 0 이 됐다.

    ★**fallback 조회는 여전히 안 부른다.** 늘어난 것은 존재 확인 하나(실측
      6.5ms · 종단 15초의 0.04%)뿐이다. 41건 중 33건이 이 경로다(§8-5)."""
    calls = []
    monkeypatch.setattr(qu.company_service, "names_by_keys",
                        lambda keys: calls.append(("exists", tuple(keys)))
                        or {k: k for k in keys})
    monkeypatch.setattr(qu.company_service, "find_by_names",
                        lambda n: calls.append("find") or None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: calls.append("label") or {})

    qu.decide_anchor("삼성전자 실적", [_resolution(_SAMSUNG, "삼성전자")], _WS)

    assert calls == [("exists", (_SAMSUNG,))], "존재 확인 하나로 끝나야 한다"


# ══════════════════════════════════════════════════════════════════════
#  ★해소됐다 ≠ 그래프에 있다 (§6-0 A-2)
# ══════════════════════════════════════════════════════════════════════

def test_a_resolved_key_the_graph_does_not_have_is_not_an_anchor(graph):
    """★회귀 그물. 「요즘」·「대상」·「미래」·「오늘」·「우리」가 **실제 사명**이라
    1.000 으로 정확히 붙는데 **그래프엔 하나도 없다**(실측 13개 낱말 중 12개).
    앵커로 세우면 재료가 통째로 0 이 되고 답이 죽는다 —
    실측 전: 관계 0 · 사건 0 · 근거 0 → 「확인되지 않았습니다」.

    ★닫힌 낱말 목록으로 막지 않는다. 「그래프에 있나」 하나로 전부 걸린다."""
    graph["missing_keys"].add("01719318")

    decision = qu.decide_anchor("요즘 반도체 업계 어때?",
                                [_resolution("01719318", "요즘")], _WS)

    assert decision.source is AnchorSource.ANCHORLESS
    assert decision.anchors == []


def test_a_resolved_key_the_graph_has_is_still_an_anchor(graph):
    """★기존 동작. 이 줄이 깨지면 고친 게 아니라 부순 것이다."""
    decision = qu.decide_anchor("삼성전자 실적",
                                [_resolution(_SAMSUNG, "삼성전자")], _WS)

    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == [_SAMSUNG]


def test_a_key_missing_from_the_graph_falls_through_to_the_name_lookup(graph):
    """★떨어뜨리지 않고 **아래로 흘린다.** `corp_code` 가 그래프에 없어도 이름이
    거기 있으면 2단이 문다 — 두 표기가 갈린 기업을 잃지 않는다."""
    graph["missing_keys"].add("99999999")
    graph["companies"]["TSMC"] = {"key": "tsmc", "name": "TSMC", "corp_code": None}

    decision = qu.decide_anchor("TSMC 리스크", [_resolution("99999999", "TSMC")], _WS)

    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == ["tsmc"]


def test_question_without_name_tokens_skips_the_non_company_lookup(monkeypatch):
    """★고유명사가 하나도 없으면 걸러낼 것도 없다 — 조회하지 않는다."""
    called = []
    monkeypatch.setattr(qu.company_service, "find_by_names", lambda n: None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: called.append(n) or {})
    assert qu.decide_anchor("납품 단가 압박", [], _WS).source is AnchorSource.ANCHORLESS
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
