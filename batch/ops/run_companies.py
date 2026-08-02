"""여러 기업을 한 번에 추출한다 — 한 곳이 실패해도 나머지는 계속.

`pilot_company`는 기업 하나만 돈다. 배치로 10개사를 돌릴 때 손으로 열 번 치면
중간에 하나 실패했을 때 어디까지 됐는지 놓친다. 이 모듈이 그 순서를 잡는다.

    python -m batch.ops.run_companies --plan 10          # 밸류체인 우선순위로 10개사
    python -m batch.ops.run_companies 고영 넥스틴          # 이름을 직접
    python -m batch.ops.run_companies --plan 10 --dry-run # 무엇을 돌릴지만

★쉘 스크립트로 쓰지 않는 이유: Windows PowerShell 5.1은 BOM 없는 `.ps1`을
  시스템 코드페이지로 읽어 **한글 기업명이 깨진다**(실측: 파서 에러로 즉시 실패).
  파이썬은 소스 인코딩이 UTF-8로 고정이라 그 문제가 없다.

추출이 끝나면 정리·검사는 따로 한 번만 돌린다(엣지 정리는 전량 대상이므로
기업마다 돌릴 필요가 없다):
    python -m batch.ops.finalize
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from app.core.config import ETF_LIST_PATH
from app.core.database import postgres_connection
from batch.ops.status import VALUE_CHAIN_PRIORITY
from batch.ops.pilot_company import _EXTRACT_KRW
from pipeline.importer.extraction_ledger import ensure_table, summary

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def pending_by_priority(limit: int) -> list[str]:
    """미진행 기업을 밸류체인 응집 순으로. 무작위면 서로 안 이어진다."""
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seeds = [(c["corpCode"], c["companyName"])
                 for c in json.load(f)["companies"]]
    with postgres_connection() as conn:
        ensure_table(conn)
        done = {r["corp_code"] for r in summary(conn)}
    order = {n: i for i, n in enumerate(VALUE_CHAIN_PRIORITY)}
    todo = [name for code, name in seeds if code not in done]
    todo.sort(key=lambda n: order.get(n, 999))
    return todo[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("companies", nargs="*", help="기업명 (비우면 --plan 사용)")
    ap.add_argument("--plan", type=int, metavar="N",
                    help="미진행 기업 중 밸류체인 우선순위 N개")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--limit", type=int, default=100, help="기업당 추출 상한")
    ap.add_argument("--month-split", action="store_true",
                    help="대형주용 — 월 단위로 쪼개 수집(1,000건 상한 회피)")
    ap.add_argument("--bucket", choices=["month", "quarter", "year"],
                    default="month", help="기간 균등 배분 단위")
    ap.add_argument("--resolve-factor", type=int, default=4,
                    help="URL 해석 대상 = limit × 이 값. 대형사 실측 생존율이 "
                         "27%라 3으로는 상한을 못 채운다(SK 240→193건)")
    ap.add_argument("--dry-run", action="store_true", help="목록만 보고 끝")
    args = ap.parse_args()

    names = args.companies or (pending_by_priority(args.plan) if args.plan else [])
    if not names:
        print("돌릴 기업이 없습니다. 기업명을 주거나 --plan N 을 쓰세요.")
        return 1

    print(f"대상 {len(names)}개사 · 기업당 최대 {args.limit}건 · {args.years}년치")
    for i, n in enumerate(names, 1):
        print(f"  {i:>2}. {n}")
    # 상한 = 전 기업이 CAP을 채웠을 때. 실제로는 중소형사가 공급 부족으로
    # 훨씬 적게 나오므로 **상한이지 예상치가 아니다**(10개사 배치 실측: 상한의 25%).
    cap_cost = len(names) * args.limit * _EXTRACT_KRW
    print(f"\n비용 상한 {cap_cost:,.0f}원 (≈ ${cap_cost/1380:.1f}) "
          f"— 전 기업이 상한 {args.limit}건을 채울 경우")
    print(f"  실제는 중소형사가 공급 부족으로 미달하므로 이보다 적습니다")
    if args.dry_run:
        print("\n[dry-run] 실행하지 않았습니다.")
        return 0

    started = time.time()
    results: list[tuple[str, bool, float]] = []

    for i, name in enumerate(names, 1):
        elapsed = (time.time() - started) / 60
        print("\n" + "=" * 70)
        print(f"[{i}/{len(names)}] {name}   (경과 {elapsed:.0f}분)")
        print("=" * 70, flush=True)

        cmd = [sys.executable, "-u", "-m", "batch.ops.pilot_company", name,
               "--years", str(args.years), "--limit", str(args.limit),
               "--bucket", args.bucket,
               "--resolve-factor", str(args.resolve_factor)]
        if args.month_split:
            cmd.append("--month-split")

        t0 = time.time()
        # 출력을 그대로 흘려보낸다 — 진행 상황을 사람이 볼 수 있어야 한다
        proc = subprocess.run(cmd)
        took = (time.time() - t0) / 60
        ok = proc.returncode == 0
        results.append((name, ok, took))
        print(f"\n[{name}] {'완료' if ok else f'실패(코드 {proc.returncode})'} "
              f"· {took:.1f}분", flush=True)
        if proc.returncode == 3:
            # ★속도 제한은 **전체를 멈춘다**. 한 기업의 문제가 아니라 우리가
            #   차단당한 것이므로, 다음 기업을 돌려도 똑같이 실패한다.
            #   2026-07-31에 이 처리가 없어 심텍이 480질의를 전부 실패하며
            #   73분을 버렸고, 이어서 리노공업도 같은 길을 갔다.
            print(f"\n{'='*70}")
            print(f"⛔ 구글 속도 제한 — 배치를 중단합니다 ({name}에서 발생)")
            print(f"   지금까지 {i-1}개사 완료. 시간을 두고(수십 분~수 시간) 재개하세요.")
            print(f"   재개: 같은 명령을 다시 실행하면 미진행 기업부터 이어갑니다.")
            print(f"{'='*70}")
            break
        if not ok:
            # 그 외 실패는 그 기업만의 문제이므로 계속한다.
            print(f"  → 다음 기업으로 계속합니다.", flush=True)

    print("\n" + "=" * 70)
    ok_n = sum(1 for _, ok, _ in results if ok)
    print(f"배치 종료: 성공 {ok_n} · 실패 {len(results)-ok_n} · "
          f"총 {(time.time()-started)/60:.0f}분")
    for name, ok, took in results:
        print(f"  {'✅' if ok else '❌'} {name:20} {took:5.1f}분")
    print("=" * 70)
    print("\n다음 단계:")
    print("  python -m batch.ops.finalize                    # 정리·검사 (전량, 증분)")
    print("  python -m batch.audit.spot_check --source news    # 표본 심층검사")
    print("  python -m batch.ops.status --write-doc")
    return 0 if ok_n == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
