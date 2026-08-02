"""저장된 기사 전체에 **현재 규칙 필터를 다시 적용**한다.

`rule_passed`는 수집 시점의 규칙으로 판정된 값이다. 그런데 규칙(시드 목록·키워드)은
계속 바뀐다. 그러면 **과거 수집분이 낡은 판정으로 남는다.**

실측(2026-07-31): 저장된 5,618건에 현재 규칙을 다시 적용했더니 34건이
탈락→통과로 바뀌었다. 그리고 그 34건이 정확히 우리가 원하던 것들이었다:

    SK하이닉스 청주공장 잇단 사고…지역사회 불안
    판례로 본 삼성전자 성과급 파업…어디까지 합법인가
    KB증권 "지소미아 종료, 삼성전자·SK하이닉스 생산 차질 우려"

수집도 규칙 필터도 **무료**다. 그러니 규칙을 고칠 때마다 전체를 다시 거르는 게 맞다.
새로 수집한 것만 판정하면 과거는 영영 낡은 채로 남는다.

    python -m batch.ops.refilter --dry-run
    python -m batch.ops.refilter

★추출 파이프라인 앞에 붙여 매번 돌리는 것을 권장한다(`pilot_company` 전에).
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import postgres_connection
from pipeline.news.relevance import rule_filter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--track", default="both",
                   choices=["relation", "risk", "both"])
    args = ap.parse_args()

    with postgres_connection() as conn:
        rows = conn.execute(
            "SELECT url, title, rule_passed, matched_corps FROM news_articles "
            "WHERE title IS NOT NULL").fetchall()
        print(f"저장된 기사 {len(rows):,}건에 현재 규칙 재적용")

        gained: list[tuple] = []      # 탈락 → 통과
        lost: list[tuple] = []        # 통과 → 탈락
        updates: list[tuple] = []
        for url, title, was, corps in rows:
            r = rule_filter(title, "", track=args.track)
            if bool(r.passed) == bool(was):
                continue
            (gained if r.passed else lost).append((title, r))
            updates.append((r.passed, r.corps if hasattr(r, "corps") else corps, url))

        print(f"  ↑ 탈락→통과 {len(gained):,}건")
        print(f"  ↓ 통과→탈락 {len(lost):,}건")

        for label, items in (("새로 통과", gained), ("새로 탈락", lost)):
            if items:
                print(f"\n[{label}] 예시:")
                for title, _ in items[:8]:
                    print(f"   {title[:78]}")
                if len(items) > 8:
                    print(f"   … 외 {len(items)-8}건")

        if not updates:
            print("\n바뀐 판정이 없습니다.")
            return 0
        if args.dry_run:
            print("\n[dry-run] 변경 없음")
            return 0

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE news_articles SET rule_passed = %s WHERE url = %s",
                [(p, u) for p, _, u in updates])
        print(f"\n✅ {len(updates):,}건 판정 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
