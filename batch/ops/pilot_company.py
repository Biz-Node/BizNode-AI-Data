"""기업 1개로 전체 파이프라인을 돌려 **깔때기를 실측**한다.

전체 64개사로 확장하기 전에 단계별 잔존율을 재는 것이 목적이다.
계획서의 추정치(URL 해석 80% · 크롤링 63% · 라우터 79%)는 네이버 기준을 구글에
적용한 것이라, 실제로 맞는지 확인해야 확장 비용을 예측할 수 있다.

흐름 (계획서 §3):
  구글 수집 → 제목 dedup → 규칙 필터 → **우선순위 정렬 + 상한**
  → URL 해석(네이버) → 본문 크롤링 → LLM 라우터 → 트리플 추출 → 적재 → ER

실행:
  python -m batch.ops.pilot_company 한미반도체 --years 3 --limit 100
  python -m batch.ops.pilot_company SK하이닉스 --years 1 --month-split --limit 50
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from app.core.database import postgres_connection
from pipeline.extractors.news.crawler import enrich_bodies
from pipeline.extractors.news.gnews import (
    FAILED_QUERIES, RateLimited, clear_checkpoint, collect_company, resolve_urls)
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.event_er import resolve_events
from pipeline.importer.extraction_ledger import record, resolve_corp_code
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.news_loader import build_news_document
from pipeline.importer.person_er import resolve_persons
from pipeline.importer.staging import stage_document
from pipeline.news.extractor import extract_relations
from pipeline.normalizer.product_registry import prompt_block as product_prompt_block
from pipeline.news.relevance import llm_router_batch, rule_filter
from pipeline.normalizer import resolver
from pipeline.normalizer.relations import record_unmapped

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 단가 (원/건) ────────────────────────────────────────────────
#
# 실측(2026-07-30, OpenAI 청구 기준): 10개사 252건 = $2.68 → 기사당 약 14.7원.
#
# ★단가를 두 번 틀렸다. 기록해 둔다:
#   · 처음 26원  — 「본문 6,000자 + 출력 넉넉히」로 잡은 **상한**. 실제 본문은
#                  평균 1,483자라 1.8배 과대였다.
#   · 다음 5.3원 — 중간 집계($0.97)를 최종값으로 착각했다. 대시보드 반영이
#                  늦어 실제의 1/3만 보이던 시점이었다.
#   교훈: 사용량 대시보드는 **배치가 끝난 뒤 충분히 기다려** 읽어야 한다.
#         중간값으로 단가를 고치면 예산 판단이 거꾸로 흔들린다.
_EXTRACT_KRW = 14.7      # gpt-4o 트리플 추출, 기사당 (2026-07-30 청구 실측)
_ROUTER_KRW = 0.25       # gpt-4o-mini 관련성 판정, 기사당

# ── 추출 우선순위 (계획서 §6-1) ──────────────────────────────────
# `--limit`이 "먼저 통과한 N건"을 뽑으면 이미 1,721개인 OWNS_STAKE_IN을 또 뽑는다.
# 부족한 것부터 가져오도록 정렬한다.
_DEFICIT_KEYWORDS = (      # 2순위 — 뉴스가 유일 경로인데 부족한 엣지
    "경쟁", "점유율", "맞대결", "추격",              # COMPETES_WITH
    "과징금", "제재", "공정위", "담합", "압수수색",   # REGULATES
    "소송", "특허침해", "제소", "손해배상",           # SUES
)
_RISK_KEYWORDS = (         # 3순위 — Event 브리지를 만든다
    "화재", "폭발", "가동중단", "리콜", "결함", "파업", "노동쟁의",
    "안전사고", "어닝쇼크", "적자전환", "공급망", "감산",
)


def _period_key(published, bucket: str = "quarter") -> str:
    """기사 발행일 → 기간 버킷 키. 날짜가 없으면 '미상'."""
    if not published:
        return "미상"
    if bucket == "month":
        return f"{published.year}-{published.month:02d}"
    if bucket == "year":
        return str(published.year)
    return f"{published.year}-Q{(published.month - 1) // 3 + 1}"


def allocate_by_period(passed: list[tuple], budget: int,
                       bucket: str = "quarter") -> list[tuple]:
    """추출 예산을 **기간별로 균등 배분**한다(라운드로빈).

    ★이 함수가 없을 때 무슨 일이 있었나 (2026-07-30 실측):
      정렬 2차 키가 `-published_at`(최신순)이라 예산 300건이 최근 기사에
      전부 소진됐다. 분기별 공급과 실제 추출을 대조하면 이렇게 나왔다.

          분기        규칙통과(공급)   실제추출
          2024-Q1          65           1     ← 65건 있는데 1건
          2024-Q2          73           0     ← 73건 있는데 0건
          2026-Q2         512          37

      공급이 없어서가 아니라 **선별이 최근으로 쏠려서** 과거가 빈 것이다.
      그래프가 「2026년 사건만 아는」 상태가 되어 시계열 추론이 불가능해진다.
      월분할 수집으로는 고쳐지지 않는다(수집이 아니라 선별 문제이므로).

    배분 규칙 — 라운드로빈으로 한 바퀴씩 돌며 뽑는다:
      · 각 기간에서 **우선순위 높은 것부터** 한 건씩
      · 공급이 적은 기간은 있는 만큼만 (강제로 채우지 않는다)
      · 남는 예산은 공급이 남은 기간으로 자동 흘러간다
    이러면 「기간마다 최소한은 본다 + 기사 많은 기간은 더 본다」가 동시에 된다.
    """
    if budget <= 0 or not passed:
        return []

    buckets: dict[str, list[tuple]] = defaultdict(list)
    for item in passed:
        buckets[_period_key(item[0].published_at, bucket)].append(item)

    # 각 기간 안에서는 우선순위 → 최신순
    for items in buckets.values():
        items.sort(key=lambda t: (priority(t[0].title, t[2]),
                                  -(t[0].published_at.timestamp()
                                    if t[0].published_at else 0)))

    keys = sorted(buckets)                      # 시간순 (미상은 맨 뒤로 밀림)
    out: list[tuple] = []
    idx = {k: 0 for k in keys}
    while len(out) < budget:
        moved = False
        for k in keys:
            if len(out) >= budget:
                break
            i = idx[k]
            if i < len(buckets[k]):
                out.append(buckets[k][i])
                idx[k] = i + 1
                moved = True
        if not moved:                           # 모든 기간이 바닥나면 종료
            break
    return out


def priority(title: str, matched_names: list[str]) -> int:
    """낮을수록 먼저 추출. 계획서 §6-1.

    2026-07-31 순서 변경: 리스크를 3위 → 1위, 시드2개↑를 1위 → 3위.

    원래 1위였던 「시드 2개 이상 언급」은 **기업↔기업 엣지의 유일한 출처**라는
    가정에서 나왔다. 그런데 실측이 그 가정을 지지하지 않았다:

        등급          기사당 엣지   기사당 시드↔시드
        1 시드2개↑        3.40         0.55
        2 부족엣지        2.92         0.52
        3 리스크          3.46         0.58   ← 오히려 가장 높다
        4 기타            2.82         0.51

    네 등급의 산출이 사실상 같다. 그런데 시드2개↑ 기사는 **매달 수십 편**이라
    저밀도에서 다른 유형을 전부 밀어냈다:

        월        시드2개↑  부족엣지  리스크
        2026-01      47       6       3
        2026-03      38      16       1
        → 상위 3편만 뽑으면 리스크가 밀려나는 달: 11/12

    리스크·경쟁·규제·소송은 **뉴스가 유일한 경로**다(DART에 없다). 반면
    시드 2개가 같이 나오는 기사는 흔하고 대체 경로도 있다. 그래서 희소한 쪽을
    위로 올린다.

    ※ 고정 할당(각 유형에 몫 배정)은 쓰지 않는다. 그 달에 리스크만 5건이면
      5건 다 뽑혀야 하는데, 할당은 억지로 다른 유형을 끼워넣기 때문이다.
      순서만 바꾸면 공급이 쏠린 달에도 자연스럽게 대응된다.
    """
    if any(k in title for k in _RISK_KEYWORDS):
        return 1                                    # 사건 — 뉴스가 유일 경로
    if any(k in title for k in _DEFICIT_KEYWORDS):
        return 2                                    # 경쟁·규제·소송 — 뉴스가 유일 경로
    if len(set(matched_names)) >= 2:
        return 3                                    # 시드 2개 이상 언급
    return 4


_SELECT_EXTRACTED = ("SELECT title_hash FROM news_articles "
                     "WHERE title_hash IS NOT NULL AND extracted_at IS NOT NULL")

# 추출한 기사에 **완료 표시**를 남긴다. 이게 없으면 다음 실행이 또 뽑는다.
_MARK_EXTRACTED = ("UPDATE news_articles SET extracted_at = now() "
                   "WHERE url = ANY(%s)")


def _extracted_hashes(conn) -> set[str]:
    """이미 트리플 추출을 마친 기사의 title_hash 집합."""
    with conn.cursor() as cur:
        cur.execute(_SELECT_EXTRACTED)
        return {r[0] for r in cur.fetchall()}


_UPSERT = """
INSERT INTO news_articles (url, title, press, published_at, source_channel, title_hash,
                           body_length, rule_passed, llm_relevant, matched_corps)
VALUES (%(url)s, %(title)s, %(press)s, %(published_at)s, %(source_channel)s,
        %(title_hash)s, %(body_length)s, %(rule_passed)s, %(llm_relevant)s,
        %(matched_corps)s)
ON CONFLICT (url) DO UPDATE SET
    rule_passed=EXCLUDED.rule_passed, llm_relevant=EXCLUDED.llm_relevant,
    matched_corps=EXCLUDED.matched_corps, body_length=EXCLUDED.body_length
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("company")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--month-split", action="store_true",
                    help="대형주 — 분기로도 100건 상한에 걸릴 때")
    ap.add_argument("--limit", type=int, default=100, help="트리플 추출 상한(비용)")
    # 깔때기 실측(2026-07-28 한미반도체): URL해석 71% × 크롤링 70% × 라우터 79% = 39%
    # → 추출 N건을 채우려면 N/0.39 ≈ 2.6배가 필요하다. 여유를 둬 3으로 잡는다.
    # (5로 잡았더니 197건이 남는데 100건만 써서 네이버 호출 245회를 낭비했다.
    #  64개사로 확장하면 5배는 일 한도 25,000회를 넘긴다.)
    ap.add_argument("--bucket", choices=["month", "quarter", "year"],
                    default="quarter",
                    help="기간 균등 배분의 단위. 대형주는 month가 촘촘하다")
    ap.add_argument("--resolve-factor", type=int, default=3,
                    help="URL 해석 대상 = limit × 이 값 (깔때기 손실 보전)")
    args = ap.parse_args()

    funnel: list[tuple[str, int, str]] = []

    print(f"{'='*72}\n{args.company} · 최근 {args.years}년 · 추출 상한 {args.limit}건\n{'='*72}")

    # ① 수집
    # ★어떤 속도로 던졌는지 로그에 남긴다 — 나중에 「그때 왜 통과/차단됐나」를
    #   되짚으려면 이 값이 있어야 한다(2026-08-04에 딜레이를 실험 중).
    from pipeline.extractors.news.gnews import _DELAY as _GD
    print(f"\n[1/8] 구글 뉴스 수집  (질의 간격 {_GD}초)")
    try:
        articles = collect_company(args.company, years=args.years,
                                   month_split=args.month_split)
    except RateLimited as exc:
        # ★수집이 막히면 **대장에 기록하지 않고** 실패로 끝낸다.
        #   2026-07-31에 심텍이 480개 질의를 전부 503으로 실패했는데도
        #   「수집 0 · 추출 0 · 완료」로 대장에 남아 다시 돌지 않게 됐다.
        print(f"\n❌ 수집 중단: {exc}")
        print(f"   대장에 기록하지 않습니다 — 나중에 다시 돌 수 있습니다.")
        # ★여기까지 받아온 것은 **버리지 않는다**(2026-08-03). 이전에는 메모리의
        #   458건이 통째로 사라져, 다음 실행이 2026-08부터 똑같은 400질의를 다시
        #   던지고 또 같은 자리에서 막혔다 — 그래서 뒤쪽 기간에 영영 못 닿았다.
        print(f"   ↻ 받아온 기간까지는 저장했습니다 — 다시 돌리면 **거기서 이어갑니다**")
        if FAILED_QUERIES:
            print(f"   실패한 질의 {len(FAILED_QUERIES)}건")
        # ★차단과 회선 오류를 **다른 코드로** 돌려준다(2026-08-12).
        #   전에는 둘 다 3이라 배치가 구분을 못 했다. 실측: 하이젠알앤엠이
        #   ReadTimeout 8연속으로 끊겼는데 `run_companies`가 「⛔ 구글 속도 제한 —
        #   시간을 두고 재개하세요」라고 찍었다. 실제로는 **바로 재시도해도 되는**
        #   상황이었고, 확인해 보니 구글은 200을 잘 돌려주고 있었다.
        #   잘못된 안내는 몇 시간을 버리게 만든다.
        #     3 = 구글이 막았다   → 3시간 규칙을 지켜야 한다
        #     5 = 회선이 끊겼다   → 바로 다시 돌려도 된다
        return 5 if "회선 오류" in str(exc) else 3
    funnel.append(("수집(제목 dedup 후)", len(articles), "무료"))

    # 수집이 0건이면 그 뒤 단계가 무의미하다. 이것도 기록하지 않는다.
    if not articles:
        print(f"\n❌ 수집 0건 — 대장에 기록하지 않습니다.")
        return 3

    # ② 규칙 필터 — 제목 기준(구글은 본문을 주지 않는다)
    print(f"\n[2/8] 규칙 필터 (제목 기준)")
    passed: list[tuple] = []
    for a in articles:
        r = rule_filter(a.title, "", track="both")
        if r.passed:
            passed.append((a, r.matched_corps, r.matched_names))
    funnel.append(("규칙 통과", len(passed), "무료"))
    print(f"  → {len(passed)}/{len(articles)} ({len(passed)/max(len(articles),1)*100:.0f}%)")

    # ③ 이미 추출한 기사 제외 → 우선순위 정렬 + 기간 균등 배분 + 상한
    #
    # ★2026-07-31 수정: 여기가 비어 있어서 **같은 기사를 다시 추출**하고 있었다.
    #   `collector.py`에는 `_extracted_title_hashes()`가 있는데 네이버 경로에만
    #   쓰이고 구글 경로(이 파일)에서는 호출되지 않았다. 실측: news_articles
    #   5,618건 중 `extracted_at` 표시가 154건뿐인데 실제 추출은 700건이 넘었다.
    #
    #   증분 확장(150건 하고 나중에 300건으로 올리기)의 전제가 이것이다.
    #   표시가 없으면 재실행할 때마다 앞서 뽑은 것을 다시 뽑아 **돈을 두 번 낸다**.
    with postgres_connection() as conn:
        seen = _extracted_hashes(conn)
    fresh = [t for t in passed if t[0].title_hash not in seen]
    if len(fresh) < len(passed):
        print(f"  · 이미 추출한 기사 {len(passed)-len(fresh)}건 제외 "
              f"(남은 후보 {len(fresh)}건)")
    budget = args.limit * args.resolve_factor
    targets = allocate_by_period(fresh, budget, bucket=args.bucket)
    dist = {}
    for a, _, names in targets:
        p = priority(a.title, names)
        dist[p] = dist.get(p, 0) + 1
    print(f"\n[3/8] 우선순위 정렬 + 기간 균등 배분 → {len(targets)}건 선택")
    labels = {1: "시드2개↑", 2: "부족엣지", 3: "리스크", 4: "기타"}
    print("  " + " · ".join(f"{labels[k]} {v}" for k, v in sorted(dist.items())))
    per = defaultdict(int)
    for a, _, _ in targets:
        per[_period_key(a.published_at, args.bucket)] += 1
    if len(per) > 1:
        line = " · ".join(f"{k}:{v}" for k, v in sorted(per.items()) if k != "미상")
        print(f"  기간 분포  {line}"
              + (f" · 미상:{per['미상']}" if per.get("미상") else ""))

    # ④ URL 해석
    print(f"\n[4/8] URL 해석 (네이버 재검색)")
    resolved_arts = resolve_urls([a for a, _, _ in targets])
    by_hash = {a.title_hash: (c, n) for a, c, n in targets}
    funnel.append(("URL 해석", len(resolved_arts), f"네이버 {len(targets)}회"))

    # ⑤ 본문 크롤링
    print(f"\n[5/8] 본문 크롤링")
    improved, failed = enrich_bodies(resolved_arts)
    with_body = [a for a in resolved_arts if a.body]
    print(f"  → 확보 {len(with_body)}/{len(resolved_arts)} "
          f"({len(with_body)/max(len(resolved_arts),1)*100:.0f}%)")
    funnel.append(("본문 확보", len(with_body), "무료(시간)"))

    # ⑥ 라우터
    print(f"\n[6/8] LLM 라우터")
    verdicts = llm_router_batch([(a.title, a.body) for a in with_body], track="relation")
    retry = [i for i, (ok, _) in enumerate(verdicts) if not ok]
    if retry:
        again = llm_router_batch([(with_body[i].title, with_body[i].body) for i in retry],
                                 track="risk")
        for i, v in zip(retry, again):
            verdicts[i] = v
        print(f"  · 리스크 기준 재판정으로 {sum(1 for ok, _ in again if ok)}건 추가 통과")
    screened = [a for a, (ok, _) in zip(with_body, verdicts) if ok]
    print(f"  → 통과 {len(screened)}/{len(with_body)} "
          f"({len(screened)/max(len(with_body),1)*100:.0f}%)")
    funnel.append(("라우터 통과", len(screened),
                   f"{len(with_body)*_ROUTER_KRW:,.0f}원"))

    # ⑦ 트리플 추출
    extract_targets = screened[: args.limit]
    print(f"\n[7/8] 트리플 추출 ({len(extract_targets)}건)")
    all_ev, total_edges, total_invalid, total_unmapped = [], 0, 0, 0
    rows: list[dict] = []

    with postgres_connection() as conn:
        # 이미 쓰는 제품명을 프롬프트에 붙인다 — 표기가 갈려 노드가 쪼개지는 걸 막는다
        prod_block = product_prompt_block(conn)
        for i, a in enumerate(extract_targets, 1):
            corps, names = by_hash.get(a.title_hash, ([], []))
            rels = extract_relations(a.title, a.body, names, prod_block)
            if not rels:
                continue
            doc, evs, unmapped = build_news_document(
                rels, a.url, a.title,
                a.published_at.date() if a.published_at else None, conn=conn)
            n, invalid = stage_document(conn, f"news:{a.url}", doc)
            all_ev.extend(evs)
            total_edges += n
            total_invalid += invalid
            for u in unmapped:
                record_unmapped(conn, **u)
            total_unmapped += len(unmapped)
            if i % 10 == 0 or i == len(extract_targets):
                print(f"  [{i}/{len(extract_targets)}] 누적 엣지 {total_edges}")

        print(f"  → 엣지 {total_edges} (매트릭스 위반 {total_invalid} 차단, "
              f"미매핑 {total_unmapped} 기록)")
        funnel.append(("트리플 엣지", total_edges,
                       f"{len(extract_targets)*_EXTRACT_KRW:,.0f}원"))

        # 기사 메타 적재
        for a in resolved_arts:
            corps, _ = by_hash.get(a.title_hash, ([], []))
            rows.append({
                "url": a.url, "title": a.title, "press": a.press,
                "published_at": a.published_at, "source_channel": a.source_channel,
                "title_hash": a.title_hash, "body_length": len(a.body),
                "rule_passed": True,
                "llm_relevant": a in screened,
                "matched_corps": json.dumps(corps, ensure_ascii=False),
            })
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, rows)

        print(f"\n[8/8] evidence → ChromaDB ({len(all_ev)}건)")
        upsert_evidence(conn, all_ev)

        # ★추출 완료 표시 — 다음 실행이 같은 기사를 다시 뽑지 않도록.
        #   추출을 **시도한** 전부에 남긴다(엣지가 0건이어도 마찬가지).
        #   엣지가 안 나온 기사를 다시 뽑아도 또 안 나오므로 돈만 든다.
        with conn.cursor() as cur:
            cur.execute(_MARK_EXTRACTED, ([a.url for a in extract_targets],))
        print(f"  · 추출 완료 표시 {len(extract_targets)}건")

        # 진행 이력 기록 — 64개사를 여러 번에 나눠 돌리므로 어디까지 했는지 남긴다
        record(conn,
               corp_code=resolve_corp_code(conn, args.company) or "",
               company_name=args.company, years=args.years,
               month_split=args.month_split, extract_limit=args.limit,
               collected=len(articles), rule_passed=len(passed),
               url_resolved=len(resolved_arts), body_ok=len(with_body),
               router_passed=len(screened), extracted=len(extract_targets),
               edges=total_edges,
               cost_krw=round(len(extract_targets) * _EXTRACT_KRW
                              + len(with_body) * _ROUTER_KRW),
               note=f"track=both, resolve_factor={args.resolve_factor}")

    # ★여기서야 중간본을 지운다 — 기사가 `news_articles`에 들어갔고 대장에도
    #   기록됐다. 앞 단계 어디서든 죽으면 중간본이 남아 **구글을 다시 안 친다.**
    #   (2026-08-10 덕산네오룩스: 375건을 40분에 받고 다음 단계에서 DB가 안 떠
    #    죽었는데, 중간본이 이미 지워져 40분을 통째로 다시 써야 했다.)
    clear_checkpoint(args.company, args.years, args.month_split)

    print("\nstaged_edges → Neo4j")
    load_staged_to_neo4j()
    print("Person ER"); pe = resolve_persons()
    print(f"  → 병합 {pe['merged']}")
    print("Event ER"); ee = resolve_events()
    print(f"  → {ee['groups']}개 사건군 {ee['merged']}건 병합")
    resolver.close()

    # ── 깔때기 보고 ──────────────────────────────────────────
    print(f"\n{'='*72}\n깔때기 실측 — {args.company}\n{'='*72}")
    print(f"{'단계':22} {'건수':>8} {'잔존율':>8}  비용")
    print("-" * 62)
    base = funnel[0][1] or 1
    prev = base
    for label, n, cost in funnel:
        print(f"{label:22} {n:>8,} {n/base*100:>7.1f}%  {cost}")
        prev = n
    print(f"\n추출 {len(extract_targets)}건 → 엣지 {total_edges}건 "
          f"(기사당 {total_edges/max(len(extract_targets),1):.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
