"""뉴스 → 관계 엣지 파이프라인.

RSS 수집 → dedup → 2단 게이트 → 관계 추출 → ER → staged_edges → evidence → Neo4j.
쓰기 순서는 P1과 동일(§5-2). 뉴스 엣지는 source_type="news", confidence=LLM 점수라
DART 관측(1.0)과 구분된다.

실행:
  python -m batch.build.news                 # 전체
  python -m batch.build.news --limit 30      # 추출 상한(비용 통제)
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import postgres_connection
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.news_loader import build_news_document
from pipeline.importer.event_er import resolve_events
from pipeline.importer.person_er import resolve_persons
from pipeline.importer.staging import stage_document
from pipeline.extractors.news.crawler import enrich_bodies
from pipeline.news.collector import collect_and_screen, pending_articles
from pipeline.news.extractor import extract_relations
from pipeline.normalizer.product_registry import prompt_block as product_prompt_block
from pipeline.normalizer import resolver
from pipeline.normalizer.relations import record_unmapped

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30, help="관계 추출 기사 상한(비용)")
    parser.add_argument("--router-limit", type=int, default=80, help="LLM 라우터 호출 상한")
    parser.add_argument("--naver", action="store_true",
                        help="네이버 검색 API로 관계 기사 추가 수집(키 필요)")
    parser.add_argument("--naver-seeds", type=int, default=0,
                        help="네이버 검색 대상 시드 수(0=전체). 호출량 통제용")
    parser.add_argument("--no-crawl", action="store_true", help="본문 크롤링 보강 생략")
    # ★기본이 PG 다. 수집은 `batch.build.news_feed` 가 매일 하고, 여기는 그 결과를
    #   이어받는다. 전에는 RSS 를 다시 받아서 **수집 배치가 저장한 기사를 못 봤다**
    #   (실측 2026-08-18: 미처리 6,403건). `--collect` 를 주면 옛 동작이다.
    parser.add_argument("--collect", action="store_true",
                        help="PG 를 읽지 않고 RSS·네이버를 새로 수집한다(옛 동작)")
    parser.add_argument("--dry-run", action="store_true",
                        help="추출하지 않고 **대상 수와 예상 비용만** 찍는다")
    parser.add_argument("--track", choices=["relation", "risk", "both"], default="relation",
                        help="relation=기업 간 관계 / risk=사건·리스크 / both=둘 다. "
                             "검색 키워드·규칙 필터·라우터 프롬프트가 트랙별로 달라진다")
    args = parser.parse_args()

    seed_names = None
    if args.naver:
        import json as _json
        from app.core.config import ETF_LIST_PATH
        with open(ETF_LIST_PATH, encoding="utf-8") as f:
            seed_names = [c["companyName"] for c in _json.load(f)["companies"]]
        if args.naver_seeds:
            seed_names = seed_names[: args.naver_seeds]

    all_evidence = []
    total_edges = total_invalid = total_unmapped = 0

    with postgres_connection() as conn:
        if args.collect:
            screened = collect_and_screen(
                conn, limit_router=args.router_limit,
                use_naver=args.naver, seed_names=seed_names,
                crawl_bodies=not args.no_crawl, track=args.track,
            )
        else:
            # 이미 모아 둔 것을 이어받는다 — 수집과 추출을 잇는 자리
            print("[1/5] PG 미처리 기사 읽기")
            screened = pending_articles(conn, limit=args.limit)
            print(f"  → {len(screened)}건")
            if screened and not args.no_crawl:
                # 본문은 저장하지 않으므로(방법서 §8) 추출 직전에 다시 받는다
                print(f"\n[2/5] 본문 크롤링 ({len(screened)}건)")
                ok, bad = enrich_bodies([s.article for s in screened])
                print(f"  → 확보 {ok}건, 실패 {bad}건")
                # 본문을 못 구하면 추출할 게 없다. 돈만 쓰고 빈손이 된다
                screened = [s for s in screened if s.article.body]
                print(f"  → 본문 있는 {len(screened)}건만 진행")

        targets = screened[: args.limit]

        if args.dry_run:
            # 라우터는 이미 통과한 것만 읽어 오므로 여기서는 추출 비용만 든다
            print(f"\n--dry-run — 추출 대상 {len(targets)}건 "
                  f"· 예상 {len(targets) * 14.7:,.0f}원")
            return 0

        print(f"\n[4/5] 관계 추출 ({len(targets)}건 기사, 상위 모델)")
        # 이미 쓰는 제품명을 프롬프트에 붙인다 — 표기가 갈려 노드가 쪼개지는 걸 막는다
        prod_block = product_prompt_block(conn)
        for i, s in enumerate(targets, 1):
            relations = extract_relations(s.article.title, s.article.body,
                                          s.matched_names, prod_block)

            # ★추출 완료 표시는 **시도한 즉시** 남긴다(2026-08-13 수정).
            #
            #   전에는 아래 `if not relations: continue` 뒤, 엣지를 만든 기사에만
            #   표시했다. 그래서 **엣지가 0건인 기사는 영원히 미완료로 남았다.**
            #   돈은 이미 냈는데 기록이 없으니, 나중에 보면 「안 뽑은 기사」와
            #   구분되지 않는다. 실측(2026-08-13): 네이버 경로 295건이 이 상태로
            #   쌓여 있었고, 다시 뽑으면 또 0건이 나올 것들이라 헛돈이 된다.
            #
            #   `pilot_company`는 처음부터 「시도한 전부에 표시」였다. 같은 규칙을
            #   여기에도 맞춘다 — 엣지가 안 나온 기사를 다시 뽑아도 또 안 나온다.
            with conn.cursor() as cur:
                cur.execute("UPDATE news_articles SET extracted_at = now() WHERE url = %s",
                            (s.article.url,))

            if not relations:
                continue
            doc, evs, unmapped = build_news_document(
                relations, s.article.url, s.article.title,
                s.article.published_at.date() if s.article.published_at else None,
                conn=conn,
            )
            n, invalid = stage_document(conn, f"news:{s.article.url}", doc)
            all_evidence.extend(evs)
            total_edges += n
            total_invalid += invalid

            # 12종에 못 넣은 표현을 누적 — 렉시콘 개정의 근거
            for u in unmapped:
                record_unmapped(conn, **u)
            total_unmapped += len(unmapped)

            drop_note = f", 미매핑 {len(unmapped)}" if unmapped else ""
            print(f"  [{i}/{len(targets)}] {s.article.title[:44]} "
                  f"→ 엣지 {n} (위반 {invalid}{drop_note})")

        print(f"  → 총 {total_edges}건 스테이징 "
              f"(매트릭스 위반 {total_invalid}건 차단, 미매핑 {total_unmapped}건 기록)")

        print(f"\n[5/5] evidence 임베딩 → ChromaDB ({len(all_evidence)}건)")
        upsert_evidence(conn, all_evidence)

    print("\nstaged_edges → Neo4j 적재")
    load_staged_to_neo4j()

    # 뉴스는 생년월 없는 Person을 새로 만든다(`이재용@news`). 적재 직후 ER을 돌려
    # DART 노드와 합치지 않으면 같은 사람이 경로 수만큼 쪼개진 채 남는다.
    print("\nPerson ER (뉴스 인물 ↔ DART 인물 병합)")
    er = resolve_persons()
    print(f"  → 병합 {er['merged']}건, 보류 {er['skipped']}건")

    # 같은 사건을 기사마다 다르게 이름 붙이면 노드가 갈린다
    # (「청주 공장 화재」/「청주4캠퍼스 화재」). 사건이 갈리면 그 사건이 어느 기업까지
    # 번졌는지가 한곳에 모이지 않아 리스크 추론이 조각난다.
    print("\nEvent ER (이름만 다른 동일 사건 병합)")
    ev_er = resolve_events()
    print(f"  → {ev_er['groups']}개 사건군에서 {ev_er['merged']}건 병합")

    resolver.close()
    print("\n✅ P2 뉴스 파이프라인 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
