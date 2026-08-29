"""`context_keys` — 「지금 **보고 있는** 기업」을 앵커로 (2026-08-29).

★**왜 워크스페이스와 갈라 받나.** 기업 상세 페이지에서 「이 회사 노조 리스크
  어때?」를 물으면 「이 회사」는 **화면이 알고 문장은 모른다.** 그 값을 받을 자리가
  없어서, 여태 이런 질문은 두 갈래로만 갔다:

      워크스페이스가 있으면   담아 둔 기업으로 답한다   ← 물은 것과 다른 대상
      워크스페이스가 비면     검색조차 안 하고 거절     ← 답이 있는데 안 준다

  둘 다 §14-3 이 막으려는 「물은 것과 다른 대상으로 답하기」의 같은 종류다.

★**판정 순서가 이 기능의 전부다.**

      query        질문이 이름을 지목했고 해소됐다
      unresolved   지목했는데 못 찾았다            ← 여전히 여기서 끝낸다
      context      **보고 있는 기업**              ← 신설 · 워크스페이스보다 먼저
      workspace    담아 둔 기업
      halt         셋 다 없다

★이 파일이 보는 것은 **판정과 게이트**다. 재료를 어떻게 모으는지는 안 본다 —
  `_scope_keys()` 가 `companies + decision.anchors` 라 새 앵커가 자동으로 들어가고,
  그건 `test_material_anchor.py` 가 이미 지키는 계약이다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource, AskRequest, MatchType
from app.graph.nodes import answer as answer_nodes
from app.graph.nodes.material import _has_starting_point, has_workspace
from app.llm.prompt import TARGET_NOTE_BY_SOURCE
from app.services import query_understanding as qu
from app.services.query_understanding import AnchorDecision
from pipeline.normalizer.resolver import Resolution

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_HYUNDAI = "00164742"
_WS = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}
_CTX = {_HYUNDAI: "현대자동차"}


def _resolution(corp_code: str, corp_name: str, score: float = 1.0) -> Resolution:
    return Resolution(corp_code=corp_code, corp_name=corp_name, stock_code=None,
                      method="exact", score=score)


@pytest.fixture
def graph(monkeypatch):
    """그래프 조회 둘을 가짜로 세운다 — 판정 규칙만 본다.

    `test_query_understanding.py` 와 **같은 모양**이다. 판정기를 두 파일이
    보는데 가짜가 갈리면 「어느 쪽이 진짜 규칙인가」를 못 따진다.
    """
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
#  판정 순서 — context 는 workspace 보다 **먼저**, unresolved 보다 **뒤**
# ══════════════════════════════════════════════════════════════════════

def test_context_beats_workspace(graph):
    """★이 테스트가 이 기능의 핵심이다.

    담아 둔 기업이 있어도, 화면이 보여주는 기업이 있으면 그쪽이 대상이다.
    반대로 가면 「현대자동차 페이지를 보며 물었는데 삼성전자로 답하는」 것이 된다.
    """
    decision = qu.decide_anchor("이 회사 노조 리스크 어때?", [], _WS, _CTX)

    assert decision.source is AnchorSource.CONTEXT
    assert [(a.key, a.name, a.source) for a in decision.anchors] == [
        (_HYUNDAI, "현대자동차", AnchorSource.CONTEXT)]


def test_workspace_still_wins_when_there_is_no_context(graph):
    """★기존 동작을 안 바꾼다 — `context_names` 가 비면 예전 그대로다."""
    decision = qu.decide_anchor("이 회사 노조 리스크 어때?", [], _WS, {})

    assert decision.source is AnchorSource.WORKSPACE
    assert {a.key for a in decision.anchors} == set(_WS)


def test_context_names_default_to_empty(graph):
    """★4번째 인자를 **안 주면** 예전과 똑같아야 한다.

    부르는 쪽이 셋인데(`material.resolve_anchor`·`retrieve_service._search`·
    테스트) 하나라도 빠뜨리면 그 경로만 조용히 다르게 동작한다. 기본값이
    「보고 있는 기업 없음」인 것이 그 방어다.
    """
    assert qu.decide_anchor("이 회사 어때?", [], _WS).source is AnchorSource.WORKSPACE


def test_query_still_beats_context(graph):
    """질문이 이름을 지목하고 해소됐으면 그쪽이 먼저다 — 화면보다 문장이 우선."""
    decision = qu.decide_anchor("SK하이닉스 소송 상황",
                                [_resolution(_HYNIX, "SK하이닉스")], _WS, _CTX)

    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == [_HYNIX]


def test_unresolved_still_beats_context(graph):
    """★§14-3 의 방어 장치가 살아 있다.

    「TSMC 어때?」를 현대자동차 페이지에서 물었다면, 답은 **못 찾았다**이지
    현대자동차가 아니다. context 로 갈아타면 그게 곧 §14-3 이 막으려는
    「물은 것과 다른 대상으로 답하기」다.
    """
    decision = qu.decide_anchor("TSMC는 어떤가?", [], _WS, _CTX)

    assert decision.source is AnchorSource.UNRESOLVED
    assert decision.named == "TSMC"
    assert decision.anchors == []


def test_context_company_named_in_the_question_resolves_to_query(graph):
    """질문이 **보고 있는 기업의 이름**을 썼으면 `query` 다 — 문장이 지목했다.

    `ws_hit` 과 같은 자리에서 처리한다. 갈라 둘 이유가 없다 — 둘 다 「질문에
    이름이 있는데 ② Search 가 못 해소했다」이고, 찾으면 질문이 지목한 것이다.
    """
    graph["companies"]["현대자동차"] = {"key": _HYUNDAI, "name": "현대자동차"}

    decision = qu.decide_anchor("현대자동차 노조 어때?", [], {}, _CTX)

    assert decision.source is AnchorSource.QUERY
    assert [a.key for a in decision.anchors] == [_HYUNDAI]


def test_decision_always_carries_context_names(graph):
    """★`workspace_names` 와 **같은 규약** — `source` 와 무관하게 항상 채운다.

    문구를 조립하는 쪽이 같은 조회를 또 하지 않게 하려는 값이다.
    """
    for question, resolved in (("이 회사 어때?", []),
                               ("SK하이닉스 소송", [_resolution(_HYNIX, "SK하이닉스")]),
                               ("TSMC는 어떤가?", [])):
        assert qu.decide_anchor(question, resolved, _WS, _CTX).context_names == _CTX


# ══════════════════════════════════════════════════════════════════════
#  게이트 — 「워크스페이스가 비었나」가 아니라 「출발점이 없나」
# ══════════════════════════════════════════════════════════════════════

def _state(**kwargs) -> dict:
    return {"request": AskRequest(question="이 회사 어때?", **kwargs)}


def test_context_only_passes_the_gate():
    """★①번 문제(빈 워크스페이스에서 답 못 함)가 여기서 풀린다.

    기업 상세 페이지에서 묻는 질문은 담아 둔 것이 없어도 대상이 있다.
    """
    state = _state(context_keys=[_HYUNDAI])

    assert _has_starting_point(state) is True
    assert has_workspace(state) == "search"


def test_workspace_only_still_passes():
    assert has_workspace(_state(workspace_keys=[_SAMSUNG])) == "search"


def test_neither_halts():
    """★둘 다 없으면 **검색조차 하지 않는다**(설계서 §16-2). 이건 안 바뀐다."""
    state = _state()

    assert _has_starting_point(state) is False
    assert has_workspace(state) == "halt_no_material"


def test_empty_string_keys_do_not_open_the_gate():
    """★`[""]` 는 길이가 1 이라 `bool()` 로 보면 게이트가 열린다.

    그러면 출발점이 없는 채로 검색까지 가고, `names_of([""])` 가 아무것도 못
    찾아 앵커도 안 선다 — 「거절」이 아니라 **빈 답**이 나가는 길이다.
    `scope.anchor_scope()` 가 빈 key 를 거르는 것과 같은 규약으로 막는다.
    """
    assert _has_starting_point(_state(context_keys=[""])) is False
    assert _has_starting_point(_state(workspace_keys=["", ""])) is False
    assert _has_starting_point(_state(workspace_keys=[""],
                                      context_keys=["", _HYUNDAI])) is True


# ══════════════════════════════════════════════════════════════════════
#  halt 문구 — 사용자가 할 일이 다르다
# ══════════════════════════════════════════════════════════════════════

def test_halt_message_for_no_starting_point():
    state = _state() | {"decision": AnchorDecision(source=AnchorSource.UNRESOLVED)}
    answer = answer_nodes.halt_no_material(state)["response"].answer

    assert "담긴 기업이 없어" in answer


def test_halt_message_with_context_is_the_unresolved_one():
    """★`workspace_keys` 만 보면 여기서 **엉뚱한 문구**가 나간다.

    보고 있는 기업은 있는데 이름을 못 찾아 온 길이다 — 사용자가 할 일은
    기업을 담는 것이 아니라 **다시 묻는 것**이다.
    """
    state = _state(context_keys=[_HYUNDAI]) | {
        "decision": AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                   context_names=_CTX)}
    answer = answer_nodes.halt_no_material(state)["response"].answer

    assert "TSMC" in answer
    assert "담긴 기업이 없어" not in answer


# ══════════════════════════════════════════════════════════════════════
#  프롬프트 — 표기가 갈려야 LLM 이 대상을 안 헷갈린다
# ══════════════════════════════════════════════════════════════════════

def test_target_note_for_context_is_not_the_workspace_one():
    """★한 문구를 돌려 쓰면 LLM 이 보고 있는 기업을 워크스페이스 기업으로 읽는다."""
    note = TARGET_NOTE_BY_SOURCE[AnchorSource.CONTEXT]

    assert note and note != TARGET_NOTE_BY_SOURCE[AnchorSource.WORKSPACE]
    assert "보고 있는 기업" in note


def test_every_anchor_source_has_a_target_note():
    """★전수 분기 dict 다 — 값이 늘면 문구도 늘어야 하고, 안 늘면 KeyError 로 죽는다."""
    assert set(TARGET_NOTE_BY_SOURCE) == set(AnchorSource)


def test_context_names_do_not_become_workspace_membership():
    """★**보고 있는 기업을 「=워크스페이스」로 표기하면 안 된다.**

    담지도 않은 기업을 담긴 것으로 말하는 셈이고, `evidence_about` 의 귀속도
    같은 집합을 읽는다. 머리말 한 줄만 늘고 소속 표기는 안 바뀌어야 한다.
    """
    from app.graph import prompt

    facts = prompt.fact_lines(
        match_type=MatchType.EXACT,
        companies=[], events=[], relations=[], propagation=[], evidence=[],
        workspace_keys=set(_WS), workspace_names=_WS, context_names=_CTX)

    assert "보고 있는 기업: 현대자동차" in facts
    assert "현대자동차=워크스페이스" not in facts
