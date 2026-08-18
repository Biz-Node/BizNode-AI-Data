"""기존 기사에 `topics` 를 채운다 — **한 번만 돌리면 되는 소급 작업.**

★왜 필요한가

  뉴스 화면의 축 1(공급망·지분·규제·사건)이 `topics` 를 쓴다. 그런데 컬럼을
  나중에 추가해서, `news_feed` 배치가 도는 오늘 이후 기사에만 값이 있다.
  기존 11,753건이 비어 있으면 **주제 필터에 오늘 기사만 나온다.**

★제목만 본다

  본문은 저장하지 않으므로(방법서 §8) 제목으로만 분류한다. 수집 시점에는
  본문까지 보고 분류하니 이 소급분이 조금 덜 잡힌다 — 그래도 없는 것보다 낫고,
  기사가 새로 들어오면 본문까지 본 값으로 덮인다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from app.core.database import postgres_connection
from pipeline.news.relevance import topics_of

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT url, title FROM news_articles
                           WHERE topics IS NULL
                             AND matched_corps IS NOT NULL
                             AND jsonb_array_length(matched_corps) > 0""")
            rows = cur.fetchall()
        print(f"■ 대상 {len(rows):,}건 (기업이 붙었는데 topics 가 빈 것)")

        stat, payload = Counter(), []
        for url, title in rows:
            tp = topics_of(title or "")
            for t in tp:
                stat[t] += 1
            if not tp:
                stat["(주제 없음)"] += 1
            payload.append({"u": url, "t": json.dumps(tp, ensure_ascii=False)})

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE news_articles SET topics = %(t)s::jsonb WHERE url = %(u)s",
                payload)
        conn.commit()

    print(f"  분포: {dict(stat)}")
    print(f"  → {len(payload):,}건 채움")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
