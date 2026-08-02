"""구글 뉴스 RSS 수집 — **기간 제약을 푸는 경로**.

네이버 검색 API는 `start` 상한이 1,000이라 대형주는 하루치밖에 못 본다
(실측: `"삼성전자"` 1,000건 → 커버 0일). 기업의 사건 이력을 쌓으려면 과거를
훑어야 하는데, 그게 안 되면 P2의 목적 자체가 성립하지 않는다.

구글 뉴스 RSS는 두 가지를 준다 (실측 2026-07-28):
  · **날짜 범위 지정** `after:` / `before:` → 분기로 쪼개면 상한을 우회한다
  · **OR 연산자** → 검색어를 묶어 질의 수를 1/N로 줄인다 (네이버는 OR 미지원)

한계와 대응:
  · 원문 URL을 안 준다 — 링크가 JS 리다이렉트라 안 풀리고 base64 디코딩도 실패한다.
    → 제목을 네이버에서 재검색해 `originallink`를 얻는다 (실측 80% 성공).
  · 질의당 100건 상한 — 대형주는 분기로도 걸린다. `month_split=True`로 더 쪼갠다.
"""

from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator, Optional
from urllib.parse import quote

import requests

from app.core.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
from pipeline.extractors.news.rss import Article

_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
_NAVER = "https://openapi.naver.com/v1/search/news.json"
_UA = {"User-Agent": "BizNode-Research/0.1 (academic knowledge-graph project)"}
# ★2026-07-31: 0.4초로 2시간 반에 5,000회 넘게 던져 503 차단을 당했다.
#   월분할은 기업당 480질의가 필요한데(실측상 이게 회수량이 가장 많다),
#   그 요청 수를 유지하려면 **속도를 낮추는 수밖에 없다**.
#
#   1.2초로 올려 3개사(1,440질의)를 503 없이 통과시켰다. 다만 그게 속도 덕인지
#   누적량이 적어서인지는 구분되지 않았다 — 구글의 실제 한도는 공개돼 있지 않다.
#
#   그래서 0.8초로 되돌린다. 근거는 **손실이 제한적이 됐다는 것**이다:
#   이제 503 감지·지수 백오프·배치 중단이 붙어 있어, 차단되더라도 심텍 때처럼
#   73분을 버리지 않고 즉시 멈춘다. 안전장치가 있으니 조금 더 공격적으로 간다.
#
#   실측 참고 — HTTP 왕복 자체가 2.4초라 딜레이는 질의당 시간의 3분의 1뿐이다:
#       0.4초 → 기업당 22분 (차단당한 설정)
#       0.8초 → 기업당 26분
#       1.2초 → 기업당 29분
_DELAY = 0.8          # 구글 요청 간격
_NAVER_DELAY = 0.12
_PER_QUERY_CAP = 100  # 구글 RSS 질의당 상한(관측값)

# ── 검색어 묶음 — 온톨로지 축별 (방법서 §12 / P2 계획서 §5-3) ─────────
#
# 개별 질의로 돌리면 65종 × 12분기 = 780회/기업이 되어 과다하다.
# 구글은 OR을 지원하므로 **축 단위로 묶어** 5회 × 12분기 = 60회로 줄인다.
#
# ★묶음 안에서 100건 상한을 나눠 쓰므로, 흔한 키워드가 드문 키워드를 밀어낼 수 있다.
#   그래서 수율이 크게 다른 것끼리는 묶지 않는다(공급계약 90% ↔ 경쟁 3%는 분리).
ONTOLOGY_GROUPS: dict[str, str] = {
    # SUPPLIES_TO · DEPENDS_ON
    "거래공급": "공급계약 OR 납품 OR 수주 OR 공급 OR 계약체결 OR 독점공급 OR 국산화",
    # OWNS_STAKE_IN · ACQUIRES · PARTNERS_WITH
    "소유협력": "인수 OR 합병 OR 매각 OR 지분인수 OR 유상증자 OR MOU OR 합작 OR 제휴",
    # SUES · REGULATES
    "분쟁규제": "소송 OR 특허침해 OR 과징금 OR 제재 OR 압수수색 OR 담합 OR 공정위",
    # HAS_EVENT · IMPACTS (사고·노무)
    "사고노무": "화재 OR 폭발 OR 가동중단 OR 리콜 OR 파업 OR 노동쟁의 OR 안전사고 OR 결함",
    # HAS_EVENT · IMPACTS (실적·공급망)
    "실적공급망": "어닝쇼크 OR 적자전환 OR 신용등급 OR 공급망차질 OR 수출규제 OR 감산",
    # ★COMPETES_WITH — 수율 3~4%지만 **뉴스가 유일 경로**라 수율과 무관하게 검색한다
    #   (계획서 §5-2: 낮은 수율은 대체 소스가 있을 때만 배제 근거)
    "경쟁": "경쟁사 OR 점유율 OR 맞대결 OR 추격",
    # DEVELOPS · Product 노드
    "제품설비": "신제품 OR 양산 OR 신공장 OR 증설 OR 개발",
    # IS_EXECUTIVE_OF · Person 노드
    "인물": "대표이사 OR 회장 취임 OR 사장 선임 OR 임원 인사",
}

_TITLE_SRC_RE = re.compile(r"\s+-\s+[^-]+$")   # "제목 - 매체명" 꼬리 제거


class GNewsError(RuntimeError):
    pass


# ── 기간 분할 ────────────────────────────────────────────────────
def quarters(years: int, *, end: Optional[date] = None) -> list[tuple[str, str]]:
    """최근 `years`년을 분기 경계로 쪼갠다. [(after, before), ...] 최신순."""
    end = end or date.today()
    out: list[tuple[str, str]] = []
    cur = date(end.year, ((end.month - 1) // 3) * 3 + 1, 1)
    for _ in range(years * 4):
        nxt = date(cur.year + 1, 1, 1) if cur.month == 10 else \
            date(cur.year, cur.month + 3, 1)
        out.append((cur.isoformat(), nxt.isoformat()))
        cur = date(cur.year - 1, 10, 1) if cur.month == 1 else \
            date(cur.year, cur.month - 3, 1)
    return out


def months(years: int, *, end: Optional[date] = None) -> list[tuple[str, str]]:
    """대형주용 — 분기로도 100건 상한에 걸릴 때 월 단위로 쪼갠다."""
    end = end or date.today()
    out: list[tuple[str, str]] = []
    cur = date(end.year, end.month, 1)
    for _ in range(years * 12):
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else \
            date(cur.year, cur.month + 1, 1)
        out.append((cur.isoformat(), nxt.isoformat()))
        cur = date(cur.year - 1, 12, 1) if cur.month == 1 else \
            date(cur.year, cur.month - 1, 1)
    return out


# ── 수집 ─────────────────────────────────────────────────────────
def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class RateLimited(RuntimeError):
    """구글이 연속으로 거절했다 — 물러나야 한다."""


# ★2026-07-31: 구글이 503으로 차단했다. 심텍의 480개 질의가 **전부** 실패했는데
#   멈추지 않고 계속 던져 73분을 버리고 수집 0건으로 끝났다.
#   원인은 2시간 반 동안 5,000회 넘게 질의한 것. 아래 셋으로 대응한다:
#     1) 실패가 연속되면 **중단**한다(무한히 던지지 않는다)
#     2) 실패마다 대기를 늘린다(지수 백오프)
#     3) 실패한 질의를 **기록**한다 — 지금까지는 화면에만 찍히고 사라졌다
_MAX_CONSECUTIVE_FAIL = 8
_BACKOFF_BASE = 3.0        # 초. 실패마다 ×2

_consecutive_fail = 0
FAILED_QUERIES: list[str] = []      # 실행 중 누적 — 나중에 재시도할 목록


def _months_in(lo: str, hi: str) -> list[tuple[str, str]]:
    """분기 구간 [lo, hi) 를 월 구간들로 쪼갠다. 적응형 분할에서 쓴다."""
    y, m = int(lo[:4]), int(lo[5:7])
    out: list[tuple[str, str]] = []
    while True:
        cur = f"{y:04d}-{m:02d}-01"
        if cur >= hi:
            break
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((cur, min(f"{ny:04d}-{nm:02d}-01", hi)))
        y, m = ny, nm
    return out


def _fetch(query: str) -> list[Article]:
    """구글 뉴스 RSS 1회 질의 → Article 목록 (url은 구글 리다이렉트, 본문 없음).

    연속 실패가 `_MAX_CONSECUTIVE_FAIL`을 넘으면 `RateLimited`를 올린다.
    호출자가 잡아서 그 기업을 건너뛰거나 배치를 멈춰야 한다.
    """
    global _consecutive_fail
    try:
        resp = requests.get(_RSS.format(q=quote(query)), headers=_UA, timeout=25)
        if resp.status_code != 200:
            _consecutive_fail += 1
            FAILED_QUERIES.append(query)
            print(f"    ✗ 구글 HTTP {resp.status_code} "
                  f"(연속 {_consecutive_fail}): {query[:46]}")
            if _consecutive_fail >= _MAX_CONSECUTIVE_FAIL:
                raise RateLimited(
                    f"구글이 {_consecutive_fail}회 연속 거절 (HTTP {resp.status_code}). "
                    f"속도 제한으로 보입니다 — 시간을 두고 재개하세요.")
            # 지수 백오프 — 한 번 막히면 곧바로 다시 던지지 않는다
            time.sleep(min(_BACKOFF_BASE * (2 ** (_consecutive_fail - 1)), 60))
            return []
        root = ET.fromstring(resp.content)
        _consecutive_fail = 0                      # 성공하면 초기화
    except RateLimited:
        raise
    except Exception as exc:
        _consecutive_fail += 1
        FAILED_QUERIES.append(query)
        print(f"    ✗ 구글 질의 실패 (연속 {_consecutive_fail}): {exc!r}")
        if _consecutive_fail >= _MAX_CONSECUTIVE_FAIL:
            raise RateLimited(f"구글 질의가 {_consecutive_fail}회 연속 실패")
        time.sleep(min(_BACKOFF_BASE * (2 ** (_consecutive_fail - 1)), 60))
        return []
    finally:
        time.sleep(_DELAY)

    out: list[Article] = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        out.append(Article(
            url=(item.findtext("link") or "").strip(),      # 구글 리다이렉트(임시)
            title=_TITLE_SRC_RE.sub("", raw_title).strip(),
            press=(item.findtext("source") or "").strip(),
            published_at=_parse_date(item.findtext("pubDate")),
            body="",                                        # 구글은 본문 미제공
            source_channel="gnews",
        ))
    return out


def collect_company(name: str, *, years: int = 3, month_split: bool = False,
                    groups: Optional[dict[str, str]] = None,
                    verbose: bool = True) -> list[Article]:
    """한 기업의 뉴스를 기간 분할로 수집한다. 제목 해시로 중복 제거.

    month_split: 분기 질의가 상한(100)에 자주 걸리는 대형주에 사용.
    """
    groups = groups or ONTOLOGY_GROUPS
    seen: set[str] = set()
    out: list[Article] = []
    capped = 0

    def take(arts: list[Article]) -> int:
        added = 0
        for a in arts:
            h = a.title_hash
            if h not in seen:
                seen.add(h)
                out.append(a)
                added += 1
        return added

    # ── 기간 분할 ──────────────────────────────────────────────
    #
    # ★적응형 분할(분기로 먼저 질의하고 상한에 걸린 분기만 월로 쪼개기)을
    #   시도했다가 **실측으로 반박되어 되돌렸다** (2026-07-31).
    #
    #   동기는 옳았다: 전 기업 월분할이 기업당 480질의를 만들어 구글이 503으로
    #   차단했고, 심텍은 480질의가 전부 실패해 73분을 버리고 0건으로 끝났다.
    #
    #   가정: 「질의가 상한(100) 미만을 돌려주면 구글이 가진 전부다」
    #   → 그러면 분기 결과와 월 합집합이 같아야 한다.
    #
    #   실측 (같은 기업·같은 기간·같은 키워드묶음으로 대조):
    #       가온칩스 2025-Q4   분기 19 · 월 합집합 19   → 일치 ✅
    #       DB하이텍 2026-Q1   분기 31 · 월 합집합 33   → **월에만 9 · 분기에만 7**
    #
    #   31건은 상한에 한참 못 미치는데도 결과가 갈렸다. 구글은 상한과 무관하게
    #   **질의마다 다른 부분집합**을 돌려준다(기간이 좁으면 그 안에서 다시 추리는 듯).
    #   놓친 것도 하필 「소액주주 은닉지분」「국민성장펀드 무산」처럼 우리가 원하는
    #   리스크 기사였다.
    #
    #   그래서 월분할을 기본으로 되돌리고, 차단은 **속도**로 피한다(`_DELAY`).
    #   ※ 월분할도 완전하지 않다 — 분기에만 있던 7건은 월 질의가 놓쳤다.
    #     어느 한 방식도 전부를 주지 않으므로, 회수량이 많은 쪽을 택한 것이다.
    periods = months(years) if month_split else quarters(years)
    for lo, hi in periods:
        before = len(out)
        for gname, kws in groups.items():
            arts = _fetch(f"{name} ({kws}) after:{lo} before:{hi}")
            if len(arts) >= _PER_QUERY_CAP:
                capped += 1
            take(arts)
        if verbose:
            print(f"    {lo[:7]} → +{len(out) - before:>3}건 (누적 {len(out)})")

    if capped and verbose:
        print(f"    ⚠ 질의 {capped}건이 상한({_PER_QUERY_CAP})에 걸림 — "
              f"{'월' if month_split else '분기'} 분할로도 부족")
    return out


# ── 원문 URL 해석 (네이버 제목 재검색) ────────────────────────────
def _naver_headers() -> dict[str, str]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise GNewsError("NAVER_CLIENT_ID/SECRET 없음 — URL 해석에 필요합니다")
    return {"X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def resolve_url(article: Article) -> Optional[str]:
    """제목을 네이버에서 재검색해 언론사 원문 URL을 얻는다. 실패 시 None.

    구글이 **찾고** 네이버가 **주소를 알려주는** 분업이다.
    앞부분 12자가 일치하면 같은 기사로 본다(매체별 말머리 차이 흡수).
    """
    try:
        resp = requests.get(_NAVER, params={"query": article.title, "display": 3,
                                            "sort": "sim"},
                            headers=_naver_headers(), timeout=20)
        items = resp.json().get("items", []) if resp.status_code == 200 else []
    except Exception:
        return None
    finally:
        time.sleep(_NAVER_DELAY)

    key = article.title[:12]
    for cand in items:
        title = _strip_tags(cand.get("title", ""))
        if title[:12] == key or key in title:
            return (cand.get("originallink") or cand.get("link") or "").strip() or None
    return None


def resolve_urls(articles: list[Article], *, verbose: bool = True) -> list[Article]:
    """URL이 해석된 기사만 돌려준다(구글 리다이렉트는 크롤링할 수 없다)."""
    out: list[Article] = []
    for i, a in enumerate(articles, 1):
        url = resolve_url(a)
        if url:
            a.url = url
            out.append(a)
        if verbose and i % 50 == 0:
            print(f"    URL 해석 {i}/{len(articles)} → 성공 {len(out)}")
    if verbose:
        print(f"    → 해석 {len(out)}/{len(articles)} "
              f"({len(out)/max(len(articles),1)*100:.0f}%)")
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    target = sys.argv[1] if len(sys.argv) > 1 else "한미반도체"
    n_years = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print(f"{target} 최근 {n_years}년 수집")
    arts = collect_company(target, years=n_years)
    print(f"\n총 {len(arts)}건 (제목 중복 제거 후)")
    ds = sorted(a.published_at for a in arts if a.published_at)
    if ds:
        print(f"기간 {ds[0].date()} ~ {ds[-1].date()}")
