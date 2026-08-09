"""EntityResolver 테스트.

실제 Docker Compose PostgreSQL 데이터 대상(mock 없음). 순수 랭킹/동점 처리
로직만 in-memory Resolution으로 별도 단위 테스트한다(DB 접근 없는 순수 함수
검증이지, "실제 DB 검증을 mock으로 대체"하는 것이 아니다).

실측 근거(2026-08-09, docker exec psql):
  - "삼성전자"(00126380) 검색: corp_name 정확 일치(exact) + "삼성전자판매"
    fuzzy(sim=0.5, threshold 0.50 통과) 총 2건
  - "삼성전자판애"(오타) 검색: exact 없음. fuzzy만 2건 —
    "삼성전자판매"(sim=0.5556), "삼성전자"(sim=0.5)
  - "삼성전자판매"(00252074)는 corp_code_master에는 있으나 companies(64건)엔 없음 — stub
  - "SK하이닉스"(00164779, 000660) corp_code_master에 실존
  - "Samsung Electronics" -> normalize_company_name()의 FOREIGN_ALIASES
    ("samsungelectronics"->"삼성전자")를 거쳐 fuzzy, score=1.0으로 매칭됨(Task 2에서 확인)
"""

from pipeline.normalizer.resolver import Resolution
from search.service.entity_resolver import EntityResolver, _pick_best, _tier


# ── 순수 랭킹/동점 판정 로직 (in-memory, DB 접근 없음) ──────────────────

def test_tier_exact_is_zero():
    r = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)
    assert _tier(r) == 0


def test_tier_fuzzy_score_one_is_one():
    """정규화/별칭을 거쳐 사실상 완전 일치(예: alias 매칭)."""
    r = Resolution("00126380", "삼성전자", "005930", "fuzzy", 1.0)
    assert _tier(r) == 1


def test_tier_genuine_fuzzy_is_two():
    r = Resolution("00252074", "삼성전자판매", None, "fuzzy", 0.5556)
    assert _tier(r) == 2


def test_pick_best_prefers_lower_tier():
    exact = Resolution("00126380", "삼성전자", "005930", "exact", 1.0)
    fuzzy = Resolution("00252074", "삼성전자판매", None, "fuzzy", 0.5556)
    assert _pick_best([fuzzy, exact]) == exact


def test_pick_best_within_same_tier_prefers_higher_score():
    high = Resolution("A", "가", None, "fuzzy", 0.9)
    low = Resolution("B", "나", None, "fuzzy", 0.6)
    assert _pick_best([low, high]) == high


def test_pick_best_returns_none_on_genuine_tie():
    """같은 계층·같은 score로 완전히 동점이면 임의로 하나를 확정하지 않는다(§9)."""
    a = Resolution("A", "가", None, "fuzzy", 0.7)
    b = Resolution("B", "나", None, "fuzzy", 0.7)
    assert _pick_best([a, b]) is None


def test_pick_best_empty_returns_none():
    assert _pick_best([]) is None


def test_pick_best_single_candidate_returned_even_if_score_low():
    only = Resolution("A", "가", None, "fuzzy", 0.51)
    assert _pick_best([only]) == only


# ── 실제 DB 대상 (mock 없음) ─────────────────────────────────────────

def test_real_db_connection(entity_resolver):
    result = entity_resolver.resolve("삼성전자")
    assert result is not None


def test_exact_company_name_match(entity_resolver):
    result = entity_resolver.resolve("삼성전자")
    assert result.corp_code == "00126380"
    assert result.method == "exact"


def test_exact_stock_code_match(entity_resolver):
    """Task 지침은 이를 "corp_code 직접 입력"이라 부르지만, "005930"은 실제로는
    corp_code(00126380)가 아니라 stock_code다 — 실제 스키마 기준으로 검증한다.
    """
    result = entity_resolver.resolve("005930")
    assert result.corp_code == "00126380"


def test_normalization_alias_english_name(entity_resolver):
    """"Samsung Electronics" -> normalize_company_name()의 FOREIGN_ALIASES를
    거쳐 "삼성전자"로 resolve — corp_code_master/companies에 영문 컬럼은 없다.
    """
    result = entity_resolver.resolve("Samsung Electronics")
    assert result is not None
    assert result.corp_code == "00126380"


def test_exact_preferred_over_fuzzy_when_both_present(entity_resolver):
    """"삼성전자" 질의는 exact(자기 자신)와 fuzzy("삼성전자판매", sim=0.5) 둘 다
    후보로 나오지만, resolve()는 exact를 우선한다(정확한 후보가 있으면 fuzzy로
    밀리지 않아야 함, §9).
    """
    candidates = entity_resolver.resolve_candidates("삼성전자")
    corp_codes = {c.corp_code for c in candidates}
    assert "00126380" in corp_codes  # 삼성전자(exact)
    assert len(candidates) >= 2  # 삼성전자판매(fuzzy)도 후보에 포함
    result = entity_resolver.resolve("삼성전자")
    assert result.corp_code == "00126380"
    assert result.method == "exact"


def test_fuzzy_typo_returns_multiple_ranked_candidates(entity_resolver):
    """오타 "삼성전자판애" -> 실제 fuzzy 후보 2건(둘 다 threshold 통과),
    score 내림차순으로 정렬된다.
    """
    candidates = entity_resolver.resolve_candidates("삼성전자판애")
    assert len(candidates) >= 2
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)
    names = {c.corp_name for c in candidates}
    assert "삼성전자판매" in names
    assert "삼성전자" in names


def test_fuzzy_below_threshold_returns_none():
    """"삼성전차"(1글자 오타)는 실측상 유사도가 threshold(0.50) 미만이라
    후보가 없다 — 임의로 threshold를 낮추지 않고 현재 결과를 그대로 확인한다.
    """
    resolver = EntityResolver()
    result = resolver.resolve("삼성전차")
    assert result is None


def test_no_match_returns_none(entity_resolver):
    result = entity_resolver.resolve("존재하지않는가상의회사이름ZZZ999")
    assert result is None


def test_stub_entity_resolves_even_without_companies_row(entity_resolver, postgres_repo):
    """corp_code_master에는 있지만 companies(64건)에는 없는 기업도 resolve
    성공해야 한다(§3, §10) — "삼성전자판매"(00252074)로 검증.
    """
    assert postgres_repo.find_by_corp_code("00252074") is None  # companies엔 없음(전제 확인)

    result = entity_resolver.resolve("삼성전자판매")
    assert result is not None
    assert result.corp_code == "00252074"


def test_resolve_many_returns_dict_keyed_by_query(entity_resolver):
    results = entity_resolver.resolve_many(["삼성전자", "SK하이닉스", "존재하지않는가상기업ZZZ"])
    assert results["삼성전자"].corp_code == "00126380"
    assert results["SK하이닉스"].corp_code == "00164779"
    assert results["존재하지않는가상기업ZZZ"] is None


def test_resolve_many_input_is_list_of_strings_not_parsed(entity_resolver):
    """콤마로 구분된 단일 문자열을 파싱하는 것은 이 Task의 책임이 아니다 —
    이미 나뉜 리스트만 입력으로 받는다.
    """
    results = entity_resolver.resolve_many(["삼성전자, SK하이닉스"])
    assert "삼성전자, SK하이닉스" in results
    assert results["삼성전자, SK하이닉스"] is None
