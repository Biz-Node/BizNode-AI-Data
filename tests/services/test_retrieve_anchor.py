"""`RetrieveService` 가 앵커를 싣는가.

★**전제가 하나 사라졌다**(2026-09-05 · §6-0 A-6). 이 파일은 「`/retrieve` 는
  안 바뀌고 `/ask` 만 바뀐다」는 **경계**를 보고 있었는데, 그 경계가 곧 결함이었다
  — 같은 질문이 입구에 따라 다른 재료를 냈다. 선정은 이제 `material_companies()`
  한 곳이고 두 입구가 그것을 공유한다.

★`SEMANTIC` 은 `/retrieve` 에서 **여전히 살아 있는 경로**다(설계서 §14-5) —
  `match_type` 은 그대로 나간다. 바뀐 것은 「의미 유사 기업을 재료로도 쓰나」다.
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
#  ★죽은 입구를 지웠다 — 남은 불변식만 본다 (2026-09-05)
# ══════════════════════════════════════════════════════════════════════

# ★`retrieve_for_ask()` 를 지웠다(2026-09-05 · §6-0 A-6). 운영에서는 아무도 안
#   부르고 있었다 — `/ask` 가 LangGraph 로 넘어가며 `plan_material` 이 그 일을
#   가져갔는데 옛 입구가 남아, **같은 분기 로직이 세 벌**이 됐고 그중 `/retrieve`
#   한 벌만 판정을 안 쓰고 있었다. 그 입구가 보던 둘은 살아 있는 자리로 옮겼다:
#
#     「unresolved 면 재료를 안 만든다」 → 그래프 조건부 엣지
#         tests/graph/test_conditional_edges.py::test_unresolved_anchor_routes_to_halt
#     「앵커가 있으면 재료가 나온다」   → 아래 `test_material_comes_back_when_anchored`


def test_material_comes_back_when_anchored(wired):
    retrieved = RetrieveService(_orchestrator()).retrieve(_request())
    assert retrieved is not None
    assert retrieved.question == "q"


def test_search_runs_before_the_anchor_is_judged(wired, monkeypatch):
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
    RetrieveService(orchestrator).retrieve(_request())
    assert order == ["search", "decide"]
