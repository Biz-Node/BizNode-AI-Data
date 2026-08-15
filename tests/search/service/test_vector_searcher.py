"""VectorSearcher 테스트.

순수 로직(_score_from_l2_distance/_entity_id/_resolve_top_k)과 ChromaRepository
변환 로직은 fake repo로 통제된 입력을 준다 — `search_company()`가 실제로 무엇을
반환하든 VectorSearcher가 정확히 무엇으로 바꾸는가라는 코드 자체의 계약을
확인하는 것이라, "실제 DB, mock 없음" 원칙과 배치되지 않는다(Task5/6 GraphSearcher
테스트와 동일한 근거, tests/search/service/test_graph_searcher.py 참고).

§6-6 "HBM을 만드는 기업" 재현은 그대로 실제 Docker ChromaDB 대상(mock 없음).

실측 근거(2026-08-12):
  - company 컬렉션(count=2219)은 생성 시 hnsw:space를 지정하지 않아 ChromaDB 기본값
    l2(제곱 유클리드 거리)를 쓴다(collection.metadata=None,
    chromadb.segment.impl.vector.hnsw_params.py 기본값 확인).
  - OpenAI 임베딩은 단위벡터로 정규화돼 있다(실측 norm≈1.0) → 단위벡터에서
    d² = 2(1-cos) 이므로 cos = 1 - d²/2, score=(cos+1)/2 = 1 - d²/4.
  - metadata.corp_code가 빈 문자열인 항목이 2219건 중 1400건(63%, 해외 stub) —
    corp_code만으로는 entity_id를 채울 수 없어 chroma id 기반 fallback이 필요하다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from search.model.enums import EntityType
from search.service.vector_searcher import (
    VectorSearcher,
    _entity_id,
    _resolve_top_k,
    _score_from_l2_distance,
)


def _chroma_result(ids, distances, metadatas):
    return {"ids": [ids], "distances": [distances], "metadatas": [metadatas]}


# ── 순수 로직 ────────────────────────────────────────────────────────────

def test_score_from_l2_distance_zero_is_perfect_match():
    assert _score_from_l2_distance(0.0) == 1.0


def test_score_from_l2_distance_four_is_opposite():
    assert _score_from_l2_distance(4.0) == 0.0


def test_score_from_l2_distance_two_is_midpoint():
    assert _score_from_l2_distance(2.0) == 0.5


def test_score_from_l2_distance_clips_above_theoretical_max():
    assert _score_from_l2_distance(10.0) == 0.0


def test_entity_id_uses_corp_code_when_present():
    assert _entity_id("삼성전자", "00126380") == "00126380"


def test_entity_id_falls_back_to_normalized_name_when_corp_code_empty():
    """GraphSearcher가 stub 기업에 쓰는 식별자(norm_name = normalize_company_name(name))와
    일치시킨다 — chroma id 해시를 쓰면 같은 기업이 소스마다 다른 entity_id를 갖게 된다."""
    assert _entity_id("BOE", "") == "boe"


def test_resolve_top_k_defaults_to_ten_when_none():
    assert _resolve_top_k(None) == 10


def test_resolve_top_k_uses_value_under_hard_cap():
    assert _resolve_top_k(5) == 5


def test_resolve_top_k_caps_at_hard_limit():
    assert _resolve_top_k(9999) == 50


# ── ChromaRepository 변환 계약(fake repo) ─────────────────────────────────

def test_search_returns_empty_list_when_no_matches():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result([], [], [])

    hits = VectorSearcher(fake_repo).search("존재하지 않는 질의")

    assert hits == []


def test_search_converts_chroma_result_to_search_hit():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result(
        ["co_00126380"], [0.8], [{"corp_code": "00126380", "name": "삼성전자"}],
    )

    hits = VectorSearcher(fake_repo).search("반도체를 만드는 기업")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.entity_type == EntityType.COMPANY
    assert hit.entity_id == "00126380"
    assert hit.name == "삼성전자"
    assert hit.score == _score_from_l2_distance(0.8)
    assert hit.sources == ["chroma"]
    assert hit.freshness is None
    assert hit.verdict is None
    assert hit.relations is None


def test_search_falls_back_to_normalized_name_when_corp_code_missing():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result(
        ["co_n1e5e56dbf203"], [0.9], [{"corp_code": "", "name": "XMC"}],
    )

    hits = VectorSearcher(fake_repo).search("파운드리")

    assert hits[0].entity_id == "xmc"


def test_search_exposes_kind_from_metadata():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result(
        ["co_00126380"], [0.8], [{"corp_code": "00126380", "name": "삼성전자", "kind": "기업"}],
    )

    hits = VectorSearcher(fake_repo).search("반도체를 만드는 기업")

    assert hits[0].kind == "기업"


def test_search_defaults_kind_to_none_when_metadata_missing_it():
    """is_stub/confidence/kind가 없는 레코드가 실제로 존재한다(실측 확인) — .get()으로 접근."""
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result(
        ["co_00126380"], [0.8], [{"corp_code": "00126380", "name": "삼성전자"}],
    )

    hits = VectorSearcher(fake_repo).search("반도체를 만드는 기업")

    assert hits[0].kind is None


def test_search_preserves_low_score_without_cutoff():
    """최소 유사도 컷오프를 적용하지 않는다(Task6 지침 §6) — 낮은 점수도 그대로 반환."""
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result(
        ["co_00126380"], [3.9], [{"corp_code": "00126380", "name": "삼성전자"}],
    )

    hits = VectorSearcher(fake_repo).search("전혀 무관한 질의")

    assert len(hits) == 1
    assert hits[0].score == _score_from_l2_distance(3.9)


def test_search_passes_normalized_query_and_default_top_k():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result([], [], [])

    VectorSearcher(fake_repo).search("HBM을 만드는 기업")

    fake_repo.search_company.assert_called_once_with(
        "HBM을 만드는 기업", n_results=10,
    )


def test_search_respects_explicit_top_k():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result([], [], [])

    VectorSearcher(fake_repo).search("질의", top_k=5)

    assert fake_repo.search_company.call_args.kwargs["n_results"] == 5


def test_search_caps_top_k_at_hard_limit():
    fake_repo = MagicMock()
    fake_repo.search_company.return_value = _chroma_result([], [], [])

    VectorSearcher(fake_repo).search("질의", top_k=9999)

    assert fake_repo.search_company.call_args.kwargs["n_results"] == 50


# ── entity_id 소스 간 일치(patch, 2026-08-15) — 실제 Docker Neo4j·ChromaDB, mock 없음

def test_stub_company_entity_id_matches_graph_searcher(entity_resolver, graph_searcher, vector_searcher):
    """corp_code 없는 stub 기업(BOE)이 GraphSearcher·VectorSearcher 양쪽에서 같은
    entity_id로 나오는지 확인한다 — LX세미콘 -> BOE(SUPPLIES_TO) 실제 겹치는 사례
    (2026-08-15 실측). 다르면 다운스트림(Task7 ResultRanker)에서 같은 기업이 별개
    엔티티로 분리된다."""
    resolution = entity_resolver.resolve("LX세미콘")
    graph_hits = graph_searcher.search([resolution], ["SUPPLIES_TO"], None, top_k=50)
    boe_graph_hits = [h for h in graph_hits if h.name == "BOE"]
    assert len(boe_graph_hits) == 1

    vector_hits = vector_searcher.search("BOE")
    boe_vector_hits = [h for h in vector_hits if h.name == "BOE"]
    assert len(boe_vector_hits) == 1

    assert boe_vector_hits[0].entity_id == boe_graph_hits[0].entity_id == "boe"


# ── §6-6 "HBM을 만드는 기업" 재현 — 실제 Docker ChromaDB, mock 없음 ─────────

def test_hbm_query_returns_results(vector_searcher):
    hits = vector_searcher.search("HBM을 만드는 기업")

    assert len(hits) > 0
    for hit in hits:
        assert hit.entity_type == EntityType.COMPANY
        assert hit.sources == ["chroma"]
        assert hit.entity_id
        assert 0.0 <= hit.score <= 1.0
        assert hit.freshness is None
        assert hit.verdict is None
        assert hit.relations is None


def test_search_default_top_k_returns_at_most_ten(vector_searcher):
    hits = vector_searcher.search("반도체를 만드는 기업")

    assert 0 < len(hits) <= 10
