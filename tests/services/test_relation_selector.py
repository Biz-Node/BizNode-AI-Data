"""relation_selector — 질문 의도로 관계 순서를 정하는 순수 함수들.

★왜 이 파일이 있나 (2026-08-26)

사건에는 의도 선택(`evidence_selector`)이 있는데 **관계에는 없었다** —
비대칭이다(설계서 §11 · 현황서 §5-4).

    사건   events_of() → evidence_selector 가 질문 의도로 고른다        ✅
    관계   relations_of(key) → 8종을 점수순으로 자를 뿐                 🔴

게다가 `SearchQuery.edge_types`·`direction` 을 `retrieve_service` 가 한 번도
참조하지 않았다(grep 0회). **질문이 무슨 관계를 물었는지가 Retrieve 경계에서
사라진다.** 관계가 상한을 넘는 기업에서는 질문이 물은 엣지가 점수순 상위에
못 들면 빠지고, 그러면 LLM 이 관계를 **원문에서 읽어내야** 한다 — ③과 ⑥을
섞는 일이다(설계서 §10 규칙 위반).

★`evidence_selector` 와 **다른 점 하나** — 여기서는 아무것도 버리지 않는다.
  flow ④a 의 금지사항이 「관계를 **지우지** 않는다(없는 것으로 읽힌다)」다
  (설계서 §10). 그래서 `select`(kept, cut)가 아니라 `order`(순서만)다.
  자르는 것은 `retrieve_service` 의 링 상한이 이미 하고 있고 로그도 거기 있다.
"""

from __future__ import annotations

from app.services import relation_selector as sel
from search.model.enums import Direction


def _row(edge_id, etype, *, source_key="00126380", target_key="00301246",
         symmetric=False):
    return {
        "edge_id": edge_id,
        "type": etype,
        "source": {"key": source_key, "name": "삼성전자", "label": "Company"},
        "target": {"key": target_key, "name": "상대", "label": "Company"},
        "symmetric": symmetric,
    }


class _Query:
    """`SearchQuery` 중 이 모듈이 읽는 두 필드만."""

    def __init__(self, edge_types=None, direction=None):
        self.edge_types = edge_types
        self.direction = direction


# ── 의도 추출 ────────────────────────────────────────────────────────────

def test_matched_edge_types_reads_the_query_edge_types():
    """★신호는 이미 Retrieve 손에 와 있다 — Search Layer 를 고칠 필요가 없다."""
    assert sel.matched_edge_types(_Query(edge_types=["SUPPLIES_TO"])) == frozenset(
        {"SUPPLIES_TO"})


def test_matched_edge_types_is_empty_when_the_query_asked_for_no_relation():
    """관계 키워드가 없는 질의 — 티어를 주지 않는다(hard filter 가 아니다)."""
    assert sel.matched_edge_types(_Query()) == frozenset()


# ── 순서 ────────────────────────────────────────────────────────────────

def test_order_puts_the_asked_edge_type_first():
    """★완료조건 ⓐ — 「삼성전자가 납품하는 기업」에서 SUPPLIES_TO 가 위로 온다."""
    rows = [_row("e1", "PARTNERS_WITH"), _row("e2", "SUPPLIES_TO")]

    got = sel.order(rows, matched=frozenset({"SUPPLIES_TO"}),
                    direction=None, anchor_keys=set())

    assert [r["edge_id"] for r in got] == ["e2", "e1"]


def test_order_never_drops_a_relation():
    """★flow ④a 금지사항 — 관계를 **지우지** 않는다. 지우면 「없는 것」으로 읽힌다."""
    rows = [_row("e1", "PARTNERS_WITH"), _row("e2", "SUPPLIES_TO"),
            _row("e3", "COMPETES_WITH")]

    got = sel.order(rows, matched=frozenset({"SUPPLIES_TO"}),
                    direction=None, anchor_keys=set())

    assert {r["edge_id"] for r in got} == {"e1", "e2", "e3"}


def test_order_keeps_input_order_within_the_same_tier():
    """동점이면 입력 순서(=점수순)가 남는다 — 같은 질문에 매번 다른 순서가
    나오면 안 된다(`evidence_selector.select` 와 같은 규약)."""
    rows = [_row("e1", "PARTNERS_WITH"), _row("e2", "COMPETES_WITH"),
            _row("e3", "ACQUIRES")]

    got = sel.order(rows, matched=frozenset({"SUPPLIES_TO"}),
                    direction=None, anchor_keys=set())

    assert [r["edge_id"] for r in got] == ["e1", "e2", "e3"]


def test_order_prefers_the_asked_direction():
    """「삼성전자**가** 납품하는」(outgoing)과 「삼성전자**에** 납품하는」(incoming)은
    같은 edge_type 의 방향만 다르다 — 조사 하나로 갈리는 신호다."""
    outgoing = _row("e_out", "SUPPLIES_TO", source_key="00126380")
    incoming = _row("e_in", "SUPPLIES_TO", source_key="99999999",
                    target_key="00126380")
    rows = [incoming, outgoing]

    got = sel.order(rows, matched=frozenset({"SUPPLIES_TO"}),
                    direction=Direction.OUTGOING, anchor_keys={"00126380"})

    assert [r["edge_id"] for r in got] == ["e_out", "e_in"]


def test_order_does_not_demote_symmetric_relations_by_direction():
    """★PARTNERS_WITH·COMPETES_WITH 의 방향은 **저장 구조가 만든 인공 방향**이다
    (설계서 §9-3 ⓐ). 그 방향으로 줄을 세우면 없는 신호로 순서를 정하게 된다."""
    artificial = _row("e_sym", "PARTNERS_WITH", source_key="99999999",
                      target_key="00126380", symmetric=True)
    rows = [artificial, _row("e_other", "PARTNERS_WITH", source_key="00126380")]

    got = sel.order(rows, matched=frozenset({"PARTNERS_WITH"}),
                    direction=Direction.OUTGOING, anchor_keys={"00126380"})

    # 인공 방향을 이유로 뒤로 밀리지 않는다 — 입력 순서가 그대로 남는다.
    assert [r["edge_id"] for r in got] == ["e_sym", "e_other"]


def test_order_is_a_no_op_when_the_query_asked_for_no_relation():
    """의도가 없으면 순서를 바꾸지 않는다 — 점수순(입력 순서)이 그대로다."""
    rows = [_row("e1", "PARTNERS_WITH"), _row("e2", "SUPPLIES_TO")]

    got = sel.order(rows, matched=frozenset(), direction=None, anchor_keys=set())

    assert [r["edge_id"] for r in got] == ["e1", "e2"]
