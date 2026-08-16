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
