"""언론사 원문 본문 크롤러 — robots.txt 준수.

네이버 검색 API는 URL만 주므로 본문을 직접 확보해야 한다. **네이버 페이지가 아니라
언론사 원문(originallink)** 을 대상으로 한다 — 네이버 robots.txt는 AI/RAG 용도를
명시적으로 금지하지만, 언론사 자체 사이트는 대개 그런 조항이 없다.

준수 사항:
  · robots.txt 사전 확인 (도메인별 캐시)
  · ClaudeBot/anthropic-ai를 명시 차단하는 매체는 화이트리스트에서 제외
  · 요청 간 딜레이, User-Agent에 연락 가능 정보 명시
  · **본문 저장 금지** — 관계 추출에만 쓰고 URL만 보관(방법서 §8)
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

import requests
import trafilatura

USER_AGENT = "BizNode-Research/0.1 (academic knowledge-graph project; contact via project repo)"
_TIMEOUT = 15
_DELAY = 1.2          # **같은 도메인** 요청 간 딜레이(초) — 서버 부하 방지
_MIN_BODY = 200       # 이보다 짧으면 추출 실패로 간주
_WORKERS = 8          # 동시 크롤링 매체 수(도메인 간 병렬, 도메인 내 순차)

# 도메인별 락 — 같은 매체로는 한 번에 하나만 나가게 한다.
_domain_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _domain_lock(domain: str) -> threading.Lock:
    with _locks_guard:
        return _domain_locks[domain]

# robots.txt에서 ClaudeBot/anthropic-ai를 명시 차단하는 매체 (실측 2026-07-27)
# 크롤링 대상에서 원천 제외한다.
BLOCKED_DOMAINS = {
    "yna.co.kr", "yonhapnews.co.kr",     # 연합뉴스
    "hani.co.kr",                         # 한겨레
    "khan.co.kr",                         # 경향신문
    "mk.co.kr",                           # 매일경제
    "kbs.co.kr", "news.kbs.co.kr",        # KBS
    "naver.com", "n.news.naver.com",      # 네이버 (AI/RAG 명시 금지)
}


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _base_domain(url: str) -> str:
    """서브도메인을 제외한 등록 도메인(mk.co.kr 등) — 차단 목록 대조용."""
    parts = _domain(url).split(".")
    return ".".join(parts[-3:]) if len(parts) >= 3 and parts[-2] in {"co", "or", "go"} \
        else ".".join(parts[-2:])


@lru_cache(maxsize=256)
def _robots(domain: str) -> Optional[urllib.robotparser.RobotFileParser]:
    """도메인별 robots.txt 파서(캐시). 실패 시 None(보수적으로 허용 안 함)."""
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"https://{domain}/robots.txt")
    try:
        parser.read()
        return parser
    except Exception:
        return None


def is_allowed(url: str) -> tuple[bool, str]:
    """크롤링 허용 여부. (허용, 사유)"""
    if _base_domain(url) in BLOCKED_DOMAINS or _domain(url) in BLOCKED_DOMAINS:
        return False, "AI 크롤러 차단 매체"

    parser = _robots(_domain(url))
    if parser is None:
        return False, "robots.txt 확인 불가"
    if not parser.can_fetch(USER_AGENT, url):
        return False, "robots.txt 비허용"
    return True, ""


def fetch_body(url: str, *, delay: float = _DELAY) -> Optional[str]:
    """언론사 원문에서 본문 텍스트를 추출한다. 실패 시 None.

    반환값은 **저장하지 않는다** — 관계 추출에 쓰고 폐기한다.
    """
    allowed, reason = is_allowed(url)
    if not allowed:
        return None

    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception:
        return None
    finally:
        time.sleep(delay)

    # trafilatura: 광고·메뉴·관련기사를 걷어내고 본문만 추출
    # favor_recall — 한국 뉴스는 문단 구조가 단순해 정밀도보다 회수율이 유리(실측)
    body = trafilatura.extract(
        resp.text, include_comments=False, include_tables=False, favor_recall=True
    )
    if not body or len(body) < _MIN_BODY:
        return None
    return body.strip()


def enrich_bodies(articles: list, *, limit: Optional[int] = None,
                  workers: int = _WORKERS) -> tuple[int, int]:
    """Article의 body를 크롤링으로 보강한다(제자리 수정). (개선, 실패) 반환.

    RSS 본문이 있어도 **크롤링이 더 길면 교체**한다 — 실측상 매체마다 우열이 갈린다:
      · 뉴시스: RSS 8자 vs 크롤링 997자   → 크롤링 승
      · 조선비즈: RSS 745자 vs 크롤링 0자(SPA) → RSS 승
    둘 중 긴 쪽을 쓰는 게 안전하다.

    ★도메인별로는 순차, 매체 간에는 병렬.
      1.2초 지연은 **한 서버**를 과부하시키지 않으려는 것이지 전역 속도 제한이 아니다.
      매체가 수십 곳으로 흩어지는데 전부 순차로 돌면 시드 64개 확장 시 1시간을 넘긴다.
      도메인마다 락을 걸어 예의는 지키고, 서로 다른 매체는 동시에 가져온다.
    """
    targets = articles[:limit] if limit else articles
    improved = failed = 0

    def _one(article) -> bool:
        """(개선 여부). 같은 도메인 요청은 락으로 직렬화 + 지연 유지."""
        with _domain_lock(_domain(article.url)):
            body = fetch_body(article.url)
        if not article.press:
            article.press = _domain(article.url)
        if body and len(body) > len(article.body or ""):
            article.body = body
            return True
        return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for article, ok in zip(targets, pool.map(_one, targets)):
            if ok:
                improved += 1
            elif not article.body:
                failed += 1
    return improved, failed
