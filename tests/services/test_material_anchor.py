"""A-3 — **앵커가 재료를 정한다.** 검색 히트가 아니라.

지금까지 `/ask` 의 재료 기업은 `_companies_from(result)` 가 정했다. 그런데 해소에
실패한 질의에서 그 히트는 **의미검색이 고른 무관한 기업**이다 — 실측(2026-08-25):

    「엔비디아는 어떤가?」  anchor=엔비디아
      companies = 에스비비테크 · 현대모비스 · 로보티즈 · 제이브이엠 · 에스피지   🔴

★**규칙은 「히트를 믿을 수 있는가」다.** ② Search 가 **실제로 앵커를 잡고** 그래프를
  돈 경우(`resolved_entities` 가 있음)에만 히트가 앵커를 반영한다. 그 외 —
  SEMANTIC 폴백, anchorless 슬롯, `norm_name` fallback 으로 우리가 뒤늦게 찾은 앵커 —
  는 히트가 앵커와 무관하므로 **앵커 자신을 재료로 삼는다**(설계서 §14-5·§3).

★**링(ring)은 관련도를 그대로 재사용한다**(설계서 §3) — 새 값을 만들지 않는다.

    Ring 0  양끝이 둘 다 워크스페이스 안
    Ring 1  워크스페이스 ↔ 바깥 **기업**
    Ring 2  워크스페이스 ↔ 비-Company (사건·인물·기관·제품)
    Ring 3  워크스페이스와 닿지 않음        ★버리지 않는다 — hard filter 가 아니다
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.api.schemas import Anchor, AnchorSource, AskRequest
from app.services import retrieve_service as rs_module
from app.services.query_understanding import AnchorDecision
from app.services.retrieve_service import RetrieveService
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode
from pipeline.normalizer.resolver import Resolution

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_WS = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def _resolution(corp_code=_SAMSUNG, corp_name="삼성전자"):
    return Resolution(corp_code=corp_code, corp_name=corp_name, stock_code=None,
                      method="exact", score=1.0)


def _hit(entity_id, name):
    return SearchHit(entity_type=EntityType.COMPANY, entity_id=entity_id, name=name,
                     source_score=1.0, sources=["chroma"])


def _orchestrator(hits=(), *, mode=SearchMode.SEMANTIC, resolved=(),
                  edge_types=None, direction=None):
    query = SearchQuery(raw_query="q", normalized_query="q", mode=mode,
                        today=date(2026, 8, 25), resolved_entities=list(resolved),
                        edge_types=edge_types, direction=direction)
    result = SearchResult(query="q", mode=mode, hits=list(hits), total=len(hits),
                          took_ms=1, cache_hit=False, used_semantic_fallback=False)
    orchestrator = MagicMock()
    orchestrator.search.return_value = (query, result)
    return orchestrator


def _row(edge_id, src_key, src_name, tgt_key, tgt_name, *, tgt_label="Company",
         src_label="Company", score=0.9, rel_type="SUPPLIES_TO"):
    return {"edge_id": edge_id, "evidence_id": None, "type": rel_type,
            "subtype": None,
            "source": {"key": src_key, "name": src_name, "label": src_label},
            "target": {"key": tgt_key, "name": tgt_name, "label": tgt_label},
            "symmetric": False, "freshness": "current", "score": score,
            "corroboration": 1, "source_type": "news"}


@pytest.fixture
def wired(monkeypatch):
    """이름 조회·판정·그래프 조회를 세운다 — 재료 선택 규칙만 본다."""
    state = {"decision": None, "relations": {}, "events": {}}
    monkeypatch.setattr(rs_module.workspace_service, "names_of", lambda keys: _WS)
    monkeypatch.setattr(rs_module.query_understanding, "decide_anchor",
                        lambda *a, **k: state["decision"])
    monkeypatch.setattr(rs_module.company_service, "relations_of",
                        lambda key, **kw: state["relations"].get(key, []))
    monkeypatch.setattr(rs_module.company_service, "events_of",
                        lambda key: state["events"].get(key, []))
    monkeypatch.setattr(rs_module.relation_service, "evidence_for_ids", lambda ids: [])
    return state


def _request():
    return AskRequest(question="q", workspace_keys=[_SAMSUNG, _HYNIX])


def _workspace_decision():
    return AnchorDecision(
        source=AnchorSource.WORKSPACE, workspace_names=_WS,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.WORKSPACE)
                 for k, n in _WS.items()])


def _query_decision(key="tsmc", name="TSMC"):
    return AnchorDecision(
        source=AnchorSource.QUERY, workspace_names=_WS,
        anchors=[Anchor(key=key, name=name, source=AnchorSource.QUERY)])


# ══════════════════════════════════════════════════════════════════════
#  누가 companies 를 정하는가
# ══════════════════════════════════════════════════════════════════════

def test_semantic_hits_do_not_decide_companies_for_a_query_anchor(wired):
    """★설계서 §14-5 — `/ask` 에서 SEMANTIC 이 company 선택에 개입하지 않는다.

    실측한 바로 그 사고다: 앵커는 엔비디아인데 재료는 에스비비테크였다."""
    wired["decision"] = _query_decision(key="엔비디아", name="엔비디아")
    orchestrator = _orchestrator([_hit("01234567", "에스비비테크"),
                                  _hit("00111111", "현대모비스")])
    _, retrieved = RetrieveService(orchestrator).retrieve_for_ask(_request())
    assert [c.name for c in retrieved.companies] == ["엔비디아"]


def test_anchored_graph_hits_are_kept_as_material(wired):
    """★반대쪽도 지킨다 — 「삼성전자에 납품하는 기업」의 **상대 기업들이 곧 답**이다.
    ② 가 실제로 앵커를 잡고 그래프를 돈 경우 히트는 앵커를 반영한다."""
    wired["decision"] = _query_decision(key=_SAMSUNG, name="삼성전자")
    orchestrator = _orchestrator([_hit("00301246", "SFA반도체"), _hit("01095722", "심텍")],
                                 mode=SearchMode.RELATIONSHIP,
                                 resolved=[_resolution()])
    _, retrieved = RetrieveService(orchestrator).retrieve_for_ask(_request())
    assert [c.name for c in retrieved.companies] == ["SFA반도체", "심텍"]


def test_workspace_anchor_collects_from_the_workspace(wired):
    """★설계서 §14-7 ⓑ — 「점수순으로 아무거나」가 아니라 워크스페이스 기업이 앵커다."""
    wired["decision"] = _workspace_decision()
    orchestrator = _orchestrator([_hit("01234567", "무관한기업")])
    _, retrieved = RetrieveService(orchestrator).retrieve_for_ask(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG, _HYNIX]


def test_retrieve_route_still_uses_the_search_hits(wired):
    """★`/retrieve` 는 **무변경**이다(설계서 §14-5) — SEMANTIC 이 여기서는 살아 있다."""
    wired["decision"] = _query_decision(key="엔비디아", name="엔비디아")
    orchestrator = _orchestrator([_hit("01234567", "에스비비테크")])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.name for c in retrieved.companies] == ["에스비비테크"]


def test_anchor_companies_are_capped_and_the_cut_is_logged(wired, caplog):
    """★앵커 기업 수 상한은 기존 `_MAX_COMPANIES` 를 그대로 쓴다 — 새 숫자를
    만들지 않는다. **조용히 자르지 않는다**([규칙 2])."""
    many = {f"0000000{i}": f"기업{i}" for i in range(8)}
    wired["decision"] = AnchorDecision(
        source=AnchorSource.WORKSPACE, workspace_names=many,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.WORKSPACE)
                 for k, n in many.items()])
    with caplog.at_level("INFO"):
        _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert len(retrieved.companies) == rs_module._MAX_COMPANIES
    assert "anchors truncated" in caplog.text


def test_search_hit_evidence_is_kept_even_when_hits_are_not_the_material(wired, monkeypatch):
    """★기업은 앵커가 정하지만 **근거는 히트 것도 그대로 모은다.**

    한 번 걸러 봤다가 실측으로 되돌렸다(현황서 §8-6).

        SEMANTIC 히트      근거를 아예 안 들고 온다 — 거를 게 없다 (실측 0건)
        anchorless 히트    근거의 **절반가량이 워크스페이스에 닿는다**
                           「납품 단가 압박」 38건 중 18 · 「최근 인수 사례」 140건 중 78

    거르면 질문이 물은 바로 그 사례(삼성전자↔레인보우로보틱스 인수)를 버린다.
    """
    captured = {}
    wired["decision"] = _query_decision()
    hit = _hit("01234567", "에스비비테크")
    hit.evidence = [{"evidence_id": "ev_from_hit"}]

    def _evidence_for_ids(ids):
        captured["ids"] = list(ids)
        return []

    monkeypatch.setattr(rs_module.relation_service, "evidence_for_ids", _evidence_for_ids)
    RetrieveService(_orchestrator([hit])).retrieve_for_ask(_request())
    assert "ev_from_hit" in captured["ids"]


# ══════════════════════════════════════════════════════════════════════
#  링(ring) 순서
# ══════════════════════════════════════════════════════════════════════

def test_relations_come_out_in_ring_order(wired):
    """Ring 0 → 1 → 2 → 3. 점수가 낮아도 안쪽 링이 먼저다."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_ring3", "09999999", "남", "08888888", "남2", score=0.99),
        _row("e_ring2", _SAMSUNG, "삼성전자", "evt_1", "어떤 사건",
             tgt_label="Event", score=0.98),
        _row("e_ring1", _SAMSUNG, "삼성전자", "00301246", "SFA반도체", score=0.97),
        _row("e_ring0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스", score=0.10),
    ]}
    _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert [r.edge_id for r in retrieved.relations] == [
        "e_ring0", "e_ring1", "e_ring2", "e_ring3"]


def test_ring_does_not_drop_unrelated_relations(wired):
    """★**hard filter 가 아니다**(설계서 §3) — 워크스페이스와 안 닿는 관계도 남는다.
    순서만 뒤로 간다."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [_row("e_far", "09999999", "남", "08888888", "남2")]}
    _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert [r.edge_id for r in retrieved.relations] == ["e_far"]


def test_same_ring_keeps_the_incoming_order(wired):
    """★같은 링 안에서는 입력 순서(=점수순)가 남는다 — 같은 질문에 매번 다른
    순서가 나오면 안 된다."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_a", _SAMSUNG, "삼성전자", "00301246", "SFA반도체", score=0.9),
        _row("e_b", _SAMSUNG, "삼성전자", "01095722", "심텍", score=0.8),
    ]}
    _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert [r.edge_id for r in retrieved.relations] == ["e_a", "e_b"]


def test_ring_distribution_is_logged(wired, caplog):
    """★어느 링까지 갔는지·링마다 몇 건인지 남긴다(설계서 §3 · [규칙 2])."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스"),
        _row("e1", _SAMSUNG, "삼성전자", "00301246", "SFA반도체"),
    ]}
    with caplog.at_level("INFO"):
        RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert "relations.rings" in caplog.text


def test_ring_values_match_the_ranker(wired):
    """★설계서 §3 「링은 위 관련도를 **그대로 재사용**한다 — 새 값을 만들지
    않는다」. 두 곳이 갈라지면 순서가 조용히 어긋난다."""
    from search.service import result_ranker as rr

    assert (rs_module._RING_BOTH_INSIDE, rs_module._RING_OUTSIDE_COMPANY,
            rs_module._RING_OUTSIDE_OTHER, rs_module._RING_UNRELATED) == (
        rr._WS_BOTH_INSIDE, rr._WS_OUTSIDE_COMPANY,
        rr._WS_OUTSIDE_OTHER, rr._WS_UNRELATED)


def test_ring_zero_survives_the_score_cap(wired):
    """★실측(2026-08-25) — 삼성전자 관계 526건에서 Ring 0 은 **137·225·414번째**다.
    점수순 상위 10건만 받아 오면 Ring 0 이 통째로 사라진다. 그래서 자르기 **전에**
    링으로 줄을 세운다."""
    wired["decision"] = _workspace_decision()
    filler = [_row(f"e_{i}", _SAMSUNG, "삼성전자", f"0777777{i}", f"밖{i}", score=0.99)
              for i in range(30)]
    wired["relations"] = {_SAMSUNG: filler + [
        _row("e_ring0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스", score=0.01)]}
    _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())
    assert retrieved.relations[0].edge_id == "e_ring0"


# ══════════════════════════════════════════════════════════════════════
#  ④a 관계 의도 선택 (§5-4 · 완료조건 ⓐ)
# ══════════════════════════════════════════════════════════════════════

def test_the_asked_edge_type_comes_first_within_a_ring(wired):
    """★완료조건 ⓐ — 「삼성전자가 납품하는 기업」에서 `relations[]` 가
    `SUPPLIES_TO` 를 위에 싣는다.

    지금까지 `edge_types` 는 `SearchQuery` 에 와 있는데도 **한 번도 참조되지
    않았다**(grep 0회). 질문이 물은 엣지가 점수순 상위에 못 들면 빠지고, 그러면
    LLM 이 관계를 근거 원문에서 읽어내야 한다(설계서 §10 규칙 위반)."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_partner", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             score=0.99, rel_type="PARTNERS_WITH"),
        _row("e_supply", _SAMSUNG, "삼성전자", "01095722", "심텍",
             score=0.10, rel_type="SUPPLIES_TO"),
    ]}

    _, retrieved = RetrieveService(
        _orchestrator(edge_types=["SUPPLIES_TO"])).retrieve_for_ask(_request())

    assert [r.edge_id for r in retrieved.relations] == ["e_supply", "e_partner"]


def test_intent_does_not_beat_the_ring_order(wired):
    """★링을 **가로질러** 의도를 우선할지는 아직 `[DECIDE]` 다(현황서 §5-17·§7-3) —
    링별 quota 냐 의도별 우선순위냐를 **둘 다 재 본 적이 없다.** 실측 없이
    그 결정을 코드로 못 박지 않는다. 의도는 링 **안에서만** 줄을 세운다."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        # Ring 1 인데 질문이 물은 타입
        _row("e_ring1_match", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             rel_type="SUPPLIES_TO"),
        # Ring 0 인데 질문이 안 물은 타입
        _row("e_ring0_other", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스",
             rel_type="COMPETES_WITH"),
    ]}

    _, retrieved = RetrieveService(
        _orchestrator(edge_types=["SUPPLIES_TO"])).retrieve_for_ask(_request())

    assert [r.edge_id for r in retrieved.relations] == [
        "e_ring0_other", "e_ring1_match"]


def test_relation_intent_is_a_no_op_when_the_query_asked_for_no_relation(wired):
    """관계 키워드가 없는 질의 — 순서를 건드리지 않는다(hard filter 가 아니다)."""
    wired["decision"] = _workspace_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_a", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             rel_type="PARTNERS_WITH"),
        _row("e_b", _SAMSUNG, "삼성전자", "01095722", "심텍",
             rel_type="SUPPLIES_TO"),
    ]}

    _, retrieved = RetrieveService(_orchestrator()).retrieve_for_ask(_request())

    assert [r.edge_id for r in retrieved.relations] == ["e_a", "e_b"]


def test_the_relation_cut_count_is_logged(wired, caplog):
    """★완료조건 ⓓ — 잘라낸 관계 개수가 로그에 남는다. 조용히 자르면
    「그게 전부」로 읽힌다([규칙 2])."""
    wired["decision"] = _workspace_decision()
    # 상한(_MAX_RELATIONS_PER_COMPANY × 기업 수)을 확실히 넘긴다.
    wired["relations"] = {_SAMSUNG: [
        _row(f"e_{i}", _SAMSUNG, "삼성전자", f"0777777{i}", f"밖{i}")
        for i in range(rs_module._MAX_RELATIONS_PER_COMPANY * 2 + 5)]}

    with caplog.at_level("INFO"):
        RetrieveService(_orchestrator()).retrieve_for_ask(_request())

    assert "cut=5" in caplog.text
