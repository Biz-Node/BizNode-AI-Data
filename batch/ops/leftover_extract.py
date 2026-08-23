"""라우터는 통과했는데 **추출 상한에 걸려 못 본 기사**를 마저 뽑는다.

왜 따로 두는가 (2026-08-13)

`pilot_company`는 라우터 통과분을 `--limit`(기본 240)까지만 추출한다. 대형 기업은
여기 걸린다. 실측:

    현대모비스   라우터 363 → 추출 240   못 본 것 123
    LG전자      라우터 353 → 추출 240   못 본 것 113
    삼성전자     라우터 244 → 추출 240   못 본 것   4
    ─────────────────────────────────────────────
    전체 남은 것 553건 (54개사 중 상한에 걸린 건 위 3곳뿐)

기업을 다시 돌리면 안 되는 이유

`run_companies`는 **대장에 없는 기업**만 집으므로 이 셋은 영영 안 잡힌다. 그렇다고
`pilot_company`를 다시 돌리면 **구글 검색을 처음부터 다시** 한다:

    재실행              이 스크립트
    ────────────      ────────────
    구글 질의 수백 회     구글 안 씀
    45분 + 차단 위험     언론사 크롤만
    같은 기사를 또 모음    못 본 것만 정확히

기사 URL이 `news_articles`에 남아 있어서 **검색을 건너뛸 수 있다.** 본문은
저장하지 않는 설계(방법서 §8)라 크롤링만 다시 한다 — 시간은 들지만 돈도
차단 위험도 없다.

★라우터도 건너뛴다 — `llm_relevant=true`는 이미 통과했다는 뜻이다. 다시 물으면
  같은 답에 돈만 든다.

    python -m batch.ops.leftover_extract --dry-run
    python -m batch.ops.leftover_extract --limit 200
    python -m batch.ops.leftover_extract --company 현대모비스
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import postgres_connection
from batch.ops.pilot_company import _EXTRACT_KRW, _MARK_EXTRACTED
from pipeline.extractors.news.crawler import enrich_bodies
from pipeline.extractors.news.rss import Article
from pipeline.importer.news_loader import build_news_document
from pipeline.importer.staging import stage_document
from pipeline.news.extractor import extract_relations
from pipeline.normalizer.product_registry import prompt_block as product_prompt_block
from pipeline.normalizer.relations import record_unmapped
from pipeline.importer.evidence import upsert_evidence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 라우터를 통과했으나 추출 표시가 없는 기사.
# ★`body_length > 0`을 요구한다 — 본문을 한 번도 못 구한 기사는 이번에도 못 구할
#   가능성이 높다(사이트가 막혔거나 사라졌다). 크롤 시간을 거기 쓰지 않는다.
_LEFTOVER = """
SELECT url, title, press, published_at, source_channel, matched_corps
  FROM news_articles
 WHERE llm_relevant IS TRUE
   AND extracted_at IS NULL
   AND body_length > 0
 ORDER BY published_at DESC NULLS LAST
"""

_CORP_NAMES = "SELECT corp_code, corp_name FROM corp_code_master WHERE corp_code = ANY(%s)"


def _hint_names(conn, rows: list[dict]) -> dict[str, list[str]]:
    """기사 URL → 시드 기업 이름들.

    `matched_corps`에는 corp_code만 있는데 `extract_relations`는 **이름**을 힌트로
    받는다. 코드로 주면 프롬프트에서 아무 뜻이 없다.
    """
    codes = sorted({c for r in rows for c in (r["matched_corps"] or [])})
    if not codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(_CORP_NAMES, (codes,))
        name_of = dict(cur.fetchall())
    return {r["url"]: [name_of[c] for c in (r["matched_corps"] or []) if c in name_of]
            for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="이번에 처리할 기사 수 상한(비용 통제)")
    ap.add_argument("--company", help="이 기업이 언급된 기사만")
    args = ap.parse_args()

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_LEFTOVER)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # 기업 지정 시 그 기업의 corp_code가 matched_corps에 든 것만
        if args.company:
            with conn.cursor() as cur:
                cur.execute("SELECT corp_code FROM corp_code_master WHERE corp_name = %s",
                            (args.company,))
                got = cur.fetchone()
            if not got:
                print(f"✗ 「{args.company}」를 법인 명부에서 못 찾았습니다.")
                return 1
            rows = [r for r in rows if got[0] in (r["matched_corps"] or [])]

        print("=" * 68)
        print(f"  못 본 기사 마저 추출 — 대상 {len(rows):,}건")
        print("=" * 68)

        # 어느 기업 것인지 보여준다(사용자가 규모를 가늠하게)
        tally: dict[str, int] = {}
        for r in rows:
            for c in (r["matched_corps"] or []):
                tally[c] = tally.get(c, 0) + 1
        if tally:
            with conn.cursor() as cur:
                cur.execute(_CORP_NAMES, (sorted(tally),))
                name_of = dict(cur.fetchall())
            for code, n in sorted(tally.items(), key=lambda x: -x[1])[:8]:
                print(f"   {name_of.get(code, code):<16}{n:>5}건")

        if args.limit:
            rows = rows[:args.limit]
            print(f"\n   → 이번에는 {len(rows):,}건만 처리합니다 (--limit)")

        cost = len(rows) * _EXTRACT_KRW
        print(f"\n   추출 비용 약 {cost:,.0f}원 · 구글 질의 0회 (차단 위험 없음)")

        if args.dry_run or not rows:
            print("\n[dry-run] 크롤링도 추출도 하지 않았습니다."
                  if args.dry_run else "\n· 남은 기사가 없습니다.")
            return 0

        names_by_url = _hint_names(conn, rows)

        # ── 본문 크롤링 (구글 없음) ─────────────────────────
        arts = [Article(url=r["url"], title=r["title"], press=r["press"] or "",
                        published_at=r["published_at"], body="",
                        source_channel=r["source_channel"] or "rss")
                for r in rows]
        print(f"\n[1/3] 본문 크롤링 {len(arts):,}건")
        enrich_bodies(arts)
        with_body = [a for a in arts if a.body]
        print(f"  → 확보 {len(with_body):,}/{len(arts):,} "
              f"({len(with_body) / max(len(arts), 1) * 100:.0f}%)")

        # ── 추출·적재 ────────────────────────────────────
        print(f"\n[2/3] 트리플 추출 {len(with_body):,}건")
        all_ev, edges, invalid, unmapped_n = [], 0, 0, 0
        prod_block = product_prompt_block(conn)
        for i, a in enumerate(with_body, 1):
            rels = extract_relations(a.title, a.body,
                                     names_by_url.get(a.url, []), prod_block)
            if rels:
                doc, evs, unmapped = build_news_document(
                    rels, a.url, a.title,
                    a.published_at.date() if a.published_at else None, conn=conn)
                n, inv = stage_document(conn, f"news:{a.url}", doc)
                all_ev.extend(evs)
                edges += n
                invalid += inv
                for u in unmapped:
                    record_unmapped(conn, **u)
                unmapped_n += len(unmapped)
            if i % 20 == 0 or i == len(with_body):
                print(f"  [{i}/{len(with_body)}] 누적 엣지 {edges}")
        print(f"  → 엣지 {edges} (매트릭스 위반 {invalid} 차단, 미매핑 {unmapped_n} 기록)")

        print(f"\n[3/3] evidence → ChromaDB ({len(all_ev)}건)")
        upsert_evidence(conn, all_ev)

        # ★본문을 못 구한 것도 완료로 표시한다. 다음 실행이 또 크롤을 시도하면
        #   같은 사이트에서 또 막힌다 — 시간만 든다.
        with conn.cursor() as cur:
            cur.execute(_MARK_EXTRACTED, ([a.url for a in arts],))

        print(f"\n✅ 엣지 {edges}건 추가 · 기사 {len(arts):,}건 완료 표시")
        print(f"   정리·검사는 따로: python -m batch.ops.finalize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
