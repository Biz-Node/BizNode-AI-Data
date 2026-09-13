"""A-3 — **앵커가 재료를 정한다.** 검색 히트가 아니라.

지금까지 `/ask` 의 재료 기업은 `companies_from(result)` 가 정했다. 그런데 해소에
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


def _context_decision():
    return AnchorDecision(
        source=AnchorSource.CONTEXT, workspace_names=_WS,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.CONTEXT)
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
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.name for c in retrieved.companies] == ["엔비디아"]


def test_anchored_graph_hits_are_kept_as_material(wired):
    """★반대쪽도 지킨다 — 「삼성전자에 납품하는 기업」의 **상대 기업들이 곧 답**이다.
    ② 가 실제로 앵커를 잡고 그래프를 돈 경우 히트는 앵커를 반영한다."""
    wired["decision"] = _query_decision(key=_SAMSUNG, name="삼성전자")
    orchestrator = _orchestrator([_hit("00301246", "SFA반도체"), _hit("01095722", "심텍")],
                                 mode=SearchMode.RELATIONSHIP,
                                 resolved=[_resolution()])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.name for c in retrieved.companies] == ["SFA반도체", "심텍"]


def test_context_anchor_collects_from_the_anchor(wired):
    """★설계서 §14-7 ⓑ — 「점수순으로 아무거나」가 아니라 워크스페이스 기업이 앵커다."""
    wired["decision"] = _context_decision()
    orchestrator = _orchestrator([_hit("01234567", "무관한기업")])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG, _HYNIX]


def test_context_anchor_supplies_names_for_similarity_stripping(wired, monkeypatch):
    """★**workspace 앵커 경로에서 `anchor_names` 가 비어 순위가 퇴행했다**
    (2026-08-26 실측).

    `decide_anchor()` 는 `resolved_entities` 가 있으면 `query` 로 가므로
    `source=workspace` 는 **정의상 `resolved_entities` 가 0** 이다. 그런데
    `anchor_names` 를 거기서만 읽어서 workspace 질의는 늘 `[]` 였고, 그러면
    `evidence_selector` 가 실험 3회로 정한 「질문과 라벨 **양쪽에서** 기업명 제거」가
    라벨 쪽에서 안 걸린다 — 모듈이 **실패로 기록한 실험 ②** 로 되돌아간다.

    이름은 이미 손에 있다 — `decision.anchors` 가 그 워크스페이스 기업들이다.
    """
    seen: dict = {}

    def _spy(events, *, intent, embed, anchor_names):
        seen["anchor_names"] = list(anchor_names)
        return {}

    monkeypatch.setattr(
        "app.services.retrieve_service.evidence_selector.similarities", _spy)
    wired["decision"] = _context_decision()
    orchestrator = _orchestrator([_hit("01234567", "무관한기업")])   # resolved 없음

    RetrieveService(orchestrator).retrieve(_request())

    assert seen["anchor_names"] == ["삼성전자", "SK하이닉스"]


def test_both_entrances_pick_the_same_material(wired):
    """★**계약이 뒤집혔다**(2026-09-05 · §6-0 A-6). 전에는 이 자리가
    「`/retrieve` 는 무변경」이라 히트(에스비비테크)를 그대로 재료로 삼는 것을
    못 박고 있었다 — 이 파일 머리말이 🔴 로 적어 둔 바로 그 모양인데,
    `/ask` 에서만 고치고 `/retrieve` 에는 남겨 뒀던 것이다.

    ★그래서 같은 질문이 **입구에 따라 갈렸다.** 판정 함수
      (`hits_reflect_the_anchor`)는 이미 공유하고 있었고 `/retrieve` 만 그 답을
      안 썼다. 선정을 `material_companies()` 한 곳으로 모아 둘을 맞춘다.

    ★`SEMANTIC` 은 죽지 않았다 — `match_type` 은 그대로 나간다. 바뀐 것은
      「의미 유사 기업을 **재료로도 쓰나**」뿐이다."""
    wired["decision"] = _query_decision(key="엔비디아", name="엔비디아")
    orchestrator = _orchestrator([_hit("01234567", "에스비비테크")])

    retrieved = RetrieveService(orchestrator).retrieve(_request())

    assert [c.name for c in retrieved.companies] == ["엔비디아"], \
        "앵커가 재료를 정한다 — 히트는 이 앵커를 반영하지 않는다"


def test_anchor_companies_are_capped_and_the_cut_is_logged(wired, caplog):
    """★앵커 기업 수 상한은 기존 `_MAX_COMPANIES` 를 그대로 쓴다 — 새 숫자를
    만들지 않는다. **조용히 자르지 않는다**([규칙 2])."""
    many = {f"0000000{i}": f"기업{i}" for i in range(8)}
    wired["decision"] = AnchorDecision(
        source=AnchorSource.CONTEXT, workspace_names=many,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.CONTEXT)
                 for k, n in many.items()])
    with caplog.at_level("INFO"):
        retrieved = RetrieveService(_orchestrator()).retrieve(_request())
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
    RetrieveService(_orchestrator([hit])).retrieve(_request())
    assert "ev_from_hit" in captured["ids"]


# ══════════════════════════════════════════════════════════════════════
#  질문이 지목한 대상은 전부 재료 기업이다 (§6-0 A-9)
#
#  A-7 이 2차 앵커를 세웠는데 **랭킹에만** 걸었다. 히트 갈래는 `companies_from()`
#  이 히트만 보므로 「삼성전자와 현대차 중 어디가 리스크가 커?」의 사건·근거가
#  전부 삼성전자 것이었다 — 현대차 사건 9건이 실재하는데도(실측 2026-09-05).
# ══════════════════════════════════════════════════════════════════════

_HYUNDAI = "00164742"


def _two_anchor_decision(first=(_SAMSUNG, "삼성전자"), second=(_HYUNDAI, "현대자동차")):
    return AnchorDecision(
        source=AnchorSource.QUERY, workspace_names=_WS,
        anchors=[Anchor(key=k, name=n, source=AnchorSource.QUERY)
                 for k, n in (first, second)])


def test_every_named_anchor_is_material_even_when_the_hits_only_reflect_the_first(wired):
    """★비교 질의 — 두 대상의 사건을 나란히 놓아야 답이 된다. 히트는 1차 앵커
    하나뿐이라 그것만 믿으면 2차 앵커의 재료가 통째로 빈다."""
    wired["decision"] = _two_anchor_decision()
    orchestrator = _orchestrator([_hit(_SAMSUNG, "삼성전자")],
                                 mode=SearchMode.NAME, resolved=[_resolution()])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.key for c in retrieved.companies] == [_SAMSUNG, _HYUNDAI]


def test_named_anchors_come_after_the_hits_and_are_not_duplicated(wired):
    """★히트 순서가 이긴다 — 앵커는 **빠진 것만 뒤에** 붙는다. 앞에 세우면
    `_MAX_COMPANIES` 때문에 히트가 밀린다(`with_anchor_backstop` 과 같은 교환)."""
    wired["decision"] = _two_anchor_decision()
    orchestrator = _orchestrator([_hit("00301246", "SFA반도체"), _hit(_HYUNDAI, "현대자동차")],
                                 mode=SearchMode.RELATIONSHIP, resolved=[_resolution()])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.key for c in retrieved.companies] == ["00301246", _HYUNDAI, _SAMSUNG]


def test_a_single_anchor_still_leaves_relationship_hits_alone(wired):
    """★불변식 — 앵커가 하나면 예전과 글자까지 같다. 「삼성전자에 납품하는 기업」
    에서 앵커를 끼워 넣으면 공급사 한 곳이 밀린다(§5-16 이 남긴 교환)."""
    wired["decision"] = _query_decision(key=_SAMSUNG, name="삼성전자")
    orchestrator = _orchestrator([_hit("00301246", "SFA반도체"), _hit("01095722", "심텍")],
                                 mode=SearchMode.RELATIONSHIP, resolved=[_resolution()])
    retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert [c.key for c in retrieved.companies] == ["00301246", "01095722"]


def test_named_anchors_respect_the_company_cap_and_the_cut_is_logged(wired, caplog):
    """★새 숫자를 만들지 않는다 — 상한은 기존 `_MAX_COMPANIES` 고, 잘리면 적는다."""
    wired["decision"] = _two_anchor_decision()
    hits = [_hit(f"0000000{i}", f"기업{i}") for i in range(rs_module._MAX_COMPANIES)]
    orchestrator = _orchestrator(hits, mode=SearchMode.RELATIONSHIP, resolved=[_resolution()])
    with caplog.at_level("INFO"):
        retrieved = RetrieveService(orchestrator).retrieve(_request())
    assert len(retrieved.companies) == rs_module._MAX_COMPANIES
    assert "truncated" in caplog.text

# ══════════════════════════════════════════════════════════════════════
#  링(ring) 순서
# ══════════════════════════════════════════════════════════════════════

def test_relations_come_out_in_ring_order(wired):
    """Ring 0 → 1 → 2 → 3. 점수가 낮아도 안쪽 링이 먼저다."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_ring3", "09999999", "남", "08888888", "남2", score=0.99),
        _row("e_ring2", _SAMSUNG, "삼성전자", "evt_1", "어떤 사건",
             tgt_label="Event", score=0.98),
        _row("e_ring1", _SAMSUNG, "삼성전자", "00301246", "SFA반도체", score=0.97),
        _row("e_ring0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스", score=0.10),
    ]}
    retrieved = RetrieveService(_orchestrator()).retrieve(_request())
    assert [r.edge_id for r in retrieved.relations] == [
        "e_ring0", "e_ring1", "e_ring2", "e_ring3"]


def test_ring_does_not_drop_unrelated_relations(wired):
    """★**hard filter 가 아니다**(설계서 §3) — 워크스페이스와 안 닿는 관계도 남는다.
    순서만 뒤로 간다."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [_row("e_far", "09999999", "남", "08888888", "남2")]}
    retrieved = RetrieveService(_orchestrator()).retrieve(_request())
    assert [r.edge_id for r in retrieved.relations] == ["e_far"]


def test_same_ring_keeps_the_incoming_order(wired):
    """★같은 링 안에서는 입력 순서(=점수순)가 남는다 — 같은 질문에 매번 다른
    순서가 나오면 안 된다."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_a", _SAMSUNG, "삼성전자", "00301246", "SFA반도체", score=0.9),
        _row("e_b", _SAMSUNG, "삼성전자", "01095722", "심텍", score=0.8),
    ]}
    retrieved = RetrieveService(_orchestrator()).retrieve(_request())
    assert [r.edge_id for r in retrieved.relations] == ["e_a", "e_b"]


def test_ring_distribution_is_logged(wired, caplog):
    """★어느 링까지 갔는지·링마다 몇 건인지 남긴다(설계서 §3 · [규칙 2])."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스"),
        _row("e1", _SAMSUNG, "삼성전자", "00301246", "SFA반도체"),
    ]}
    with caplog.at_level("INFO"):
        RetrieveService(_orchestrator()).retrieve(_request())
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
    wired["decision"] = _context_decision()
    filler = [_row(f"e_{i}", _SAMSUNG, "삼성전자", f"0777777{i}", f"밖{i}", score=0.99)
              for i in range(30)]
    wired["relations"] = {_SAMSUNG: filler + [
        _row("e_ring0", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스", score=0.01)]}
    retrieved = RetrieveService(_orchestrator()).retrieve(_request())
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
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_partner", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             score=0.99, rel_type="PARTNERS_WITH"),
        _row("e_supply", _SAMSUNG, "삼성전자", "01095722", "심텍",
             score=0.10, rel_type="SUPPLIES_TO"),
    ]}

    retrieved = RetrieveService(
        _orchestrator(edge_types=["SUPPLIES_TO"])).retrieve(_request())

    assert [r.edge_id for r in retrieved.relations] == ["e_supply", "e_partner"]


def test_intent_does_not_beat_the_ring_order(wired):
    """★링을 **가로질러** 의도를 우선할지는 아직 `[DECIDE]` 다(현황서 §5-17·§7-3) —
    링별 quota 냐 의도별 우선순위냐를 **둘 다 재 본 적이 없다.** 실측 없이
    그 결정을 코드로 못 박지 않는다. 의도는 링 **안에서만** 줄을 세운다."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        # Ring 1 인데 질문이 물은 타입
        _row("e_ring1_match", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             rel_type="SUPPLIES_TO"),
        # Ring 0 인데 질문이 안 물은 타입
        _row("e_ring0_other", _SAMSUNG, "삼성전자", _HYNIX, "SK하이닉스",
             rel_type="COMPETES_WITH"),
    ]}

    retrieved = RetrieveService(
        _orchestrator(edge_types=["SUPPLIES_TO"])).retrieve(_request())

    assert [r.edge_id for r in retrieved.relations] == [
        "e_ring0_other", "e_ring1_match"]


def test_relation_intent_is_a_no_op_when_the_query_asked_for_no_relation(wired):
    """관계 키워드가 없는 질의 — 순서를 건드리지 않는다(hard filter 가 아니다)."""
    wired["decision"] = _context_decision()
    wired["relations"] = {_SAMSUNG: [
        _row("e_a", _SAMSUNG, "삼성전자", "00301246", "SFA반도체",
             rel_type="PARTNERS_WITH"),
        _row("e_b", _SAMSUNG, "삼성전자", "01095722", "심텍",
             rel_type="SUPPLIES_TO"),
    ]}

    retrieved = RetrieveService(_orchestrator()).retrieve(_request())

    assert [r.edge_id for r in retrieved.relations] == ["e_a", "e_b"]


def test_the_relation_cut_count_is_logged(wired, caplog):
    """★완료조건 ⓓ — 잘라낸 관계 개수가 로그에 남는다. 조용히 자르면
    「그게 전부」로 읽힌다([규칙 2])."""
    wired["decision"] = _context_decision()
    # 상한(MAX_RELATIONS_PER_COMPANY × 기업 수)을 확실히 넘긴다.
    wired["relations"] = {_SAMSUNG: [
        _row(f"e_{i}", _SAMSUNG, "삼성전자", f"0777777{i}", f"밖{i}")
        for i in range(rs_module.MAX_RELATIONS_PER_COMPANY * 2 + 5)]}

    with caplog.at_level("INFO"):
        RetrieveService(_orchestrator()).retrieve(_request())

    assert "cut=5" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  Path B — 비-Company 앵커의 재료는 **1홉 안의 기업**이다 (§6-0 A-8)
#
#  실측(2026-09-06): 앵커→Company→Event 도달률이 Company 81.3% · Organization
#  78.0% · Person 76.0% · Product 74.3% 로 **네 타입이 거의 같다.** 「Person 은
#  Event 연결이 0이니 REJECT」가 아니라, 우회 경로가 열려 있다는 뜻이다.
# ══════════════════════════════════════════════════════════════════════

from app.api.schemas import NodeLabel                                # noqa: E402
from app.services.graph_service import Relation                      # noqa: E402
from pipeline.freshness import assess                                # noqa: E402

_LEE = "이재용@00126186"


def _edge(target_name, target_key, *, confidence=0.9, target_label="Company",
          source_key=_LEE, source_name="이재용", source_label="Person"):
    """앵커에서 나가는 엣지 하나. ★진짜 `Relation` 을 쓴다 — 가짜 객체를 쓰면
    필드 이름이 바뀌어도 시험이 안 깨진다."""
    return Relation(
        source=source_name, target=target_name, edge_type="IS_EXECUTIVE_OF",
        subtype="", source_type="dart", confidence=confidence, corroboration=1,
        freshness=assess({}, today=date(2026, 9, 10)), props={},
        source_id=source_key, source_entity_type=source_label,
        target_id=target_key, target_entity_type=target_label)


@pytest.fixture
def one_hop(monkeypatch):
    """앵커에서 1홉 — `graph_service.relations_of` 를 세운다."""
    rows: list = []

    def _install(*edges):
        rows[:] = list(edges)
        monkeypatch.setattr(rs_module.graph_service, "relations_of",
                            lambda key, **kw: list(rows))
    return _install


def _person_decision():
    return AnchorDecision(
        source=AnchorSource.QUERY, workspace_names=_WS,
        anchors=[Anchor(key=_LEE, name="이재용", source=AnchorSource.QUERY,
                        label=NodeLabel.Person)])


def test_a_person_anchor_takes_its_material_from_one_hop_companies(one_hop):
    """★「이재용 관련 최근 이슈가 뭐야?」가 **전역 사건 목록**으로 답하던 자리다."""
    one_hop(_edge("삼성전자", "00126380"), _edge("삼성에스디에스", "00126186"))

    companies, events = rs_module.material_companies(
        _person_decision(), MagicMock(), MagicMock(), "이재용 관련 최근 이슈가 뭐야?")

    assert [c.key for c in companies] == ["00126380", "00126186"]
    assert events is None, "앵커 경로다 — 사건은 기업이 정해진 뒤에 고른다"


def test_the_non_company_end_never_becomes_material(one_hop):
    """★`companies` 는 **Company 만** 담는다(설계서 §9). 비-Company key 를
    `events_of` 에 넣으면 예외가 아니라 **조용히 0건**이라 「사건이 없다」로 읽힌다."""
    one_hop(_edge("HBM", "hbm", target_label="Product"),
            _edge("삼성전자", "00126380"))

    companies, _ = rs_module.material_companies(
        _person_decision(), MagicMock(), MagicMock(), "질문")

    assert [c.key for c in companies] == ["00126380"]


def test_the_anchor_itself_is_not_material(one_hop):
    """★앵커 자신은 Company 가 아니므로 재료에 안 들어간다 — 양끝을 다 보되
    라벨로 가른다. 넣으면 위와 같은 조용한 0건이 된다."""
    one_hop(_edge("삼성전자", "00126380"))
    companies, _ = rs_module.material_companies(
        _person_decision(), MagicMock(), MagicMock(), "질문")
    assert _LEE not in [c.key for c in companies]


def test_one_hop_companies_reuse_the_existing_company_cap(one_hop):
    """★**새 숫자를 만들지 않는다.** 상한은 기존 `_MAX_COMPANIES` 다.
    공정거래위원회는 1홉 기업이 73곳이다(실측) — 허브 절단 규칙은 아직 미결이라
    (A-8 Deferred) 여기서는 기존 상한만 건다."""
    one_hop(*[_edge(f"기업{i}", f"0000000{i}", confidence=0.9 - i / 100)
              for i in range(rs_module._MAX_COMPANIES + 5)])

    companies, _ = rs_module.material_companies(
        _person_decision(), MagicMock(), MagicMock(), "질문")

    assert len(companies) == rs_module._MAX_COMPANIES


def test_a_non_company_anchor_never_trusts_the_search_hits(one_hop, monkeypatch):
    """★검색 히트는 **Company 만** 추려 온다(`companies_from`). 비-Company 앵커에서
    히트를 믿으면 앵커와 아무 관계 없는 기업이 재료가 된다 — A-3 이 고친 그 오답이다."""
    one_hop(_edge("삼성전자", "00126380"))
    monkeypatch.setattr(rs_module, "companies_from",
                        lambda result: (_ for _ in ()).throw(
                            AssertionError("히트를 봤다")))

    companies, _ = rs_module.material_companies(
        _person_decision(), MagicMock(), MagicMock(), "질문")

    assert [c.key for c in companies] == ["00126380"]
