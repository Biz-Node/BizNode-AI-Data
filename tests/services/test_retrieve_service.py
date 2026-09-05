"""RetrieveService — 챗봇 재료 조립.

Tier A는 협력자(Orchestrator·company_service·relation_service)를 통제해 "무엇을
어떤 순서로 부르고 무엇을 합치는가"라는 조립 계약만 본다. 실제 DB 데이터에 기대면
사건·관계 건수가 재적재마다 달라져 재현이 안 된다.

Tier B는 실 Docker PostgreSQL/Neo4j/ChromaDB로 한 바퀴를 돈다(mock 없음).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.api.schemas import AskRequest, MatchType
from app.services import retrieve_service as rs_module
from app.services.retrieve_service import RetrieveService
from search.dto.search_hit import SearchHit
from search.dto.search_query import SearchQuery
from search.dto.search_result import SearchResult
from pipeline.normalizer.resolver import Resolution
from search.model.enums import EntityType, SearchMode

_TODAY = date(2026, 8, 20)


def _hit(entity_id, name, *, entity_type=EntityType.COMPANY, evidence=()):
    return SearchHit(
        entity_type=entity_type, entity_id=entity_id, name=name,
        source_score=0.9, sources=["neo4j"], evidence=list(evidence),
    )


def _resolution(corp_code, corp_name):
    return Resolution(corp_code=corp_code, corp_name=corp_name, stock_code=None,
                      method="exact", score=1.0)


def _anchored(hits, **kw):
    """★히트가 재료가 되는 것은 **② Search 가 실제로 앵커를 잡았을 때**다
    (`hits_reflect_the_anchor`). 아래 조립 계약들은 그 경우를 본다 — 못 잡은
    경우에는 앵커 자신이 재료라 히트 매핑을 볼 수가 없다.

    ★전에는 이 전제를 안 세워도 됐다. `/retrieve` 가 판정을 무시하고 **늘**
      히트를 썼기 때문이다(2026-09-05 · §6-0 A-6 에서 고쳤다)."""
    return _orchestrator(hits, resolved=[_resolution("00126380", "삼성전자")], **kw)


def _orchestrator(hits, *, mode=SearchMode.RELATIONSHIP, resolved=()):
    orch = MagicMock()
    query = SearchQuery(raw_query="q", normalized_query="q",
                        mode=mode, today=_TODAY, resolved_entities=list(resolved))
    result = SearchResult(query="q", mode=mode, hits=list(hits),
                          total=len(hits), took_ms=1, cache_hit=False,
                          used_semantic_fallback=False)
    orch.search.return_value = (query, result)
    return orch


def _event(event_id, name, *, is_risk=False, evidence_ids=(), event_type="사고재해"):
    return {"event_id": event_id, "name": name, "event_type": event_type,
            "is_risk": is_risk, "role": "subject", "occurred_at": "2026-06-11",
            "article_count": 1, "timeline": [], "evidence_ids": list(evidence_ids)}


def _relation(edge_id, *, evidence_id=None):
    return {"edge_id": edge_id, "evidence_id": evidence_id, "type": "SUPPLIES_TO",
            "subtype": None,
            "source": {"key": "00301246", "name": "SFA반도체", "label": "Company"},
            "target": {"key": "00126380", "name": "삼성전자", "label": "Company"},
            "symmetric": False, "amount": None, "ratio": None,
            "freshness": "current", "last_seen": "2026-03-23", "valid_from": None,
            "valid_until": None, "score": 0.9, "corroboration": 1,
            "source_type": "dart", "refresh_cycle_days": 365, "days_since": 10,
            "days_until_refresh": 355, "exclusive": None, "other_counterparties": []}


@pytest.fixture
def stub_services(monkeypatch):
    """company_service·relation_service를 통제한다. 반환값은 테스트가 정한다."""
    company = MagicMock()
    company.events_of.return_value = []
    company.relations_of.return_value = []
    relation = MagicMock()
    relation.evidence_for_ids.return_value = []
    relation.event_impact.return_value = []
    monkeypatch.setattr(rs_module, "company_service", company)
    monkeypatch.setattr(rs_module, "relation_service", relation)
    # ★임베더를 끈다 — Tier A 는 조립 계약만 본다. 켜 두면 단위 테스트가
    #   OpenAI 를 부르고, 유사도 때문에 순서가 흔들려 재현이 안 된다.
    monkeypatch.setattr(rs_module, "default_embed", None)
    return company, relation


# ── 검색 호출 계약 ──────────────────────────────────────────────────────

def test_question_becomes_the_search_query(stub_services):
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(AskRequest(question="삼성전자에 납품하는 기업"))

    sent = orch.search.call_args.args[0]
    assert sent.query == "삼성전자에 납품하는 기업"


def test_workspace_keys_reach_the_search_layer(stub_services):
    """★후필터가 아니다 — 범위를 검색 단계로 내려보낸다(설계서 §7)."""
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(
        AskRequest(question="q", workspace_keys=["00126380", "00164779"]))

    assert orch.search.call_args.args[0].workspace_keys == ["00126380", "00164779"]


def test_evidence_is_always_requested(stub_services):
    """인용이 목적이라 끄지 않는다."""
    orch = _orchestrator([])
    RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert orch.search.call_args.args[0].include_evidence is True


def test_question_is_echoed_back(stub_services):
    got = RetrieveService(_orchestrator([])).retrieve(AskRequest(question="무슨 일?"))
    assert got.question == "무슨 일?"


# ── Company 만 추린다 (설계서 §9) ───────────────────────────────────────

def test_only_company_hits_become_companies(stub_services):
    orch = _anchored([
        _hit("00126380", "삼성전자"),
        _hit("김준성|1967-10", "김준성", entity_type=EntityType.PERSON),
        _hit("evt_abc", "삼성전자 압수수색", entity_type=EntityType.EVENT),
    ])
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [c.key for c in got.companies] == ["00126380"]


def test_non_company_hits_are_not_sent_to_company_lookups(stub_services):
    """★Person·Event를 기업 조회에 넣으면 **조용히 빈 결과**가 되어
    「사건이 없다」로 잘못 읽힌다."""
    company, _ = stub_services
    orch = _orchestrator([_hit("김준성|1967-10", "김준성", entity_type=EntityType.PERSON)])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    company.events_of.assert_not_called()
    company.relations_of.assert_not_called()


def test_duplicate_company_hits_are_collapsed(stub_services):
    orch = _anchored([_hit("00126380", "삼성전자"), _hit("00126380", "삼성전자")])
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert len(got.companies) == 1


def test_companies_are_capped_but_reported(stub_services, caplog):
    orch = _anchored([_hit(f"c{i}", f"기업{i}") for i in range(9)])
    with caplog.at_level("INFO"):
        got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.companies) == rs_module._MAX_COMPANIES
    assert "truncated" in caplog.text, "조용히 자르면 「그게 전부」로 읽힌다"


# ── 사건 ────────────────────────────────────────────────────────────────

def test_events_are_collected_per_company(stub_services):
    company, _ = stub_services
    company.events_of.return_value = [_event("evt_1", "화재")]
    orch = _anchored([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    company.events_of.assert_called_once_with("00126380")
    assert [e.event_id for e in got.events] == ["evt_1"]


def test_same_event_from_two_companies_appears_once(stub_services):
    """★엣지가 아니라 **Event 노드 기준**으로 묶는다 — 같은 사건에 여러 기업이
    엮여 있으면 그대로 쌓을 경우 같은 사건을 두 번 말하게 된다."""
    company, _ = stub_services
    company.events_of.return_value = [_event("evt_1", "화재")]
    orch = _anchored([_hit("00126380", "삼성전자"), _hit("00164779", "SK하이닉스")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.events) == 1


# ── 사건 selection (Step2, 2026-08-23) ──────────────────────────────────

def test_events_are_capped_per_company_and_reported(stub_services, caplog):
    """★사건에 상한이 없었다 — 실측으로 근거 205건·34,430자가 프롬프트에 실렸다.
    자르되 **조용히 자르지 않는다.**"""
    company, _ = stub_services
    company.events_of.return_value = [
        _event(f"evt_{i}", f"사건{i}") for i in range(rs_module.MAX_EVENTS_PER_COMPANY + 4)]
    orch = _anchored([_hit("00126380", "삼성전자")])

    with caplog.at_level("INFO"):
        got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.events) == rs_module.MAX_EVENTS_PER_COMPANY
    assert "events truncated" in caplog.text


def test_each_company_gets_its_own_quota(stub_services):
    """★기업별로 **독립** selection 한다 — 한 기업이 상한을 다 먹으면 다른
    기업의 사건이 통째로 사라진다. 그건 「다른 기업이라서 버린 것」이 된다."""
    company, _ = stub_services
    n = rs_module.MAX_EVENTS_PER_COMPANY
    company.events_of.side_effect = lambda key: (
        [_event(f"a{i}", f"A사건{i}") for i in range(n + 5)] if key == "A"
        else [_event("b1", "B사건")])
    orch = _anchored([_hit("A", "A기업"), _hit("B", "B기업")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    ids = {e.event_id for e in got.events}
    assert "b1" in ids, "두 번째 기업의 사건이 첫 기업에 밀려 사라졌다"


def test_rule_matched_event_types_are_kept_over_others(stub_services):
    """규칙 신호가 우선 — 「노조」를 물으면 노무 사건이 상한 안에 들어온다."""
    company, _ = stub_services
    company.events_of.return_value = (
        [_event(f"x{i}", f"확장{i}", event_type="사업확장")
         for i in range(rs_module.MAX_EVENTS_PER_COMPANY)]
        + [_event("labour", "노조 설립", event_type="노무")])
    # ★앵커를 명시한다 — 이 테스트가 보는 것은 **기업별 경로**의 규칙 티어다.
    #   앵커가 없으면 전역 사건 검색으로 빠진다(2026-09-02).
    orch = _orchestrator([_hit("00126380", "삼성전자")],
                         resolved=[_resolution("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="삼성전자 노조 관련 리스크"))

    assert "labour" in {e.event_id for e in got.events}


def test_shared_event_keeps_evidence_from_every_in_scope_company(stub_services):
    """★공유 사건은 event_id 로 한 번만 나가는데(기존 계약), 그때 **먼저 온
    기업의 근거만** 남아 나머지가 조용히 사라졌다. 둘 다 질문이 부른 기업이면
    둘 다 근거다 — 실측으로 「담합 소송」 질의에서 3건을 잃고 있었다."""
    company, relation = stub_services
    company.events_of.side_effect = lambda key: [
        _event("shared", "담합 혐의 피소",
               evidence_ids=["ev_samsung"] if key == "00126380" else ["ev_hynix"])]
    # ★앵커를 명시한다 — 공유 사건 병합은 **기업별 경로**의 규칙이다.
    orch = _orchestrator([_hit("00126380", "삼성전자"), _hit("00164779", "SK하이닉스")],
                         resolved=[_resolution("00126380", "삼성전자"),
                                   _resolution("00164779", "SK하이닉스")])

    got = RetrieveService(orch).retrieve(
        AskRequest(question="삼성전자와 SK하이닉스의 담합 소송"))

    assert len(got.events) == 1, "공유 사건은 여전히 한 번만 나가야 한다"
    assert set(got.events[0].evidence_ids) == {"ev_samsung", "ev_hynix"}


def test_out_of_scope_company_evidence_is_still_not_merged(stub_services):
    """★Step1 회귀 — in-scope 기업만 합친다. 검색에 안 걸린 기업 근거는 안 온다."""
    company, _ = stub_services
    company.events_of.side_effect = lambda key: [
        _event("shared", "노조 설립", evidence_ids=["ev_samsung"])]
    orch = _orchestrator([_hit("00126380", "삼성전자")],
                         resolved=[_resolution("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="삼성전자 노조"))

    assert got.events[0].evidence_ids == ["ev_samsung"]


def test_anchor_name_is_stripped_using_resolved_entities(stub_services):
    """★앵커명을 질문·라벨 양쪽에서 지워야 유사도가 맞는다(실험 ③).
    앵커는 SearchQuery.resolved_entities 에 이미 있다 — 새로 추출하지 않는다."""
    company, _ = stub_services
    company.events_of.return_value = [_event("e1", "SK하이닉스 압수수색",
                                             event_type="규제수사")]
    seen: list[str] = []

    def recording_embed(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    orch = _orchestrator([_hit("00164779", "SK하이닉스")],
                         resolved=[_resolution("00164779", "SK하이닉스")])

    RetrieveService(orch, embed=recording_embed).retrieve(
        AskRequest(question="SK하이닉스 압수수색"))

    assert seen, "임베더가 호출돼야 한다"
    assert not any("SK하이닉스" in t for t in seen), seen


def test_answer_material_survives_when_the_embedder_dies(stub_services):
    """★OpenAI 가 죽어도 /ask 는 죽지 않는다 — 규칙 티어로 폴백한다."""
    company, _ = stub_services
    company.events_of.return_value = [_event("e1", "노조 설립", event_type="노무")]

    def broken_embed(texts):
        raise RuntimeError("openai down")

    orch = _orchestrator([_hit("00164779", "SK하이닉스")],
                         resolved=[_resolution("00164779", "SK하이닉스")])
    got = RetrieveService(orch, embed=broken_embed).retrieve(
        AskRequest(question="SK하이닉스 노조 리스크"))

    assert [e.event_id for e in got.events] == ["e1"]


# ── 파급 ────────────────────────────────────────────────────────────────

def test_propagation_runs_only_for_risk_events(stub_services):
    company, relation = stub_services
    company.events_of.return_value = [
        _event("evt_risk", "화재", is_risk=True), _event("evt_plain", "신제품")]
    orch = _anchored([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.event_impact.assert_called_once_with("evt_risk")


def test_propagation_is_not_called_before_events_exist(stub_services):
    company, relation = stub_services
    company.events_of.return_value = []
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.event_impact.assert_not_called()


def test_missing_event_node_is_logged_not_silently_dropped(stub_services, caplog):
    company, relation = stub_services
    company.events_of.return_value = [_event("evt_risk", "화재", is_risk=True)]
    relation.event_impact.return_value = None
    orch = _anchored([_hit("00126380", "삼성전자")])

    with caplog.at_level("WARNING"):
        got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert got.propagation == []
    assert "event_impact miss" in caplog.text


# ── 관계 ────────────────────────────────────────────────────────────────

def test_relations_keep_edge_id(stub_services):
    """★`edge_id`가 관계를 가리키는 유일한 id다. `evidence_id`로 대신할 수 없다."""
    company, _ = stub_services
    company.relations_of.return_value = [_relation("5:abc:1")]
    orch = _anchored([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [r.edge_id for r in got.relations] == ["5:abc:1"]


def test_same_edge_from_two_companies_appears_once(stub_services):
    """관계의 양끝이 둘 다 결과에 있으면 같은 엣지를 두 번 읽게 된다."""
    company, _ = stub_services
    company.relations_of.return_value = [_relation("5:abc:1")]
    orch = _anchored([_hit("00301246", "SFA반도체"), _hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert len(got.relations) == 1


# ── 근거 ────────────────────────────────────────────────────────────────

def test_evidence_ids_are_fetched_in_one_batch(stub_services):
    """★관계마다 부르면 근거·언론사·공시제목 조회가 관계 수만큼 반복된다(§14·§26)."""
    company, relation = stub_services
    company.relations_of.return_value = [
        _relation("5:a:1", evidence_id="ev_1"), _relation("5:a:2", evidence_id="ev_2")]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    relation.evidence_for_ids.assert_called_once()


def test_evidence_ids_come_from_relations_events_and_hits(stub_services):
    """출처가 셋이다 — 어느 하나만 보면 인용할 문장이 줄어든다."""
    company, relation = stub_services
    company.relations_of.return_value = [_relation("5:a:1", evidence_id="ev_rel")]
    company.events_of.return_value = [_event("evt_1", "화재", evidence_ids=["ev_evt"])]
    orch = _anchored([_hit("00126380", "삼성전자",
                               evidence=[{"evidence_id": "ev_hit"}])])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    sent = relation.evidence_for_ids.call_args.args[0]
    assert {"ev_rel", "ev_evt", "ev_hit"} <= set(sent)


def test_duplicate_evidence_ids_are_collapsed(stub_services):
    """한 근거가 여러 관계를 뒷받침한다(엣지 11,060 : 근거 9,228)."""
    company, relation = stub_services
    company.relations_of.return_value = [
        _relation("5:a:1", evidence_id="ev_same"), _relation("5:a:2", evidence_id="ev_same")]
    orch = _anchored([_hit("00126380", "삼성전자")])

    RetrieveService(orch).retrieve(AskRequest(question="q"))

    sent = relation.evidence_for_ids.call_args.args[0]
    assert len([i for i in sent if i == "ev_same"]) >= 1
    # 배치 함수가 중복을 제거한다 — 여기서 중복이 넘어가도 결과는 1건이다
    assert relation.evidence_for_ids.call_count == 1


def test_missing_evidence_is_kept_not_hidden(stub_services):
    """★못 꺼낸 근거를 **조용히 빼지 않는다.** 빼면 「근거가 없는 관계」로 읽힌다.
    다만 인용 가능한 source 로 쓰면 안 된다 — 그 판정은 추론 계층 몫이다."""
    company, relation = stub_services
    relation.evidence_for_ids.return_value = [
        {"evidence_id": "ev_gone", "text": "", "source_doc": "",
         "source_type": "news", "press": None, "published_at": None, "missing": True}]
    orch = _orchestrator([_hit("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="q"))

    assert [e.evidence_id for e in got.evidence] == ["ev_gone"]
    assert got.evidence[0].missing is True


# ── match_type ─────────────────────────────────────────────────────────

def test_match_type_is_exact_for_relationship_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.RELATIONSHIP)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_exact_for_name_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.NAME)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_semantic_for_semantic_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.SEMANTIC)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.SEMANTIC


# ══════════════════════════════════════════════════════════════════════
#  Tier B — 실제 저장소로 한 바퀴 (mock 없음)
# ══════════════════════════════════════════════════════════════════════

def test_real_retrieve_returns_material():
    got = RetrieveService().retrieve(AskRequest(question="삼성전자에 납품하는 기업"))

    assert got.companies
    assert got.relations
    assert all(r.edge_id for r in got.relations)
    assert got.evidence
    assert all(e.evidence_id for e in got.evidence)


def test_real_workspace_does_not_shrink_the_material():
    """★워크스페이스를 줘도 재료가 쪼그라들지 않는다(2026-08-20 정책 변경).

    워크스페이스는 순서를 정하는 문맥이지 필터가 아니다. 한때 양끝 모두를
    요구하는 hard filter 였을 때는 같은 질문이 재료를 크게 잃었다.
    """
    question = "삼성전자에 납품하는 기업"
    wide = RetrieveService().retrieve(AskRequest(question=question))
    scoped = RetrieveService().retrieve(
        AskRequest(question=question, workspace_keys=["00126380", "00301246"]))

    assert len(scoped.companies) == len(wide.companies)
    assert scoped.relations


def test_real_shared_event_does_not_leak_other_companies_evidence():
    """★회귀 — 「SK하이닉스」 재료에 남의 회사 근거가 섞이지 않는다(2026-08-23).

    실제 사고: 「SK하이닉스」 질의의 /ask sources 에 현대오토에버 노조
    기사(ev_14df4ce056904b8b)가 들어갔다. Event '노조 설립' 을 현대오토에버·
    SK하이닉스·신세계아이앤씨가 공유하는데, `company_service.events_of()` 가
    Event **노드**의 `evidence_ids`(전 기업 합집합)를 돌려줬기 때문이다.
    기업별 근거는 `HAS_EVENT` **엣지**에 있다.

    금지 집합을 「남의 엣지 근거 − SK하이닉스가 어디서든 든 근거」로 잡는다 —
    한 문장이 두 기업을 함께 다루면 양쪽 엣지에 같은 id 가 달리는데(실측 28건),
    그건 남의 것이 아니라 공동 근거다.
    """
    from app.core.database import neo4j_session

    key = "00164779"  # SK하이닉스
    with neo4j_session() as s:
        row = s.run("""
            MATCH (c:Company)-[:HAS_EVENT]->(e:Event)<-[oh:HAS_EVENT]-(o:Company)
            WHERE (c.corp_code = $k OR c.norm_name = $k) AND o <> c
            WITH collect(DISTINCT oh.evidence_id) AS theirs
            MATCH (c2:Company)-[r]-() WHERE c2.corp_code = $k OR c2.norm_name = $k
            WITH theirs, collect(DISTINCT r.evidence_id) AS mine
            RETURN [x IN theirs WHERE x IS NOT NULL AND x <> '' AND NOT x IN mine]
                   AS forbidden
        """, k=key).single()
    forbidden = set(row["forbidden"])
    assert forbidden, "남의 근거 후보가 0건이면 이 회귀를 검증할 수 없다"

    got = RetrieveService().retrieve(AskRequest(question="SK하이닉스"))

    leaked = {e.evidence_id for e in got.evidence} & forbidden
    assert not leaked, f"남의 회사 근거가 /ask 재료에 섞였다: {sorted(leaked)}"


# ══════════════════════════════════════════════════════════════════
#  전역 사건 검색 — 앵커 없는 질문 (2026-09-02)
# ══════════════════════════════════════════════════════════════════
#
# 최종 설계 §5 시나리오 3 · §17-2. 세 번 옮겨 온 자리다 — `workspace` 시절에는
# 담아 둔 기업이 재료였고, §17-3 이 그걸 폐기한 뒤로는 **검색 히트**가 재료였다.
# 그런데 앵커 없는 질의의 히트는 관계 freshness 순이라 기업이 사실상 임의로
# 정해졌고(F1), 히트에 실려 오던 Event 노드는 `companies_from()` 이 통째로
# 버렸다(F4). 지금은 **전역 사건을 먼저 고르고 기업을 역산한다**(설계 Q3).

def _global_row(event_id, name, ckey, cname, *, event_type="사업확장", is_risk=False):
    """`company_service.global_events()` 가 주는 행 — (기업, 사건) 한 쌍."""
    row = _event(event_id, name, event_type=event_type)
    row["is_risk"] = is_risk
    row["company"] = {"key": ckey, "name": cname}
    return row


def test_anchorless_material_comes_from_global_events_not_from_the_hits(stub_services):
    """★히트에만 있는 기업은 재료가 아니다 — **사건이 기업을 정한다.**"""
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row("g1", "EUV 장비 투자", "00164779", "SK하이닉스")]
    orch = _orchestrator([_hit("00999999", "아무기업")])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 주요 투자 이벤트가 뭐야?"))

    assert [e.event_id for e in got.events] == ["g1"]
    assert [c.key for c in got.companies] == ["00164779"]
    company.events_of.assert_not_called()


def test_the_event_says_which_company_it_happened_to(stub_services):
    """★앵커가 없으면 사건마다 기업이 다르다 — 안 실으면 「누구에게 난 일인지
    모르는 사건」이 재료로 나가고, 그건 곧 엉뚱한 기업에 사건을 붙이는 일이다."""
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row("g1", "인산 노출 사고", "00164779", "SK하이닉스"),
        _global_row("g2", "울산공장 화재", "00126380", "삼성전자")]
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 사고 뭐가 있었어?"))

    assert [(e.company.key, e.company.name) for e in got.events] == [
        ("00164779", "SK하이닉스"), ("00126380", "삼성전자")]


def test_the_same_event_at_two_companies_is_not_deduped_when_anchorless(stub_services):
    """★`event_id` 로 **접지 않는다**(설계 Q2). 사건 하나에 기업이 둘 이상 붙은
    것이 5.7%(실측)이고, 그때 `role`·`occurred_at`·`evidence_ids` 가 기업마다
    다르다. 앵커 없는 질문에서는 **누구에게 난 일인가**가 답의 일부다.

    ★앵커 경로는 반대다 — 거기서는 기업이 이미 정해져 있어 같은 사건을 여러 번
      말하는 것이 중복이다(`test_shared_event_keeps_evidence_from_every_in_scope_company`).
    """
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row("shared", "담합 혐의 피소", "00126380", "삼성전자"),
        _global_row("shared", "담합 혐의 피소", "00164779", "SK하이닉스")]
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 담합 소송"))

    assert len(got.events) == 2, "앵커가 없으면 같은 사건도 기업마다 한 번씩이다"
    assert {c.key for c in got.companies} == {"00126380", "00164779"}


def test_the_derived_companies_are_not_truncated(stub_services):
    """★`_MAX_COMPANIES` 를 여기 걸면 안 된다.

    이 목록이 곧 Agent 의 `scope.allowed` 라, 5 로 자르면 나머지 사건의 기업이
    범위 밖이 되어 도구가 `OutOfScopeKey` 로 거부한다. 실측(2026-09-02): 상위
    10건이 **6~10개 기업**에 걸쳐 질의 5건 전부가 5를 넘었다.

    상한은 이미 **사건 쪽**에 있다(`_MAX_GLOBAL_EVENTS`) — 기업 수는 사건 수를
    넘지 못한다.
    """
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row(f"g{i}", f"사건{i}", f"0000000{i}", f"기업{i}")
        for i in range(rs_module._MAX_GLOBAL_EVENTS)]
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 주요 사건"))

    assert len(got.companies) > rs_module._MAX_COMPANIES
    assert len(got.companies) == len(got.events)


def test_global_events_are_capped_as_one_list_not_per_company(stub_services):
    """★상한을 **기업마다** 걸면 안 된다 — 후보가 933행 · 234기업이라 기업당
    10 이면 상한이 사실상 없는 것과 같다."""
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row(f"g{i}", f"사건{i}", f"0000000{i % 3}", f"기업{i % 3}")
        for i in range(rs_module._MAX_GLOBAL_EVENTS * 4)]
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 주요 사건"))

    assert len(got.events) == rs_module._MAX_GLOBAL_EVENTS


def test_the_rule_tier_still_decides_the_order_without_an_anchor(stub_services):
    """★랭킹을 **새로 만들지 않는다.** `evidence_selector.select()` 를 그대로
    부르므로 규칙 티어가 앵커 경로에서와 똑같이 먼저 온다."""
    company, _ = stub_services
    company.global_events.return_value = (
        [_global_row(f"x{i}", f"확장{i}", f"0000000{i}", f"기업{i}")
         for i in range(rs_module._MAX_GLOBAL_EVENTS)]
        + [_global_row("labour", "노조 설립", "00164779", "SK하이닉스",
                       event_type="노무")])
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 노조 이슈"))

    assert "labour" in {e.event_id for e in got.events}


def test_no_anchor_means_it_is_not_an_exact_match(stub_services):
    """★F1 — 앵커를 하나도 못 잡았는데 `EXACT` 로 나가고 있었다.

    추론 계층이 이 값에서 읽는 것은 「그래프에서 정확히 찾았나, 의미 유사도로
    찾았나」뿐이다(§11). 앵커 없는 전역 사건 검색은 규칙 티어와 임베딩 유사도가
    순위를 만드니 `SEMANTIC` 쪽이고, 「같은 무게로 말하지 않는다」가 걸려야 한다.
    """
    company, _ = stub_services
    company.global_events.return_value = []
    orch = _orchestrator([], mode=SearchMode.RELATIONSHIP)

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 주요 투자 이벤트가 뭐야?"))

    assert got.match_type is MatchType.SEMANTIC


def test_an_anchored_question_is_still_exact(stub_services):
    """대조군 — 앵커가 있으면 예전 그대로다."""
    orch = _orchestrator([_hit("00126380", "삼성전자")],
                         resolved=[_resolution("00126380", "삼성전자")])

    got = RetrieveService(orch).retrieve(AskRequest(question="삼성전자 최근 리스크"))

    assert got.match_type is MatchType.EXACT
    assert all(e.company is None for e in got.events), \
        "앵커가 있으면 재료 기업이 하나뿐이라 사건에 기업을 달지 않는다"


def test_relations_do_not_grow_with_the_derived_companies(stub_services):
    """★기업 수에 **천장을 씌운다.**

    앵커 없는 경로에서 `companies` 가 5곳 → 최대 10곳이 된 것은 도구 범위와
    사건을 위한 변경이지 **관계를 늘리려던 것이 아니다.** 씌우지 않았더니 관계
    90~100건 · 재료 94,921자로 앵커 경로(48,719자)의 두 배가 됐다(실측).
    """
    company, _ = stub_services
    company.global_events.return_value = [
        _global_row(f"g{i}", f"사건{i}", f"0000000{i}", f"기업{i}")
        for i in range(rs_module._MAX_GLOBAL_EVENTS)]
    company.relations_of.side_effect = lambda key: [
        _relation(f"{key}-{i}") for i in range(rs_module.MAX_RELATIONS_PER_COMPANY)]
    orch = _orchestrator([])

    got = RetrieveService(orch).retrieve(AskRequest(question="최근 주요 사건"))

    ceiling = rs_module.MAX_RELATIONS_PER_COMPANY * rs_module._MAX_COMPANIES
    assert len(got.relations) <= ceiling
