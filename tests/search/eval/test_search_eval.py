"""Search Layer 회귀 평가셋 — 실행·판정.

실제 PostgreSQL·Neo4j·ChromaDB를 대상으로 `SearchOrchestrator.search()`를 끝까지
돌린다(mock 없음, 현황서 §10 원칙). 케이스 정의는 `cases.py`에 있다.

    .venv-wsl/bin/python -m pytest tests/search/eval -q

케이스마다 검색을 한 번만 돌리도록 세션 스코프 fixture에 결과를 모아 두고,
공통 판정(`test_case`)과 분기별 심층 판정(아래 개별 테스트)이 그것을 나눠 쓴다.

★기업명을 못 박는 것은 `kind="fixed"` 케이스뿐이다. 관계 점수·임베딩 유사도는
  데이터가 늘면 순위가 바뀌므로 나머지는 mode·direction·edge_type·source·엔티티
  타입·건수 같은 구조 조건만 본다(cases.py 독스트링).
"""

from __future__ import annotations

import pytest

from search.model.enums import Direction, EntityType, SearchMode
from search.service.result_ranker import workspace_priority
from tests.search.eval.cases import CASES, EvalCase
from tests.search.eval.runner import CaseRun, run_all

# graph_service가 기본으로 버리는 것들 — 검색 결과에 새어 나오면 안 된다.
_EXCLUDED_FRESHNESS = {"expired"}
_HIDDEN_VERDICTS = {"unfounded", "insufficient"}

# stale 히트의 점수 천장 = 신선도 가중치 0.6 × 뒷받침 보정 최대 1.2
# (pipeline/freshness.py · graph_service.Relation.score). confidence는 1.0이 최대다.
_STALE_SCORE_CEILING = 0.6 * 1.2

_RRF_K = 60

# 평가셋이 반드시 덮어야 하는 검색 분기. 케이스를 지우다 분기가 통째로 비면
# 여기서 잡는다.
_REQUIRED_COVERAGE = frozenset({
    "mode:NAME", "mode:RELATIONSHIP", "mode:SEMANTIC",
    "direction:OUTGOING", "direction:INCOMING", "direction:없음(양방향)",
    "anchor:DART 1차", "anchor:Kiwi 조사 분리", "anchor:company_aliases fallback",
    "anchor:추출 실패",
    "router:깊은 규칙", "router:얕은 키워드",
    "graph:anchored", "graph:anchorless",
    "entity:Company", "entity:Person", "entity:Organization",
    "ranking:RRF", "ranking:workspace_keys", "ranking:freshness",
    "negative:일반명사 오인 방지", "negative:2글자 기업명",
    "negative:존재하지 않는 기업", "negative:한글/영문 alias",
    "negative:조사에 따른 방향 반전",
})


@pytest.fixture(scope="session")
def runs(orchestrator, anchor_extractor) -> dict[str, CaseRun]:
    return run_all(orchestrator, anchor_extractor)


def _params() -> list:
    return [
        pytest.param(
            case, id=case.id,
            marks=[pytest.mark.xfail(strict=True, reason=case.known_issue)]
            if case.known_issue else [],
        )
        for case in CASES
    ]


def _describe(run: CaseRun) -> str:
    names = ", ".join(f"{h.rank}:{h.name}" for h in run.result.hits[:5])
    return (f"\n  query={run.case.query!r}"
            f"\n  anchor={run.anchor!r} mode={run.query.mode.value}"
            f" edge_types={run.query.edge_types} direction={run.query.direction}"
            f" total={run.result.total}\n  상위={names}")


# ══════════════════════════════════════════════════════════════════════
#  공통 판정 — 모든 케이스
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("case", _params())
def test_case(case: EvalCase, runs: dict[str, CaseRun]):
    run = runs[case.id]
    ctx = _describe(run)

    assert run.anchor == case.expected_anchor, f"anchor 불일치{ctx}"
    assert run.query.mode is case.expected_mode, f"mode 불일치{ctx}"
    assert run.query.direction == case.expected_direction, f"direction 불일치{ctx}"
    assert tuple(run.query.edge_types or ()) == case.expected_edge_types, \
        f"edge_types 불일치{ctx}"
    assert run.result.used_semantic_fallback is (case.expected_mode is SearchMode.SEMANTIC), \
        f"used_semantic_fallback이 mode와 어긋난다{ctx}"

    hits = run.result.hits
    if case.exact_total is not None:
        assert run.result.total == case.exact_total, f"결과 건수 불일치{ctx}"
    assert len(hits) >= case.min_hits, f"결과가 {case.min_hits}건에 못 미친다{ctx}"
    assert run.result.total == len(hits)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1)), f"rank가 연속이 아니다{ctx}"

    for hit in hits:
        assert set(hit.sources) == case.expected_sources, f"source 불일치({hit.name}){ctx}"

    # NAME 분기만 ResultRanker를 건너뛴다 → rrf_score가 비어 있다(orchestrator §5).
    if case.expected_mode is SearchMode.NAME:
        assert all(h.rrf_score is None for h in hits), f"NAME 분기에 rrf_score가 붙었다{ctx}"
    else:
        assert all(h.rrf_score is not None for h in hits), f"rrf_score가 비었다{ctx}"

    allowed = case.allowed_types()
    if allowed is not None:
        actual = {h.entity_type.value for h in hits}
        assert actual <= allowed, f"허용되지 않은 엔티티 타입 {actual - allowed}{ctx}"
    for entity_type in case.must_contain_entity_types:
        assert any(h.entity_type is entity_type for h in hits), \
            f"{entity_type.value} 히트가 없다{ctx}"

    # 관계 히트는 방향·edge_type·edge_id 계약을 지켜야 한다.
    for hit in hits:
        for relation in hit.relations or ():
            assert relation.edge_id, f"edge_id가 비었다({hit.name}){ctx}"
            assert relation.edge_type in case.expected_edge_types, \
                f"요청하지 않은 edge_type {relation.edge_type}{ctx}"
            if case.expected_direction is not None:
                assert relation.direction == case.expected_direction.value, \
                    f"방향 필터를 통과하지 못한 관계({hit.name}){ctx}"

    # kind="fixed"만 기업을 못 박는다.
    if case.must_include:
        assert case.kind == "fixed", "must_include는 kind='fixed'에서만 쓴다"
        found = {(h.name, h.entity_id) for h in hits}
        for expected in case.must_include:
            assert expected in found, f"{expected}가 결과에 없다{ctx}"


def test_coverage_is_complete():
    """평가셋이 선언한 검색 분기를 하나도 빠뜨리지 않았는가."""
    covered = {tag for case in CASES for tag in case.coverage}
    assert _REQUIRED_COVERAGE <= covered, f"덮이지 않은 분기: {_REQUIRED_COVERAGE - covered}"


def test_excluded_relations_never_leak(runs: dict[str, CaseRun]):
    """graph_service가 기본으로 거르는 것(expired 신선도 · 근거없음/근거부족 판정)이
    검색 결과에 새어 나오지 않는가 — 전 케이스 공통 불변식."""
    for run in runs.values():
        for hit in run.result.hits:
            if "neo4j" not in hit.sources:
                continue
            status = (hit.freshness or {}).get("status")
            assert status not in _EXCLUDED_FRESHNESS, \
                f"[{run.case.id}] expired 관계가 노출됐다: {hit.name}"
            assert hit.verdict not in _HIDDEN_VERDICTS, \
                f"[{run.case.id}] 숨겨야 할 판정({hit.verdict})이 노출됐다: {hit.name}"


# ══════════════════════════════════════════════════════════════════════
#  분기별 심층 판정
# ══════════════════════════════════════════════════════════════════════

def test_direction_flips_with_josa(runs: dict[str, CaseRun]):
    """조사 하나로 같은 edge_type의 방향이 뒤집히는가 — 「가 납품」 vs 「에 납품」.

    앵커(삼성전자)가 OUTGOING에서는 관계의 source, INCOMING에서는 target에 서야
    한다. 결과 기업명은 보지 않는다(구조 조건만)."""
    outgoing = runs["rel-supplies-outgoing"]
    incoming = runs["rel-supplies-incoming"]

    for hit in outgoing.result.hits:
        for relation in hit.relations:
            assert relation.source == "삼성전자", f"OUTGOING인데 앵커가 source가 아니다: {relation}"
            assert relation.target == hit.name
    for hit in incoming.result.hits:
        for relation in hit.relations:
            assert relation.target == "삼성전자", f"INCOMING인데 앵커가 target이 아니다: {relation}"
            assert relation.source == hit.name


def test_bidirectional_query_keeps_both_directions(runs: dict[str, CaseRun]):
    """direction=None 이면 방향으로 거르지 않는다 — 결과에 양방향이 함께 나오는가.

    ★**여기서 보는 것은 결과이지 후보가 아니다.** 후보 집합에 양방향이 남는지는
      검색기 레벨(`test_graph_searcher.test_investment_query_direction_none_
      returns_both_sides`)이 본다. 이쪽은 그 뒤 랭킹까지 지나고도 한쪽이 통째로
      사라지지 않는가를 본다 — 그래서 케이스의 `top_k` 가 관측 창이다.
    """
    run = runs["rel-stake-bidirectional"]
    directions = {r.direction for h in run.result.hits for r in h.relations or ()}
    assert directions == {Direction.OUTGOING.value, Direction.INCOMING.value}, \
        f"양방향 질의인데 한쪽만 남았다: {directions}{_describe(run)}"


def test_anchorless_search_fills_both_slots(runs: dict[str, CaseRun]):
    """anchor가 없으면 앵커 기준 방향을 지어내지 않고(None), source/target 슬롯을
    따로 채운다. 관계 질의에 의미검색을 섞지 않는지도 함께 본다."""
    run = runs["rel-anchorless-sues"]
    assert run.query.resolved_entities == [], f"앵커가 없어야 한다{_describe(run)}"

    directions = {r.direction for h in run.result.hits for r in h.relations or ()}
    assert directions == {None}, f"앵커가 없는데 방향이 붙었다: {directions}"

    # 슬롯이 실제로 양쪽에서 채워졌는가 — 히트가 관계의 source 쪽인 것과 target
    # 쪽인 것이 모두 있어야 한다.
    sides = set()
    for hit in run.result.hits:
        for relation in hit.relations or ():
            if hit.entity_id == relation.source_id:
                sides.add("source")
            if hit.entity_id == relation.target_id:
                sides.add("target")
    assert sides == {"source", "target"}, f"슬롯 한쪽만 찼다: {sides}{_describe(run)}"

    assert all("chroma" not in h.sources for h in run.result.hits), \
        "관계 질의에 의미검색 결과가 섞였다"


def test_rrf_score_follows_rank_for_single_source(runs: dict[str, CaseRun]):
    """소스가 하나뿐인 결과의 RRF 값은 1/(60+순위)와 정확히 같아야 한다 —
    rrf_score를 확률·신뢰도로 오해하지 못하게 하는 규약(현황서 §5)."""
    run = runs["rel-supplies-incoming"]
    for hit in run.result.hits:
        assert hit.rrf_score == pytest.approx(1.0 / (_RRF_K + hit.rank)), \
            f"RRF 값이 순위와 어긋난다: rank={hit.rank} rrf={hit.rrf_score}"
    scores = [h.rrf_score for h in run.result.hits]
    assert scores == sorted(scores, reverse=True)


def test_freshness_demotes_stale_relations(runs: dict[str, CaseRun]):
    """신선도가 순위에 실제로 반영되는가.

    stale은 버리지 않고 가중치(0.6)만 곱해 뒤로 민다 — 그래서 stale 히트의
    source_score는 0.72(=0.6×뒷받침 보정 최대 1.2)를 넘을 수 없다. 워크스페이스가
    없으면 최종 순서는 source_score 내림차순이므로, 그 천장을 넘는 current 히트는
    반드시 stale보다 앞선다."""
    run = runs["rel-supplies-outgoing"]
    hits = run.result.hits
    statuses = [(h.freshness or {}).get("status") for h in hits]

    assert "stale" in statuses, (
        "이 케이스는 stale이 섞여 있어야 신선도 감점을 검증할 수 있다 — "
        f"데이터가 바뀌었으면 케이스를 교체하라{_describe(run)}")

    for hit, status in zip(hits, statuses):
        if status == "stale":
            assert hit.source_score <= _STALE_SCORE_CEILING + 1e-9, \
                f"stale인데 점수 천장을 넘었다: {hit.name} {hit.source_score}"

    scores = [h.source_score for h in hits]
    assert scores == sorted(scores, reverse=True), \
        f"워크스페이스가 없으면 source_score 내림차순이어야 한다: {scores}"


def test_workspace_keys_rank_without_filtering(runs: dict[str, CaseRun]):
    """워크스페이스는 후보를 지우지 않고 순서만 바꾼다.

    · 워크스페이스에 닿는 히트가 점수를 이기고 먼저 온다
    · 워크스페이스 밖 기업도 그대로 남는다(필터가 아니다)
    · 워크스페이스가 없으면 top-10에 못 들던 기업이 들어온다
    """
    with_ws = runs["rank-workspace-relationship"]
    without_ws = runs["rel-supplies-incoming"]
    keys = set(with_ws.case.workspace_keys)

    priorities = [workspace_priority(h, keys) for h in with_ws.result.hits]
    assert priorities == sorted(priorities), \
        f"워크스페이스 관련도 순이 아니다: {priorities}{_describe(with_ws)}"

    top = with_ws.result.hits[0]
    assert top.entity_id in keys, f"워크스페이스 기업이 1위가 아니다{_describe(with_ws)}"
    assert top.source_score < with_ws.result.hits[1].source_score, (
        "워크스페이스가 점수를 이겼다는 증거가 없다 — 1위의 원점수가 2위보다 낮아야 "
        f"관련도가 점수보다 먼저 걸렸음이 드러난다{_describe(with_ws)}")

    outside = [h for h in with_ws.result.hits if h.entity_id not in keys]
    assert outside, "워크스페이스 밖 기업이 통째로 사라졌다 — 필터로 동작하고 있다"

    assert top.entity_id not in {h.entity_id for h in without_ws.result.hits}, (
        "워크스페이스 없이도 top-10에 드는 기업이라 랭킹 효과를 증명하지 못한다 — "
        "케이스를 교체하라")


def test_alias_fallback_is_the_second_window(postgres_repo, runs: dict[str, CaseRun]):
    """「네이버」는 corp_code_master 1차에서 못 찾고 company_aliases 2차에서만
    잡힌다 — pg_trgm이 한글↔영문을 원리적으로 못 잇기 때문이다.

    ★2026-08-23 해소 — 2차 창구가 **정식 법인명(canon_name)**을 돌려주게 되어
    EntityResolver도 같은 회사에 닿는다(현황서 §4-6). 케이스의 xfail을 뗐다."""
    assert postgres_repo.match_candidates(["네이버"]) == [], \
        "corp_code_master 1차에서 「네이버」가 잡히면 이 케이스의 전제가 깨진다"
    assert postgres_repo.alias_exact_match(["네이버"]) == "NAVER Corporation"
    assert runs["known-alias-naver"].anchor == "NAVER Corporation"


def test_unknown_company_is_not_resolved(entity_resolver, runs: dict[str, CaseRun]):
    """없는 기업명이 실존 기업으로 둔갑하지 않는가 — 앵커도 해소도 실패해야 하고,
    의미검색 결과를 이름 해소인 척하지 않아야 한다."""
    run = runs["sem-unknown-company"]
    assert run.anchor is None
    assert entity_resolver.resolve("존재하지않는기업") is None
    assert entity_resolver.resolve(run.case.query) is None
    assert run.query.resolved_entities == []
    assert run.query.mode is SearchMode.SEMANTIC
    assert all(h.entity_type is EntityType.COMPANY for h in run.result.hits)
