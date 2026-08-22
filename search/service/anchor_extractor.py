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
- 대신 기존 `%`(similarity) 연산자로 후보마다 같은 GIN 인덱스를 그대로
  타게 한다(15~25ms) — PostgresRepository.match_candidates().

SearchQuery.normalized_query(공백 전부 제거됨)가 아니라 원본 raw_query(공백
보존)를 입력으로 받는다 — orchestrator.py의 _normalize_for_routing 참고.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pipeline.normalizer.generic_names import is_generic_name
from search.repository.postgres_repository import PostgresRepository

# Task 9 실측(2026-08-16): 정답 후보는 1.0(정확 일치) 또는 0.5 이상(부분
# 일치)에 몰리고, 노이즈 어절("기업"→기업은행 0.33, "뉴스"→뉴스1 0.4)은
# 0.33~0.4에 몰린다 — 그 사이 간격이 뚜렷해 EntityResolver의 기존 fuzzy
# threshold(postgres_repository._DEFAULT_FUZZY_THRESHOLD)와 같은 값을 쓴다.
_CONFIDENCE_THRESHOLD = 0.50

# 어절 끝에서 떼어낼 것 — 조사(J*)·어미(E*)·용언파생접미사(XS[AV])·
# 긍정지정사(VCP '이다')·기호(S[FPESOW]). 명사파생접미사(XSN '들'·'님')는
# 넣지 않는다 — 사명 끝 음절을 먹을 수 있는데 질의에서 이득이 거의 없다.
_TAIL_TAGS = frozenset({
    "JKS", "JKC", "JKG", "JKO", "JKB", "JKV", "JKQ", "JX", "JC",
    "EP", "EF", "EC", "ETN", "ETM",
    "XSA", "XSV", "VCP",
    "SF", "SP", "SE", "SS", "SO", "SW",
})

_MIN_CANDIDATE_LEN = 2
# 실측 근거 없는 잠정치 — 비정상적으로 긴 질의에서 DB 배치 쿼리 후보 수를
# 제한하기 위한 안전장치.
_MAX_WORDS = 10


@lru_cache(maxsize=1)
def _kiwi():
    """Kiwi 인스턴스는 로드에 1.3초가 걸린다(실측 2026-08-22) — 프로세스당
    한 번만 만든다. tokenize 자체는 질의당 0.14ms라 요청 경로에 둬도 된다."""
    from kiwipiepy import Kiwi

    return Kiwi()


def _word_spans(raw_query: str) -> list[tuple[str, int]]:
    """어절과 그 시작 오프셋. Kiwi 토큰의 start가 원문 절대 좌표라 필요하다."""
    spans: list[tuple[str, int]] = []
    idx = 0
    for word in raw_query.split()[:_MAX_WORDS]:
        idx = raw_query.index(word, idx)
        spans.append((word, idx))
        idx += len(word)
    return spans


def _strip_tail(word: str, tokens, word_start: int) -> Optional[str]:
    """어절 끝에서 조사·어미로 태깅된 만큼만 잘라낸 나머지. 없으면 None.

    ★Kiwi가 **자른 길이**만 쓰고 형태소의 표면형은 쓰지 않는다. Kiwi는 앞
    음절을 먹는 오분석을 한다(실측 2026-08-22: 상장사 3,979곳 중 413곳,
    10.4% — "제일창업투자"→"일창업투자", "세신"→"신"). 뒤에서만 자르면
    그 오분석이 후보에 닿지 않는다."""
    cut = len(word)
    for token in sorted(tokens, key=lambda t: t.start, reverse=True):
        local_start, local_end = token.start - word_start, token.start + token.len - word_start
        if token.tag in _TAIL_TAGS and local_end >= cut:
            cut = min(cut, local_start)
        else:
            break
    return word[:cut] if 0 <= cut < len(word) else None


def _strip_trailing_josa(word: str) -> Optional[str]:
    """어절 하나만 놓고 조사를 뗀다. 문맥이 없어 문장 경로보다 부정확하다
    (실측: "상상인" 단독 → 상상/NNG + 이/VCP + ᆫ/ETM 으로 깨진다) — 실제
    추출은 _analyze()가 문장 전체를 한 번에 본다. 잘라낸 나머지가 1글자면
    None."""
    stripped = _strip_tail(word, _kiwi().tokenize(word), 0)
    if stripped is None or len(stripped) < _MIN_CANDIDATE_LEN:
        return None
    return stripped


def _analyze(raw_query: str) -> list[tuple[str, Optional[str], bool]]:
    """문장 전체를 Kiwi에 **한 번** 통과시켜 어절마다
    `(어절, 조사를 뗀 명사부 또는 None, 고유명사를 포함하는가)`를 낸다.

    ★어절 단위가 아니라 문장 단위로 돌리는 것이 중요하다(실측 2026-08-22).
    문맥이 있으면 살고 없으면 깨지는 사명이 많다:
        "상상인 최근 실적"    → 상상/NNG 이/VCP ᆫ/ETM      ✗
        "상상인에 생산 차질을"  → 상상인/NNG 에/JKB          ○
    """
    spans = _word_spans(raw_query)
    if not spans:
        return []
    grouped: dict[int, list] = {}
    for token in _kiwi().tokenize(raw_query):
        if token.word_position < len(spans):
            grouped.setdefault(token.word_position, []).append(token)
    out = []
    for i, (word, start) in enumerate(spans):
        tokens = grouped.get(i, [])
        noun_part = _strip_tail(word, tokens, start) if tokens else None
        out.append((word, noun_part, any(t.tag == "NNP" for t in tokens)))
    return out


def _candidates_of(word: str, noun_part: Optional[str]) -> list[str]:
    """어절 하나가 내놓는 후보들. 조사를 떼고 남은 명사부가 1글자 이하면
    **원본 어절까지 통째로 버린다** — 이것이 §4-1의 직격 지점이다.

        "일이"  →  일/NNG + 이/JKS  →  명사부 "일"(1글자)  →  후보 없음
                   남겨 두면 실존 법인 「일이」(01355031)와 1.000으로 정확히
                   일치해 「SK하이닉스」·「농심」의 1.000을 동점에서 이긴다.

    반대로 조사 꼬리가 없으면 원본을 그대로 둔다 — Kiwi 오분석(10.4%)에
    대한 안전망이라 여기서 기존 exact match 경로가 그대로 살아 있다."""
    if noun_part is not None and len(noun_part) < _MIN_CANDIDATE_LEN:
        return []
    return [c for c in (word, noun_part) if c]


def _build_candidates(raw_query: str) -> list[str]:
    """질의에서 기업명 후보들을 만든다. 일반명사(generic_names)를 걸러낸 뒤
    순서를 보존해 중복 제거한다."""
    candidates: list[str] = []
    seen: set[str] = set()
    for word, noun_part, _ in _analyze(raw_query):
        for candidate in _candidates_of(word, noun_part):
            if len(candidate) < _MIN_CANDIDATE_LEN:
                continue
            if is_generic_name(candidate):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


class AnchorExtractor:
    def __init__(self, repo: Optional[PostgresRepository] = None):
        self._repo = repo or PostgresRepository()

    def extract(self, raw_query: str) -> Optional[str]:
        """문장에서 기업명 후보 1개를 뽑는다. 확신(threshold 이상) 없으면
        None — 호출부는 None을 "원문을 그대로 쓰라"는 신호로 해석한다."""
        analysis = _analyze(raw_query)
        candidates = _build_candidates(raw_query)
        if not candidates:
            return None

        matches = self._repo.match_candidates(candidates, threshold=_CONFIDENCE_THRESHOLD)
        if matches:
            # score 내림차순 → 후보 길이 내림차순. 길이가 tie-break인 이유는
            # 1.000 동점이 실제로 나기 때문이다("농심" vs "일이" 둘 다 실존
            # 법인). 저장소의 ORDER BY는 동점 순서를 정의하지 않아 물리적
            # 행 순서에 좌우됐다.
            return max(matches, key=lambda m: (m[2], len(m[0])))[0]

        # DART 1차가 비었을 때만 별칭 표에 묻는다. pg_trgm이 한글↔영문을
        # 원리적으로 못 잇기 때문이다 — similarity('NAVER','네이버')=0.000.
        # ★Kiwi가 고유명사(NNP)로 본 후보만 넘긴다. 3글자 이하 별칭 523개 중
        # 215개를 Kiwi가 일반명사로 읽어("기타"·"대상"·"디스코") 문을 활짝
        # 열면 일상어가 기업으로 둔갑한다(실측 2026-08-22).
        proper: list[str] = []
        for word, noun_part, has_proper in analysis:
            if has_proper:
                proper.extend(_candidates_of(word, noun_part))
        if not proper:
            return None
        return self._repo.alias_exact_match(proper)
