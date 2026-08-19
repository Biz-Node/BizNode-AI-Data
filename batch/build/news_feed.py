"""뉴스 피드 갱신 — **매일 도는 가벼운 쪽.**

★왜 `batch.build.news` 와 따로 있나

  `news` 는 RSS 수집부터 관계 추출·Neo4j 적재까지 한 덩어리로 돈다. 그런데
  뉴스 화면에 필요한 건 **제목·언론사·발행일·링크·어느 기업 기사인가**뿐이다.
  둘이 붙어 있으면 화면을 갱신하려고 관계 추출까지 돌게 되는데, 그건 매일
  돌릴 것이 못 된다 —

      관계 추출   기사당 14.7원 · 기업당 40분 · Neo4j 를 쓴다 → 서비스 시간 밖
      기사 메타     무료 · 수 분 · PostgreSQL 만 쓴다        → 서비스 중에도 가능

  그래서 **무료·PG 전용 구간만** 떼어 냈다. 규칙 게이트까지만 돌고 멈춘다.

★구글을 안 쓴다

  과거 5년치를 팔 때는 구글 뉴스 RSS 를 썼다 — `after:`/`before:` 로 기간을
  지정할 수 있어서다(`pipeline/extractors/news/gnews.py`). 대신 원문 URL 을
  안 주고, **차단되면 12시간 잠긴다.**

  일일 갱신은 그럴 이유가 없다. 언론사 공식 RSS 는 최근 것만 계속 뱉으니
  매일 아침 받으면 어제 것이 들어온다. 발행사가 배포를 의도한 채널이라
  **수집 정당성도 가장 높다.**

★본문은 저장하지 않는다

  제목·언론사·발행일·URL 까지만 남긴다(방법서 §8). 원문은 언론사 링크로
  보내므로 저작권 문제가 없다. 본문 크롤링도 하지 않는다 — 관계 추출이
  아니면 본문이 필요 없고, 크롤링은 시간을 많이 쓴다.

실행:
    python -m batch.build.news_feed
    python -m batch.build.news_feed --dry-run     # 적재 없이 세기만
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.news.naver import search_latest
from pipeline.extractors.news.rss import fetch_all
from pipeline.news.relevance import rule_filter, topics_of

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ★`llm_relevant` 를 건드리지 않는다. 관계 추출 배치가 채우는 칸이라
#   여기서 덮으면 「추출 대상인가」 판정을 지워 버린다.
_UPSERT = """
INSERT INTO news_articles (url, title, press, published_at, source_channel,
                           title_hash, body_length, rule_passed, matched_corps, topics)
VALUES (%(url)s, %(title)s, %(press)s, %(published_at)s, %(source_channel)s,
        %(title_hash)s, %(body_length)s, %(rule_passed)s, %(matched_corps)s, %(topics)s)
ON CONFLICT (url) DO UPDATE SET
    rule_passed   = EXCLUDED.rule_passed,
    matched_corps = EXCLUDED.matched_corps,
    topics        = EXCLUDED.topics
"""


def _targets(limit: int = 0) -> list[str]:
    """네이버로 훑을 기업. **자료가 있는 곳**을 먼저 본다.

    관계만 있는 2,900여 곳까지 훑으면 질의는 늘고 새 기사는 별로 안 는다 —
    그런 곳은 애초에 기사가 적어서 관계로만 걸린 것이다.
    """
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT corp_code FROM financials
                       UNION SELECT DISTINCT corp_code FROM market_metrics""")
        corps = [r[0] for r in cur.fetchall() if r[0]]
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(
            """UNWIND $ks AS k MATCH (c:Company) WHERE c.corp_code = k
               RETURN c.name AS n, COUNT { (c)--() } AS d ORDER BY d DESC""", ks=corps)]
    names = [r["n"] for r in rows if r["n"]]
    return names[:limit] if limit else names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="적재하지 않고 세기만")
    ap.add_argument("--no-naver", action="store_true",
                    help="네이버 증분 조회 생략(RSS 만). 커버리지가 크게 준다")
    ap.add_argument("--companies", type=int, default=0,
                    help="네이버로 훑을 기업 수 (0=자료가 있는 곳 전부)")
    ap.add_argument("--per-company", type=int, default=60,
                    help="기업당 최신 몇 건까지 훑을까")
    args = ap.parse_args()

    print("■ 언론사 RSS 수집")
    articles = fetch_all()
    print(f"  → {len(articles)}건")

    # ★RSS 만으로는 우리 기업을 못 덮는다.
    #   실측(2026-08-18): RSS 551건 → 우리 기업 기사 98건 → 등장 기업 18곳.
    #   자료를 가진 477곳 중 18곳뿐이다. RSS 는 「누가 오늘 경제면에 났나」를
    #   알려 줄 뿐, 우리 기업을 보장하지 않는다.
    #
    #   방향을 뒤집는다 — **우리 기업으로 조회한다.** 네이버는 날짜 필터가
    #   없지만 최신순으로 받다가 아는 URL 을 만나면 멈추면 되므로 증분이 된다.
    if not args.no_naver:
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT url FROM news_articles")
            known = {r[0] for r in cur.fetchall()}
        names = _targets(args.companies)
        print(f"\n■ 네이버 증분 조회 ({len(names)}곳 · 아는 URL {len(known):,}개)")
        fresh, gaps = search_latest(names, known, per_company=args.per_company)
        print(f"  → 새 기사 {len(fresh)}건")
        if gaps:
            # ★따라잡지 못한 기업을 **조용히 넘기지 않는다.** 질의당 1,000건
            #   상한이라 2~3주 방치하면 대형주에 구멍이 난다. 그때는 구글 뉴스
            #   RSS 로 그 기간만 메워야 한다.
            print(f"  ⚠ 상한까지 받았는데 아는 기사에 못 닿은 곳 {len(gaps)}곳: "
                  f"{' · '.join(gaps[:6])}{' …' if len(gaps) > 6 else ''}")
            print("    → 오래 안 돌렸다면 구글 뉴스 RSS 로 그 기간을 메우세요.")
        seen_urls = {a.url for a in articles}
        articles.extend(a for a in fresh if a.url not in seen_urls)
        print(f"  → 합계 {len(articles)}건")

    if not articles:
        print("  받은 기사가 없습니다. 피드가 막혔는지 확인하세요.")
        return 1

    print("\n■ 기업 매칭 + 주제 분류 (규칙 게이트 · 무료)")
    # ★같은 사건을 여러 매체가 그대로 싣는다. 실측: 「모두의 AI 6파전」이
    #   제목만 조금씩 다른 12건으로 들어왔다. 화면에 그대로 나가면 피드가
    #   한 사건으로 도배된다. `title_hash` 가 그걸 잡으라고 있는 값이다.
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT title_hash FROM news_articles WHERE title_hash IS NOT NULL")
        seen_hash = {r[0] for r in cur.fetchall()}
    dup = 0

    rows, topic_stat, corp_hits = [], Counter(), 0
    for a in articles:
        # ★`track="both"` — 피드는 관계 기사와 사건 기사를 **둘 다** 실어야 한다.
        #   관계만 받으면 「공장 화재」가 빠지고, 그건 리스크 화면의 핵심이다.
        r = rule_filter(a.title, a.body, track="both")
        if not r.matched_corps:
            continue                      # 우리 기업이 안 나오면 피드에 못 쓴다
        # ★제목에 기업명이 있어야 한다. 네이버로 기업명을 검색하면 시황 기사가
        #   딸려 오는데(「코스피 마감시황」이 본문에서 심텍을 스치듯 언급),
        #   규칙 게이트는 「매수·거래」 같은 관계 키워드에 걸려 통과시킨다.
        #   실측: 새 기사 59건 중 규칙 통과 5건, 그중 3건이 수급 기사였다.
        if not any(n in a.title for n in r.matched_names):
            continue
        if a.title_hash in seen_hash:
            dup += 1
            continue
        seen_hash.add(a.title_hash)
        corp_hits += 1
        tp = topics_of(a.title, a.body)
        for t in tp:
            topic_stat[t] += 1
        rows.append({
            "url": a.url, "title": a.title, "press": a.press,
            "published_at": a.published_at, "source_channel": a.source_channel,
            "title_hash": a.title_hash, "body_length": len(a.body),
            "rule_passed": r.passed,
            "matched_corps": json.dumps(r.matched_corps, ensure_ascii=False),
            "topics": json.dumps(tp, ensure_ascii=False),
        })

    print(f"  기업이 언급된 기사 {corp_hits}건 (전재 중복 {dup}건 제외)")
    print(f"  주제 분포: {dict(topic_stat) or '없음'}")
    # ★주제가 하나도 안 걸린 기사도 싣는다. 화면의 축 1 에 「전체」가 있고,
    #   「삼성전자 신제품 공개」처럼 네 갈래에 안 드는 기사도 뉴스이기 때문이다.
    print(f"  주제 없는 기사 {sum(1 for r in rows if r['topics'] == '[]')}건 (「전체」에만 나옴)")

    if args.dry_run:
        print("\n--dry-run — 적재하지 않았습니다.")
        return 0

    print("\n■ 적재")
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, rows)
        conn.commit()
    print(f"  → {len(rows)}건 upsert")

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(published_at)::date, count(*) FROM news_articles")
        latest, total = cur.fetchone()
        print(f"\n전체 {total:,}건 · 최신 기사 {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
