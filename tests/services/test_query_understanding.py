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
#  ★사명은 **붙어 있던 덩어리 단위**로 본다 (현황서 §5-15 해소)
#
#  Kiwi 는 사명을 쪼갠다 — `SK하이닉스에` → SK/SL + 하이닉스/NNP + 에/JKB.
#  조각을 그대로 후보로 쓰면 두 방향으로 틀린다:
#
#      오탐   「TSMC반도체홀딩스코리아」의 조각 `TSMC` 가 실존 기업을 문다
#      미탐   「SK하이닉스」가 조각 `SK`(SK주식회사)로 해소된다
#
#  그래서 **원문에서 사이가 벌어져 있지 않았던 내용어를 도로 붙인 덩어리** 중
#  고유명사를 품은 것만 후보로 쓴다(`token_overlap.merged_spans`).
#
#  ★실측 2026-09-10 — 질의 758건(실존 사명 58곳 × 7틀 + 합성 사명 348 + 허구 4).
#    ①b 2단으로 내려오는 539건에서 **오탐 121 → 12**, 정답 111 → 110.
#    앵커리스 판정은 774건 전수에서 **공집합 동치가 어긋나지 않았다** — 덩어리는
#    고유명사를 품어야 남으므로 「고유명사가 없다」가 양쪽에서 같은 뜻이다.
# ══════════════════════════════════════════════════════════════════════

def test_a_made_up_name_does_not_anchor_through_one_of_its_fragments(graph):
    """★실재하지 않는 이름인데 그 **조각**이 실존 기업이라 앵커가 붙던 자리다.

        「TSMC반도체홀딩스코리아는 어떤가?」
          전  Kiwi → 'TSMC'(SL)·'코리아' → find_by_names 가 실존 TSMC 를 문다
          후  덩어리 'TSMC반도체홀딩스코리아' 하나 → 그래프에 없다 → unresolved

    ★`query` 로 나가면 **헤지 없이** 나간다 — 「못 찾았다」와 「이 회사 얘기다」는
      사용자에게 전혀 다른 말이다.
    """
    graph["companies"]["TSMC"] = {"key": "tsmc", "name": "TSMC", "corp_code": None}
    decision = qu.decide_anchor("TSMC반도체홀딩스코리아는 어떤가?", [], _WS)
    assert decision.source is AnchorSource.UNRESOLVED
    # ★**무엇을 못 찾았는지도 덩어리로 말한다** — 「TSMC 를 못 찾았다」는 거짓이다.
    assert decision.named == "TSMC반도체홀딩스코리아"


def test_a_split_company_name_is_put_back_together(graph):
    """★`SK하이닉스에` 를 쪼갠 채 두면 `SK` 가 **SK주식회사로 실제로 해소된다.**"""
    assert qu._name_tokens("SK하이닉스에 납품하는 기업은?") == ["SK하이닉스"]
    assert qu._name_tokens("TSMC반도체홀딩스코리아는 어떤가?") == ["TSMC반도체홀딩스코리아"]


def test_a_question_that_names_no_company_stays_anchorless(graph):
    """★**앵커리스를 죽이면 안 된다.** 덩어리를 그냥 쓰면 「최근」·「업계」가
    대상이 되어 Global Event Search 가 통째로 막힌다 — 실측에서 12/12 가
    `unresolved` 로 뒤집혔다. 고유명사를 품은 덩어리만 남기는 이유다."""
    for question in ("최근 반도체 업계 주요 이슈가 뭐야?", "납품 단가 압박",
                     "메모리 가격 담합 관련 소식", "최근 인수 사례"):
        assert qu._name_tokens(question) == [], question
        assert qu.decide_anchor(question, [], _WS).source is AnchorSource.ANCHORLESS


def test_a_common_noun_glued_to_a_name_is_not_split_back_off(graph):
    """★붙여 쓴 「삼성전자주가」는 덩어리 하나다 — 조각으로 되돌리지 않는다.

    ★**그래서 미탐이 하나 남는다.** 합성 사명과 구별할 신호가 없어서인데,
      실측(2026-09-10)에서 이 형태 58건 중 **26건은 ①b 1단(corp_code)이 흡수**한다.
      조각 폴백을 넣으면 §5-15 가 그대로 돌아온다 — 재 봤고, 오탐이 121 로 되돌아갔다.
    """
    graph["companies"]["삼성전자"] = {"key": _SAMSUNG, "name": "삼성전자",
                                   "corp_code": _SAMSUNG}
    assert qu._name_tokens("삼성전자주가 알려줘") == ["삼성전자주가"]
    assert qu.decide_anchor("삼성전자주가 알려줘", [], {}).source is AnchorSource.UNRESOLVED


# ══════════════════════════════════════════════════════════════════════
#  ★두 기업을 물으면 둘 다 앵커다 (§6-0 A-7)
#
#  실측(2026-09-05) — 「삼성전자와 SK하이닉스는 무슨 관계야?」에서 앵커가
#  SK하이닉스 하나뿐이라 재료 기업도 1곳이었다. 그래프에는 두 기업을 **직접 잇는
#  엣지가 3건**(공급·경쟁·소송) 있는데, 앵커 기업의 관계 225~566건 중 상한 10 에
#  밀려 하나도 안 실렸다. 네 쌍을 재서 4/4 가 같았다.
# ══════════════════════════════════════════════════════════════════════

def test_a_second_named_company_becomes_an_anchor_too(graph):
    graph["companies"]["삼성전자"] = {"key": _SAMSUNG, "name": "삼성전자",
                                   "corp_code": _SAMSUNG}
    decision = qu.decide_anchor("삼성전자와 SK하이닉스는 무슨 관계야?",
                                [_resolution(_HYNIX, "SK하이닉스")], {})
    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == [_HYNIX, _SAMSUNG]
    assert all(a.source is AnchorSource.QUERY for a in decision.anchors)


def test_a_partial_token_of_the_first_anchor_is_not_a_second_anchor(graph):
    """★실측 — `SK` 는 **SK주식회사**(00144155)로 실제로 해소된다. 「SK하이닉스」를
    쪼갠 조각이 별개 기업으로 앵커가 되면 묻지 않은 대상이 재료에 들어온다.

    ★후보가 덩어리가 된 뒤로는(§5-15) 「SK하이닉스」가 애초에 안 쪼개진다.
      이 줄이 남는 이유는 **남는 토큰이 없으면 조회를 안 하는** 비용 쪽이다.
    """
    graph["companies"]["SK"] = {"key": "00144155", "name": "SK", "corp_code": "00144155"}
    decision = qu.decide_anchor("SK하이닉스 소송 상황",
                                [_resolution(_HYNIX, "SK하이닉스")], {})
    assert [a.key for a in decision.anchors] == [_HYNIX]


def test_the_second_anchor_is_the_whole_name_not_a_fragment(graph):
    """★A-7 이 절반만 맞았다 (실측 2026-09-10 · §5-15 가 드러냄).

        「삼성전자와 SK하이닉스는 무슨 관계야?」
          전  2차 앵커 = `SK`(SK주식회사 00144155)   🔴 묻지 않은 회사다
          후  2차 앵커 = SK하이닉스

    `_second_anchor` 의 부분 문자열 차단은 **1차 앵커 이름의 조각**만 막는다.
    2차 쪽이 쪼개진 것은 못 막았다 — 덩어리로 보면 애초에 안 쪼개진다.
    """
    graph["companies"]["SK"] = {"key": "00144155", "name": "SK", "corp_code": "00144155"}
    graph["companies"]["SK하이닉스"] = {"key": _HYNIX, "name": "SK하이닉스",
                                    "corp_code": _HYNIX}
    decision = qu.decide_anchor("삼성전자와 SK하이닉스는 무슨 관계야?",
                                [_resolution(_SAMSUNG, "삼성전자")], {})
    assert [a.key for a in decision.anchors] == [_SAMSUNG, _HYNIX]


def test_a_leftover_token_that_is_no_company_adds_no_anchor(graph):
    decision = qu.decide_anchor("삼성전자와 반도체 업황",
                                [_resolution(_SAMSUNG, "삼성전자")], {})
    assert [a.key for a in decision.anchors] == [_SAMSUNG]


def test_a_single_target_question_still_costs_one_graph_lookup(monkeypatch):
    """★불변식 — 남는 토큰이 없으면 **2차 조회 자체가 안 일어난다.** 41건 중 33건이
    이 경로다(§8-5). 여기가 늘면 종단 지연이 전 질의에서 는다."""
    calls = []
    monkeypatch.setattr(qu.company_service, "names_by_keys",
                        lambda keys: calls.append(("exists", tuple(keys)))
                        or {k: k for k in keys})
    monkeypatch.setattr(qu.company_service, "find_by_names",
                        lambda n: calls.append(("find", tuple(n))) or None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: calls.append("label") or {})

    qu.decide_anchor("삼성전자 실적", [_resolution(_SAMSUNG, "삼성전자")], _WS)

    assert calls == [("exists", (_SAMSUNG,))]


def test_a_second_target_costs_exactly_one_more_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(qu.company_service, "names_by_keys",
                        lambda keys: calls.append(("exists", tuple(keys)))
                        or {k: k for k in keys})
    monkeypatch.setattr(qu.company_service, "find_by_names",
                        lambda n: calls.append(("find", tuple(n))) or None)
    monkeypatch.setattr(qu.company_service, "non_company_labels",
                        lambda n: calls.append("label") or {})

    qu.decide_anchor("삼성전자와 SK하이닉스는 무슨 관계야?",
                     [_resolution(_HYNIX, "SK하이닉스")], {})

    assert calls == [("exists", (_HYNIX,)), ("find", ("삼성전자",))]
