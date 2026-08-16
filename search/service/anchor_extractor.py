"""AnchorExtractor — 자연어 질의 문장에서 기업명(anchor) 후보를 추출한다.

Task 9. SearchOrchestrator가 "삼성전자에 납품하는 기업" 같은 서술어 붙은
문장을 원문 그대로 EntityResolver.resolve_candidates()에 넘기면 pg_trgm fuzzy
threshold(0.50)를 못 넘어 해소 실패 → GraphSearcher가 anchor 없는 전역 top-N
경로로 빠지는 버그(현황서 §3-1)를 고친다.

**안전 원칙(정밀도 우선)**: 확신 없으면 추출하지 않는다(None) — 잘못
추출해 엉뚱한 기업에 매칭되는 게, anchor 없이 정직하게 무관한 결과를 내는
것보다 나쁘다.

접근법 선정 근거(Task 9 실측, 2026-08-16) — 상세는
docs/superpowers/plans/2026-08-16-anchor-extraction.md 참고:
- word_similarity()/`<%` 연산자는 corp_code_master(118,535건)에서 GIN
  트라이그램 인덱스를 타지 않고 Seq Scan(400~800ms)으로 떨어져 폐기했다.
- 대신 기존 `%`(similarity) 연산자를 `corp_name % ANY(candidates)` 형태로
  묶으면 같은 GIN 인덱스를 그대로 타면서(15~25ms) 어절 후보 전체를 단일
  쿼리로 처리할 수 있다(PostgresRepository.best_candidate_match()).

SearchQuery.normalized_query(공백 전부 제거됨)가 아니라 원본 raw_query(공백
보존)를 입력으로 받는다 — orchestrator.py의 _normalize_for_routing 참고.
"""

from __future__ import annotations

from typing import Optional

from pipeline.normalizer.generic_names import is_generic_name

# Task 9 실측(2026-08-16): 정답 후보는 1.0(정확 일치) 또는 0.5 이상(부분
# 일치)에 몰리고, 노이즈 어절("기업"→기업은행 0.33, "뉴스"→뉴스1 0.4)은
# 0.33~0.4에 몰린다 — 그 사이 간격이 뚜렷해 EntityResolver의 기존 fuzzy
# threshold(postgres_repository._DEFAULT_FUZZY_THRESHOLD)와 같은 값을 쓴다.
_CONFIDENCE_THRESHOLD = 0.50

# 긴 조사부터 검사해야 부분 절단을 피한다("에게"를 "에"보다 먼저).
_TRAILING_JOSA = (
    "에게", "에서", "으로", "부터", "까지",
    "은", "는", "이", "가", "을", "를", "에", "로", "의", "도", "만", "과", "와",
)

_MIN_CANDIDATE_LEN = 2
# 실측 근거 없는 잠정치 — 비정상적으로 긴 질의에서 DB 배치 쿼리 후보 수를
# 제한하기 위한 안전장치.
_MAX_WORDS = 10


def _strip_trailing_josa(word: str) -> Optional[str]:
    """끝에 붙은 조사로 보이는 부분을 잘라낸다. 매칭 조사가 없거나 잘라낸
    나머지가 너무 짧으면(1글자) None — 문법 분석이 아니라 휴리스틱이므로
    잘못 잘라도(예: "만드는" -> "만드") DB 존재 확인 단계에서 threshold로
    걸러진다(정밀도는 threshold가 보장, 여기서는 재현율만 넓힌다)."""
    for josa in _TRAILING_JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= _MIN_CANDIDATE_LEN:
            return word[: -len(josa)]
    return None


def _build_candidates(raw_query: str) -> list[str]:
    """어절(공백 분리) 단위로 원본 + 조사 제거 후보를 만들고, 일반명사
    (pipeline.normalizer.generic_names)를 걸러낸 뒤 순서를 보존해 중복
    제거한다."""
    words = raw_query.split()[:_MAX_WORDS]
    candidates: list[str] = []
    seen: set[str] = set()
    for word in words:
        for candidate in (word, _strip_trailing_josa(word)):
            if candidate is None or len(candidate) < _MIN_CANDIDATE_LEN:
                continue
            if is_generic_name(candidate):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates
