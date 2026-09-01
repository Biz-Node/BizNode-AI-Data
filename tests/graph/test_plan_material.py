"""`plan_material` — 재료의 출발점을 확정하는 노드.

이 노드가 정하는 셋(`companies`·`anchor_names`·`intent`)은 **뒤 노드 전부가
읽는 값**이다. 여기서 틀리면 조회가 통째로 어긋나는데, 어긋난 결과가 예외가
아니라 **조용한 0건**으로 나온다.

★`use_hits`·`backstop` 은 State 에서 빠졌다(1.5차 정리). 그 둘이 지키던 의미는
  **없어지지 않았다** — 판정이 실제로 한 일은 전부 `companies` 에 드러나므로,
  아래 테스트는 플래그 대신 **그 결과**를 본다. 「히트를 믿었나」는 companies 가
  히트에서 왔는지로, 「백스톱이 끼어들었나」는 companies 가 앵커로 메워졌는지로
  검증한다. 관측 지점만 옮긴 것이지 그물이 성겨진 것이 아니다.

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
    """`resolved_entities` 가 비면 앵커로 내려간다 — `source=context` 가 그렇다.

    ★이 fallback 이 없으면 `anchor_names` 가 `[]` 가 되어 `evidence_selector`
      의 「질문과 라벨 양쪽에서 앵커명 제거」 규칙이 라벨 쪽에서 안 걸린다
      (현황서 §5-23 이 고친 퇴행).
    """
    decision = AnchorDecision(
        source=AnchorSource.CONTEXT,
        anchors=[Anchor(key=_SAMSUNG, name="삼성전자", source=AnchorSource.CONTEXT)])

    got = plan_material(_state(request_, resolved=[], decision=decision))

    assert got["anchor_names"] == ["삼성전자"]


def test_anchor_names_fall_back_to_material_companies_when_anchorless(request_):
    """★**셋째 갈래**(최종 설계 §17-3 이 열었다 · `_anchor_names_for`).

    `anchorless` 는 `resolved_entities` 도 `decision.anchors` 도 비어 있다.
    거기서 멈추면 §5-23 이 고친 퇴행이 그대로 돌아온다 — 라벨에 든 기업명이
    유사도 상위를 먹는다. 전에는 워크스페이스 앵커가 그 이름을 댔고, 지금은
    **히트가 준 재료 기업**이 댄다.
    """
    decision = AnchorDecision(source=AnchorSource.ANCHORLESS)

    got = plan_material(_state(request_, resolved=[], decision=decision,
                               hits=[_hit("00164779", "SK하이닉스")]))

    assert got["anchor_names"] == ["SK하이닉스"]


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
#  히트를 재료로 믿어도 되나 — ★`companies` 가 어디서 왔는지로 본다
#
#  전에는 `use_hits` 플래그를 봤다. 그 값이 State 에서 빠졌다고 검증까지
#  빠지면 안 된다 — **플래그가 갈랐던 두 갈래는 `companies` 의 출처로
#  그대로 드러난다.** 히트를 믿으면 히트에서, 안 믿으면 앵커에서 온다.
# ══════════════════════════════════════════════════════════════════

def test_hits_are_trusted_only_when_search_actually_resolved_an_anchor(request_):
    """★믿어도 되는 경우는 하나다 — ② Search 가 실제로 앵커를 잡고 그래프를 돈
    경우. 그때 히트는 「그 기업의 관계 상대」이고 그게 곧 답이다.

    믿었다는 것은 **재료가 히트에서 왔다**는 뜻이다 — 앵커(삼성전자)가 아니라
    관계 상대(SK하이닉스)가 `companies` 에 선다.
    """
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[_hit("00164779", "SK하이닉스")]))

    assert [c.key for c in got["companies"]] == ["00164779"]
    assert _SAMSUNG not in [c.key for c in got["companies"]], "앵커가 아니라 히트다"


def test_hits_are_not_trusted_without_resolved_entities(request_):
    """`norm_name` fallback 으로 뒤늦게 앵커를 찾은 경우 — 히트는 앵커와 무관하다.

    ★히트가 있는데도 **재료는 앵커 자신**이다. 히트를 썼다면 「아무기업」이
      섰을 것이다 — 그게 안 섰다는 것이 「안 믿었다」의 관측 가능한 형태다.
    """
    got = plan_material(_state(request_, resolved=[],
                               hits=[_hit("00999999", "아무기업")]))

    assert [c.key for c in got["companies"]] == [_SAMSUNG], "앵커 자신이 출발점이다"


def test_context_anchor_never_trusts_hits(request_):
    """`source=context` 는 화면이 대상을 알고 있다 — 히트가 아니라 그 기업이다.

    ★따라서 히트가 몇 건이든 재료는 앵커다. 여기서 히트를 믿으면 「현대차
      페이지를 보며 물었는데 의미가 비슷한 남의 기업으로 답하는」 것이 된다.
    """
    decision = AnchorDecision(
        source=AnchorSource.CONTEXT,
        anchors=[Anchor(key=_SAMSUNG, name="삼성전자", source=AnchorSource.CONTEXT)])

    got = plan_material(_state(request_, hits=[_hit("00999999", "아무기업")],
                               decision=decision))

    assert [c.key for c in got["companies"]] == [_SAMSUNG]
    assert "00999999" not in [c.key for c in got["companies"]], "히트를 쓰면 안 된다"


def test_anchorless_takes_its_material_from_the_hits(request_):
    """★**앵커가 없으면 히트가 재료다**(최종 설계 §8 · `_hits_reflect_the_anchor`).

    전에는 이 상태가 `workspace` 였고 담아 둔 기업이 재료가 됐다. 지금은 앵커가
    없으므로 Global Search 가 찾아 준 것이 유일한 재료다 — 워크스페이스는
    그 결과의 **순서**에만 관여한다.
    """
    decision = AnchorDecision(source=AnchorSource.ANCHORLESS,
                              workspace_names={_SAMSUNG: "삼성전자"})

    got = plan_material(_state(request_, resolved=[], decision=decision,
                               hits=[_hit("00999999", "아무기업")]))

    assert [c.key for c in got["companies"]] == ["00999999"]
    assert _SAMSUNG not in [c.key for c in got["companies"]], \
        "워크스페이스 기업이 재료로 승격되면 안 된다"


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


def test_backstop_does_not_intrude_when_material_already_exists(request_):
    """재료가 이미 있으면 백스톱은 끼어들지 않는다 — 늘 넣으면 재료 구성이 바뀐다
    (실측 41건 중 13건에서 공급사가 밀려났다).

    ★끼어들지 않았다는 것은 **앵커가 `companies` 에 안 섰다**는 뜻이다.
      끼어들었다면 삼성전자가 SK하이닉스 앞에 섰을 것이다.
    """
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[_hit("00164779", "SK하이닉스")]))

    assert [c.key for c in got["companies"]] == ["00164779"]
    assert _SAMSUNG not in [c.key for c in got["companies"]], "백스톱이 끼어들었다"


def test_backstop_fills_companies_when_hits_yield_none(request_):
    """관계 상대가 Person·Organization·Event 인 질의에서 재료가 통째로 0 이 됐다
    (현황서 §5-16). 앵커는 멀쩡히 잡혀 있는데도 그랬다.

    ★검증 지점이 플래그에서 **결과**로 옮겨졌다. 백스톱이 도는지는
      `companies` 가 비지 않고 앵커로 메워졌는지로 본다 — 애초에 이 로직이
      막으려던 것이 「앵커는 멀쩡한데 재료가 0」이라, 그게 곧 계약이다.
      `backstop` 플래그가 State 에서 빠져도 이 계약은 그대로 검사된다.

    ★`_with_anchor_backstop()` 을 **대역으로 바꾸지 않는다.** 그러면 백스톱이
      실제로 무엇을 넣는지는 아무도 안 보게 된다. 대역은 그 함수가 존재 확인에
      쓰는 DB 한 자리(`company_service.names_by_keys`)뿐이고, 그건 conftest 의
      `graph_companies` 가 세운다.
    """
    got = plan_material(_state(request_, resolved=[_resolution(_SAMSUNG, "삼성전자")],
                               hits=[]))

    assert [c.key for c in got["companies"]] == [_SAMSUNG]
