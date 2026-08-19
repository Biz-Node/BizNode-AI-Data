"""네이버 검색 API — 관계 기사 발견.

RSS와 달리 쿼리로 관계 기사만 골라 가져온다. "삼성전자 인수" 같은 조합을
던져 무관 기사를 수집 단계에서 차단한다(RSS는 87%를 받아서 버림).

약관 준수: API 응답(description 등)을 DB에 적재하지 않는다.
네이버는 원문 URL(originallink) 발견 용도로만 쓰고, 본문은 언론사 원문에서
크롤링해 확보한다.

한계: 불리언 OR/NOT 미지원(문서 미명시) → 쿼리를 분할해 여러 번 호출한다.
날짜 필터 없음(sort=date로 최신순만). start 최대 1000.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator, Optional

import requests

from app.core.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
from pipeline.extractors.news.rss import Article

_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
_DISPLAY = 100          # 최대 100
_MAX_START = 1000       # API 상한
_DELAY = 0.12           # 호출 간격

_TAG_RE = re.compile(r"<[^>]+>")

# ★검색용 키워드
# 1차 규칙 필터의 85개와 역할이 다르다. 규칙 필터는 "받은 것을 거르는" 무료 작업이라
# 넓을수록 좋지만, 검색은 호출당 비용이 있어 수율 높은 것만 남긴다.
SEARCH_KEYWORDS = (
    # 거래·공급 (최고 수율대: 40~90%)
    "공급계약", "계약체결", "납품", "공급", "수주",
    # 협력 (구체 형태만: 31~76%)
    "MOU", "합작", "제휴",
    # 소유·지배 (17~37%)
    "합병", "매각", "인수", "지분인수", "지분매각",
    # 분쟁 (20~65%)
    "특허침해", "소송",
)

# ★리스크·사건 검색 키워드
RISK_KEYWORDS = (
    # 사고·생산중단 (공급망 리스크의 핵심)
    "화재", "폭발", "정전", "가동중단", "생산차질", "공장중단",
    # 품질·안전
    "리콜", "결함", "불량", "안전사고",
    # 노무
    "파업", "노조", "노동쟁의", "임단협",
    # 규제·수사·법
    "과징금", "제재", "압수수색", "기소", "담합", "공정위", "제재금",
    # 지배구조·주주
    "경영권분쟁", "소액주주", "행동주의", "지분경쟁",
    # 실적·신용
    "어닝쇼크", "적자전환", "신용등급", "유동성위기", "감사의견",
    # 공급망·통상
    "공급망차질", "수출규제", "관세", "수급차질",
)

# ✗ 검색에서 제외한 키워드와 사유 (실측):
#   경쟁·경쟁사(3~4%)  — 어떤 형태로도 노이즈. COMPETES_WITH는 **검색 대상이 아니라
#                        다른 관계 기사에서 부수적으로 추출**하는 편이 현실적이다.
#   협력(6.7%)·협약(4.2%) — "협력 기대감에 주가 강세" 류 시황 기사가 대부분
#   지분(6.7%)·투자(10%)  — 단독어는 흔해서 묻힘 → 복합어(지분인수/지분매각)로 대체
#   제재(9.1%)·과징금(10%)·리콜(0%) — 국내 시드에 사례 자체가 희소
#   ※ 제외한 관계들도 1차 규칙 필터(85개)에는 남아 있어, 수집된 기사 안에서는 잡힌다.


class NaverSearchError(RuntimeError):
    pass


def _clean(text: str) -> str:
    """<b> 하이라이트 태그·HTML 엔티티 제거."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _headers() -> dict[str, str]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise NaverSearchError(
            "NAVER_CLIENT_ID/SECRET이 없습니다. .env를 확인하세요 "
            "(발급: https://developers.naver.com/apps)"
        )
    return {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }


def search(query: str, *, limit: int = 100, sort: str = "date") -> Iterator[Article]:
    """검색어로 기사를 수집한다. 본문은 비어 있고(요약은 저장 안 함) URL 발견이 목적.

    sort: date(최신순) | sim(정확도순)
    """
    fetched = 0
    start = 1
    while fetched < limit and start <= _MAX_START:
        params = {"query": query, "display": min(_DISPLAY, limit - fetched),
                  "start": start, "sort": sort}
        try:
            resp = requests.get(_SEARCH_URL, params=params, headers=_headers(), timeout=20)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except NaverSearchError:
            raise
        except Exception as exc:
            print(f"  ✗ 네이버 검색 실패({query}): {exc!r}")
            return
        time.sleep(_DELAY)

        if not items:
            return

        for item in items:
            # 언론사 원문 URL 우선 — 네이버 페이지는 robots.txt가 AI/RAG를 금지
            url = (item.get("originallink") or "").strip()
            if not url:
                continue
            yield Article(
                url=url,
                title=_clean(item.get("title", "")),
                press="",                     # 원문 도메인에서 유추(크롤링 단계)
                published_at=_parse_date(item.get("pubDate")),
                body="",                      # ★약관 준수: 요약을 저장하지 않는다
                source_channel="naver",
            )
            fetched += 1

        start += len(items)


def search_relations(company_names: list[str], *, per_query: int = 20,
                     keywords: tuple[str, ...] = SEARCH_KEYWORDS) -> list[Article]:
    """시드 기업 × 관계 키워드 조합으로 관계 기사만 수집 (URL 기준 dedup).

    ★따옴표 구문검색이 정확도를 좌우한다(실측 2026-07-27):
        삼성전자 공급계약    → 규칙통과 0/10  (시황·칼럼이 딸려옴)
        "삼성전자 공급계약"  → 규칙통과 10/10 (실제 공급계약만)
    네이버는 공백을 느슨하게 해석하고 `+`·`AND`는 무시하지만, 따옴표는 인접 구문으로
    처리한다. 이 한 줄이 수집 품질을 결정한다.
    """
    seen: set[str] = set()
    articles: list[Article] = []
    total_queries = len(company_names) * len(keywords)

    for i, name in enumerate(company_names, 1):
        before = len(articles)
        for keyword in keywords:
            for article in search(f'"{name} {keyword}"', limit=per_query):
                if article.url in seen:
                    continue
                seen.add(article.url)
                articles.append(article)
        # 시드 단위 진행 표시 — 64개사면 960회 질의라 침묵이 길다
        print(f"    [{i}/{len(company_names)}] {name}: +{len(articles) - before}건 "
              f"(누적 {len(articles)})")

    print(f"    질의 {total_queries}회 → 고유 기사 {len(articles)}건")
    return articles


# ══════════════════════════════════════════════════════════════════
#  증분 갱신 — 아는 기업의 새 기사만
# ══════════════════════════════════════════════════════════════════
#
# ★`search_relations` 와 목적이 다르다.
#
#     search_relations   기업명 × 관계 키워드 → 관계 기사만. 추출용
#     search_latest      기업명만 최신순 → 어제 이후 새 기사. 피드용
#
# ★날짜 필터가 없는 게 여기서는 문제가 아니다. 최신순으로 받다가 **이미 아는
#   URL 을 만나면 그 기업은 거기서 끝**이다. 그게 증분이다.
#
# ★따라잡을 수 있는 폭은 질의당 1,000건(`_MAX_START`)이 정한다.
#   실측(2026-08-18): 삼성전자·SK하이닉스는 하루 100건이 넘어 1,000건이 열흘치다.
#   2~3주 방치하면 구멍이 난다 — 그때는 구글 뉴스 RSS 로 그 기간만 메워야 한다.


def search_latest(names: list[str], known_urls: set[str], *,
                  per_company: int = 100, max_per_company: int = 1000
                  ) -> tuple[list[Article], list[str]]:
    """기업마다 최신순으로 새 기사만. `(새 기사, 못 따라잡은 기업)` 을 준다.

    아는 URL 을 만나면 그 기업은 멈춘다. `max_per_company` 까지 받았는데도
    아는 URL 을 못 만났으면 **그 사이가 비었다는 뜻**이라 이름을 돌려준다.
    """
    fresh: list[Article] = []
    gaps: list[str] = []
    seen: set[str] = set()

    for name in names:
        got = 0
        caught_up = False
        for article in search(name, limit=max_per_company, sort="date"):
            got += 1
            if article.url in known_urls:
                caught_up = True          # 여기부터는 이미 있는 것
                break
            if article.url not in seen:
                seen.add(article.url)
                fresh.append(article)
            if got >= per_company:
                # 상한까지 받았는데 아는 게 안 나왔다 — 더 파야 할 수도 있다.
                # `per_company` 는 평상시 예산이고, 못 따라잡았으면 아래에서 표시한다.
                break
        if not caught_up and got >= per_company:
            gaps.append(name)
    return fresh, gaps
