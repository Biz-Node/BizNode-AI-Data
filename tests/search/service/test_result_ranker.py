"""ResultRanker 테스트.

순수 로직(RRF 계산, 소스 내부 dedup)은 in-memory SearchHit으로 검증한다.
GraphSearcher는 anchor 없는 조회에서 source/target 슬롯을 따로 채우기 때문에
"소스가 반환한 리스트 = score 내림차순"을 보장하지 않고(Task5 실측), 같은
entity_id가 두 슬롯에 중복으로 나올 수 있다(Task5 §8, 의도적으로 dedup 안 하고
ResultRanker로 넘겨둔 것) — 이 두 가지를 각각 회귀 테스트로 재현한다(Task7 지침 §3·§5).

§6-6 "삼성전자에 납품하는 기업"(GraphSearcher·VectorSearcher 양쪽 다 결과가 나오는
케이스) 재현은 그대로 실제 Docker Neo4j/ChromaDB 대상(mock 없음).
"""

from __future__ import annotations

import pytest

from search.dto.search_hit import SearchHit, SearchRelation
from search.model.enums import EntityType
from search.service.result_ranker import _RRF_K, ResultRanker



def _relation(edge_type: str = "SUPPLIES_TO") -> SearchRelation:
    """SearchRelation은 edge_id를 포함해 필드가 전부 필수다(설계서 Rule 7) —
    테스트가 부분 dict를 넘길 수 없으므로 한 곳에서 만든다."""
    return SearchRelation(
        edge_id="5:abc:0", edge_type=edge_type,
        source="세메스", source_id="00164742", source_entity_type="Company",
        target="삼성전자", target_id="00126380", target_entity_type="Company",
    )

def _hit(entity_id, *, score, sources=("neo4j",), entity_type=EntityType.COMPANY, name=None, **overrides):
    return SearchHit(
        entity_type=entity_type,
        entity_id=entity_id,
        name=name or entity_id,
        source_score=score,
        sources=list(sources),
        **overrides,
    )


def test_rrf_k_is_60():
    """RRF 원 논문·TREC 관행값. 실측 튜닝값이 아니다(Task7 지침 §4)."""
    assert _RRF_K == 60


def test_empty_inputs_returns_empty_list():
    assert ResultRanker().rank([], []) == []


# ── 소스 내부 dedup(Task7 지침 §5, GraphSearcher anchorless 중복 재현) ──────

def test_source_internal_duplicate_only_contributes_once_to_rrf():
    """같은 entity_id가 한 소스 리스트 안에 두 번(예: source 슬롯·target 슬롯) 나오면
    dedup 없이 RRF를 그대로 돌릴 때처럼 두 번 기여해선 안 된다 — 순위 높은(점수 높은)
    쪽 한 번만 남기고 나머지는 버린다."""
    dup_high = _hit("dup", score=0.9)
    other = _hit("other", score=0.85)
    dup_low = _hit("dup", score=0.5)  # 같은 entity_id, 다른 슬롯에서 낮은 점수로 재등장

    result = ResultRanker().rank([dup_high, other, dup_low], [])

    dup = next(h for h in result if h.entity_id == "dup")
    assert dup.rrf_score == pytest.approx(1 / (_RRF_K + 1))  # rank=1 한 번만 기여


# ── 정렬 미보장 입력 방어(Task7 지침 §3) ────────────────────────────────────

def test_ranks_by_score_even_when_input_not_pre_sorted():
    """GraphSearcher anchorless 출력(source_hits + target_hits)은 전역 score
    내림차순을 보장하지 않는다 — RRF는 순위 기반이므로 여기서 먼저 정렬해야 한다."""
    low_first = _hit("low", score=0.3)
    high_second = _hit("high", score=0.9)

    result = ResultRanker().rank([low_first, high_second], [])

    assert [h.entity_id for h in result] == ["high", "low"]
    assert result[0].rrf_score == pytest.approx(1 / (_RRF_K + 1))
    assert result[1].rrf_score == pytest.approx(1 / (_RRF_K + 2))


# ── 단일 소스 엔티티 — 필드 보존 ────────────────────────────────────────────

def test_graph_only_entity_keeps_graph_fields_and_single_source():
    graph_hit = _hit("g1", score=0.7, sources=["neo4j"], verdict="supported",
                      freshness={"status": "current"}, relations=[_relation()])

    result = ResultRanker().rank([graph_hit], [])

    assert len(result) == 1
    assert result[0].sources == ["neo4j"]
    assert result[0].verdict == "supported"
    assert result[0].freshness == {"status": "current"}
    assert result[0].relations[0].edge_type == "SUPPLIES_TO"
    assert result[0].rrf_score == pytest.approx(1 / (_RRF_K + 1))


def test_vector_only_entity_keeps_kind_and_single_source():
    vector_hit = _hit("v1", score=0.9, sources=["chroma"], kind="기업")

    result = ResultRanker().rank([], [vector_hit])

    assert len(result) == 1
    assert result[0].sources == ["chroma"]
    assert result[0].kind == "기업"
    assert result[0].rrf_score == pytest.approx(1 / (_RRF_K + 1))


# ── 양쪽 소스에 다 있는 엔티티 — RRF 가산 + 필드 병합 ───────────────────────

def test_entity_in_both_sources_sums_rrf_contributions():
    graph_hit = _hit("shared", score=0.5, sources=["neo4j"])
    vector_hit = _hit("shared", score=0.99, sources=["chroma"])

    result = ResultRanker().rank([graph_hit], [vector_hit])

    assert len(result) == 1
    expected = 1 / (_RRF_K + 1) + 1 / (_RRF_K + 1)
    assert result[0].rrf_score == pytest.approx(expected)
    assert result[0].sources == ["neo4j", "chroma"]


def test_merged_hit_preserves_graph_only_and_vector_only_fields():
    """graph 전용(freshness/verdict/relations), vector 전용(kind) 필드는 재계산하지
    않고 원본 SearchHit에서 그대로 보존한다(Task7 지침 §6)."""
    graph_hit = _hit(
        "shared", score=0.5, sources=["neo4j"], verdict="supported",
        freshness={"status": "current"}, relations=[_relation()],
    )
    vector_hit = _hit("shared", score=0.9, sources=["chroma"], kind="기업")

    result = ResultRanker().rank([graph_hit], [vector_hit])

    hit = result[0]
    assert hit.verdict == "supported"
    assert hit.freshness == {"status": "current"}
    assert hit.relations[0].edge_type == "SUPPLIES_TO"
    assert hit.kind == "기업"


def test_entity_in_both_sources_outranks_single_source_entity_at_similar_position():
    """두 소스에서 동시에 발견된 엔티티는 한쪽에서만 발견된 동일 순위 엔티티보다
    RRF 점수가 높아야 한다(Task7 지침 §6) — 별도 가산 로직 없이 RRF 합산만으로 성립."""
    shared_graph = _hit("shared", score=0.5, sources=["neo4j"])
    shared_vector = _hit("shared", score=0.5, sources=["chroma"])
    solo_graph = _hit("solo", score=0.51, sources=["neo4j"])  # graph 소스 내 1위(=shared보다 근소 우위)

    result = ResultRanker().rank([solo_graph, shared_graph], [shared_vector])

    by_id = {h.entity_id: h for h in result}
    assert by_id["shared"].rrf_score > by_id["solo"].rrf_score


# ── top_k 절삭 ──────────────────────────────────────────────────────────

def test_top_k_truncates_merged_results():
    hits = [_hit(f"e{i}", score=1.0 - i * 0.01) for i in range(5)]

    result = ResultRanker().rank(hits, [], top_k=2)

    assert len(result) == 2


def test_top_k_zero_returns_empty_list():
    hits = [_hit("e1", score=0.9)]

    result = ResultRanker().rank(hits, [], top_k=0)

    assert result == []


def test_top_k_none_returns_all():
    hits = [_hit(f"e{i}", score=1.0 - i * 0.01) for i in range(3)]

    result = ResultRanker().rank(hits, [], top_k=None)

    assert len(result) == 3


# ── §6-6 "삼성전자에 납품하는 기업" — 실제 Docker Neo4j/ChromaDB, mock 없음 ──

def test_supplies_to_query_merges_graph_and_vector_hits(
    entity_resolver, query_router, graph_searcher, vector_searcher, result_ranker,
):
    resolution = entity_resolver.resolve("삼성전자")
    routing = query_router.route("삼성전자에납품하는기업")

    graph_hits = graph_searcher.search([resolution], routing.edge_types, routing.direction, top_k=20)
    vector_hits = vector_searcher.search("삼성전자에 납품하는 기업", top_k=10)

    assert len(graph_hits) > 0
    assert len(vector_hits) > 0

    merged = result_ranker.rank(graph_hits, vector_hits, top_k=20)

    assert len(merged) > 0
    assert len(merged) <= len(graph_hits) + len(vector_hits)
    # score 내림차순 유지
    scores = [hit.rrf_score for hit in merged]
    assert scores == sorted(scores, reverse=True)

    graph_ids = {hit.entity_id for hit in graph_hits}
    vector_ids = {hit.entity_id for hit in vector_hits}
    overlap = graph_ids & vector_ids
    if overlap:
        shared_id = next(iter(overlap))
        shared = next(hit for hit in merged if hit.entity_id == shared_id)
        assert set(shared.sources) == {"neo4j", "chroma"}


# ── 응답 계약 확정(D2, 2026-08-19) — 점수 셋을 이름으로 가른다 ────────────────

def test_rank_sets_rrf_score_and_preserves_source_score():
    """RRF 값과 생산자 원점수는 다른 것이다.

    `rrf_score`는 ResultRanker가 채우는 순위값(1위 ≈ 0.0164)이고, `source_score`는
    GraphSearcher의 Neo4j 관계 점수 / VectorSearcher의 코사인 유사도다. 하나의
    필드로 뭉뚱그리면 프론트가 RRF 값을 신뢰도로 오독한다(현황서 §5-1).
    """
    ranked = ResultRanker().rank([_hit("A", score=0.87)], [])

    assert len(ranked) == 1
    assert ranked[0].rrf_score == pytest.approx(1 / (_RRF_K + 1))
    assert ranked[0].source_score == 0.87


def test_rrf_score_orders_results_when_both_sources_contribute():
    """양쪽 소스에 다 있는 항목이 한쪽에만 있는 항목보다 위에 온다 — 정렬 기준은
    source_score 가 아니라 rrf_score 다."""
    graph = [_hit("A", score=0.1), _hit("B", score=0.9)]
    vector = [_hit("A", score=0.5, sources=("chroma",))]

    ranked = ResultRanker().rank(graph, vector)

    assert [h.entity_id for h in ranked] == ["A", "B"]
    assert ranked[0].rrf_score > ranked[1].rrf_score


# ══════════════════════════════════════════════════════════════════════════
#  워크스페이스 관련도 랭킹 (2026-08-20 정책)
#
#  ★워크스페이스는 **필터가 아니라 랭킹 문맥**이다. 후보를 지우지 않고 순서만
#    정한다. 아래 테스트들은 「지우지 않는다」와 「순서를 정한다」를 각각 못박는다.
# ══════════════════════════════════════════════════════════════════════════

_SAMSUNG, _HYUNDAI, _HYNIX = "00126380", "00164742", "00164779"
_WORKSPACE = [_SAMSUNG, _HYUNDAI]


def _rel(target_id, target_type="Company", *, edge_type="PARTNERS_WITH",
         source_id=_SAMSUNG, source_type="Company"):
    return SearchRelation(
        edge_id=f"5:e:{target_id}", edge_type=edge_type,
        source="삼성전자", source_id=source_id, source_entity_type=source_type,
        target=target_id, target_id=target_id, target_entity_type=target_type,
    )


def _graph_hit(entity_id, *, entity_type=EntityType.COMPANY, score, relation):
    return SearchHit(
        entity_type=entity_type, entity_id=entity_id, name=entity_id,
        source_score=score, sources=["neo4j"], relations=[relation],
    )


def _order(hits, workspace_keys=_WORKSPACE, **kw):
    return [h.entity_id for h in
            ResultRanker().rank(hits, [], workspace_keys=workspace_keys, **kw)]


# ── CASE 1 · 내부 관계가 외부 기업 관계보다 앞선다 ────────────────────────

def test_case1_workspace_internal_relation_outranks_external_company():
    """삼성전자↔현대차(둘 다 워크스페이스)가 삼성전자↔SK하이닉스보다 앞이다.

    ★현대차 쪽 점수를 **일부러 낮게** 뒀다. 순수 RRF 라면 SK하이닉스가 앞선다 —
      워크스페이스 관련도가 점수를 이긴다는 것을 보이기 위해서다.
    """
    hits = [
        _graph_hit(_HYNIX, score=0.99, relation=_rel(_HYNIX)),
        _graph_hit(_HYUNDAI, score=0.10, relation=_rel(_HYUNDAI)),
    ]
    assert _order(hits) == [_HYUNDAI, _HYNIX]


def test_case1_external_company_is_not_removed():
    """★후보에서 지우지 않는다 — 후순위로 **제공**한다."""
    hits = [
        _graph_hit(_HYNIX, score=0.99, relation=_rel(_HYNIX)),
        _graph_hit(_HYUNDAI, score=0.10, relation=_rel(_HYUNDAI)),
    ]
    assert _HYNIX in _order(hits)


# ── CASE 2 · 비-Company 엔티티가 살아남는다 ──────────────────────────────

def test_case2_non_company_entities_survive():
    """워크스페이스에 등록돼 있지 않아도 워크스페이스 기업과 이어져 있으면 남는다.

    ★한때 양끝 모두를 corp_code 로 요구하는 hard filter 였을 때, Event·Person·
      Organization·Product 는 애초에 corp_code 가 없어 **하나도 남지 않았다.**
    """
    hits = [
        _graph_hit("evt_a", entity_type=EntityType.EVENT, score=0.9,
                   relation=_rel("evt_a", "Event", edge_type="HAS_EVENT")),
        _graph_hit("김준성|1967-10", entity_type=EntityType.PERSON, score=0.8,
                   relation=_rel("김준성|1967-10", "Person", edge_type="IS_EXECUTIVE_OF")),
        _graph_hit("금융위원회", entity_type=EntityType.ORGANIZATION, score=0.7,
                   relation=_rel("금융위원회", "Organization", edge_type="REGULATES")),
        _graph_hit("DRAM", entity_type=EntityType.PRODUCT, score=0.6,
                   relation=_rel("DRAM", "Product", edge_type="DEVELOPS")),
    ]
    got = _order(hits, workspace_keys=[_SAMSUNG])

    assert set(got) == {"evt_a", "김준성|1967-10", "금융위원회", "DRAM"}


def test_case2_non_company_ranks_below_external_company():
    """우선순위 셋이 실제로 갈린다 — 내부 > 바깥 기업 > 비-Company."""
    hits = [
        _graph_hit("DRAM", entity_type=EntityType.PRODUCT, score=0.99,
                   relation=_rel("DRAM", "Product", edge_type="DEVELOPS")),
        _graph_hit(_HYNIX, score=0.50, relation=_rel(_HYNIX)),
        _graph_hit(_HYUNDAI, score=0.10, relation=_rel(_HYUNDAI)),
    ]
    assert _order(hits) == [_HYUNDAI, _HYNIX, "DRAM"]


# ── CASE 3 · 바깥 기업이 후보 풀에서 사라지지 않는다 ──────────────────────

def test_case3_external_company_stays_in_the_pool_with_single_key_workspace():
    hits = [_graph_hit(_HYNIX, score=0.9, relation=_rel(_HYNIX))]
    assert _order(hits, workspace_keys=[_SAMSUNG]) == [_HYNIX]


# ── CASE 4 · top_k 가 랭킹 **뒤에** 적용된다 ─────────────────────────────

def test_case4_top_k_is_applied_after_workspace_ranking():
    """★순서가 뒤집히면 「점수순 상위 N 을 고른 뒤 워크스페이스로 거르기」가 되어
    결과가 이유 없이 쪼그라든다(요구사항 §9)."""
    hits = [
        _graph_hit(f"out{i}", score=0.99 - i * 0.01, relation=_rel(f"out{i}"))
        for i in range(5)
    ] + [_graph_hit(_HYUNDAI, score=0.01, relation=_rel(_HYUNDAI))]

    got = _order(hits, top_k=1)

    assert got == [_HYUNDAI], "워크스페이스 내부 관계가 top_k 안에 들어와야 한다"


def test_top_k_still_returns_the_requested_count():
    """워크스페이스 때문에 결과 수가 줄지 않는다."""
    hits = [
        _graph_hit(f"out{i}", score=0.9 - i * 0.01, relation=_rel(f"out{i}"))
        for i in range(5)
    ]
    assert len(_order(hits, top_k=3)) == 3


# ── 워크스페이스를 안 주면 기존 동작 그대로 ──────────────────────────────

def test_no_workspace_keeps_pure_rrf_order():
    hits = [
        _graph_hit(_HYNIX, score=0.99, relation=_rel(_HYNIX)),
        _graph_hit(_HYUNDAI, score=0.10, relation=_rel(_HYUNDAI)),
    ]
    assert _order(hits, workspace_keys=None) == [_HYNIX, _HYUNDAI]


def test_hit_without_relations_uses_its_own_membership():
    """의미검색·이름 해소 히트는 관계가 없다 — 자기가 워크스페이스 안이냐만 본다."""
    inside = SearchHit(entity_type=EntityType.COMPANY, entity_id=_SAMSUNG,
                       name="삼성전자", source_score=0.1, sources=["chroma"])
    outside = SearchHit(entity_type=EntityType.COMPANY, entity_id=_HYNIX,
                        name="SK하이닉스", source_score=0.9, sources=["chroma"])

    got = [h.entity_id for h in
           ResultRanker().rank([], [outside, inside], workspace_keys=_WORKSPACE)]

    assert got == [_SAMSUNG, _HYNIX]


def test_entity_touching_workspace_by_any_relation_takes_its_closest_link():
    """관계를 여럿 든 엔티티는 **가장 가까운 연결**로 평가한다."""
    hit = _graph_hit(_HYNIX, score=0.5, relation=_rel(_HYNIX))
    hit.relations.append(_rel(_HYUNDAI, source_id=_SAMSUNG))  # 양끝 다 워크스페이스

    assert _order([hit]) == [_HYNIX]
