"""재료에 실리는 **관계 유형 목록**의 계약.

★왜 파일을 따로 두나 — 이 목록은 한 상수인데 **네 곳이 함께** 쓴다:
  `/retrieve` 재료 · `/ask` 도구 · 기업 상세의 관계 목록 · 관계 그래프의 must-draw.
  한 곳에서 조용히 갈리면 「목록엔 있는데 그래프엔 없다」가 되므로 상수 자체를
  못 박는다.

★계약은 **「사건 축 둘을 뺀 전부」**다. 열거로 두면 새 `EdgeType` 이 생겼을 때
  아무도 이 목록을 안 고치고, 그 유형은 **어느 경로로도 재료에 못 들어온다** —
  실제로 `IS_EXECUTIVE_OF`(787엣지)와 `DEVELOPS`(2,050엣지)가 그렇게 빠져 있었다
  (그래프 전체의 25.4%, 현황서 §6-0 A-8 §13①).
"""

from __future__ import annotations

from app.api.schemas import EdgeType
from app.services import company_service
from app.services.company_service import _REL_TYPES

# 사건은 관계 목록이 아니라 **사건 경로**(`events_of`)가 받는다.
_EVENT_AXIS = {"HAS_EVENT", "IMPACTS"}


def test_relation_types_are_every_edge_type_except_the_event_axis():
    """★열거가 아니라 **여집합**이 계약이다. 새 `EdgeType` 이 생기면 여기서 깨진다."""
    assert set(_REL_TYPES) | _EVENT_AXIS == {e.value for e in EdgeType}


def _row(edge_type, *, olabel="Company", oname="상대", okey="k2"):
    """`company_service._REL_Q` 가 내는 raw 행 모양."""
    return {"t": edge_type, "p": {"confidence": 0.9, "last_seen": "2026-08-01",
                                  "source_type": "news"},
            "eid": f"e:{edge_type}", "outgoing": True,
            "oname": oname, "okey": okey, "olabel": olabel,
            "cname": "삼성전자", "ckey": "00126380"}


def test_executive_and_product_edges_survive_the_material_filter():
    """★`relations_of(rows=)` 는 유형으로 거른다 — 그 체에 둘이 걸리면 안 된다.

    걸리면 「삼성전자 임원이 누구야?」가 임원 0건으로 나간다(실측 2026-09-06:
    앵커가 완벽히 잡혔는데도 `OWNS_STAKE_IN ×10` 만 나왔다).
    """
    rows = [_row("IS_EXECUTIVE_OF", olabel="Person", oname="이재용", okey="p1"),
            _row("DEVELOPS", olabel="Product", oname="HBM", okey="pr1"),
            _row("SUPPLIES_TO")]
    kept = company_service.relations_of("00126380", rows=rows)
    assert {r["type"] for r in kept} == {"IS_EXECUTIVE_OF", "DEVELOPS", "SUPPLIES_TO"}


def test_non_company_endpoints_keep_their_label():
    """★상대가 Company 가 아니어도 라벨이 살아야 한다 — `ring_of` 가 이걸 본다."""
    kept = company_service.relations_of(
        "00126380", rows=[_row("IS_EXECUTIVE_OF", olabel="Person", oname="이재용", okey="p1"),
                          _row("DEVELOPS", olabel="Product", oname="HBM", okey="pr1")])
    assert {r["target"]["label"] for r in kept} == {"Person", "Product"}


def test_the_news_develops_caution_is_reachable():
    """★`graph_tools._caution_of` 의 `DEVELOPS` 분기는 **stub 없이도** 돌아야 한다.

    `DEVELOPS` 가 목록 밖이면 그 경고는 실제 요청에서 한 번도 안 붙는다 —
    시험은 stub 을 먹여 통과하는데 운영에서는 죽어 있는 상태가 된다.
    """
    assert "DEVELOPS" in _REL_TYPES


# ── 종단 — 실 그래프가 있어야 돈다 ──────────────────────────────────────
#
# ★위 계약 시험은 **상수와 체**만 본다. 「그래서 질문에 답할 재료가 실제로
#   들어오나」는 링 순서·상한까지 지나야 알 수 있어 여기서만 확인한다.
#
#     pytest -m needs_db tests/services/test_relation_types.py
import pytest


@pytest.mark.needs_db
@pytest.mark.parametrize("question,edge_type", [
    ("삼성전자 임원이 누구야?", "IS_EXECUTIVE_OF"),
    ("삼성전자가 개발하는 제품은?", "DEVELOPS"),
])
def test_the_asked_relation_reaches_the_material(question, edge_type):
    """★앵커가 완벽히 잡힌 질의인데도 물은 관계가 재료에 없었다(실측 2026-09-06).

    ★**워크스페이스를 안 준다.** 워크스페이스가 있으면 링이 의도를 이겨서
      이 유형이 상한 밖으로 밀린다 — 그건 이 변경이 아니라 §5-17 `[DECIDE]`
      (링 대 의도)의 몫이고, 여기서 함께 검사하면 두 결정이 한 시험에 묶인다.
    """
    from app.api.schemas import AskRequest
    from app.services.retrieve_service import RetrieveService
    from search.service.factory import build_orchestrator

    response = RetrieveService(build_orchestrator()).retrieve(
        AskRequest(question=question))
    assert edge_type in {r.type.value for r in response.relations}


@pytest.mark.needs_db
@pytest.mark.parametrize("name", ["삼성전자", "삼성에스디에스", "CJ대한통운"])
def test_the_relation_list_holds_every_relation_the_graph_drew(name):
    """★「목록과 그림은 같은 목록」 — 이 불변식이 두 유형에서만 깨져 있었다.

    `company_graph` 는 `IS_EXECUTIVE_OF`·`DEVELOPS` 를 **그리고 있었는데**
    `relations_of` 는 담을 수가 없어서, 상세 화면에 선은 있고 목록엔 없는 관계가
    삼성전자에서 10건 있었다(실측 2026-09-06).

    ★사건 축 둘은 뺀다 — 그건 `events` 블록이 받는다.
    """
    key = company_service.find_by_names([name])["key"]
    detail = company_service.company_detail(key)
    drawn = {e["edge_id"] for e in detail["graph"]["edges"]
             if e["type"] not in _EVENT_AXIS}
    assert {r["edge_id"] for r in detail["related"]} == drawn


# ══════════════════════════════════════════════════════════════════════
#  ★관계 상대의 key 는 상세 그래프와 같은 식별자다 (§5-17 ③ · 2026-09-12)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.needs_db
def test_a_person_end_carries_the_same_key_as_the_detail_graph():
    """★같은 `relation_row()` 가 **먹이는 쿼리에 따라** Person key 를 다르게 냈다.

        _REL_Q   (/retrieve·/ask)   Person 끝 = 이름
        _GRAPH_Q (기업 상세)         Person 끝 = person_key

    저장소의 다른 모든 자리(`graph_service._QUERY`·GraphSearcher·비-Company 앵커)는
    `person_key` 다. `_REL_Q` 하나만 달라서 **Person 앵커의 링 대조가 원리적으로
    불가능**했다 — 앵커 key 는 `이재용@00126186` 인데 row 는 `이재용` 을 실었다.
    """
    key = company_service.find_by_names(["삼성전자"])["key"]
    ends = {r["edge_id"]: e for r in company_service.relations_of(key)
            for e in (r["source"], r["target"]) if e["label"] == "Person"}
    listed = {eid: e["key"] for eid, e in ends.items()}
    detail = company_service.company_detail(key)
    persons = {n["key"] for n in detail["graph"]["nodes"] if n["label"] == "Person"}
    # ★그래프는 표시 상한(`_TYPE_CAP`)이 걸린 **부분집합**이다 — 양쪽에 있는 엣지끼리 본다
    drawn = {e["edge_id"]: end for e in detail["graph"]["edges"]
             for end in (e["source"], e["target"]) if end in persons}
    both = listed.keys() & drawn.keys()
    assert both, "목록과 그래프에 같이 있는 Person 엣지가 없다 — 전제가 깨졌다"
    assert all(listed[eid] == drawn[eid] for eid in both), \
        "같은 엣지의 Person key 가 목록과 그래프에서 다르다"
    # ★버그의 본질 — 전에는 key 가 **이름 그대로**였다. person_key 는 `이름@corp`·
    #   `이름|생년월` 두 꼴이라 모양이 아니라 「이름과 다르다」로 본다.
    assert all(e["key"] != e["name"] for e in ends.values()), "Person key 가 이름 그대로다"
