"""RSS 뉴스 수집 — 본문 전문을 제공하는 매체만.

조사 실측(2026-07-27): 21개 피드 중 6개사가 본문 전문 제공. 나머지는 절단형.
발행사가 배포를 의도한 채널이라 수집 정당성이 가장 높다(네이버 크롤링과 대비).

**본문은 저장하지 않는다** — 관계 추출에만 쓰고 URL만 보관(방법서 §8, 저작권).
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator, Optional

import requests

USER_AGENT = "BizNode-Research/0.1 (academic knowledge-graph project)"
_TIMEOUT = 20

# 본문 전문 제공 확인된 피드 (실측 기반)
FEEDS: dict[str, str] = {
    "뉴시스": "https://newsis.com/RSS/economy.xml",
    "뉴시스산업": "https://newsis.com/RSS/industry.xml",
    "조선비즈": "https://biz.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
    "서울신문": "https://www.seoul.co.kr/xml/rss/rss_economy.xml",
    "데일리안": "https://www.dailian.co.kr/rss/economy",
    "노컷뉴스": "https://rss.nocutnews.co.kr/nocutnews.xml",
    "ZDNet": "https://feeds.feedburner.com/zdkorea",
}

_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# 데일리안 등은 본문 뒤에 관련기사 목록이 붙는다
_TRAILER_RE = re.compile(r"(관련기사|저작권자|무단전재|ⓒ|Copyright).*$", re.S)
# 매체별 말머리/꼬리표 — 같은 기사인데 표기만 다른 경우 dedup을 위해 제거
_TITLE_TAG_RE = re.compile(r"^\s*[\[\(【][^\]\)】]{1,12}[\]\)】]\s*")
_TITLE_SUFFIX_RE = re.compile(r"\s*[\(（](종합|재종합|속보|영상|사진|전문)\d*[\)）]\s*$")


@dataclass
class Article:
    url: str
    title: str
    press: str
    published_at: Optional[datetime]
    body: str              # 관계 추출용 — DB에 저장하지 않음
    source_channel: str = "rss"

    @property
    def title_hash(self) -> str:
        """제목 정규화 해시 — 통신사 전재 중복 제거용.

        같은 기사를 여러 매체가 그대로 싣는다(뉴시스→서울신문→조선비즈).
        안 거르면 같은 관계를 N번 추출해 LLM 비용과 중복 근거가 늘어난다.
        매체마다 붙이는 말머리([단독]·[속보])와 꼬리표(…(종합))를 제거해
        표기가 조금 달라도 같은 기사로 인식한다.
        """
        title = _TITLE_TAG_RE.sub("", self.title)        # [단독]·[속보] 등 제거
        title = _TITLE_SUFFIX_RE.sub("", title)          # (종합)·(재종합) 등 제거
        normalized = _WS_RE.sub("", re.sub(r"[^\w가-힣]", "", title))
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _clean_body(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = _WS_RE.sub(" ", text).strip()
    return _TRAILER_RE.sub("", text).strip()


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_feed(press: str, url: str) -> Iterator[Article]:
    """RSS 한 피드에서 기사를 파싱한다. 실패 시 조용히 빈 이터레이터."""
    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        print(f"  ✗ {press}: 수집 실패 {exc!r}")
        return

    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue

        encoded = item.find(_CONTENT_NS)
        raw_body = encoded.text if encoded is not None and encoded.text else item.findtext("description")
        body = _clean_body(raw_body or "")

        yield Article(
            url=link,
            title=title,
            press=press,
            published_at=_parse_date(item.findtext("pubDate")),
            body=body,
        )


def fetch_all(feeds: Optional[dict[str, str]] = None) -> list[Article]:
    """전 피드 수집 + URL 기준 중복 제거."""
    seen: set[str] = set()
    articles: list[Article] = []
    for press, url in (feeds or FEEDS).items():
        count = 0
        for article in fetch_feed(press, url):
            if article.url in seen:
                continue
            seen.add(article.url)
            articles.append(article)
            count += 1
        print(f"  {press:10} {count:3}건")
    return articles
