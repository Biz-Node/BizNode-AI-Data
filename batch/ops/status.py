"""추출 진행 현황 — 어느 기업을 얼마나 돌렸나.

64개사를 예산에 맞춰 여러 번에 나눠 돌린다. 이 스크립트가 **진행/미진행 목록**과
누적 비용을 보여준다. 다음에 무엇을 돌릴지 여기서 정한다.

실행:
  python -m batch.ops.status              # 현황
  python -m batch.ops.status --pending    # 미진행만
  python -m batch.ops.status --plan 10    # 다음 10개사 추천
  python -m batch.ops.status --write-doc  # 진행현황 문서 갱신

★진행 현황은 **문서에 손으로 쓰지 않는다.** 대장(PostgreSQL `extraction_runs`)이
  유일한 사실이고, 문서는 `--write-doc`으로 거기서 **생성**한다. 손으로 적으면
  배치를 돌린 다음날부터 어긋나기 시작한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.extraction_ledger import ensure_table, summary

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 밸류체인 응집도 — 함께 돌리면 서로 연결될 가능성이 높은 순서.
# 무작위로 고르면 서로 안 이어져 2홉 경로가 안 생긴다.
VALUE_CHAIN_PRIORITY = [
    # 메모리 (허브)
    "삼성전자", "SK하이닉스",
    # 후공정·검사 장비
    "한미반도체", "이오테크닉스", "테크윙", "두산테스나", "SFA반도체", "하나마이크론",
    # 전공정 장비
    "주성엔지니어링", "원익IPS", "테스", "HPSP", "피에스케이", "유진테크", "제우스",
    # 소재·부품
    "티씨케이", "원익머트리얼즈", "동진쎄미켐", "솔브레인", "하나머티리얼즈",
    # 검사·계측
    "고영", "넥스틴", "오로스테크놀로지", "인텍플러스",
    # 팹리스·기타 반도체
    "DB하이텍", "LX세미콘", "가온칩스", "제주반도체", "심텍", "리노공업", "ISC",
    # 로봇 (별도 클러스터)
    "레인보우로보틱스", "두산로보틱스", "유진로봇", "로보티즈", "뉴로메카",
    "유일로보틱스", "나우로보틱스", "에스피지", "하이젠알앤엠",
    # 전장·완성품
    "현대모비스", "LG전자", "LG이노텍", "현대오토에버", "삼성에스디에스",
]


DOC_PATH = Path("BizNode_추출_진행현황.md")


def write_doc(seeds: list[dict], done: list[dict], todo: list[dict],
              graph: dict, path: Path) -> None:
    """대장 + 그래프 상태로 진행현황 문서를 **생성**한다(손으로 쓰지 않는다)."""
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for s in seeds:
        by_sector[" · ".join(s.get("sector") or ["기타"])].append(s)
    done_codes = {c["corpCode"] for c in done}

    total_cost = sum((c["rec"]["total_cost"] or 0) for c in done)
    total_edges = sum((c["rec"]["total_edges"] or 0) for c in done)

    L: list[str] = []
    L.append("# BizNode 뉴스 추출 진행현황")
    L.append("")
    L.append("> ⚠ **이 문서는 자동 생성됩니다. 직접 고치지 마세요.**")
    L.append("> `python -m batch.ops.status --write-doc` 으로 갱신하세요.")
    L.append("> 사실의 출처는 PostgreSQL `extraction_runs` 대장입니다.")
    L.append("")
    L.append(f"생성 시각: {graph['now']}")
    L.append("")
    L.append("## 요약")
    L.append("")
    L.append("| 항목 | 값 |")
    L.append("|---|---:|")
    L.append(f"| 시드 기업 | {len(seeds)} |")
    L.append(f"| 추출 완료 | **{len(done)}** |")
    L.append(f"| 미진행 | {len(todo)} |")
    L.append(f"| 누적 추출 비용 | {total_cost:,}원 |")
    L.append(f"| 뉴스 엣지(대장 누계) | {total_edges:,} |")
    for key, label in (("nodes", "그래프 전체 노드"), ("edges", "그래프 전체 엣지"),
                       ("news_edges", "그래프 뉴스 엣지"),
                       ("events", "Event 노드"), ("products", "Product 노드")):
        if key in graph:
            L.append(f"| {label} | {graph[key]:,} |")
    L.append("")

    L.append("## 완료 기업")
    L.append("")
    L.append("| 기업 | 시장 | 섹터 | 추출 기사 | 생성 엣지 | 비용 | 최근 실행 |")
    L.append("|---|---|---|---:|---:|---:|---|")
    for c in sorted(done, key=lambda x: -(x["rec"]["total_edges"] or 0)):
        r = c["rec"]
        L.append(f"| {c['companyName']} | {c.get('market','-')} | "
                 f"{' · '.join(c.get('sector') or ['-'])} | "
                 f"{r['total_extracted'] or 0} | {r['total_edges'] or 0} | "
                 f"{r['total_cost'] or 0:,}원 | {str(r['last_run'])[:10]} |")
    L.append("")

    L.append("## 미진행 기업 (섹터별)")
    L.append("")
    for sector in sorted(by_sector, key=lambda k: (-len(by_sector[k]), k)):
        rows = by_sector[sector]
        pending = [r for r in rows if r["corpCode"] not in done_codes]
        L.append(f"### {sector} — {len(rows)-len(pending)}/{len(rows)} 완료")
        L.append("")
        if not pending:
            L.append("전부 완료.")
        else:
            for i in range(0, len(pending), 4):
                L.append("- " + " · ".join(
                    f"{p['companyName']}({p['market']})" for p in pending[i:i + 4]))
        L.append("")

    order = {n: i for i, n in enumerate(VALUE_CHAIN_PRIORITY)}
    picks = sorted(todo, key=lambda c: order.get(c["companyName"], 999))[:10]
    if picks:
        L.append("## 다음 배치 추천 (밸류체인 응집 순)")
        L.append("")
        L.append("무작위로 고르면 서로 안 이어져 2홉 경로가 생기지 않습니다.")
        L.append("이미 stub으로 등장하는 기업을 먼저 하면 새 노드를 만드는 대신")
        L.append("**기존 stub을 실제 데이터로 채우게** 되어 연결이 촘촘해집니다.")
        L.append("")
        for i, c in enumerate(picks, 1):
            L.append(f"{i}. `python -m batch.ops.pilot_company {c['companyName']} "
                     f"--years 3 --limit 100`")
        L.append("")
        L.append(f"예상 비용 약 {len(picks) * 2600:,}원 "
                 f"(≈ ${len(picks) * 2600 / 1380:.1f})")
        L.append("")

    L.append("## 배치 실행 순서")
    L.append("")
    L.append("```bash")
    L.append("# 1) 추출 — 기업별로 (대장에 자동 기록됨)")
    L.append("python -m batch.ops.pilot_company <기업명> --years 3 --limit 100")
    L.append("")
    L.append("# 2) 정리·검사 — 배치가 끝나면 한 번 (전량 대상, 증분 실행)")
    L.append("python -m batch.ops.finalize")
    L.append("")
    L.append("# 3) 표본 심층검사 — 사람이 12건 정독 (새 실패 유형 찾기)")
    L.append("python -m batch.audit.spot_check --per-type 1 --source news")
    L.append("")
    L.append("# 4) 이 문서 갱신")
    L.append("python -m batch.ops.status --write-doc")
    L.append("```")
    L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    print(f"✅ {path} 갱신 ({len(L)}줄)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pending", action="store_true", help="미진행 기업만")
    ap.add_argument("--plan", type=int, metavar="N", help="다음 N개사 추천")
    ap.add_argument("--write-doc", action="store_true",
                    help=f"{DOC_PATH} 를 대장에서 생성")
    args = ap.parse_args()

    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seeds = [{"corpCode": c["corpCode"], "companyName": c["companyName"],
                  "market": c.get("market", "-"), "sector": c.get("sector")}
                 for c in json.load(f)["companies"]]

    with postgres_connection() as conn:
        ensure_table(conn)
        rows = {r["corp_code"]: r for r in summary(conn)}

    done, todo = [], []
    for s in seeds:
        rec = rows.get(s["corpCode"])
        (done if rec else todo).append({**s, "rec": rec})

    if args.write_doc:
        # 그래프 실제 규모도 같이 담는다 — 대장 누계와 다를 수 있다
        # (클러스터링·중복제거로 엣지가 줄고, 다른 기업 기사에서도 엣지가 생긴다).
        graph = {"now": datetime.now().strftime("%Y-%m-%d %H:%M")}
        try:
            with neo4j_session() as s:
                graph["nodes"] = s.run(
                    "MATCH (n) RETURN count(*) AS n").single()["n"]
                graph["edges"] = s.run(
                    "MATCH ()-[r]->() RETURN count(*) AS n").single()["n"]
                graph["news_edges"] = s.run(
                    "MATCH ()-[r]->() WHERE r.source_type='news' "
                    "RETURN count(*) AS n").single()["n"]
                graph["events"] = s.run(
                    "MATCH (e:Event) RETURN count(*) AS n").single()["n"]
                graph["products"] = s.run(
                    "MATCH (p:Product) RETURN count(*) AS n").single()["n"]
        except Exception as exc:
            print(f"  ⚠ 그래프 통계 생략 (Neo4j 연결 실패: {exc!r})")
        write_doc(seeds, done, todo, graph, DOC_PATH)
        return 0

    if args.plan:
        # 밸류체인 우선순위 → 그다음 시드 순서
        order = {n: i for i, n in enumerate(VALUE_CHAIN_PRIORITY)}
        todo.sort(key=lambda c: order.get(c["companyName"], 999))
        picks = todo[: args.plan]
        print(f"다음 {len(picks)}개사 추천 (밸류체인 응집 순)\n")
        for i, c in enumerate(picks, 1):
            tag = "★" if c["companyName"] in order else " "
            print(f"  {tag} {i:>2}. {c['companyName']}")
        print(f"\n예상 비용: 추출 100건 기준 약 {len(picks) * 2600:,}원 "
              f"(≈ ${len(picks) * 2600 / 1380:.0f})")
        print("\n실행 명령:")
        for c in picks:
            print(f"  python -m batch.ops.pilot_company {c['companyName']} "
                  f"--years 3 --limit 100")
        return 0

    if not args.pending:
        print(f"{'기업':22} {'실행':>4} {'추출':>6} {'엣지':>7} {'비용(원)':>9} 최근")
        print("-" * 72)
        total_cost = total_edges = 0
        for c in sorted(done, key=lambda x: -(x["rec"]["total_edges"] or 0)):
            r = c["rec"]
            total_cost += r["total_cost"] or 0
            total_edges += r["total_edges"] or 0
            print(f"{c['companyName']:22} {r['runs']:>4} {r['total_extracted'] or 0:>6} "
                  f"{r['total_edges'] or 0:>7} {r['total_cost'] or 0:>9,} "
                  f"{str(r['last_run'])[:10]}")
        print("-" * 72)
        print(f"{'합계':22} {'':>4} {'':>6} {total_edges:>7} {total_cost:>9,}")

    print(f"\n진행 {len(done)}/{len(seeds)}개사 · 미진행 {len(todo)}개사")
    if args.pending or len(todo) <= 20:
        names = [c["companyName"] for c in todo]
        for i in range(0, len(names), 5):
            print("   " + " · ".join(names[i:i + 5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
