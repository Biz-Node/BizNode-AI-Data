"""company_service.events_of() — 기업별 사건 조회의 **근거 귀속**.

★왜 이 파일이 있나 (2026-08-23)

하나의 Event 노드를 여러 기업이 공유한다(실측: 938건 중 85건). 그런데 Event
노드의 `evidence_ids` 는 그 사건에 엮인 **모든 기업의 근거 합집합**이다. 기업별
근거는 `HAS_EVENT` **엣지**에 따로 실려 있다.

    Event evt_news_75e265ad0857 '노조 설립'
      e.evidence_ids = [현대오토에버 것, 현대오토에버 것, SK하이닉스 것, 신세계 것]
      SK하이닉스   -[HAS_EVENT {evidence_id: SK하이닉스 것}]->
      현대오토에버 -[HAS_EVENT {evidence_id: 현대오토에버 것}]->

`events_of()` 가 노드 쪽 합집합을 돌려주는 바람에 「SK하이닉스」 질의의 /ask
근거에 현대오토에버 기사가 섞였다. `role`·`occurred_at` 은 이미 엣지에서
가져오고 있었는데(schemas.py:425 「날짜는 사건 노드가 아니라 관계에 있다」)
`evidence_ids` 만 그 원칙에서 빠져 있었다.

Tier A 는 세션을 가짜로 세워 조립 규칙만 본다. Tier B 는 실 Neo4j 로 돈다 —
건수를 박지 않고 **재적재로도 흔들리지 않는 불변식**만 검사한다.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.core.database import neo4j_session
from app.services import company_service

_HYNIX = "00164779"


# ══════════════════════════════════════════════════════════════════════
#  Tier A — 가짜 세션 (조립 규칙)
# ══════════════════════════════════════════════════════════════════════

def _stub_rows(monkeypatch, rows):
    @contextmanager
    def _session():
        class _Session:
            def run(self, query, **params):
                return iter(rows)
        yield _Session()

    monkeypatch.setattr(company_service, "neo4j_session", _session)


def _row(*, node_evidence_ids, edge_props):
    return {"e": {"event_id": "evt_1", "name": "노조 설립", "event_type": "노무",
                  "is_risk": True, "article_count": 3, "timeline": [],
                  "evidence_ids": list(node_evidence_ids)},
            "h": dict(edge_props)}


def test_evidence_comes_from_the_companys_own_edge(monkeypatch):
    """★공유 사건에서 남의 근거를 물고 오지 않는다 — 이게 이 파일의 존재 이유."""
    _stub_rows(monkeypatch, [_row(
        node_evidence_ids=["ev_autoever", "ev_hynix", "ev_shinsegae"],
        edge_props={"role": "subject", "evidence_id": "ev_hynix"})])

    got = company_service.events_of(_HYNIX)

    assert got[0]["evidence_ids"] == ["ev_hynix"]


def test_node_union_is_not_used_as_a_fallback(monkeypatch):
    """★엣지에 근거가 없으면 **빈 채로 둔다.** 노드 합집합으로 메우면 남의
    근거가 다시 새어 들어온다 — 없는 것과 남의 것은 다르다."""
    _stub_rows(monkeypatch, [_row(
        node_evidence_ids=["ev_someone_else"],
        edge_props={"role": "subject"})])

    got = company_service.events_of(_HYNIX)

    assert got[0]["evidence_ids"] == []


def test_plural_evidence_ids_on_the_edge_are_kept(monkeypatch):
    """엣지도 근거를 여럿 들 수 있다(실측: HAS_EVENT 1,062건 중 11건).
    `relation_service._evidence()`·`graph_searcher._evidence_refs()` 와 같은 규약."""
    _stub_rows(monkeypatch, [_row(
        node_evidence_ids=["ev_a", "ev_b", "ev_other"],
        edge_props={"role": "subject", "evidence_id": "ev_a",
                    "evidence_ids": ["ev_b"]})])

    got = company_service.events_of(_HYNIX)

    assert got[0]["evidence_ids"] == ["ev_a", "ev_b"]


def test_duplicate_evidence_ids_on_the_edge_are_collapsed(monkeypatch):
    """단수 필드가 복수 목록에 또 들어 있는 경우 — 순서는 지키고 중복만 없앤다."""
    _stub_rows(monkeypatch, [_row(
        node_evidence_ids=["ev_a"],
        edge_props={"evidence_id": "ev_a", "evidence_ids": ["ev_a", "ev_b"]})])

    got = company_service.events_of(_HYNIX)

    assert got[0]["evidence_ids"] == ["ev_a", "ev_b"]


def test_other_event_fields_are_unchanged(monkeypatch):
    """근거만 바꾼다 — 사건 자체는 그대로 나가야 한다(공유 구조 유지)."""
    _stub_rows(monkeypatch, [_row(
        node_evidence_ids=["ev_x"],
        edge_props={"role": "subject", "occurred_at": "2026-08-09",
                    "evidence_id": "ev_x"})])

    got = company_service.events_of(_HYNIX)

    assert got[0]["event_id"] == "evt_1"
    assert got[0]["name"] == "노조 설립"
    assert got[0]["is_risk"] is True
    assert got[0]["role"] == "subject"
    assert got[0]["occurred_at"] == "2026-08-09"


# ══════════════════════════════════════════════════════════════════════
#  Tier B — 실 Neo4j (mock 없음)
# ══════════════════════════════════════════════════════════════════════

_OWN_EDGE_EVIDENCE = """
MATCH (c:Company)-[h:HAS_EVENT]->(e:Event {event_id: $eid})
WHERE c.corp_code = $k OR c.norm_name = $k
RETURN [x IN ([h.evidence_id] + coalesce(h.evidence_ids, []))
        WHERE x IS NOT NULL AND x <> ''] AS own
"""


@pytest.fixture(scope="module")
def hynix_events():
    return company_service.events_of(_HYNIX)


def test_real_hynix_has_events(hynix_events):
    assert hynix_events, "SK하이닉스 사건이 0건이면 데이터가 비었거나 질의가 깨진 것"


def test_real_every_returned_evidence_sits_on_the_companys_own_edge(hynix_events):
    """불변식 — 돌려준 근거는 **전부** 그 기업 자기 HAS_EVENT 엣지의 것이다."""
    with neo4j_session() as s:
        for event in hynix_events:
            own = set(s.run(_OWN_EDGE_EVIDENCE,
                            eid=event["event_id"], k=_HYNIX).single()["own"])
            assert set(event["evidence_ids"]) <= own, (
                f"{event['name']}: 자기 엣지에 없는 근거 "
                f"{set(event['evidence_ids']) - own}")


def test_real_shared_event_does_not_leak_other_companies_evidence():
    """★회귀 — 여러 기업이 공유하는 사건에서 남의 근거가 섞이지 않는다.

    실제로 터진 사고: 「SK하이닉스」 질의의 /ask sources 에 현대오토에버
    노조 기사(ev_14df4ce056904b8b)가 들어갔다. 그 근거는 현대오토에버의
    HAS_EVENT 엣지에 달려 있고 SK하이닉스 엣지에는 없다.

    ★**공동 근거는 남의 것이 아니다.** 한 문장이 두 기업을 함께 다루면 양쪽
      엣지에 같은 `evidence_id` 가 달린다(실측: 사건 28건 — 예: 「D램 가격 담합
      소송 기각」이 삼성전자·SK하이닉스 엣지에 같은 id). 그래서 「남의 것」은
      **자기 엣지에 없는 것**으로만 센다.
    """
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run("""
            MATCH (c:Company)-[h:HAS_EVENT]->(e:Event)
            WHERE c.corp_code = $k OR c.norm_name = $k
            MATCH (o:Company)-[oh:HAS_EVENT]->(e)
            WHERE o <> c
            WITH e,
                 [x IN ([h.evidence_id] + coalesce(h.evidence_ids, []))
                  WHERE x IS NOT NULL AND x <> ''] AS mine,
                 collect(DISTINCT oh.evidence_id) AS others
            RETURN e.event_id AS eid, e.name AS name,
                   [x IN others WHERE x IS NOT NULL AND x <> '' AND NOT x IN mine]
                   AS theirs
        """, k=_HYNIX)]
        rows = [r for r in rows if r["theirs"]]

    assert rows, "남의 근거를 가진 공유 사건이 0건이면 이 회귀를 검증할 수 없다"

    by_id = {e["event_id"]: e for e in company_service.events_of(_HYNIX)}
    for row in rows:
        got = set(by_id[row["eid"]]["evidence_ids"])
        assert got.isdisjoint(row["theirs"]), (
            f"{row['name']}: 남의 근거 {got & set(row['theirs'])} 가 섞였다")
