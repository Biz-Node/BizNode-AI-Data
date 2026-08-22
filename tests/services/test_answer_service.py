from __future__ import annotations

from app.api.schemas import AskResponse, Evidence, Relation, RelationEndpoint, Source
from app.api.schemas import RetrieveResponse
from app.services import answer_service as as_module


def test_source_defaults():
    s = Source(evidence_id="ev_1", text="t", source_doc="doc", source_type="news")
    assert s.edge_id is None
    assert s.published_at is None


def test_ask_response_defaults():
    r = AskResponse(answer="답")
    assert r.sources == []
    assert r.failed is False


def _evidence(eid, *, missing=False, text="원문"):
    return Evidence(evidence_id=eid, text=text, source_doc="doc",
                    source_type="news", missing=missing)


def _relation(edge_id, evidence_id, *, freshness="current"):
    return Relation(
        edge_id=edge_id, evidence_id=evidence_id, type="SUPPLIES_TO",
        source=RelationEndpoint(key="00126380", name="삼성전자"),
        target=RelationEndpoint(key="00301246", name="SFA반도체"),
        freshness=freshness)


def _retrieved(*, evidence=(), relations=()):
    return RetrieveResponse(question="q", evidence=list(evidence), relations=list(relations))


def test_edge_id_for_matches_relation_by_evidence_id():
    relations = [_relation("5:a:1", "ev_a")]
    assert as_module._edge_id_for("ev_a", relations) == "5:a:1"


def test_edge_id_for_returns_none_when_no_relation_matches():
    assert as_module._edge_id_for("ev_ghost", []) is None


def test_sources_from_keeps_only_whitelisted_ids():
    retrieved = _retrieved(evidence=[_evidence("ev_a"), _evidence("ev_b", missing=True)])

    got = as_module._sources_from(["ev_a", "ev_b", "ev_ghost"], retrieved)

    assert [s.evidence_id for s in got] == ["ev_a"]


def test_sources_from_attaches_edge_id_when_available():
    retrieved = _retrieved(evidence=[_evidence("ev_a")],
                           relations=[_relation("5:a:1", "ev_a")])

    got = as_module._sources_from(["ev_a"], retrieved)

    assert got[0].edge_id == "5:a:1"


def test_fallback_sources_excludes_missing_but_applies_no_other_filter():
    retrieved = _retrieved(evidence=[_evidence("ev_a"), _evidence("ev_b", missing=True),
                                     _evidence("ev_c")])

    got = as_module._fallback_sources(retrieved)

    assert [s.evidence_id for s in got] == ["ev_a", "ev_c"]
