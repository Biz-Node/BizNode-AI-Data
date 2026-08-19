"""뉴스 수집·필터 파이프라인.

수집(RSS·네이버) → 제목 dedup → 1차 규칙 필터(무료) → **본문 크롤링** →
2차 LLM 라우터(저가) → news_articles 적재.

비용이 싼 관문부터 배치하되, **본문이 필요한 판정 앞에는 크롤링을 둔다**.
크롤링은 돈이 아니라 시간을 쓰므로 LLM 관문보다 앞에 오는 게 유리하다.

**본문은 저장하지 않는다** — 통과분의 본문은 메모리로만 전달해
관계 추출(P2C)에서 소비하고 폐기한다. DB에는 길이만 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pipeline.extractors.news.crawler import enrich_bodies
from pipeline.extractors.news.rss import Article, fetch_all
from pipeline.news.relevance import llm_router_batch, rule_filter


@dataclass
class ScreenedArticle:
    """필터를 통과해 관계 추출로 넘어갈 기사 (본문 포함, 비저장)."""
    article: Article
    matched_corps: list[str]
    matched_names: list[str]
    router_reason: str


_UPSERT_SQL = """
INSERT INTO news_articles (url, title, press, published_at, source_channel, title_hash,
                           body_length, rule_passed, llm_relevant, matched_corps)
VALUES (%(url)s, %(title)s, %(press)s, %(published_at)s, %(source_channel)s, %(title_hash)s,
        %(body_length)s, %(rule_passed)s, %(llm_relevant)s, %(matched_corps)s)
ON CONFLICT (url) DO UPDATE SET
    rule_passed = EXCLUDED.rule_passed,
    llm_relevant = EXCLUDED.llm_relevant,
    matched_corps = EXCLUDED.matched_corps,
    body_length = EXCLUDED.body_length
"""


def _extracted_title_hashes(conn) -> set[str]:
    """이미 **관계 추출까지 끝난** 기사의 제목 해시.

    단순히 DB에 있다고 스킵하면 재실행이 불가능하다(수집만 되고 미처리인 건도
    걸러짐). 통신사 전재 중복 제거가 목적이므로 '처리 완료' 기준으로 판단한다.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT title_hash FROM news_articles "
                    "WHERE title_hash IS NOT NULL AND extracted_at IS NOT NULL")
        return {row[0] for row in cur.fetchall()}


def _allocate_by_channel(items: list, budget: int) -> list:
    """라우터 예산을 채널별로 균등 배분(라운드로빈).

    채널마다 수율이 다른데 리스트 순서로 자르면 앞 채널이 예산을 독식한다.
    남는 몫은 다른 채널이 흡수하므로 한쪽이 적어도 예산을 낭비하지 않는다.
    """
    buckets: dict[str, list] = {}
    for item in items:
        buckets.setdefault(item[0].source_channel, []).append(item)

    out: list = []
    idx = 0
    while len(out) < budget and any(idx < len(b) for b in buckets.values()):
        for bucket in buckets.values():
            if idx < len(bucket) and len(out) < budget:
                out.append(bucket[idx])
        idx += 1
    return out


_PENDING_SQL = """
SELECT url, title, press, published_at, source_channel, title_hash, matched_corps
FROM news_articles
WHERE rule_passed
  AND extracted_at IS NULL
  AND (llm_relevant IS NULL OR llm_relevant)
ORDER BY published_at DESC NULLS LAST
LIMIT %s
"""


def pending_articles(conn, limit: int = 300) -> list[ScreenedArticle]:
    """**이미 모아 둔 기사 중 아직 추출 안 한 것.**

    ★수집과 추출을 잇는 자리다. 전에는 추출 배치가 RSS 를 **다시 받았다**.
      RSS 는 최근 것만 뱉으니, 수집 배치가 어제 저장한 기사를 추출 배치가
      오늘 돌면 **못 찾고 지나갔다** — 실측(2026-08-18): 규칙을 통과했는데
      추출이 안 된 기사가 6,403건 쌓여 있었다. `idx_news_pending` 인덱스는
      만들어져 있는데 읽는 코드가 없었다.

    ★`llm_relevant IS FALSE` 는 뺀다. 이미 「관계 기사가 아니다」로 판정된 것이라
      다시 볼 이유가 없다(6,403건 중 4,610건이 여기 해당한다).

    ★본문은 여기서 안 채운다. 저장하지 않으므로(방법서 §8) 추출 직전에
      다시 크롤링해야 한다 — 부르는 쪽이 `enrich_bodies` 를 돌린다.
    """
    import json

    with conn.cursor() as cur:
        cur.execute(_PENDING_SQL, (limit,))
        rows = cur.fetchall()

    out: list[ScreenedArticle] = []
    for url, title, press, at, channel, _thash, corps in rows:
        codes = corps if isinstance(corps, list) else json.loads(corps or "[]")
        # `title_hash` 는 Article 이 제목에서 계산하는 property 라 넣지 않는다 —
        # 제목이 같으면 같은 값이 나온다.
        art = Article(url=url, title=title or "", body="", press=press or "",
                      published_at=at, source_channel=channel or "rss")
        out.append(ScreenedArticle(art, codes, [], "PG 미처리분"))
    return out


def collect_and_screen(conn, *, limit_router: Optional[int] = None,
                       use_naver: bool = False, seed_names: Optional[list[str]] = None,
                       crawl_bodies: bool = True,
                       track: str = "relation") -> list[ScreenedArticle]:
    """수집 → dedup → 규칙 필터 → 본문 크롤링 → 라우터 → DB 기록. 통과분 반환.

    limit_router: LLM 라우터 호출 상한(비용 통제). None이면 전량. 채널별 균등 배분.
    use_naver:    네이버 검색 API로 기사를 추가 수집(키 필요).
    crawl_bodies: 규칙 통과분 중 본문 없는 건을 언론사 원문에서 크롤링.
                  끄면 네이버 수집분은 본문이 없어 라우터에서 제외된다.
    track:        relation(기업 간 관계) | risk(사건·리스크) | both
                  검색 키워드·규칙 필터·라우터 프롬프트가 전부 트랙에 따라 달라진다.
    """
    import json

    print("[1/5] 뉴스 수집")
    articles = fetch_all()
    print(f"  RSS 소계 {len(articles)}건")

    if use_naver and seed_names:
        from pipeline.extractors.news.naver import search_relations

        from pipeline.extractors.news.naver import RISK_KEYWORDS, SEARCH_KEYWORDS

        keywords: tuple[str, ...] = ()
        if track in ("relation", "both"):
            keywords += SEARCH_KEYWORDS
        if track in ("risk", "both"):
            keywords += RISK_KEYWORDS
        print(f"  네이버 검색 (트랙={track}, 키워드 {len(keywords)}종 "
              f"× 시드 {len(seed_names)}개 = {len(keywords) * len(seed_names)}회 질의)")

        naver_articles = search_relations(seed_names, keywords=keywords)
        known = {a.url for a in articles}
        added = [a for a in naver_articles if a.url not in known]
        articles.extend(added)
        print(f"  네이버 검색 {len(added)}건 추가")
    print(f"  → 총 {len(articles)}건")

    print("\n[2/5] dedup + 1차 규칙 필터")
    seen_hashes = _extracted_title_hashes(conn)
    rule_passed: list[tuple[Article, list[str], list[str]]] = []
    rows: list[dict] = []
    duplicates = 0

    for article in articles:
        h = article.title_hash
        if h in seen_hashes:          # 통신사 전재 중복
            duplicates += 1
            continue
        seen_hashes.add(h)

        result = rule_filter(article.title, article.body, track=track)
        rows.append({
            "url": article.url, "title": article.title, "press": article.press,
            "published_at": article.published_at, "source_channel": article.source_channel,
            "title_hash": h, "body_length": len(article.body),
            "rule_passed": result.passed, "llm_relevant": None,
            "matched_corps": json.dumps(result.matched_corps, ensure_ascii=False),
        })
        if result.passed:
            rule_passed.append((article, result.matched_corps, result.matched_names))

    print(f"  → 중복 {duplicates}건 제외, 1차 통과 {len(rule_passed)}건")

    by_url = {r["url"]: r for r in rows}

    # 라우터보다 **먼저** 본문을 확보한다
    # 본문을 먼저 확보하면 라우터 정밀도가 오르고, 그 뒤 gpt-4o 추출 비용을 헛되이 쓰지 않는다.
    if crawl_bodies:
        need_body = [a for a, _, _ in rule_passed if not a.body]
        if need_body:
            print(f"\n[3/5] 본문 크롤링 ({len(need_body)}건 — 본문 미확보분)")
            improved, failed = enrich_bodies(need_body)
            print(f"  → 확보 {improved}건, 실패 {failed}건")
            for article in need_body:
                by_url[article.url]["body_length"] = len(article.body)

    # 본문을 못 구한 건은 라우터에 넘기지 않는다 — 제목만으로는 판정이 부정확하고,
    # 통과해도 추출 단계에서 쓸 본문이 없다.
    routable = [(a, c, n) for a, c, n in rule_passed if a.body]
    dropped_no_body = len(rule_passed) - len(routable)

    # 라우터 예산은 채널별로 **균등 배분**한다.
    targets = _allocate_by_channel(routable, limit_router) if limit_router else routable

    print(f"\n[4/5] 2차 LLM 라우터 ({len(targets)}건"
          f"{f', 본문없어 제외 {dropped_no_body}건' if dropped_no_body else ''})")
    screened: list[ScreenedArticle] = []
    # 라우터 프롬프트는 트랙마다 다르다. both면 관계 기준으로 한 번 보고,
    # 탈락한 건을 리스크 기준으로 다시 본다 — 관계 기사와 사건 기사를 모두 살린다.
    verdicts = llm_router_batch([(a.title, a.body) for a, _, _ in targets],
                                track=("relation" if track != "risk" else "risk"))
    if track == "both":
        retry_idx = [i for i, (ok, _) in enumerate(verdicts) if not ok]
        if retry_idx:
            retried = llm_router_batch(
                [(targets[i][0].title, targets[i][0].body) for i in retry_idx],
                track="risk")
            for i, verdict in zip(retry_idx, retried):
                verdicts[i] = verdict
            recovered = sum(1 for ok, _ in retried if ok)
            print(f"  · 리스크 기준 재판정으로 {recovered}건 추가 통과")
    for (article, corps, names), (ok, reason) in zip(targets, verdicts):
        by_url[article.url]["llm_relevant"] = ok
        if ok:
            screened.append(ScreenedArticle(article, corps, names, reason))

    print(f"  → 최종 통과 {len(screened)}건 "
          f"(전체 대비 {len(screened)/max(len(articles),1)*100:.1f}%)")

    print("\n[5/5] news_articles 적재")
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, rows)
    print(f"  → {len(rows)}건 upsert")

    return screened
