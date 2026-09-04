"""구글 뉴스 RSS 수집.

네이버 검색 API는 `start` 상한이 1,000이라 과거 기사 수집이 어렵다.

구글 뉴스 RSS는 두 가지를 준다:
  · 날짜 범위 지정 `after:` / `before:` → 분기로 쪼개면 상한을 우회한다
  · OR 연산자 → 검색어를 묶어 질의 수를 1/N로 줄인다

한계와 대응:
  · 원문 URL을 안 준다 — 링크가 JS 리다이렉트라 안 풀리고 base64 디코딩도 실패한다.
    → 제목을 네이버에서 재검색해 `originallink`를 얻는다 (실측 80% 성공).
  · 질의당 100건 상한 — 대형주는 분기로도 걸린다. `month_split=True`로 더 쪼갠다.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from app.core.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
from pipeline.extractors.news.rss import Article

_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
_NAVER = "https://openapi.naver.com/v1/search/news.json"
_UA = {"User-Agent": "BizNode-Research/0.1 (academic knowledge-graph project)"}

_DELAY = float(os.getenv("GNEWS_DELAY", "2.5"))

# 구글 요청 간격에 유동성을 주어 봇 탐지를 최대한 회피.
_JITTER = float(os.getenv("GNEWS_JITTER", "0.6"))   # ±비율. 0이면 고정 간격


def _sleep_gap() -> float:
    """이번 요청 뒤 쉴 시간 — 기준값 ±60%로 흔든다(하한 0.4초)."""
    if _JITTER <= 0:
        return _DELAY
    return max(0.4, _DELAY * (1.0 + random.uniform(-_JITTER, _JITTER)))
_NAVER_DELAY = 0.12
_PER_QUERY_CAP = 100  # 구글 RSS 질의당 상한(관측값)

# ── 검색어 묶음 — 온톨로지 축별 ──
# 개별 질의로 돌리면 65종 × 12분기 = 780회/기업이 되어 과다하다.
# 구글은 OR을 지원하므로 연관있는 키워드를 축 단위로 묶어** 5회 × 12분기 = 60회로 줄인다.

# 검색 대상 키워드
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


# ── 기간 분할 ──
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


# ── 수집 ──
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


# ── 차단 시각을 남긴다 ──
_BLOCK_FILE = Path(__file__).resolve().parents[3] / "data" / "state" / "google_block.json"
_MIN_WAIT_HOURS = 3.0         # 실측 잠금 해제 1.3~2.2시간 + 여유


def record_block() -> None:
    """503 차단을 파일에 남긴다 — 다음 실행이 읽는다."""
    try:
        _BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        hist = []
        if _BLOCK_FILE.exists():
            hist = json.loads(_BLOCK_FILE.read_text(encoding="utf-8")).get("blocks", [])
        hist.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        _BLOCK_FILE.write_text(
            json.dumps({"blocks": hist[-20:]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as exc:                       # 기록 실패로 수집을 막지 않는다
        print(f"    (차단 시각 기록 실패: {exc!r})")


def hours_since_block() -> float | None:
    """마지막 503 차단 이후 몇 시간 지났나. 기록이 없으면 None."""
    try:
        hist = json.loads(_BLOCK_FILE.read_text(encoding="utf-8")).get("blocks", [])
        if not hist:
            return None
        last = datetime.fromisoformat(hist[-1])
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return None



# 실패가 연속되면 중단한다
# 실패마다 대기를 늘린다(지수 백오프)
# 실패한 질의를 기록한다
_MAX_CONSECUTIVE_FAIL = 8
_BACKOFF_BASE = 3.0        # 초. 실패마다 ×2

_consecutive_fail = 0
FAILED_QUERIES: list[str] = []      # 실행 중 누적 — 나중에 재시도할 목록


# ── 중간 저장 · 이어받기 ──
# 480질의를 한 번에 던지지 않고 진행한만큼 저장한다.
_CKPT_DIR = Path(__file__).resolve().parents[3] / "data" / "state" / "collect"


def _ckpt_path(name: str, years: int, month_split: bool) -> Path:
    """설정이 다르면 다른 파일 — 5년치 중간본을 3년 실행이 이어받으면 안 된다."""
    key = re.sub(r"[^\w가-힣]", "_", name)
    return _CKPT_DIR / f"{key}__{years}y_{'m' if month_split else 'q'}.json"


def _ckpt_load(path: Path) -> tuple[set[str], list[Article], int]:
    """(본 제목해시, 모은 기사, 끝낸 기간 수). 없으면 빈 상태."""
    if not path.exists():
        return set(), [], 0
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        arts = [Article(url=a["url"], title=a["title"], press=a["press"],
                        published_at=(datetime.fromisoformat(a["published_at"])
                                      if a.get("published_at") else None),
                        body="",                      # 본문은 저장하지 않는다(§8 저작권)
                        source_channel=a.get("source_channel", "gnews"))
                for a in d.get("articles", [])]
        return {a.title_hash for a in arts}, arts, int(d.get("done_periods", 0))
    except Exception as exc:
        print(f"    (중간본을 읽지 못했습니다 — 처음부터 갑니다: {exc!r})")
        return set(), [], 0


def _ckpt_save(path: Path, arts: list[Article], done: int, total: int,
               collected_all: bool = False) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "done_periods": done, "total_periods": total,
            "collected_all": collected_all,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "articles": [{"url": a.url, "title": a.title, "press": a.press,
                          "published_at": (a.published_at.isoformat()
                                           if a.published_at else None),
                          "source_channel": a.source_channel} for a in arts],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"    (중간 저장 실패: {exc!r})")


def clear_checkpoint(name: str, years: int, month_split: bool) -> None:
    """기사가 DB에 들어간 뒤에만 부른다."""
    p = _ckpt_path(name, years, month_split)
    if p.exists():
        p.unlink()


# 구글 `/sorry` 페이지의 표지. 응답이 RSS(XML)가 아니라 HTML로 오고, 본문에
# 「automated queries」가 들어 있다. 문구가 바뀌어도 **XML이 아닌 것**만으로도
# 걸러지도록 둘 중 하나만 맞아도 봇 차단으로 본다.
_BOT_MARKERS = ("automated queries", "unusual traffic", "/sorry/")


def _is_bot_block(resp) -> bool:
    """구글이 「자동화 질의」로 판정한 응답인가 — 일시적 오류와 구분한다."""
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype.lower():
        return False                       # RSS가 오다 만 것 — 일시적일 수 있다
    body = resp.text[:4000].lower()
    return any(m in body for m in _BOT_MARKERS) or "/sorry" in resp.url


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
            # ★「봇으로 판정됨」과 「일시적 오류」를 가른다(2026-08-03).
            #   구글이 돌려주는 본문을 열어 보니 속도 제한이 아니라 **봇 탐지**였다:
            #       HTTP 503 · "We're sorry... but your computer or network
            #        may be sending automated queries."
            #   브라우저에서 CAPTCHA가 뜨는 그 `/sorry` 페이지다. 이건 기다린다고
            #   다음 질의가 통과하지 않는데, 지금까지는 일반 실패로 보고 8회까지
            #   3→6→12→24→48→60→60초씩 **3.5분을 더 던지고 있었다.**
            #   봇 탐지에 계속 던지는 건 차단을 늘릴 수 있다 — 즉시 멈춘다.
            if _is_bot_block(resp):
                record_block()
                raise RateLimited(
                    f"구글이 자동화 질의로 판정했습니다 (HTTP {resp.status_code} · "
                    f"「sending automated queries」). 재시도해도 안 풀립니다 — "
                    f"시간을 두고 재개하세요.")
            print(f"    ✗ 구글 HTTP {resp.status_code} "
                  f"(연속 {_consecutive_fail}): {query[:46]}")
            if _consecutive_fail >= _MAX_CONSECUTIVE_FAIL:
                record_block()          # 다음 실행이 「몇 시간 지났나」를 알 수 있게
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
        # ★네트워크 오류는 차단으로 기록하지 않는다.
        _consecutive_fail += 1
        FAILED_QUERIES.append(query)
        net = isinstance(exc, (requests.Timeout, requests.ConnectionError))
        print(f"    ✗ {'회선 오류' if net else '구글 질의 실패'} "
              f"(연속 {_consecutive_fail}): {exc!r}")
        if _consecutive_fail >= _MAX_CONSECUTIVE_FAIL:
            if not net:
                record_block()
            raise RateLimited(
                f"{'회선 오류' if net else '구글 질의'}가 {_consecutive_fail}회 연속 실패"
                + ("  ※ 구글 차단이 아니라 네트워크 문제로 보입니다 — "
                   "대기 없이 바로 재시도해도 됩니다." if net else ""))
        time.sleep(min(_BACKOFF_BASE * (2 ** (_consecutive_fail - 1)), 60))
        return []
    finally:
        time.sleep(_sleep_gap())

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
    capped = 0

    ckpt = _ckpt_path(name, years, month_split)
    seen, out, done_periods = _ckpt_load(ckpt)
    if done_periods and verbose:
        print(f"    ↻ 중간본에서 이어받습니다 — 기간 {done_periods}개 완료 · "
              f"기사 {len(out)}건 (그만큼 질의를 아낍니다)")

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
    # 월분할로 하여 5년, 총 60개월에 대해 질의를 한다.
    # 1개월 당 8개의 키워드 질의를 던지기 때문에 총 480개의 질의를 던진다.
    periods = months(years) if month_split else quarters(years)
    total = len(periods)
    try:
        for idx, (lo, hi) in enumerate(periods):
            if idx < done_periods:          # 지난 실행이 이미 던진 질의
                continue
            before = len(out)
            for gname, kws in groups.items():
                arts = _fetch(f"{name} ({kws}) after:{lo} before:{hi}")
                if len(arts) >= _PER_QUERY_CAP:
                    capped += 1
                take(arts)
            _ckpt_save(ckpt, out, idx + 1, total)
            if verbose:
                print(f"    {lo[:7]} → +{len(out) - before:>3}건 (누적 {len(out)})")
    except RateLimited:
        if done_periods == 0 and not out:
            if ckpt.exists():
                try:
                    ckpt.unlink()
                except Exception:
                    pass
            raise
        saved, _, saved_n = _ckpt_load(ckpt)
        if verbose:
            print(f"    💾 중간 저장됨 — 기간 {saved_n}/{total} · 기사 {len(saved)}건")
            print(f"       다음 실행은 {periods[saved_n][0][:7] if saved_n < total else '완료'}"
                  f"부터 이어갑니다 (앞선 {saved_n * len(groups)}질의를 다시 쓰지 않습니다)")
        raise

    if capped and verbose:
        print(f"    ⚠ 질의 {capped}건이 상한({_PER_QUERY_CAP})에 걸림 — "
              f"{'월' if month_split else '분기'} 분할로도 부족")
    _ckpt_save(ckpt, out, total, total, collected_all=True)
    return out


# ── 원문 URL 해석 (네이버 제목 재검색) ──
def _naver_headers() -> dict[str, str]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise GNewsError("NAVER_CLIENT_ID/SECRET 없음 — URL 해석에 필요합니다")
    return {"X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


# ★말머리 — 「[단독]」·「[인터뷰]」·「[제조사 로봇 피벗]」·「[더벨][i-point]」.
#   같은 기사인데 매체·채널마다 다르게 붙어서 **앞부분 비교를 통째로 어긋나게** 한다:
#       구글    「에스비비테크, 대기업향 감속기 PO 쇄도」
#       네이버  「[제조사 로봇 피벗] 에스비비테크, 대기업향 감속기 PO 쇄도」
#   양쪽에서 벗기고 비교한다. 연속으로 붙는 경우가 있어 `+`로 반복 처리한다.
_HEADLINE_PREFIX_RE = re.compile(r"^(?:\s*[\[〈<【][^\]〉>】]{1,20}[\]〉>】]\s*)+")

# HTML 엔티티 — 네이버 API는 `&quot;`·`&amp;`를 그대로 준다. 구글은 실제 문자를 준다.
_ENTITY = {"&quot;": '"', "&amp;": "&", "&lt;": "<", "&gt;": ">",
           "&apos;": "'", "&#39;": "'"}


def _norm_title(text: str) -> str:
    """제목 비교용 정규화 — 말머리·엔티티·공백·따옴표 차이를 없앤다."""
    t = _strip_tags(text or "")
    for k, v in _ENTITY.items():
        t = t.replace(k, v)
    t = _HEADLINE_PREFIX_RE.sub("", t)
    # 따옴표류는 매체마다 달라서('' vs "" vs ‘’) 비교에서 뺀다
    t = re.sub(r"[\"'‘’“”·ㆍ]", "", t)
    return re.sub(r"\s+", "", t)


def resolve_url(article: Article) -> Optional[str]:
    """제목을 네이버에서 재검색해 언론사 원문 URL을 얻는다. 실패 시 None.

    구글이 **찾고** 네이버가 **주소를 알려주는** 분업이다.

    ★비교 전에 **말머리를 벗긴다**(2026-08-12). 원래는 원문 제목의 앞 12자를
      그대로 비교했는데, 네이버 쪽에만 「[제조사 로봇 피벗]」 같은 말머리가 붙으면
      앞부분이 통째로 어긋나 같은 기사를 놓쳤다. 실측 해석률이 코드 주석의
      80%가 아니라 **49%**(에스피지 43건 중 21건)였던 원인 중 하나다.

    ★네이버 검색은 「없으면 없다」가 아니라 **비슷한 걸 아무거나** 돌려준다.
      「에스피지, 로봇용 정밀감속기…」를 물으면 「로봇·산업용·협동로봇 관련주…」가
      온다. 그래서 매칭 조건을 느슨하게 하면 **엉뚱한 기사 URL을 붙인다** —
      본문이 통째로 다른 기사가 근거가 되므로 절대 하면 안 된다.
      말머리·엔티티·따옴표처럼 **같은 기사임이 확실한 차이만** 흡수한다.
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

    norm = _norm_title(article.title)
    key = norm[:12]
    if not key:
        return None
    for cand in items:
        title = _norm_title(cand.get("title", ""))
        if title[:12] == key or key in title or (title and title[:12] in norm):
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
