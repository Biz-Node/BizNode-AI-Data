"""`plan_material` — 재료의 출발점을 확정하는 노드.

이 노드가 정하는 다섯(`use_hits`·`companies`·`backstop`·`anchor_names`·`intent`)은
**뒤 노드 전부가 읽는 값**이다. 여기서 틀리면 조회가 통째로 어긋나는데, 어긋난
결과가 예외가 아니라 **조용한 0건**으로 나온다.

★특히 `anchor_names` 는 Phase 1 이 고친 자리다. 전에는 두 곳에서 따로 계산됐고
  `source=query` 면 `decision.anchors` 는 최고점 **1개**인데
  `resolved_entities` 는 복수 후보라 값이 갈릴 수 있었다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.api.schemas import Anchor, AnchorSource
from app.graph.nodes.material import plan_material
from app.services.query_understanding import AnchorDecision
from pipeline.normalizer.resolver import Resolution
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode

_SAMSUNG = "00126380"


def _resolution(corp_code, corp_name, score=1.0):
    return Resolution(corp_code=corp_code, corp_name=corp_name, stock_code=None,
                      method="exact", score=score)


def _query(resolved=()):
    return SearchQuery(raw_query="q", normalized_query="q", mode=SearchMode.NAME,
                       today=date(2026, 8, 28), resolved_entities=list(resolved))


def _result(hits=()):
    return SearchResult(query="q", mode=SearchMode.NAME, hits=list(hits), total=0,
                        took_ms=1, cache_hit=False, used_semantic_fallback=False)


def _hit(entity_id, name):
    return SearchHit(entity_id=entity_id, entity_type=EntityType.COMPANY, name=name,
                     source_score=1.0, sources=["neo4j"])


def _state(request_, *, resolved=(), hits=(), decision=None):
    return {"request": request_, "query": _query(resolved), "result": _result(hits),
            "decision": decision or AnchorDecision(
                source=AnchorSource.QUERY,
                anchors=[Anchor(key=_SAMSUNG, name="삼성전자",
                                source=AnchorSource.QUERY)])}


# ══════════════════════════════════════════════════════════════════
#  anchor_names — Phase 1 이 통일한 계산식
# ══════════════════════════════════════════════════════════════════

def test_anchor_names_prefer_resolved_entities(request_):
    """★`resolved_entities` 가 이긴다 — **재료를 실제로 고른 것이 그쪽**이다.

    `decision.anchors` 는 `_primary()` 가 고른 최고점 1개라, 그것만 쓰면
    「무엇으로 골랐나」와 「무엇으로 검사하나」가 어긋난다.
    """
    got = plan_material(_state(request_, resolved=[
        _resolution(_SAMSUNG, "삼성전자"),
        _resolution("00111111", "삼성전자판매", score=0.8)]))

    assert got["anchor_names"] == ["삼성전자", "삼성전자판매"]


def test_anchor_names_fall_back_to_decision_anchors(request_):
    """`resolved_entities` 가 비면 앵커로 내려간다 — `source=workspace` 가 그렇다.

    ★이 fallback 이 없으면 workspace 질의의 `anchor_names` 가 늘 `[]` 가 되어
      `evidence_selector` 의 「질문과 라벨 양쪽에서 앵커명 제거」 규칙이 라벨
      쪽에서 안 걸린다(현황서 §5-23 이 고친 퇴행).
    """
    decision = AnchorDecision(
        source=AnchorSource.WORKSPACE,
        anchors=[Anchor(key=_SAMSUNG, name="삼성전자", source=AnchorSource.WORKSPACE)])

    got = plan_material(_state(request_, resolved=[], decision=decision))

    assert got["anchor_names"] == ["삼성전자"]


def test_anchor_names_drop_empty_names(request_):
    """이름 없는 후보는 뺀다 — 빈 문자열을 질문에서 지우면 아무 일도 안 난다."""
    got = plan_material(_state(request_, resolved=[
        _resolution(_SAMSUNG, "삼성전자"), _resolution("00111111", "")]))

    assert got["anchor_names"] == ["삼성전자"]


def test_intent_is_derived_from_the_same_anchor_names(request_):
    """`intent` 는 `anchor_names` 로 질문에서 앵커명을 뗀 나머지다.

    ★둘이 **같은 노드에서 한 번에** 정해져야 한다. 나뉘면 또 갈린다.
    """
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")]))

    assert got["intent"] == "압수수색"
    assert "삼성전자" not in got["intent"]


# ══════════════════════════════════════════════════════════════════
#  use_hits — 검색 히트를 재료로 믿어도 되나
# ══════════════════════════════════════════════════════════════════

def test_hits_are_trusted_only_when_search_actually_resolved_an_anchor(request_):
    """★믿어도 되는 경우는 하나다 — ② Search 가 실제로 앵커를 잡고 그래프를 돈
    경우. 그때 히트는 「그 기업의 관계 상대」이고 그게 곧 답이다."""
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[_hit("00164779", "SK하이닉스")]))

    assert got["use_hits"] is True
    assert [c.key for c in got["companies"]] == ["00164779"]


def test_hits_are_not_trusted_without_resolved_entities(request_):
    """`norm_name` fallback 으로 뒤늦게 앵커를 찾은 경우 — 히트는 앵커와 무관하다."""
    got = plan_material(_state(request_, resolved=[],
                               hits=[_hit("00999999", "아무기업")]))

    assert got["use_hits"] is False
    assert [c.key for c in got["companies"]] == [_SAMSUNG], "앵커 자신이 출발점이다"


def test_workspace_anchor_never_trusts_hits(request_):
    """`source=workspace` 는 정의상 `resolved_entities` 가 0 이다."""
    decision = AnchorDecision(
        source=AnchorSource.WORKSPACE,
        anchors=[Anchor(key=_SAMSUNG, name="삼성전자", source=AnchorSource.WORKSPACE)])

    got = plan_material(_state(request_, hits=[_hit("00999999", "아무기업")],
                               decision=decision))

    assert got["use_hits"] is False


# ══════════════════════════════════════════════════════════════════
#  companies · backstop
# ══════════════════════════════════════════════════════════════════

def test_company_keys_are_passed_through_untouched(request_):
    """★`key` 형태를 **바꾸지 않는다.** `corp_code` 없는 기업이 실재하고
    (「원익아이피에스」·「램리서치」), `events_of()` 에 틀린 값을 주면 예외가
    아니라 **조용히 0건**이라 「사건이 없다」로 잘못 읽힌다."""
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[_hit("원익아이피에스", "원익아이피에스")]))

    assert [c.key for c in got["companies"]] == ["원익아이피에스"]


def test_backstop_is_flagged_only_when_it_actually_runs(request_):
    """재료가 이미 있으면 백스톱은 끼어들지 않는다 — 늘 넣으면 재료 구성이 바뀐다
    (실측 41건 중 13건에서 공급사가 밀려났다)."""
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[_hit("00164779", "SK하이닉스")]))

    assert got["backstop"] is False


def test_backstop_flag_set_when_hits_yield_no_company(request_):
    """관계 상대가 Person·Organization·Event 인 질의에서 재료가 통째로 0 이 됐다
    (현황서 §5-16). 앵커는 멀쩡히 잡혀 있는데도 그랬다.

    ★`_with_anchor_backstop()` 을 **대역으로 바꾸지 않는다.** 그러면 백스톱이
      실제로 무엇을 넣는지는 아무도 안 보게 된다. 대역은 그 함수가 존재 확인에
      쓰는 DB 한 자리(`company_service.names_by_keys`)뿐이고, 그건 conftest 의
      `graph_companies` 가 세운다.
    """
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[]))

    assert got["backstop"] is True
    assert [c.key for c in got["companies"]] == [_SAMSUNG]
