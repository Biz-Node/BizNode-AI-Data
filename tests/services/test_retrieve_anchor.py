"""`RetrieveService` 가 앵커를 싣고, `/ask` 전용 입구를 따로 갖는가.

★**`/retrieve` 동작은 안 바뀐다**(설계서 §14-5). SEMANTIC 은 `/retrieve` 에서
  여전히 살아 있는 경로다 — 바뀌는 것은 `/ask` 뿐이다. 이 파일은 그 경계를 본다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.schemas import Anchor, AnchorSource, AskRequest
from app.services import retrieve_service as rs_module
from app.services.query_understanding import AnchorDecision
from app.services.retrieve_service import RetrieveService
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import SearchMode

from datetime import date

_WS = {"00126380": "삼성전자"}


def _orchestrator(mode=SearchMode.NAME):
    query = SearchQuery(raw_query="q", normalized_query="q", mode=mode,
                        today=date(2026, 8, 25))
    result = SearchResult(query="q", mode=mode, hits=[], total=0, took_ms=1,
                          cache_hit=False, used_semantic_fallback=False)
    orchestrator = MagicMock()
    orchestrator.search.return_value = (query, result)
    return orchestrator


@pytest.fixture
def wired(monkeypatch):
    """이름 조회와 판정을 세운다 — 조립 경로만 본다."""
    state = {"decision": AnchorDecision(source=AnchorSource.ANCHORLESS,
                                        workspace_names=_WS)}
    monkeypatch.setattr(rs_module.workspace_service, "names_of", lambda keys: _WS)
    monkeypatch.setattr(rs_module.query_understanding, "decide_anchor",
                        lambda *a, **k: state["decision"])
    return state


def _request():
    return AskRequest(question="q", workspace_keys=["00126380"])


# ══════════════════════════════════════════════════════════════════════
#  /retrieve — anchors 를 싣되 재료 선택은 그대로
# ══════════════════════════════════════════════════════════════════════

def test_retrieve_carries_the_anchors(wired):
    anchors = [Anchor(key="00126380", name="삼성전자", source=AnchorSource.QUERY)]
    wired["decision"] = AnchorDecision(source=AnchorSource.QUERY, anchors=anchors,
                                       workspace_names=_WS)
    got = RetrieveService(_orchestrator()).retrieve(_request())
    assert [(a.key, a.source) for a in got.anchors] == [
        ("00126380", AnchorSource.QUERY)]


def test_anchorless_retrieve_carries_no_anchors(wired):
    """★앵커가 없다는 것이 **응답에도 드러난다**(최종 설계 §17-3).

    전에는 `source=workspace` 로 담아 둔 기업이 `anchors[]` 에 실렸다. 프론트가
    그것을 「이 기업들에 대해 답했다」로 읽었는데, 질문은 그 기업들을 묻지 않았다.
    """
    got = RetrieveService(_orchestrator()).retrieve(_request())
    assert got.anchors == []


def test_retrieve_still_assembles_material_when_unresolved(wired):
    """★`/retrieve` 는 **조기 반환하지 않는다.** unresolved 처리는 `/ask` 의
    규약이고(설계서 §14-4), `/retrieve` 의 SEMANTIC 경로는 무변경이다(§14-5)."""
    wired["decision"] = AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names=_WS)
    got = RetrieveService(_orchestrator(SearchMode.SEMANTIC)).retrieve(_request())
    assert got is not None
    assert got.question == "q"


def test_retrieve_asks_for_workspace_names_once(wired, monkeypatch):
    """★이름 조회는 **경계에서 한 번**이다(설계서 §16-3) — ①b 는 대조만 한다."""
    calls = []
    monkeypatch.setattr(rs_module.workspace_service, "names_of",
                        lambda keys: calls.append(list(keys)) or _WS)
    RetrieveService(_orchestrator()).retrieve(_request())
    assert calls == [["00126380"]]


# ══════════════════════════════════════════════════════════════════════
#  /ask 전용 입구 — unresolved 면 재료를 만들지 않는다
# ══════════════════════════════════════════════════════════════════════

def test_retrieve_for_ask_skips_material_when_unresolved(wired):
    """★못 찾은 대상에 워크스페이스 재료를 붙이면 그게 곧 조용한 오답이다
    (설계서 §14-4). 조립 자체를 하지 않는다."""
    wired["decision"] = AnchorDecision(source=AnchorSource.UNRESOLVED, named="TSMC",
                                       workspace_names=_WS)
    decision, retrieved = RetrieveService(
        _orchestrator(SearchMode.SEMANTIC)).retrieve_for_ask(_request())
    assert decision.source is AnchorSource.UNRESOLVED
    assert retrieved is None


def test_retrieve_for_ask_returns_material_when_anchored(wired):
    decision, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert decision.source is AnchorSource.ANCHORLESS
    assert retrieved is not None
    assert retrieved.question == "q"


def test_retrieve_for_ask_searches_before_judging(wired, monkeypatch):
    """★①b 는 ② Search **뒤**다(설계서 §10) — `resolved_entities` 가 ② 의
    산출물이라 그 전에는 판정할 수 없다."""
    order = []
    orchestrator = _orchestrator()

    def _search(req):
        order.append("search")
        return orchestrator.search.return_value

    orchestrator.search.side_effect = _search
    monkeypatch.setattr(rs_module.query_understanding, "decide_anchor",
                        lambda *a, **k: order.append("decide") or wired["decision"])
    RetrieveService(orchestrator).retrieve_for_ask(_request())
    assert order == ["search", "decide"]
