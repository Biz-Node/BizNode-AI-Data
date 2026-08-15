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

from search.dto.search_hit import SearchHit
from search.model.enums import EntityType
from search.service.result_ranker import _RRF_K, ResultRanker


def _hit(entity_id, *, score, sources=("neo4j",), entity_type=EntityType.COMPANY, name=None, **overrides):
    return SearchHit(
        entity_type=entity_type,
        entity_id=entity_id,
        name=name or entity_id,
        score=score,
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
    assert dup.score == pytest.approx(1 / (_RRF_K + 1))  # rank=1 한 번만 기여


# ── 정렬 미보장 입력 방어(Task7 지침 §3) ────────────────────────────────────

def test_ranks_by_score_even_when_input_not_pre_sorted():
    """GraphSearcher anchorless 출력(source_hits + target_hits)은 전역 score
    내림차순을 보장하지 않는다 — RRF는 순위 기반이므로 여기서 먼저 정렬해야 한다."""
    low_first = _hit("low", score=0.3)
    high_second = _hit("high", score=0.9)

    result = ResultRanker().rank([low_first, high_second], [])

    assert [h.entity_id for h in result] == ["high", "low"]
    assert result[0].score == pytest.approx(1 / (_RRF_K + 1))
    assert result[1].score == pytest.approx(1 / (_RRF_K + 2))


# ── 단일 소스 엔티티 — 필드 보존 ────────────────────────────────────────────

def test_graph_only_entity_keeps_graph_fields_and_single_source():
    graph_hit = _hit("g1", score=0.7, sources=["neo4j"], verdict="supported",
                      freshness={"status": "current"}, relations=[{"edge_type": "SUPPLIES_TO"}])

    result = ResultRanker().rank([graph_hit], [])

    assert len(result) == 1
    assert result[0].sources == ["neo4j"]
    assert result[0].verdict == "supported"
    assert result[0].freshness == {"status": "current"}
    assert result[0].relations == [{"edge_type": "SUPPLIES_TO"}]
    assert result[0].score == pytest.approx(1 / (_RRF_K + 1))


def test_vector_only_entity_keeps_kind_and_single_source():
    vector_hit = _hit("v1", score=0.9, sources=["chroma"], kind="기업")

    result = ResultRanker().rank([], [vector_hit])

    assert len(result) == 1
    assert result[0].sources == ["chroma"]
    assert result[0].kind == "기업"
    assert result[0].score == pytest.approx(1 / (_RRF_K + 1))


# ── 양쪽 소스에 다 있는 엔티티 — RRF 가산 + 필드 병합 ───────────────────────

def test_entity_in_both_sources_sums_rrf_contributions():
    graph_hit = _hit("shared", score=0.5, sources=["neo4j"])
    vector_hit = _hit("shared", score=0.99, sources=["chroma"])

    result = ResultRanker().rank([graph_hit], [vector_hit])

    assert len(result) == 1
    expected = 1 / (_RRF_K + 1) + 1 / (_RRF_K + 1)
    assert result[0].score == pytest.approx(expected)
    assert result[0].sources == ["neo4j", "chroma"]


def test_merged_hit_preserves_graph_only_and_vector_only_fields():
    """graph 전용(freshness/verdict/relations), vector 전용(kind) 필드는 재계산하지
    않고 원본 SearchHit에서 그대로 보존한다(Task7 지침 §6)."""
    graph_hit = _hit(
        "shared", score=0.5, sources=["neo4j"], verdict="supported",
        freshness={"status": "current"}, relations=[{"edge_type": "SUPPLIES_TO"}],
    )
    vector_hit = _hit("shared", score=0.9, sources=["chroma"], kind="기업")

    result = ResultRanker().rank([graph_hit], [vector_hit])

    hit = result[0]
    assert hit.verdict == "supported"
    assert hit.freshness == {"status": "current"}
    assert hit.relations == [{"edge_type": "SUPPLIES_TO"}]
    assert hit.kind == "기업"


def test_entity_in_both_sources_outranks_single_source_entity_at_similar_position():
    """두 소스에서 동시에 발견된 엔티티는 한쪽에서만 발견된 동일 순위 엔티티보다
    RRF 점수가 높아야 한다(Task7 지침 §6) — 별도 가산 로직 없이 RRF 합산만으로 성립."""
    shared_graph = _hit("shared", score=0.5, sources=["neo4j"])
    shared_vector = _hit("shared", score=0.5, sources=["chroma"])
    solo_graph = _hit("solo", score=0.51, sources=["neo4j"])  # graph 소스 내 1위(=shared보다 근소 우위)

    result = ResultRanker().rank([solo_graph, shared_graph], [shared_vector])

    by_id = {h.entity_id: h for h in result}
    assert by_id["shared"].score > by_id["solo"].score


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
    scores = [hit.score for hit in merged]
    assert scores == sorted(scores, reverse=True)

    graph_ids = {hit.entity_id for hit in graph_hits}
    vector_ids = {hit.entity_id for hit in vector_hits}
    overlap = graph_ids & vector_ids
    if overlap:
        shared_id = next(iter(overlap))
        shared = next(hit for hit in merged if hit.entity_id == shared_id)
        assert set(shared.sources) == {"neo4j", "chroma"}
