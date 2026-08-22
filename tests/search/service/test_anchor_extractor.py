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


# ── §4-1 회귀 테스트 (2026-08-22) ────────────────────────────────────────
#
# 「SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?」가 anchor='일이'를
# 돌려주던 결함. 「일이」는 corp_code 01355031의 실존 법인명이라 similarity가
# 1.000으로 나오고, 「SK하이닉스」의 1.000과 동점에서 이겨 버렸다.
#
# 실측(2026-08-22)으로 밝혀진 원인은 현황서 §4-1의 진단(_MIN_CANDIDATE_LEN=2)과
# 다르다 — 「농심」(00108241)이 2글자 실존 상장사라 상수를 올리면 그쪽이 죽는다.
# 진짜 원인은 둘이다:
#   (a) 조사 잔여물 「일이」가 후보로 살아남는 것
#   (b) ORDER BY score DESC LIMIT 1이 동점에서 무엇을 고를지 정의돼 있지 않은 것

def test_build_candidates_drops_josa_residue_that_is_a_real_corp_name():
    """「일이」는 일/NNG + 이/JKS 라 명사부가 1글자뿐이다 — 어절 통째로 뺀다.

    이걸 남기면 실존 법인 「일이」와 1.000으로 정확히 일치해 진짜 기업명을
    이긴다(§4-1). 명사부가 1글자면 기업명일 가능성보다 조사 잔여물일
    가능성이 압도적이라는 판단이다."""
    candidates = _build_candidates("SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?")
    assert "일이" not in candidates
    assert "SK하이닉스" in candidates


def test_build_candidates_keeps_two_letter_company_name():
    """「농심」(00108241, 004370)은 2글자 실존 상장사다 — 길이로 자르면 안 된다."""
    candidates = _build_candidates("농심에 생산 차질을 일으킬 만한 일이 있었나?")
    assert "농심" in candidates
    assert "일이" not in candidates


def test_build_candidates_keeps_original_word_when_no_josa_tail():
    """Kiwi가 앞 음절을 먹는 오분석(실측 10.4%)에 대비해 원본 어절을 남긴다.

    「상상인」(상상인/NNG)은 조사 꼬리가 없으므로 그대로 후보가 돼야 한다 —
    단독으로 주면 Kiwi가 상상/NNG + 이/VCP + ᆫ/ETM으로 깨뜨리지만, 문장
    문맥에서는 한 덩이로 본다(실측 2026-08-22)."""
    candidates = _build_candidates("상상인에 생산 차질이 있었나?")
    assert "상상인" in candidates


def test_build_candidates_drops_words_whose_noun_part_is_one_letter():
    """조사·어미를 떼고 1글자만 남는 어절은 통째로 빠진다.

        "있었나?" → 있/VV 었/EP 나/EC ?/SF → "있"
        "만한"    → 만/NNB 하/XSA ᆫ/ETM   → "만"

    ★「일으킬」(일으키/VV + ᆯ/ETM → "일으")은 **남는다.** 「명사류 토큰이
    없는 어절은 버린다」는 더 센 규칙도 검토했지만 실측(2026-08-22)에서
    실존 상장사를 떨어뜨렸다 — 조사 붙은 문맥에서 4곳(0.10%), 조사 없는
    문맥에서 17곳(0.43%): 「모다」(모/VV 다/EC)·「이루다」·「이푸른」·
    「한프」. 반면 「일으」는 corp_code_master에 걸리지 않아 기능적 이득이
    0이다. 실존 사명 4곳과 이득 없는 정리를 맞바꾸지 않는다."""
    candidates = _build_candidates("SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?")
    assert "있었나?" not in candidates
    assert "만한" not in candidates
    assert "일이" not in candidates


# ── 다건 후보 반환 · ranking ─────────────────────────────────────────────

def test_extract_picks_company_over_common_noun_homograph(extractor):
    """§4-1 원 사고 질의 — 「일이」가 아니라 「SK하이닉스」여야 한다."""
    assert extractor.extract("SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?") == "SK하이닉스"


def test_extract_picks_two_letter_company_over_common_noun_homograph(extractor):
    """2글자 기업명도 조사 잔여물을 이겨야 한다. 「농심」1.000 vs 「일이」1.000
    은 score만으로도, 후보 길이만으로도 갈리지 않는 완전 동점이었다."""
    assert extractor.extract("농심에 생산 차질을 일으킬 만한 일이 있었나?") == "농심"


def test_extract_picks_company_registered_only_in_english(extractor):
    """「네이버」는 corp_code_master에 'NAVER'(00266961)로만 있어 pg_trgm
    유사도가 0.000이다(한글↔영문은 트라이그램이 원리적으로 못 잇는다).

    company_aliases에는 ('네이버','네이버','NAVER Corporation')이 있으므로
    DART 1차 매칭이 비었을 때만 이 표를 2차 창구로 쓴다. 결함의 본질은
    「네이버가 DB에 없다」가 아니라 「일반명사 일이가 기업명을 이긴다」이므로
    기대값은 None이 아니라 '네이버'다."""
    assert extractor.extract("네이버에 생산 차질을 일으킬 만한 일이 있었나?") == "네이버"


def test_extract_still_returns_none_for_nonexistent_company_with_kiwi(extractor):
    """보조 창구(company_aliases)를 열어도 없는 기업은 여전히 None이어야 한다."""
    assert extractor.extract("존재하지않는기업에 생산 차질이 있었나?") is None
