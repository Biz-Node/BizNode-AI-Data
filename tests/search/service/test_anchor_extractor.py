"""AnchorExtractor 테스트(Task 9).

순수 로직(조사 제거, 후보 생성)은 in-memory 단위 테스트. 실제 추출(DB 접근)은
Tier B(실제 Docker PostgreSQL 대상)로 검증한다.
"""

from search.service.anchor_extractor import _build_candidates, _strip_trailing_josa


def test_strip_trailing_josa_removes_target_marker():
    assert _strip_trailing_josa("삼성전자에") == "삼성전자"


def test_strip_trailing_josa_removes_subject_marker():
    assert _strip_trailing_josa("삼성전자가") == "삼성전자"


def test_strip_trailing_josa_prefers_longest_match():
    """"에게"를 "에"보다 먼저 검사해 "삼성전자에게" -> "삼성전자에"로 덜 잘리지 않게 한다."""
    assert _strip_trailing_josa("삼성전자에게") == "삼성전자"


def test_strip_trailing_josa_returns_none_when_no_josa_matches():
    assert _strip_trailing_josa("삼성전자") is None


def test_strip_trailing_josa_returns_none_when_stripped_too_short():
    assert _strip_trailing_josa("이가") is None


def test_build_candidates_includes_original_and_stripped_forms():
    candidates = _build_candidates("삼성전자에 납품하는 기업")
    assert "삼성전자에" in candidates
    assert "삼성전자" in candidates


def test_build_candidates_filters_generic_nouns():
    """"기업"은 pipeline.normalizer.generic_names의 placeholder라 후보에서 빠진다."""
    candidates = _build_candidates("삼성전자에 납품하는 기업")
    assert "기업" not in candidates


def test_build_candidates_dedupes():
    candidates = _build_candidates("삼성전자 삼성전자")
    assert candidates.count("삼성전자") == 1


def test_build_candidates_caps_word_count():
    long_query = " ".join(f"단어{i}" for i in range(20))
    candidates = _build_candidates(long_query)
    assert len(candidates) <= 10 * 2  # 어절당 원본+조사제거 최대 2개


import pytest

from search.repository.postgres_repository import PostgresRepository
from search.service.anchor_extractor import AnchorExtractor


@pytest.fixture(scope="module")
def extractor():
    return AnchorExtractor(PostgresRepository())


# ── 양성 케이스 ──────────────────────────────────────────────────────────

def test_extract_supplies_to_query_with_josa(extractor):
    """"삼성전자에 납품하는 기업" — §3-1 실측 원 사고 재현 질의."""
    assert extractor.extract("삼성전자에 납품하는 기업") == "삼성전자"


def test_extract_investment_query_without_josa(extractor):
    """"삼성전자 최근 투자 기업" — 조사 없는 케이스(query2)."""
    assert extractor.extract("삼성전자 최근 투자 기업") == "삼성전자"


def test_extract_subject_josa_query(extractor):
    """"카카오가 투자한 기업" — 주체 조사(가).

    원래 계획서는 "네이버가"를 예시로 들었으나, corp_code_master에는
    "네이버" 단독 법인명이 없고(영문 "NAVER"로만 등록, 유사도 0.43 <
    threshold 0.50) EntityResolver.resolve("네이버")도 동일하게 None을
    반환함을 실측 확인(2026-08-16) — AnchorExtractor 구현 버그가 아니라
    테스트 데이터 선정 오류라 실존 데이터로 교체했다."""
    assert extractor.extract("카카오가 투자한 기업") == "카카오"


def test_extract_object_josa_query(extractor):
    """"SK하이닉스를 인수한 기업" — 목적 조사(를), 기업명이 문장 맨 앞."""
    assert extractor.extract("SK하이닉스를 인수한 기업") == "SK하이닉스"


def test_extract_entity_name_in_middle_of_sentence(extractor):
    """기업명이 문장 중간에 위치."""
    assert extractor.extract("최근 삼성전자와 협력한 기업") == "삼성전자"


# ── 음성 케이스(§5, 정밀도 우선 검증) ────────────────────────────────────

def test_extract_returns_none_for_purely_descriptive_query(extractor):
    """"HBM을 만드는 기업" — 기업명 없음, 추출 안 되는 게 정답(§6 완료 기준)."""
    assert extractor.extract("HBM을 만드는 기업") is None


def test_extract_returns_none_for_lawsuit_query_without_entity(extractor):
    """"최근 소송 관련 기업" — query5, 기업명 없음(§6 완료 기준)."""
    assert extractor.extract("최근 소송 관련 기업") is None


def test_extract_returns_none_for_nonexistent_company(extractor):
    """"존재하지않는기업 관련 뉴스" — corp_code_master에 없는 이름, 엉뚱한
    fuzzy 매칭이 나면 안 된다(§5 음성 케이스)."""
    assert extractor.extract("존재하지않는기업 관련 뉴스") is None


def test_extract_returns_none_for_blank_query(extractor):
    assert extractor.extract("") is None
