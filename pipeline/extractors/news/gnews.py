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
#   ★↑ 이 판단이 **틀렸다** (2026-08-04 실험으로 반박). 안전장치는 손실을 줄일
#     뿐 차단을 막지 못한다. 0.8초로는 480질의를 **한 번도** 못 끝냈다:
#
#         0.8초 →  400 · 408 · 392 · 368질의에서 차단 (4회 연속)
#         1.2초 →  1,072질의 · 62분 · **차단 0회** (기업 3곳 연속 완주)
#
#     같은 IP·같은 날인데 2.7배를 던지고도 안 막혔다. 「하루 한도가 6,240에서
#     1,300으로 떨어졌다」며 IP 평판을 의심했는데, 원인은 우리 쪽 속도였다.
#     기업당 3분 더 걸리는 대신(26분 → 29분) 차단이 사라진다 — 차단 한 번에
#     2시간 이상 잠기는 걸 생각하면 3분은 아무것도 아니다.
#
#   실측 참고 — HTTP 왕복 자체가 2.4초라 딜레이는 질의당 시간의 3분의 1뿐이다:
#       0.4초 → 기업당 22분 (차단당한 설정)
#       0.8초 → 기업당 26분
#       1.2초 → 기업당 29분
#
# ★2026-08-04 — 하루 한도가 07-31 이후 **6,240 → 1,300~1,800**으로 떨어졌다:
#
#       07-30   1,440질의   차단 없음
#       07-31   6,240질의   ← 이날 처음 차단
#       08-03   1,768질의   2회 차단
#       08-04   1,328질의   2회 차단
#
#   원인 후보가 둘인데 **구분되지 않았다**:
#     ① 07-31에 평소의 4배를 던져 IP 평판이 깎였다 (시간이 답)
#     ② 그 뒤 0.8초로 되돌려 더 빠르게 던지고 있다 (속도가 답)
#
#   ②를 시험하려고 환경변수로 뺐다. 1.2초로 돌려 400을 넘기면 속도가 요인이고,
#   또 400에서 끊기면 평판 쪽이라 기다리는 수밖에 없다.
#
#   → **②가 답이었다.** 1.2초로 1,072질의를 던져 3곳을 연속 완주했다.
#
# ★2026-08-06 — 1.2초에서 **2.5초로 올린다.** 이유는 속도가 아니라 **누적 평판**이다:
#
#       08-04   3,840질의   503 1회
#       08-05   ~1,300질의  503 **19회 이상**  ← 재시도 루프가 24회 더 두드림
#       08-06     152질의   503 1회
#
#   503을 40번 넘게 맞았고, 그 뒤로 한 번에 얻는 질의가 계속 줄고 있다.
#   봇 탐지는 재범을 가중하므로, 회복기에는 **평소보다 더 조심스럽게** 던진다.
#   1.2초가 틀렸던 게 아니라(그때는 1,072질의를 통과시켰다) 지금 상태가 다르다.
#
#   비용: HTTP 왕복 자체가 2.4초라 기업당 29분 → 약 40분. 차단 한 번에 몇 시간을
#   잃는 걸 생각하면 10분은 싸다.
#   ※ 더 내리지 말 것 — 0.4초와 0.8초가 모두 차단당했다.
_DELAY = float(os.getenv("GNEWS_DELAY", "2.5"))   # 구글 요청 간격(초)

# ★2026-08-07 — 간격에 **흔들림(jitter)** 을 준다.
#
#   그동안 「몇 질의에서 막히나」를 역설계하려다 여섯 번 다 틀렸다(400 한계 /
#   오래 쉬면 더 참 / 소량은 통과 / 1.3~1.5시간 잠금 / 하루치 소진 / …).
#   봇 탐지는 단일 규칙이 아니라 여러 신호를 종합한 분류기라 그런 것 같다.
#
#   그중 우리가 확실히 고칠 수 있는 신호가 **규칙성**이다. 2.5초 고정은
#   시계처럼 정확해서 그 자체가 기계 표식이다 — 사람도, 실제 피드 리더도
#   2.500초 간격으로 요청하지 않는다.
#
#   지터는 회피 기법이 아니라 **표준 클라이언트 위생**이다(AWS·구글 API
#   가이드가 권장). 서버 쪽 부하 쏠림도 줄인다.
#   ※ 다만 이것만으로 해결되리라 기대하지 말 것 — 20분에 480회를 던진다는
#     사실 자체는 그대로다. 근본은 **질의 수를 줄이는 것**인데, 그건 기사를
#     놓쳐서 못 한다(분기 분할이 실측으로 반박된 이유는 `collect_company` 참고).
_JITTER = float(os.getenv("GNEWS_JITTER", "0.6"))   # ±비율. 0이면 고정 간격


def _sleep_gap() -> float:
    """이번 요청 뒤 쉴 시간 — 기준값 ±60%로 흔든다(하한 0.4초)."""
    if _JITTER <= 0:
        return _DELAY
    return max(0.4, _DELAY * (1.0 + random.uniform(-_JITTER, _JITTER)))
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


# ── 차단 시각을 남긴다 ────────────────────────────────────────
#
# ★2026-08-03. 「이제 풀렸나」를 **몇 번 질의해 보고** 판단하다 틀렸다.
#   4회 질의가 4회 다 200을 받아 재수집을 시작했는데, 390회쯤에서 다시 503이
#   나 22.8분을 버리고 0건으로 끝났다.
#
#   구글은 막혀 있어도 **소량은 통과시킨다.** 그래서 적은 표본의 탐침은
#   「아직 막혀 있다」만 증명할 수 있고 「이제 안전하다」는 **증명할 수 없다.**
#   기업 하나가 480질의라, 40회를 던져 봐도 예측이 안 된다.
#
#   대신 할 수 있는 건 **마지막 차단이 언제였는지 기억**하는 것이다.
#
# ★2026-08-04 — 차단은 「임계」가 아니라 **잠금**이었다. 경과시간별 실측:
#
#       0.3시간 뒤  → 첫 질의부터 503     ❌ 잠김
#       1.3시간 뒤  → 탐침 1회도 503      ❌ 잠김
#       2.2시간 뒤  → 88질의 통과·완주     ✅ 열림
#       5.6시간 뒤  → 408질의 통과         ✅ 열림
#      14.0시간 뒤  → 392질의 통과         ✅ 열림
#
#   잠금은 **1.3~2.2시간** 사이에 풀리고, 풀린 뒤에는 400질의쯤을 준다.
#   14시간을 쉬어도 2.5시간과 결과가 같았다(392 vs 400) — 더 기다려도 소용없다.
#
#   ※ 「막혀 있어도 소량은 통과한다」고 적었던 건 **틀렸다.** 근거로 삼은
#     08-03 07:30의 탐침 4/4 성공은 그때 차단이 걸려 있지 않았던 것뿐이다
#     (그날 아직 한 번도 안 막혔고 예산만 소진된 상태였다).
#     잠금 중에는 1질의도 안 통한다 — 0.3시간 뒤 첫 질의가 막힌 것이 증거다.
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


# ── 중간 저장 · 이어받기 ──────────────────────────────────────
#
# ★왜 필요한가 (2026-08-03 실측)
#
# 이오테크닉스 재수집이 두 번 다 **같은 자리**에서 끊겼다:
#
#     1차  2026-08 → 2022-07   400질의   차단
#     2차  2026-08 → 2022-06   408질의   차단
#             ↑ 둘 다 여기서 시작
#
# 매번 2026-08부터 다시 시작하므로, 이미 400질의를 써서 받아온 458건을 버리고
# 다음 실행이 **똑같은 400질의를 또 쓴다.** 그리고 또 같은 자리에서 막힌다.
# 이대로면 2022-05 이전에는 **영원히 도달하지 못한다.**
#
# 480질의를 한 번에 던질 이유가 없다. 나눠 던지면 된다:
#
#     1회차  2026-08 → 2022-06   408질의  ✅ 여기까지 저장
#     2회차  2022-05 → 2021-09    72질의  ✅ 완료
#
# 같은 480질의 · 같은 기사 · **손실 0**. 기간 단위를 늘리거나 질의어 묶음을
# 줄이는 것과 달리 아무것도 포기하지 않는다(그 둘은 실측상 기사를 놓친다 —
# DB하이텍 2026-Q1: 분기 31건 vs 월 합집합 33건).
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


def _ckpt_save(path: Path, arts: list[Article], done: int, total: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "done_periods": done, "total_periods": total,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "articles": [{"url": a.url, "title": a.title, "press": a.press,
                          "published_at": (a.published_at.isoformat()
                                           if a.published_at else None),
                          "source_channel": a.source_channel} for a in arts],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"    (중간 저장 실패: {exc!r})")


def clear_checkpoint(name: str, years: int, month_split: bool) -> None:
    """수집이 **끝까지 갔을 때만** 부른다 — 다음 실행이 처음부터 돌게."""
    p = _ckpt_path(name, years, month_split)
    if p.exists():
        p.unlink()


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
        # ★네트워크 오류는 **차단으로 기록하지 않는다**(2026-08-05).
        #   피에스케이 수집 중 ReadTimeout이 9번, ConnectionError가 1번 났는데
        #   봇 차단과 같은 카운터로 세고 있었다. 회선이 잠깐 흔들린 것만으로
        #   「차단당했다」고 기록하면, 다음 실행이 멀쩡한데도 3시간을 기다린다.
        #   실제로 이번엔 연속 3회까지 갔다가 회복했다 — 차단이 아니었다.
        #
        #   구분 기준: 구글이 **응답을 준 것**(503/`/sorry`)만 차단이다.
        #   연결 자체가 안 된 것은 우리 쪽·중간 경로 문제일 수 있다.
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

    # ★끊긴 데서 이어받는다 — 이미 던진 질의는 다시 던지지 않는다
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
    #
    # ★2026-08-08 — 「질의어 묶음을 빼고 기간을 잘게 쪼개면 질의를 줄일 수 있지
    #   않나」를 시험했다. 산수로는 맞다(주 × 기업명만 = 260질의 < 480). **틀렸다.**
    #
    #   같은 달을 세 방식으로 뽑아 대조(각 13질의):
    #       코미코    2026-06   주 단위가 8묶음을 **100%** 덮음  ✅
    #       와이씨    2026-03   주 단위가 8묶음을 **68%** 덮음   ✗
    #       에스앤에스텍 2025-11  기업명만이 8묶음을 **61%** 덮음  ✗
    #
    #   세 번 재서 셋 다 달랐다. 원인 셋:
    #     ① **이름이 겹치는 기업** — 「와이씨」는 와이앤아처·와이제이링크·YC(실리콘
    #        밸리 액셀러레이터)와 섞인다. 기업명만 던지면 남의 기사가 100건 상한을
    #        잡아먹는다. 키워드가 붙으면 그게 걸러진다.
    #     ② 구글이 **질의마다 다른 부분집합**을 준다(위 DB하이텍 사례와 같음).
    #        에스앤에스텍은 8묶음 13건짜리라 상한과 무관한데도 8건만 겹쳤다.
    #     ③ 8묶음만 찾는 기사가 실재한다 — 「코스피200 정기변경…이수페타시스 편입」
    #        처럼 **기업명이 제목에 없고 본문에만** 있는 것. 키워드 질의가 잡아낸다.
    #
    #   대형사는 더 확실하다. 기업명만 검색 시 주당 기사 수(네이버 실측):
    #       삼성전자 4,248 · LG전자 1,517 · SK하이닉스 1,006  → 100건 상한에 걸림
    #   하루 단위로 1,825질의를 던져도 하루 607건 중 100건(16%)뿐이다.
    #   시간을 아무리 쪼개도 기업명만으로는 대형사를 못 훑는다.
    #
    #   → 480질의는 줄일 수 없다. 차단은 **속도·휴식**으로만 다룬다.
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
            # ★기간을 **끝낼 때마다** 저장한다. 중간에 차단당해도 여기까지는 남는다.
            #   기간 단위로 저장하는 이유 — 한 기간의 8묶음이 다 돌아야 그 기간을
            #   「봤다」고 할 수 있다. 묶음 중간에 저장하면 다음 실행이 나머지
            #   묶음을 건너뛰어 기사를 놓친다.
            _ckpt_save(ckpt, out, idx + 1, total)
            if verbose:
                print(f"    {lo[:7]} → +{len(out) - before:>3}건 (누적 {len(out)})")
    except RateLimited:
        # ★한 기간도 못 끝냈으면 중간본을 **남기지 않는다**(2026-08-05).
        #   잠금 중에 시도하면 첫 질의부터 막혀 「기간 0/60 · 기사 0건」짜리
        #   빈 파일이 생겼다. 이어받을 게 없는데 `collect_state`에 「중단된 수집」으로
        #   떠서, 진짜 중간본과 구분이 안 됐다.
        if done_periods == 0 and not out:
            if ckpt.exists():
                try:
                    ckpt.unlink()
                except Exception:
                    pass
            raise
        # 마지막으로 **끝낸** 기간까지는 이미 저장돼 있다. 다음 실행이 이어받는다.
        saved, _, saved_n = _ckpt_load(ckpt)
        if verbose:
            print(f"    💾 중간 저장됨 — 기간 {saved_n}/{total} · 기사 {len(saved)}건")
            print(f"       다음 실행은 {periods[saved_n][0][:7] if saved_n < total else '완료'}"
                  f"부터 이어갑니다 (앞선 {saved_n * len(groups)}질의를 다시 쓰지 않습니다)")
        raise

    if capped and verbose:
        print(f"    ⚠ 질의 {capped}건이 상한({_PER_QUERY_CAP})에 걸림 — "
              f"{'월' if month_split else '분기'} 분할로도 부족")
    # 끝까지 갔다 — 중간본을 지워 다음 실행이 새로 돌게 한다
    clear_checkpoint(name, years, month_split)
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
