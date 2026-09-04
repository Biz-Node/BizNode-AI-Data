"""링 **안**의 의도 정렬이 살아 있는가 — 1.5차 회귀 방지 (현황서 §8-18).

    링 분류(`ring_of`) → **링 안에서 의도 정렬** → `ordered[:limit]`
                            ↑ 여기가 죽어 있었다

★죽은 방식이 조용했다. `agent_tools.get_relations(keys)` 가 `edge_types` 를 안
  넘기면 `matched` 가 빈 집합이 되고, `relation_selector.order()` 는
  `if not matched: return ordered` 로 **아무 일도 없었던 것처럼** 돌려준다.
  예외도 로그도 없다 — 그래서 테스트가 아니면 안 잡힌다.

★**정책을 바꾸는 파일이 아니다.** 링 우선이냐 의도 우선이냐(§5-17·§7-3)는
  아직 `[DECIDE]` 다. 여기가 묶는 것은 「1.5차가 하던 것을 지금도 하는가」뿐이다.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.graph.nodes import agent_loop
from app.services import relation_selector
from app.tools import agent_tools, graph_tools as gt, scope
from search.model.enums import Direction

_SAMSUNG = "00126380"
_HYNIX = "00164779"


def _row(edge_id, edge_type, **over):
    """★양끝이 둘 다 워크스페이스 안 — Ring 0 으로 모아 **링 안의** 순서를 본다."""
    row = {"edge_id": edge_id, "type": edge_type, "subtype": "공급",
           "source": {"key": _SAMSUNG, "name": "삼성전자", "label": "Company"},
           "target": {"key": _HYNIX, "name": "SK하이닉스", "label": "Company"},
           "evidence_id": f"ev_{edge_id}", "source_type": "news",
           "freshness": "current", "confidence": 0.9, "ratio": None,
           "verdict": "supported"}
    row.update(over)
    return row


@pytest.fixture
def stub(monkeypatch):
    def _install(relations):
        monkeypatch.setattr(gt.company_service, "norm_names_by_keys",
                            lambda keys: {k: k for k in keys})
        monkeypatch.setattr(gt.company_service, "relations_of",
                            lambda key: list(relations))
    return _install


@pytest.fixture
def spy(monkeypatch):
    """`relation_selector.order()` 가 **무엇을 받았는가**. 신호가 죽는 자리다."""
    seen: list[dict] = []
    original = relation_selector.order

    def _order(rows, *, matched, direction=None, anchor_keys=None):
        seen.append({"matched": frozenset(matched), "direction": direction})
        return original(rows, matched=matched, direction=direction,
                        anchor_keys=anchor_keys)

    monkeypatch.setattr(gt.relation_selector, "order", _order)
    return seen


def _state(query, request_, decision):
    return {"companies": [decision.anchors[0]], "request": request_,
            "decision": decision, "anchor_names": ("삼성전자",),
            "intent": "", "query": query}


# ══════════════════════════════════════════════════════════════════
#  ★회귀 — 질의가 엣지를 물었는데 `matched` 가 비면 실패
# ══════════════════════════════════════════════════════════════════

def test_edge_types_from_the_query_reach_the_selector(stub, spy, query,
                                                      request_, decision):
    """★이 파일의 이유. `matched` 가 비면 `order()` 가 그대로 돌려주고,
    링 안의 의도 정렬이 **조용히** 사라진다."""
    stub([_row("e1", "PARTNERS_WITH"), _row("e2", "SUES")])
    asked = dataclasses.replace(query, edge_types=["SUES"],
                                direction=Direction.INCOMING)

    with agent_loop._scope_of(_state(asked, request_, decision)), \
            agent_tools.collecting():
        agent_tools.get_relations([_SAMSUNG])

    assert spy, "`relation_selector.order()` 가 아예 안 불렸다"
    assert spy[0]["matched"] == frozenset({"SUES"}), \
        "질의가 물은 엣지 타입이 선택기까지 안 왔다 — 정렬이 죽는다"


def test_direction_reaches_the_selector_as_the_value_string(stub, spy, query,
                                                            request_, decision):
    """★1.5차 `fetch_relations` 가 `getattr(query.direction, "value", None)` 로
    넘기던 **형태 그대로** 간다. 형태를 바꾸면 대조에서 티가 안 나는 차이가 된다."""
    stub([_row("e1", "SUES")])
    asked = dataclasses.replace(query, edge_types=["SUES"],
                                direction=Direction.INCOMING)

    with agent_loop._scope_of(_state(asked, request_, decision)), \
            agent_tools.collecting():
        agent_tools.get_relations([_SAMSUNG])

    assert spy[0]["direction"] == "incoming"


def test_scope_carries_the_intent_signal_the_same_way_intent_does(query, request_,
                                                                  decision):
    """★`get_events` 의 `intent` 와 **같은 자리에 같은 방식**으로 싣는다 —
    새 패턴을 만들지 않는다."""
    asked = dataclasses.replace(query, edge_types=["SUES", "REGULATES"],
                                direction=Direction.OUTGOING)
    with agent_loop._scope_of(_state(asked, request_, decision)):
        ctx = scope.context()

    assert ctx.edge_types == ("SUES", "REGULATES")
    assert ctx.direction == "outgoing"


# ══════════════════════════════════════════════════════════════════
#  ★결과 — 링 안에서 실제로 앞으로 온다
# ══════════════════════════════════════════════════════════════════

def test_asked_edge_type_moves_to_the_front_within_the_ring(stub, query,
                                                            request_, decision):
    """세 관계가 **같은 링(Ring 0)** 에 있다. 질의가 물은 `SUES` 가 입력 순서상
    가운데인데도 앞으로 와야 한다 — 자르기가 정렬 뒤라 이 순서가 곧 무엇이
    살아남는지를 정한다."""
    stub([_row("e1", "PARTNERS_WITH"), _row("e2", "SUES"),
          _row("e3", "SUPPLIES_TO")])
    asked = dataclasses.replace(query, edge_types=["SUES"])

    with agent_loop._scope_of(_state(asked, request_, decision)), \
            agent_tools.collecting() as bucket:
        agent_tools.get_relations([_SAMSUNG])

    assert [r.edge_id for r in bucket["get_relations"]] == ["e2", "e1", "e3"]


def test_a_question_that_asks_no_edge_type_keeps_score_order(stub, query,
                                                             request_, decision):
    """★**정책을 바꾸지 않는다.** 라우터가 아무것도 못 잡으면 순서는 입력
    순서(=점수순) 그대로다 — 없는 신호로 줄을 세우지 않는다."""
    stub([_row("e1", "PARTNERS_WITH"), _row("e2", "SUES")])

    with agent_loop._scope_of(_state(query, request_, decision)), \
            agent_tools.collecting() as bucket:
        agent_tools.get_relations([_SAMSUNG])

    assert [r.edge_id for r in bucket["get_relations"]] == ["e1", "e2"]


# ══════════════════════════════════════════════════════════════════
#  ★전제 — Agent 는 여전히 keys 만 넘긴다 (4원칙 ①)
# ══════════════════════════════════════════════════════════════════

def test_the_agent_still_only_gets_keys():
    """★이 복구의 전제. 신호를 되살리되 **인자로 되돌리지는 않는다** — 인자면
    순서를 LLM 이 정하게 되고, 순서는 자르는 지점을 정하므로 재료를 고르는 일이
    된다."""
    bound = {t.name: t for t in agent_tools.agent_tools()}["get_relations"]
    assert set(bound.args) == {"keys"}
