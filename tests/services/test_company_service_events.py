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


# ══════════════════════════════════════════════════════════════════════
#  전역 사건 후보 — 앵커 없는 질문 (2026-09-02)
# ══════════════════════════════════════════════════════════════════════
#
# ★`events_of()`(기업 기준)와 `global_events()`(전역)는 **행 조립부를 공유한다**
#   (`_event_row`). 사본을 두면 두 경로가 같은 사건을 다른 모양으로 내는데, 그
#   차이는 DTO 가 `extra="ignore"` 라 **예외가 아니라 조용한 결측**으로 나온다.

def _global_row(ckey="00164779", cname="SK하이닉스", **over):
    row = {"e": {"event_id": "evt_1", "name": "노조 설립", "event_type": "노무",
                 "is_risk": True, "article_count": 3, "timeline": [],
                 "evidence_ids": ["ev_node"]},
           "h": {"role": "subject", "occurred_at": "2026-06-11",
                 "evidence_id": "ev_edge"},
           "sign": "negative", "ckey": ckey, "cname": cname}
    row.update(over)
    return row


def test_the_two_paths_build_the_same_row_shape(monkeypatch):
    """★`company` 말고는 글자까지 같아야 한다 — 조립부가 한 곳이라는 증거."""
    # 같은 입력이어야 대조가 성립한다 — `_row` 는 `sign` 을 안 싣는 헬퍼다.
    same = dict(_row(node_evidence_ids=["ev_node"],
                     edge_props={"role": "subject", "occurred_at": "2026-06-11",
                                 "evidence_id": "ev_edge"}),
                sign="negative")
    _stub_rows(monkeypatch, [same])
    per_company = company_service.events_of(_HYNIX)[0]

    _stub_rows(monkeypatch, [_global_row()])
    global_ = company_service.global_events()[0]

    assert set(per_company) == set(global_)
    assert {k: v for k, v in per_company.items() if k != "company"} == \
           {k: v for k, v in global_.items() if k != "company"}


def test_the_per_company_path_leaves_the_company_empty(monkeypatch):
    """★기업 기준 조회는 부르는 쪽이 이미 어느 기업인지 안다 — 채우면 군더더기다."""
    _stub_rows(monkeypatch, [_row(node_evidence_ids=[],
                                  edge_props={"evidence_id": "ev_edge"})])

    assert company_service.events_of(_HYNIX)[0]["company"] is None


def test_the_global_path_says_who_it_happened_to(monkeypatch):
    """★전역은 행마다 기업이 달라 **행이 스스로 말해야 한다.**"""
    _stub_rows(monkeypatch, [_global_row()])

    got = company_service.global_events()[0]

    assert got["company"] == {"key": "00164779", "name": "SK하이닉스"}
    # 근거는 여전히 **이 기업의 엣지 것**이다 — 노드 합집합이 아니다.
    assert got["evidence_ids"] == ["ev_edge"]


def test_a_company_without_a_key_is_dropped(monkeypatch):
    """★key 없는 기업을 실으면 그 사건은 범위 설정에서 빠져 도구가 거부한다."""
    _stub_rows(monkeypatch, [_global_row(ckey=None)])

    assert company_service.global_events() == []


def test_pairs_come_back_in_the_order_they_were_asked_for(monkeypatch):
    """★순서가 곧 순위다 — 서버가 고른 차례를 도구가 흐트러뜨리면 안 된다.

    Cypher 의 `UNWIND` 는 순서를 보장하지 않으므로 파이썬에서 되맞춘다.
    """
    _stub_rows(monkeypatch, [
        _global_row(ckey="B", cname="비", e={"event_id": "e2", "name": "b",
                                             "event_type": "기타", "is_risk": False,
                                             "article_count": 1, "timeline": [],
                                             "evidence_ids": []}),
        _global_row(ckey="A", cname="에이", e={"event_id": "e1", "name": "a",
                                              "event_type": "기타", "is_risk": False,
                                              "article_count": 1, "timeline": [],
                                              "evidence_ids": []}),
    ])

    got = company_service.events_by_pairs([("e1", "A"), ("e2", "B")])

    assert [r["event_id"] for r in got] == ["e1", "e2"]


def test_a_pair_that_no_longer_exists_is_skipped_not_raised(monkeypatch):
    """★그래프가 그 사이 바뀌었을 수 있다. 없는 것과 터지는 것은 다르다."""
    _stub_rows(monkeypatch, [])

    assert company_service.events_by_pairs([("gone", "A")]) == []


@pytest.mark.needs_db
def test_real_global_candidates_exclude_the_eventness_suspects():
    """★표시가 있는데 재료로 쓰면 표시를 한 이유가 없어진다(ERD)."""
    rows = company_service.global_events()

    assert rows, "전역 후보가 0건이면 앵커 없는 질문에 답할 재료가 없다"
    assert not [r for r in rows if r["eventness_suspect"]]
    assert all(r["company"] and r["company"]["key"] for r in rows)


@pytest.mark.needs_db
def test_real_pairs_round_trip_through_the_graph():
    """★`global_events()` 가 준 쌍은 `events_by_pairs()` 로 **그대로** 돌아온다.
    이 왕복이 깨지면 `/ask` 의 도구가 서버가 고른 사건을 못 집어 온다."""
    rows = company_service.global_events()[:5]
    pairs = [(r["event_id"], r["company"]["key"]) for r in rows]

    back = company_service.events_by_pairs(pairs)

    assert [(r["event_id"], r["company"]["key"]) for r in back] == pairs
