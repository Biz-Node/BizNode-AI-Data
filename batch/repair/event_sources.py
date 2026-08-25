"""Event 노드에 **관련 기사 목록**을 채운다. 비용 0.

왜 필요한가 (2026-08-02)

「사건 상세」 화면이 기사를 **하나만** 보여줄 수 있었다. Event 566건 전부
`source_doc`이 스칼라 1개뿐이었기 때문이다:

    Event { source_doc: "https://…/article/123" }        ← 1건
    엣지에는 evidence_ids·source_docs 배열이 있는데      ← 여러 건
    노드에는 없다

엣지를 클러스터링할 때 근거를 배열로 누적했지만(`repair/edges.py`), **노드는
누적 대상이 아니었다.** 사건에 붙은 엣지들이 각자 다른 기사에서 왔는데도
사건 자체는 첫 기사만 기억한다.

이 도구는 **사건에 붙은 모든 엣지의 출처를 모아** 노드에 올린다. 새로 크롤링하지
않고 이미 있는 값을 옮길 뿐이라 비용이 없다.

★실측: 기사가 2건 이상인 사건은 566개 중 22개다(최대 6건). 나머지는 원래 1건이라
  달라지는 게 없다. 그래도 하는 이유는 **화면이 「기사 N건」을 정확히 셀 수 있어야**
  하고, 앞으로 수집이 늘면 이 값이 자연히 커지기 때문이다.

    python -m batch.repair.event_sources --dry-run
    python -m batch.repair.event_sources
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 사건에 **연결된 모든 엣지**에서 출처를 긁어모은다. 방향은 상관없다 —
# `Company -HAS_EVENT-> Event`와 `Event -IMPACTS-> Company` 둘 다 근거를 갖는다.
_COLLECT = """
MATCH (e:Event)-[r]-()
WITH e,
     collect(DISTINCT r.source_doc)
       + reduce(a = [], x IN collect(r.source_docs) | a + coalesce(x, [])) AS docs,
     collect(DISTINCT r.evidence_id)
       + reduce(a = [], x IN collect(r.evidence_ids) | a + coalesce(x, [])) AS evs
RETURN e.event_id AS eid, e.name AS name,
       [d IN apoc.coll.toSet(docs) WHERE d IS NOT NULL] AS docs,
       [v IN apoc.coll.toSet(evs)  WHERE v IS NOT NULL] AS evs
"""

_APPLY = """
MATCH (e:Event {event_id: $eid})
SET e.source_docs  = $docs,
    e.evidence_ids = $evs,
    e.article_count = $n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_COLLECT)]

    # 기사 URL만 센다 — DART 접수번호(14자리 숫자)는 「관련 뉴스」가 아니다
    for r in rows:
        r["urls"] = [d for d in r["docs"] if str(d).startswith("http")]

    tally = Counter(len(r["urls"]) for r in rows)
    print(f"Event {len(rows)}건 · 기사 URL 총 "
          f"{len({u for r in rows for u in r['urls']})}종\n")
    print("  기사 수 분포")
    for k in sorted(tally):
        print(f"    {k}건 → 사건 {tally[k]}개")

    multi = sorted((r for r in rows if len(r["urls"]) > 1),
                   key=lambda x: -len(x["urls"]))
    if multi:
        print(f"\n  기사가 여럿인 사건 {len(multi)}개 (지금은 1건만 보였다)")
        for r in multi[:8]:
            print(f"    {len(r['urls'])}건  {str(r['name'])[:48]}")

    # 화면에 제목·언론사·날짜를 붙일 수 있는지 확인 — URL이 뉴스 대장에 있어야 한다
    all_urls = sorted({u for r in rows for u in r["urls"]})
    with postgres_connection() as conn:
        known = conn.execute(
            "SELECT count(*) FROM news_articles WHERE url = ANY(%s)",
            (all_urls,)).fetchone()[0]
    print(f"\n  news_articles에서 제목·언론사·날짜를 찾을 수 있는 URL "
          f"{known}/{len(all_urls)}")
    if known < len(all_urls):
        print("    ⚠ 나머지는 URL만 보여줄 수 있습니다")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0

    with neo4j_session() as session:
        for r in rows:
            session.run(_APPLY, eid=r["eid"], docs=r["docs"], evs=r["evs"],
                        n=len(r["urls"]))
    print(f"\n✅ Event {len(rows)}건에 source_docs · evidence_ids · article_count 기록")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
