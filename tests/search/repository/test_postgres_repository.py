"""PostgresRepository 테스트 — 실제 Docker Compose PostgreSQL 데이터 대상(mock 없음).

실측 근거(2026-08-09, docker exec psql):
  - companies: '삼성전자'(00126380, 005930, sector=["반도체","로봇"]),
               '삼성에스디에스'(00126186, 018260) 존재
  - corp_code_master: '삼성전자판매'(정확 매칭 대상 없음, 오탈자 fuzzy 테스트용)가 존재하고,
    '삼성전자판애'(오타) 질의 시 similarity('삼성전자판매')=0.556, similarity('삼성전자')=0.5로
    둘 다 0.50 이상 — 다중 후보를 반환해야 하는 실제 사례
  - companies.sector @> '"반도체"' 로 35개 회사(삼성전자 포함) 존재
"""

import pytest

from pipeline.normalizer.resolver import Resolution


def test_real_db_connection(postgres_repo):
    """실제 DB 연결 자체가 되는지 확인 — 아무 조회나 성공하면 OK."""
    result = postgres_repo.resolve_candidates("삼성전자")
    assert isinstance(result, list)


def test_resolve_candidates_exact_match(postgres_repo):
    results = postgres_repo.resolve_candidates("삼성전자")
    assert any(r.corp_code == "00126380" and r.method == "exact" for r in results)


def test_resolve_candidates_returns_resolution_dataclass(postgres_repo):
    """pipeline.normalizer.resolver.Resolution을 그대로 재사용하는지 확인."""
    results = postgres_repo.resolve_candidates("삼성전자")
    assert all(isinstance(r, Resolution) for r in results)


def test_resolve_candidates_by_stock_code(postgres_repo):
    """종목코드("005930")로도 조회 가능해야 한다 — resolver.resolve()에는 없는 기능."""
    results = postgres_repo.resolve_candidates("005930")
    assert any(r.corp_code == "00126380" for r in results)


def test_resolve_candidates_fuzzy_returns_multiple_candidates(postgres_repo):
    """다중 후보 반환이 이번 Repository의 핵심 요구사항.

    '삼성전자판애'(오타)는 '삼성전자판매'(sim=0.556)와 '삼성전자'(sim=0.5) 둘 다
    threshold 0.50을 통과한다 — 기존 resolver.resolve()라면 1건만 반환했을 상황.
    """
    results = postgres_repo.resolve_candidates("삼성전자판애", limit=10)
    corp_names = {r.corp_name for r in results}
    assert len(results) >= 2
    assert "삼성전자" in corp_names
    assert "삼성전자판매" in corp_names


def test_resolve_candidates_no_match_returns_empty_list(postgres_repo):
    """매칭 실패는 예외가 아니라 빈 리스트."""
    results = postgres_repo.resolve_candidates("존재하지않는가상의회사이름ZZZ999")
    assert results == []


def test_resolve_candidates_respects_limit(postgres_repo):
    results = postgres_repo.resolve_candidates("삼성전자판애", limit=1)
    assert len(results) <= 1


def test_resolve_candidates_known_foreign_alias_matches(postgres_repo):
    """corp_code_master/companies에는 영문명 컬럼이 없어 일반적인 영문 검색은
    지원되지 않지만, normalize_company_name()이 호출하는
    pipeline/normalizer/foreign_aliases.FOREIGN_ALIASES에 등록된 표기는
    예외적으로 매칭된다(실측: "samsungelectronics" -> "삼성전자" 별칭 존재).
    이는 resolve_candidates가 이 정규화 함수를 재사용한 결과이지, 별도로
    구현한 영문명 검색 기능이 아니다.
    """
    results = postgres_repo.resolve_candidates("Samsung Electronics")
    assert any(r.corp_code == "00126380" for r in results)


def test_resolve_candidates_unknown_english_name_not_supported(postgres_repo):
    """별칭 테이블에 없는 임의의 영문명은 매칭되지 않는다 — 예외 없이 빈
    리스트를 반환하는지만 확인한다(구현되지 않은 것을 구현된 것처럼 가정하지
    않는다).
    """
    results = postgres_repo.resolve_candidates("Some Unregistered Foreign Co")
    assert results == []


def test_best_candidate_match_returns_top_scoring_candidate(postgres_repo):
    result = postgres_repo.best_candidate_match(["삼성전자에", "삼성전자", "납품하는"])
    assert result is not None
    matched_candidate, score = result
    assert matched_candidate == "삼성전자"
    assert score == pytest.approx(1.0)


def test_best_candidate_match_returns_none_for_empty_candidates(postgres_repo):
    assert postgres_repo.best_candidate_match([]) is None


def test_best_candidate_match_returns_none_when_nothing_matches(postgres_repo):
    result = postgres_repo.best_candidate_match(["최근", "소송", "관련"])
    assert result is None
