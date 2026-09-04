"""`/retrieve` 계보와 그래프 계보가 **같은 재료·같은 근거**를 내는가.

★Phase 1.5 계약 6번이다. `/retrieve` 는 2차 동안 **독립 경로로 유지**하되,
  두 사본이 갈리면 여기서 **즉시 실패**해야 한다. 갈려도 아무도 모르는 것이
  지금까지의 문제였다.

    /retrieve  ▶ RetrieveService._events_of / _relations_of / _propagation_of
    /ask       ▶ graph_tools.get_events / get_relations / get_propagation

  같은 조립을 두 벌 들고 있으므로, 한쪽만 고쳐지면 같은 질문에 다른 답이 나간다.
  **실제로 그런 일이 있었다** — 도구 쪽이 공유 사건의 근거를 합치지 않아
  둘째 기업의 근거가 조용히 사라졌다(아래 `test_shared_event_...`).

★이 파일은 **DB 를 쓰지 않는다.** 두 사본에 **같은 raw row** 를 먹이고 산출을
  비교하는 것이 목적이라, 진짜 그래프는 오히려 변수를 늘린다. 실 DB 대조는
  `test_parity.py`(`needs_db`)와 `batch/audit/ask_graph_parity.py` 의 몫이다.

★임베딩을 끈다(`_embed` → `None`). 임베딩 값은 **실행마다 흔들려서**
  (실측 2026-08-28: 배치 149건에서 코사인 편차 최대 4.4e-03) 켜 두면 이 테스트가
  간헐 실패한다. 여기서 보려는 것은 순위가 아니라 **두 사본이 같은가**다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import Anchor, AnchorSource, RelationEndpoint
from app.services.query_understanding import AnchorDecision
from search.dto.search_query import SearchQuery
from search.model.enums import SearchMode
from datetime import date

_SAMSUNG = "00126380"
_HYNIX = "00164779"
_NORM = {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def _event_row(event_id, evidence_ids, **over):
    row = {"event_id": event_id, "name": f"사건 {event_id}", "event_type": "분쟁소송",
           "is_risk": True, "role": "subject", "occurred_at": "2026-06-01",
           "article_count": 1, "timeline": [], "evidence_ids": list(evidence_ids),
           "eventness_suspect": False, "sign": "negative"}
    row.update(over)
    return row


@pytest.fixture
def both_paths(monkeypatch):
    """두 사본에 **같은 raw row** 를 먹이고 각각의 산출을 돌려준다."""
    from app.services import company_service, evidence_selector, retrieve_service
    from app.tools import graph_tools as gt, scope

    def _install(by_company: dict[str, list[dict]]):
        """`{norm_name: [row, ...]}` — 기업마다 다른 행을 준다."""
        def events_of(key):
            return [dict(r) for r in by_company[_NORM.get(key, key)]]

        for module in (company_service, gt.company_service):
            monkeypatch.setattr(module, "events_of", events_of)
        monkeypatch.setattr(gt.company_service, "norm_names_by_keys",
                            lambda keys: {k: _NORM[k] for k in keys})
        # ★임베딩을 끈다 — 값이 흔들려 테스트가 간헐 실패한다.
        monkeypatch.setattr(evidence_selector, "similarities", lambda *a, **k: {})
        monkeypatch.setattr(gt, "_embed", lambda: None)

        companies = [RelationEndpoint(key=k, name=v) for k, v in _NORM.items()]
        query = SearchQuery(raw_query="q", normalized_query="q",
                            mode=SearchMode.NAME, today=date(2026, 8, 28))
        decision = AnchorDecision(
            source=AnchorSource.QUERY,
            anchors=[Anchor(key=_SAMSUNG, name="삼성전자",
                            source=AnchorSource.QUERY)])

        base = retrieve_service.RetrieveService(
            orchestrator=object(), embed=None)._events_of(
                companies, "담합 소송", query, decision)
        with scope.anchor_scope(list(_NORM), anchor_names=list(_NORM.values())):
            tool = gt.get_events(list(_NORM), "담합 소송")
        return base, tool

    return _install


# ══════════════════════════════════════════════════════════════════
#  ★회귀 그물 — 공유 사건의 근거
# ══════════════════════════════════════════════════════════════════

def test_shared_event_keeps_every_companys_evidence_on_both_paths(both_paths):
    """★**같은 Event 를 여러 기업이 공유한다**(실측 938건 중 85건). 근거는
    기업마다 자기 `HAS_EVENT` 엣지에 따로 달려 있으므로, dedup 할 때 합치지
    않으면 먼저 온 기업 것만 남고 나머지가 **조용히 사라진다.**

    실측(2026-08-28)으로 도구 쪽이 정확히 그랬다 — 「담합 소송」 질의에서 3건.
    `retrieve_service._merge_evidence_ids()` 가 이미 고쳐 둔 버그를 도구화가
    되돌린 것이다.
    """
    base, tool = both_paths({
        "삼성전자": [_event_row("evt_shared", ["ev_samsung"])],
        "SK하이닉스": [_event_row("evt_shared", ["ev_hynix"])],
    })

    assert [e.event_id for e in base] == [e.event_id for e in tool] == ["evt_shared"]
    assert base[0].evidence_ids == ["ev_samsung", "ev_hynix"]
    assert tool[0].evidence_ids == base[0].evidence_ids, (
        "도구가 둘째 기업의 근거를 버렸다 — /retrieve 와 /ask 가 다른 근거를 낸다")


def test_merged_evidence_keeps_order_and_drops_duplicates(both_paths):
    """순서 보존·중복 제거까지 **같은 규칙**이어야 한다."""
    base, tool = both_paths({
        "삼성전자": [_event_row("evt_shared", ["ev_a", "ev_b"])],
        "SK하이닉스": [_event_row("evt_shared", ["ev_b", "ev_c"])],
    })

    assert base[0].evidence_ids == ["ev_a", "ev_b", "ev_c"]
    assert tool[0].evidence_ids == base[0].evidence_ids


def test_merging_does_not_leak_into_the_source_rows(both_paths):
    """★병합이 `events_of()` 가 준 dict 를 **밖에서 키우면 안 된다.** 같은
    조회를 두 번 하면 근거가 불어난다."""
    rows = {"삼성전자": [_event_row("evt_shared", ["ev_samsung"])],
            "SK하이닉스": [_event_row("evt_shared", ["ev_hynix"])]}
    both_paths(rows)

    assert rows["삼성전자"][0]["evidence_ids"] == ["ev_samsung"]
    assert rows["SK하이닉스"][0]["evidence_ids"] == ["ev_hynix"]


# ══════════════════════════════════════════════════════════════════
#  재료 집합 자체
# ══════════════════════════════════════════════════════════════════

def test_both_paths_pick_the_same_events(both_paths):
    """기업마다 다른 사건이 섞여도 **고른 사건 집합이 같아야** 한다."""
    base, tool = both_paths({
        "삼성전자": [_event_row(f"evt_s{i}", [f"ev_s{i}"]) for i in range(4)],
        "SK하이닉스": [_event_row(f"evt_h{i}", [f"ev_h{i}"]) for i in range(4)],
    })

    assert {e.event_id for e in base} == {e.event_id for e in tool}
    assert {i for e in base for i in e.evidence_ids} == \
           {i for e in tool for i in e.evidence_ids}


def test_both_paths_apply_the_same_per_company_limit(both_paths):
    """★상한은 **기업마다 따로** 걸린다 — 한 줄로 세워 자르면 사건 많은 기업이
    다 먹는다. 두 사본이 같은 상한을 같은 방식으로 써야 한다."""
    from app.services.retrieve_service import MAX_EVENTS_PER_COMPANY

    n = MAX_EVENTS_PER_COMPANY + 5
    base, tool = both_paths({
        "삼성전자": [_event_row(f"evt_s{i:02d}", [f"ev_s{i:02d}"]) for i in range(n)],
        "SK하이닉스": [_event_row(f"evt_h{i:02d}", [f"ev_h{i:02d}"]) for i in range(n)],
    })

    assert len(base) == len(tool) == MAX_EVENTS_PER_COMPANY * 2
    assert {e.event_id for e in base} == {e.event_id for e in tool}


def test_the_only_intended_difference_is_the_suspect_exclusion(both_paths):
    """★도구가 1차보다 **더 빼는 것은 `eventness_suspect` 하나뿐**이다.

    이것만이 예상된 차이다(현황서 §8-11). 다른 차이가 생기면 두 사본이 갈린 것이다.
    """
    base, tool = both_paths({
        "삼성전자": [_event_row("evt_ok", ["ev_ok"]),
                  _event_row("evt_susp", ["ev_susp"], eventness_suspect=True)],
        "SK하이닉스": [],
    })

    assert {e.event_id for e in base} == {"evt_ok", "evt_susp"}
    assert {e.event_id for e in tool} == {"evt_ok"}
    assert {e.event_id for e in base} - {e.event_id for e in tool} == {"evt_susp"}
