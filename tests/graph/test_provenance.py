"""`provenance` 가 **도구에서 프롬프트 조립까지 끊기지 않고 실려 가는가** (작업 F).

    도구 반환 → Agent 가 본 tool result → evidence_validation → build_prompt

★값은 **`direct` 하나**다. `explored` 는 탐색 도구(`explore_impact`)가 생길 때
  쓴다 — 지금은 그 값을 만드는 코드가 없어야 한다.

★**프롬프트 문구에는 아직 안 내보낸다.** 노출 여부는 아직 정하지 않았고,
  문구를 바꾸면 2차 완료 기준인 평가셋의 측정 대상이 하나 늘어난다. 그래서
  이 파일은 「State·DTO 는 들고 간다 · 프롬프트 글자에는 안 나온다」를
  **양쪽 다** 묶는다. 한쪽만 묶으면 다음 사람이 어느 쪽이 의도인지 모른다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.schemas import MatchType
from app.graph import prompt as graph_prompt
from app.graph.nodes import agent_loop
from app.graph.nodes import answer as answer_node
from app.tools import agent_tools, graph_tools as gt, scope
from app.tools.dto import PROVENANCE_DIRECT, PROVENANCE_EXPLORED

_SAMSUNG = "00126380"
_HYNIX = "00164779"


@pytest.fixture
def match_type():
    return MatchType.EXACT


def _rel_row(**over):
    row = {"edge_id": "e1", "type": "SUPPLIES_TO", "subtype": "공급",
           "source": {"key": _SAMSUNG, "name": "삼성전자"},
           "target": {"key": _HYNIX, "name": "SK하이닉스"},
           "evidence_id": "ev_1", "source_type": "news", "freshness": "current",
           "confidence": 0.9, "ratio": None, "verdict": "supported"}
    row.update(over)
    return row


def _evt_row(**over):
    row = {"event_id": "evt_1", "name": "압수수색", "event_type": "규제수사",
           "is_risk": True, "role": "subject", "occurred_at": "2026-06-11",
           "article_count": 1, "timeline": [], "evidence_ids": ["ev_e"],
           "eventness_suspect": False, "sign": "negative"}
    row.update(over)
    return row


@pytest.fixture
def stub(monkeypatch):
    """DB 없이 도구를 돌린다 — `tests/tools/test_graph_tools.py` 와 같은 대역."""
    def _install(*, relations=(), events=()):
        monkeypatch.setattr(gt.company_service, "norm_names_by_keys",
                            lambda keys: {k: k for k in keys})
        monkeypatch.setattr(gt.company_service, "relations_of",
                            lambda key: list(relations))
        monkeypatch.setattr(gt.company_service, "events_of",
                            lambda key: list(events))
        monkeypatch.setattr(gt.evidence_selector, "similarities",
                            lambda *a, **k: {})
        monkeypatch.setattr(gt, "_embed", lambda: None)
    return _install


# ══════════════════════════════════════════════════════════════════
#  ① 도구 반환 — 값을 붙이는 자리
# ══════════════════════════════════════════════════════════════════

def test_tools_stamp_every_relation_and_event_as_direct(stub):
    """★서버가 정한 재료 범위에서 **직접** 조회한 것이라 `direct` 다."""
    stub(relations=[_rel_row()], events=[_evt_row()])
    with scope.anchor_scope([_SAMSUNG], workspace_keys=[_SAMSUNG]):
        relations = gt.get_relations([_SAMSUNG])
        events = gt.get_events([_SAMSUNG], intent="압수수색")

    assert [r.provenance for r in relations] == [PROVENANCE_DIRECT]
    assert [e.provenance for e in events] == [PROVENANCE_DIRECT]


def test_no_code_path_produces_explored_yet():
    """★값을 늘리지 마라 — `explored` 는 탐색 도구가 생길 때 쓴다.

    지금 그 값을 쓰는 코드가 있으면 「탐색으로 얻었다」가 거짓이 된다. DTO 의
    **정의 자리(`app/tools/dto.py`)만** 그 이름을 알고 있어야 한다.
    """
    app = Path(__file__).resolve().parents[2] / "app"
    users = sorted(path.relative_to(app).as_posix()
                   for path in app.rglob("*.py")
                   if PROVENANCE_EXPLORED in path.read_text(encoding="utf-8"))
    assert users == ["tools/dto.py"], f"`explored` 를 쓰는 코드가 생겼다: {users}"


# ══════════════════════════════════════════════════════════════════
#  ② Agent 가 본 tool result — ★`exclude_none` 에 쓸려 나가지 않는가
# ══════════════════════════════════════════════════════════════════

def test_agent_sees_provenance_in_the_tool_result(stub):
    """★`_dump()` 가 `exclude_none=True` 로 접는다. `direct` 는 `None` 이 아니라
    살아남아야 한다 — 여기서 빠지면 Agent 는 출처 경로를 아예 못 본다."""
    stub(relations=[_rel_row()], events=[_evt_row()])
    with scope.anchor_scope([_SAMSUNG], workspace_keys=[_SAMSUNG],
                            intent="압수수색"), agent_tools.collecting():
        relations = json.loads(agent_tools.get_relations([_SAMSUNG]))
        events = json.loads(agent_tools.get_events([_SAMSUNG]))

    assert [r["provenance"] for r in relations] == [PROVENANCE_DIRECT]
    assert [e["provenance"] for e in events] == [PROVENANCE_DIRECT]


def test_collected_dtos_keep_provenance_when_they_land_in_state(stub):
    """★Agent 가 읽는 JSON 과 State 에 담기는 DTO 는 **다른 갈래**다
    (`_guard` 가 둘로 나눈다). 문자열만 지키면 마감 단계가 값을 잃는다."""
    stub(relations=[_rel_row()])
    with scope.anchor_scope([_SAMSUNG], workspace_keys=[_SAMSUNG]), \
            agent_tools.collecting() as bucket:
        agent_tools.get_relations([_SAMSUNG])

    assert [r.provenance for r in bucket["get_relations"]] == [PROVENANCE_DIRECT]


# ══════════════════════════════════════════════════════════════════
#  ③ evidence_validation — ★접으면서 잃지 않는가
# ══════════════════════════════════════════════════════════════════

def test_dedup_keeps_provenance_on_both_relations_and_events(monkeypatch, result,
                                                             relation, event):
    """★사건 dedup 은 `model_copy(update=...)` 로 **새 객체를 만든다**(공유 사건의
    근거를 합치느라). 거기서 다른 필드가 조용히 빠지면 관통이 끊긴다."""
    monkeypatch.setattr(agent_loop.relation_service, "evidence_for_ids",
                        lambda ids: [])
    other = event.model_copy(update={"evidence_ids": ["ev_other"]})
    got = agent_loop.evidence_validation({
        "result": result,
        "tool_results": {"get_relations": [relation, relation],
                         "get_events": [event, other]}})

    assert len(got["relations"]) == 1 and len(got["events"]) == 1, "접혔다"
    assert got["events"][0].evidence_ids == ["ev_evt", "ev_other"], "근거는 합친다"
    assert got["relations"][0].provenance == PROVENANCE_DIRECT
    assert got["events"][0].provenance == PROVENANCE_DIRECT


# ══════════════════════════════════════════════════════════════════
#  ④ build_prompt — ★DTO 는 들고 간다 · 글자에는 안 나온다
# ══════════════════════════════════════════════════════════════════

def test_build_prompt_receives_dtos_that_still_carry_provenance(
        monkeypatch, request_, decision, relation, event, match_type):
    """관통의 끝 — 프롬프트를 **조립하는 함수가 받는 DTO** 에 값이 살아 있는가."""
    seen: dict = {}

    def _capture(question, **kwargs):
        seen.update(kwargs)
        return "prompt"

    monkeypatch.setattr(answer_node.prompt, "build_user_prompt", _capture)
    answer_node.build_prompt({
        "request": request_, "decision": decision, "match_type": match_type,
        "companies": [], "events": [event], "relations": [relation],
        "propagation": [], "evidence": []})

    assert [r.provenance for r in seen["relations"]] == [PROVENANCE_DIRECT]
    assert [e.provenance for e in seen["events"]] == [PROVENANCE_DIRECT]


def test_prompt_text_does_not_expose_provenance_yet(request_, decision, relation,
                                                    event, match_type):
    """★노출 여부는 **아직 정하지 않았다.** 문구를 바꾸면 2차 완료 기준인
    평가셋의 측정 대상이 하나 늘어난다 — 정하기 전까지는 글자로 새지 않는다."""
    text = graph_prompt.build_user_prompt(
        request_.question, match_type=match_type, companies=[],
        events=[event], relations=[relation], propagation=[], evidence=[],
        anchor_source=decision.source, workspace_names=decision.workspace_names)

    assert "provenance" not in text
    assert PROVENANCE_DIRECT not in text
