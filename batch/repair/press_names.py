"""기사의 **언론사명**을 URL로 복구한다. 비용 0.

★왜 필요한가 (2026-08-02)

「뉴스/이슈」 화면을 붙이려고 데이터를 보니 기사 7,185건 중 **2,974건(41%)에
언론사가 비어 있고**, 426건은 언론사 자리에 도메인이 그대로 들어가 있었다:

    2026-06-08            SK하이닉스, 한미반도체에 HBM4 장비 442억…   ← 비었다
    2026-03-18  v.daum.net  검찰, 삼성전자·레인보우로보틱스 압수수색      ← 도메인이다

수집 경로마다 언론사를 주는 방식이 다른 탓이다 — 네이버 검색 API는 원문
도메인만 주고(`naver.py`: `press=""  # 원문 도메인에서 유추`), 구글은 매체명을
주고, RSS는 피드마다 제각각이다. 그런데 **같은 매체의 기사를 다른 경로로도
받았다.** 그러니 남의 경로가 채워 준 이름을 빌려 오면 된다 — 새로 크롤링하지
않는다.

★어떻게 정하나 — 다수결, 그리고 **애매하면 비워 둔다**

  1차 · 도메인이 정확히 같은 기사들의 언론사 다수결
        biz.chosun.com → 조선비즈 (300/314)
  2차 · 1차에서 못 정한 것은 **등록 도메인**까지 넓혀 다수결
        news.hankyung.com → (hankyung.com) → 한국경제

  ★왜 다수결인가: 첫 값을 쓰면 틀린다. 실측으로 `edaily.co.kr`의 첫 값이
    「네이트」였다(포털 경유 기사에서 매체명이 잘못 붙었다). 표가 갈리는
    도메인이 31종 있었다 — 뉴시스/뉴시스산업, 지디넷코리아/ZDNet처럼 대개
    같은 매체의 이름 변형이라 1위를 쓰면 맞는다.

  ★못 정하면 **지어내지 않는다.** 도메인을 언론사명처럼 보여 주는 게 지금
    상태인데, 그건 빈칸보다 나쁘다 — 사용자가 「v.daum.net」을 매체 이름으로
    읽는다. 모르는 건 비우고, 몇 건인지 보고한다.

    python -m batch.repair.press_names --dry-run
    python -m batch.repair.press_names
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

from app.core.database import postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 1위가 이 비중과 표 수를 넘어야 채운다. 낮추면 오염된 표가 통과한다.
_MIN_VOTES = 2
_MIN_SHARE = 0.5

# 한국 도메인의 공용 접미사 — 이게 있으면 등록 도메인은 뒤에서 세 마디다
# (`news.hankyung.com` → `hankyung.com`, `moneys.mt.co.kr` → `mt.co.kr`).
_KR_SUFFIX = {"co.kr", "or.kr", "ne.kr", "go.kr", "pe.kr", "re.kr", "ac.kr"}

# 포털·수집 경유 주소. 도메인이 매체를 뜻하지 않으므로 **표로 세지도, 채우지도**
# 않는다. 여기 든 주소는 언론사명이 비어 있는 게 정직하다.
_AGGREGATORS = {"v.daum.net", "news.naver.com", "n.news.naver.com",
                "media.daum.net", "news.google.com"}

# ★부모 도메인의 이름을 물려받아도 되는 서브도메인만 적는다.
#
#   서브도메인은 두 종류다. `news.hankyung.com`처럼 **같은 매체의 창구**인 것과,
#   `sports.chosun.com`처럼 **다른 매체**인 것. 구분 없이 물려받았더니 실측으로
#   이렇게 틀렸다:
#
#       sports.chosun.com   → 조선비즈   (스포츠조선이다)
#       woman.chosun.com    → 조선비즈   (여성조선이다)
#       news.sbs.co.kr      → SBS Biz  (SBS 뉴스다)
#       mbn.mk.co.kr        → 매일경제    (MBN이다)
#       moneys.mt.co.kr     → 머니투데이   (머니S다)
#
#   틀린 언론사명은 **빈칸보다 나쁘다** — 사용자가 그대로 믿는다. 그래서
#   「창구」로 확실한 것만 허용하고 나머지는 비워 둔다.
_GENERIC_SUBDOMAINS = {"news", "m", "www", "view", "n", "stock", "er", "cc"}


def _host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "").lower()


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _KR_SUFFIX:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _may_inherit(host: str) -> bool:
    """부모 도메인의 언론사명을 물려받아도 되는 주소인가."""
    reg = _registrable(host)
    if host == reg:
        return True                                   # 도메인 자체 (asiae.co.kr)
    prefix = host[: -len(reg) - 1]                    # 앞에 붙은 부분
    return prefix in _GENERIC_SUBDOMAINS              # 여러 마디면 자동 탈락


def _is_missing(press: str | None) -> bool:
    """비었거나, 언론사명이 아니라 **도메인이 들어간** 경우."""
    return not press or not press.strip() or "." in press


def _winner(counts: Counter) -> tuple[str, str] | None:
    """(이름, 근거) 또는 None. 표가 모자라거나 갈리면 None."""
    if not counts:
        return None
    name, n = counts.most_common(1)[0]
    total = sum(counts.values())
    if n < _MIN_VOTES or n / total < _MIN_SHARE:
        return None
    return name, f"{n}/{total}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with postgres_connection() as conn:
        rows = conn.execute("SELECT url, press FROM news_articles").fetchall()

    exact: dict[str, Counter] = defaultdict(Counter)
    reg: dict[str, Counter] = defaultdict(Counter)
    missing: list[tuple[str, str, str]] = []     # (url, host, 원래 press)

    for url, press in rows:
        host = _host(url)
        if _is_missing(press):
            missing.append((url, host, press or ""))
            continue
        exact[host][press.strip()] += 1
        # ★부모 도메인의 표는 **창구 주소끼리만** 모은다. `biz.sbs.co.kr`(SBS Biz)의
        #   표가 `sbs.co.kr` 풀에 섞이면 `news.sbs.co.kr`이 SBS Biz가 돼 버린다.
        if host not in _AGGREGATORS and _may_inherit(host):
            reg[_registrable(host)][press.strip()] += 1

    fills: list[tuple[str, str]] = []            # (url, press)
    clears: list[str] = []
    by_rule = Counter()
    unresolved: Counter = Counter()

    for url, host, old in missing:
        hit = None if host in _AGGREGATORS else _winner(exact.get(host, Counter()))
        rule = "도메인 일치"
        if not hit and host not in _AGGREGATORS and _may_inherit(host):
            hit = _winner(reg.get(_registrable(host), Counter()))
            rule = "등록 도메인"
        if hit:
            fills.append((url, hit[0]))
            by_rule[rule] += 1
            continue
        unresolved[host] += 1
        # ★못 정했는데 `press` 자리에 도메인이 들어 있으면 **지운다.**
        #   「v.daum.net」·「dt.co.kr」을 매체 이름으로 읽히게 두는 건 빈칸보다
        #   나쁘다. URL은 같은 행에 있으니 도메인이 필요하면 화면이 만들면 된다.
        if old.strip():
            clears.append(url)

    total_missing = len(missing)
    print(f"기사 {len(rows)}건 · 언론사가 없거나 도메인인 것 {total_missing}건 "
          f"({total_missing * 100 // len(rows)}%)\n")
    print(f"■ 채울 수 있는 것 {len(fills)}건")
    for rule, n in by_rule.most_common():
        print(f"     {rule:12} {n}건")
    print(f"■ 도메인이 박힌 값을 **지울 것** {len(clears)}건 (이름을 못 정한 것 — 빈칸이 정직하다)")
    print(f"■ 그대로 두는 것 {sum(unresolved.values())}건 · 도메인 {len(unresolved)}종")
    for host, n in unresolved.most_common(8):
        tag = "  ← 포털" if host in _AGGREGATORS else ""
        print(f"     {host:30}{n:>4}{tag}")

    seen: dict[str, str] = {}
    for url, press in fills:
        seen.setdefault(_host(url), press)
    print("\n   ── 채우는 예시 ──")
    for host, press in list(seen.items())[:10]:
        print(f"     {host:30} → {press}")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0

    with postgres_connection() as conn:
        for i in range(0, len(fills), 500):
            chunk = fills[i:i + 500]
            conn.execute(
                "UPDATE news_articles SET press = v.press "
                "FROM (SELECT unnest(%s::text[]) AS url, unnest(%s::text[]) AS press) v "
                "WHERE news_articles.url = v.url",
                ([u for u, _ in chunk], [p for _, p in chunk]))
        if clears:
            conn.execute("UPDATE news_articles SET press = '' WHERE url = ANY(%s)",
                         (clears,))
    print(f"\n✅ 언론사 {len(fills)}건 복구 · 도메인 값 {len(clears)}건 제거")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
