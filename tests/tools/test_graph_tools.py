"""Graph 도구 셋 — **4원칙이 실제로 지켜지는가.**

① 기업명이 아니라 key 만 받고, 범위 밖은 거부한다
② 표기가 끝난 DTO 를 돌려준다
③ `limit` 을 인자로 받지 않는다
④ 빈 결과와 실패를 구별한다
"""

from __future__ import annotations

import inspect

import pytest

from app.tools import graph_tools as gt
from app.tools import scope
from app.tools.dto import (CAUTION_NEWS_DEVELOPS, DIRECTION_NOTE, FRESHNESS_WEIGHT,
                           ROLE_NOTE, SOURCE_NOTE, SYMMETRIC_EDGE_TYPES)
from app.tools.errors import KeyNotResolved, OutOfScopeKey

_SAMSUNG = "00126380"
_HYNIX = "00164779"


def _row(**over):
    row = {"edge_id": "e1", "type": "SUPPLIES_TO", "subtype": "공급",
           "source": {"key": _SAMSUNG, "name": "삼성전자"},
           "target": {"key": _HYNIX, "name": "SK하이닉스"},
           "evidence_id": "ev_1", "source_type": "news", "freshness": "current",
           "confidence": 0.9, "ratio": None, "verdict": "supported"}
    row.update(over)
    return row


def _event(**over):
    row = {"event_id": "evt_1", "name": "압수수색", "event_type": "규제수사",
           "is_risk": True, "role": "subject", "occurred_at": "2026-06-11",
           "article_count": 1, "timeline": [], "evidence_ids": ["ev_e"],
           "eventness_suspect": False, "sign": "negative"}
    row.update(over)
    return row


@pytest.fixture
def stub(monkeypatch):
    """DB 없이 도구만 본다."""
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
#  ① 범위 — ★완료 기준 ③
# ══════════════════════════════════════════════════════════════════

def test_key_outside_the_scope_is_rejected(stub):
    """★앵커 키 집합 밖의 key 는 **거부**한다. 조용히 거르면 「그 기업은 재료가
    없었다」로 읽히는데 실제로는 물어본 적조차 없는 것이다."""
    stub(relations=[_row()])
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(OutOfScopeKey) as got:
            gt.get_relations([_HYNIX])
    assert _HYNIX in str(got.value)


def test_every_tool_enforces_the_scope(stub):
    stub(relations=[_row()], events=[_event()])
    with scope.anchor_scope([_SAMSUNG]):
        for call in (lambda: gt.get_relations(["밖"]),
                     lambda: gt.get_events(["밖"], intent="x")):
            with pytest.raises(OutOfScopeKey):
                call()


def test_calling_without_a_scope_is_rejected(stub):
    """★범위가 안 세워졌으면 **막는다.** 기본값이 「전부 허용」이면 방어가 아니다."""
    stub(relations=[_row()])
    with pytest.raises(OutOfScopeKey):
        gt.get_relations([_SAMSUNG])


def test_scope_is_not_an_argument():
    """★범위를 **인자로 받지 않는다.** 2차에 부르는 쪽은 LLM 이다 — 인자면
    LLM 이 넓힐 수 있어 방어가 장식이 된다."""
    for fn in (gt.get_relations, gt.get_events, gt.get_propagation):
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"allowed_keys", "scope", "workspace_keys"}), fn.__name__


def test_company_name_is_not_an_argument():
    """★기업명 문자열을 받지 않는다 — 도구가 이름을 다시 해소하면 앵커 판정이
    무의미해진다(`AskResponse.anchor_source` 는 서버가 아는 결정론적 값이다)."""
    for fn in (gt.get_relations, gt.get_events):
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"name", "names", "company_name", "query"}), fn.__name__


# ══════════════════════════════════════════════════════════════════
#  ③ limit 은 인자가 아니다
# ══════════════════════════════════════════════════════════════════

def test_limit_is_not_an_argument():
    for fn in (gt.get_relations, gt.get_events, gt.get_propagation):
        assert "limit" not in inspect.signature(fn).parameters, fn.__name__


def test_limits_are_the_same_objects_as_retrieve_service():
    """★상한 **값을 바꾸지 않는다.** 새로 쓰면 두 벌이 되어 조용히 갈린다."""
    from app.services import retrieve_service as rs

    assert gt._MAX_RELATIONS_PER_COMPANY == rs._MAX_RELATIONS_PER_COMPANY
    assert gt._MAX_EVENTS_PER_COMPANY == rs._MAX_EVENTS_PER_COMPANY
    assert gt._MAX_RISK_EVENTS_FOR_PROPAGATION == rs._MAX_RISK_EVENTS_FOR_PROPAGATION


# ══════════════════════════════════════════════════════════════════
#  ④ 빈 결과 vs 실패
# ══════════════════════════════════════════════════════════════════

def test_unresolvable_key_is_a_failure_not_zero_rows(monkeypatch, stub):
    """★`events_of()` 는 못 찾은 key 에 **예외가 아니라 빈 목록**을 준다. 그래서
    틀린 `corp_code` 가 「사건이 없는 기업」과 구별되지 않는다 — 여기서 가른다."""
    stub(events=[_event()])
    monkeypatch.setattr(gt.company_service, "norm_names_by_keys", lambda keys: {})
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(KeyNotResolved):
            gt.get_events([_SAMSUNG], intent="x")


def test_resolvable_key_with_no_rows_is_empty_not_failure(stub):
    stub(relations=[], events=[])
    with scope.anchor_scope([_SAMSUNG]):
        assert gt.get_relations([_SAMSUNG]) == []
        assert gt.get_events([_SAMSUNG], intent="x") == []


def test_missing_event_node_is_skipped_not_raised(monkeypatch):
    """★`event_impact()` 의 `None`(사건 못 찾음) vs `[]`(파급 없음) 규약을 따른다.
    여기서 예외를 던지면 사건 하나 때문에 나머지 파급이 통째로 사라져 재료가
    달라진다."""
    monkeypatch.setattr(gt.relation_service, "event_impact",
                        lambda eid: None if eid == "bad" else [])
    assert gt.get_propagation(["bad", "good"]) == []


# ══════════════════════════════════════════════════════════════════
#  ② 표기 — 하나도 빠뜨리지 않는다
# ══════════════════════════════════════════════════════════════════

def test_relation_carries_every_notation(stub):
    stub(relations=[_row(ratio=0.72)])
    with scope.anchor_scope([_SAMSUNG]):
        dto = gt.get_relations([_SAMSUNG])[0]

    assert dto.source_note == SOURCE_NOTE["news"]
    assert dto.direction_note == DIRECTION_NOTE["directed"]
    assert dto.ratio_unit == "percent" and dto.ratio_text == "0.72%"
    # confidence 0.9 × current 1.0
    assert dto.effective_confidence == 0.9


def test_symmetric_edges_are_marked(stub):
    """★`PARTNERS_WITH`·`COMPETES_WITH` 는 화살표에 뜻이 없다."""
    stub(relations=[_row(type="PARTNERS_WITH")])
    with scope.anchor_scope([_SAMSUNG]):
        dto = gt.get_relations([_SAMSUNG])[0]

    assert "PARTNERS_WITH" in SYMMETRIC_EDGE_TYPES
    assert dto.direction == "symmetric"
    assert "「A 가 B 에게」로 읽지 말 것" in dto.direction_note


def test_news_develops_gets_the_caution(stub):
    """★뉴스에서 뽑은 `DEVELOPS` 는 절반 가까이 틀린다(0차 실측 46.1%)."""
    stub(relations=[_row(type="DEVELOPS", source_type="news")])
    with scope.anchor_scope([_SAMSUNG]):
        assert gt.get_relations([_SAMSUNG])[0].caution == CAUTION_NEWS_DEVELOPS


def test_dart_develops_gets_no_caution(stub):
    """공시에서 온 `DEVELOPS` 는 오추출이 아니다 — 경고를 붙이면 거짓 경고다."""
    stub(relations=[_row(type="DEVELOPS", source_type="dart")])
    with scope.anchor_scope([_SAMSUNG]):
        assert gt.get_relations([_SAMSUNG])[0].caution is None


def test_stale_relation_gets_a_lower_effective_confidence(stub):
    """★`score` 가 아니라 `confidence × 신선도 가중치`다."""
    stub(relations=[_row(freshness="stale", confidence=0.9)])
    with scope.anchor_scope([_SAMSUNG]):
        dto = gt.get_relations([_SAMSUNG])[0]

    assert dto.effective_confidence == round(0.9 * FRESHNESS_WEIGHT["stale"], 3)


def test_event_role_note_separates_mentioned_from_subject(stub):
    stub(events=[_event(role="mentioned")])
    with scope.anchor_scope([_SAMSUNG]):
        dto = gt.get_events([_SAMSUNG], intent="x", role=None)[0]

    assert dto.role_note == ROLE_NOTE["mentioned"]
    assert "이 기업에 난 일이 아니다" in dto.role_note


def test_timeline_stays_a_list_and_gets_a_summary(stub):
    """★배열을 문자열로 **펴지 않는다.** 요약은 별도 필드다 — 편 적이 있어서
    `size()` 가 국면 수가 아니라 글자 수를 센 사고가 있었다(28건)."""
    stub(events=[_event(timeline=[{"period": "2026-06", "name": "1국면"},
                                  {"period": "2026-07", "name": "2국면"}])])
    with scope.anchor_scope([_SAMSUNG]):
        dto = gt.get_events([_SAMSUNG], intent="x", role=None)[0]

    assert len(dto.timeline) == 2
    assert dto.timeline_summary == "2026-06 1국면 → 2026-07 2국면 (2국면)"


def test_sign_comes_from_impacts_and_stays_none_when_absent(stub):
    stub(events=[_event(sign=None)])
    with scope.anchor_scope([_SAMSUNG]):
        assert gt.get_events([_SAMSUNG], intent="x", role=None)[0].sign is None


# ══════════════════════════════════════════════════════════════════
#  의심 표시 제외
# ══════════════════════════════════════════════════════════════════

def test_eventness_suspect_events_are_dropped(stub):
    """★사건이 아닌 것으로 보이는 83건에 붙은 표시다. 표시가 있는데 재료로
    쓰면 표시를 한 이유가 없어진다."""
    stub(events=[_event(event_id="ok"),
                 _event(event_id="susp", eventness_suspect=True)])
    with scope.anchor_scope([_SAMSUNG]):
        got = gt.get_events([_SAMSUNG], intent="x", role=None)

    assert [e.event_id for e in got] == ["ok"]


def test_grounding_suspect_relations_are_dropped(stub):
    """★`graph_service` 가 파급 계산에서 빼는 것과 같은 규칙이다.

    ★실측(2026-08-28)으로는 `company_service._relation()` 이 이미 같은 규칙을
      적용해 여기까지 오지 않는다 — 그래도 **도구 경계에서 다시 본다.** 위쪽
      `_HIDE` 가 느슨해지면 이 도구가 조용히 따라 느슨해지면 안 된다.
    """
    stub(relations=[_row(edge_id="ok"),
                    _row(edge_id="susp", verdict="unfounded"),
                    # ★`wrong_type` 은 **남긴다** — 관계 자체는 실재하고 유형만
                    #   틀린 것이라 지우지 않고 점수만 깎는 것이 규칙이다.
                    _row(edge_id="mistyped", verdict="wrong_type")])
    with scope.anchor_scope([_SAMSUNG]):
        got = gt.get_relations([_SAMSUNG])

    assert [r.edge_id for r in got] == ["ok", "mistyped"]


def test_service_already_hides_unfounded_relations():
    """★위쪽 규칙을 **묶어 둔다.** `_HIDE` 가 느슨해지면 여기가 먼저 깨진다."""
    from app.services.company_service import _HIDE

    assert _HIDE == frozenset({"unfounded", "insufficient"})


# ══════════════════════════════════════════════════════════════════
#  key 해소 — norm_name 변환이 재료를 바꾸지 않는다는 전제
# ══════════════════════════════════════════════════════════════════

@pytest.mark.needs_db
def test_norm_names_are_unique_in_the_graph():
    """★`_resolve()` 가 `corp_code` 를 `norm_name` 으로 바꿔 넘기는 전제다.

    겹치는 `norm_name` 이 생기면 `events_of()` 의 `OR` 조건이 **여러 노드**를
    매칭해 재료가 늘어난다. 실측(2026-08-28): 겹치는 이름 0종.
    """
    from app.core.database import neo4j_session

    with neo4j_session() as s:
        row = s.run("""MATCH (c:Company) WHERE c.norm_name IS NOT NULL
                       WITH c.norm_name AS n, count(*) AS c WHERE c > 1
                       RETURN count(*) AS dup""").single()
    assert row["dup"] == 0, "norm_name 이 겹친다 — _resolve() 의 전제가 깨졌다"
