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


# 표준 설정 — 이 셋이 다르면 그 기업은 다시 모아야 한다.
STD_YEARS, STD_LIMIT, STD_MONTH_SPLIT = 5, 240, True

_NONSTD = """
SELECT DISTINCT ON (corp_code) corp_code, company_name, years, month_split, extract_limit
FROM extraction_runs ORDER BY corp_code, run_at DESC
"""


def nonstandard_runs(limit: int | None = None) -> list[tuple[str, str]]:
    """**표준이 아닌 설정으로 모은 기업**을 돌려준다 — 재수집 대상.

    ★왜 도구가 알아야 하나: 손으로 고르면 틀린다. 실제로 29개사가 다섯 가지
      설정으로 갈려 있었고(3년/기간/100이 17곳, 5년/월별/240이 12곳 …),
      어느 게 어느 설정인지는 `extraction_runs`만 안다. 사람이 기억할 일이 아니다.

    같은 기업을 여러 번 돌렸으면 **가장 최근 실행**을 본다.
    이미 뽑은 기사는 `title_hash`로 걸러지므로 재수집은 **추가분만** 돈이 든다
    (실측: 옛 설정 평균 37건 → 표준 74건, 차이 37건 ≈ 545원/사).
    """
    order = {n: i for i, n in enumerate(VALUE_CHAIN_PRIORITY)}
    with postgres_connection() as conn:
        ensure_table(conn)
        rows = conn.execute(_NONSTD).fetchall()
    out = [(code, name) for code, name, yrs, ms, lim in rows
           if not (yrs == STD_YEARS and ms == STD_MONTH_SPLIT and lim == STD_LIMIT)]
    out.sort(key=lambda x: order.get(x[1], 999))
    return out[:limit] if limit else out


# 규모별 실측 단가 — `extraction_runs` 29개사 실지출에서 뽑았다.
_UNIT_LARGE_KRW = 3000     # 매출 5조 이상: 관련 기사가 상한 근처까지 나온다
_UNIT_OTHER_KRW = 700      # 그 외: 5년을 뒤져도 관련 기사가 40건 남짓이다
_LARGE_REVENUE = 5e12


def _estimate_cost(names: list[str]) -> float:
    """상한이 아니라 **실측 단가 기반 예상치**."""
    try:
        from app.core.database import neo4j_session
        with neo4j_session() as s:
            rev = {r["n"]: r["v"] for r in s.run(
                "MATCH (c:Company) WHERE c.name IN $ns "
                "RETURN c.name AS n, c.revenue_snapshot AS v", ns=names)}
    except Exception:
        return len(names) * _UNIT_OTHER_KRW
    return sum(_UNIT_LARGE_KRW if (rev.get(n) or 0) >= _LARGE_REVENUE
               else _UNIT_OTHER_KRW for n in names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("companies", nargs="*", help="기업명 (비우면 --plan 사용)")
    ap.add_argument("--plan", type=int, metavar="N",
                    help="미진행 기업 중 밸류체인 우선순위 N개")
    ap.add_argument("--recollect", type=int, nargs="?", const=999, metavar="N",
                    help="**표준이 아닌 설정으로 모은 기업**을 다시 (기본 전부). "
                         "이미 뽑은 기사는 건너뛰므로 추가분만 돈이 듭니다")
    # ★기본값 = **표준 설정**이다. 바꾸지 마세요(2026-08-02 확정).
    #
    #   전에는 기본이 `3년 · 상한 100 · 월별분할 없음`이었다. 그래서 그냥 돌린
    #   17개사와, 나중에 플래그를 붙여 돌린 12개사가 **밀도가 2배 다른 두 집단**이
    #   됐다. 리스크 파급은 연결 수에 직접 반응하므로(허브 감점 = 40/(40+차수-1)),
    #   수집량 차이가 그대로 위험도 차이로 보인다 —
    #   「A사가 위험해 보이는 이유」가 「A사 뉴스를 더 모아서」가 된다.
    #
    #   설정을 기본값에 박아 둬야 **아무 생각 없이 돌려도 같은 조건**이 된다.
    #   실측 단가: 대형 약 3,000원 · 그 외 약 700원(상한이 아니라 관련 기사 수가
    #   추출량을 정한다 — 29개사 중 상한을 채운 건 3곳뿐이다).
    ap.add_argument("--years", type=int, default=5,
                    help="수집 기간(년). 표준 5 — 바꾸면 기업 간 비교가 깨집니다")
    ap.add_argument("--limit", type=int, default=240,
                    help="기업당 추출 상한. 표준 240")
    ap.add_argument("--no-month-split", dest="month_split", action="store_false",
                    help="월 단위 분할 수집을 끈다 — **표준에서 벗어납니다**")
    ap.set_defaults(month_split=True)
    ap.add_argument("--bucket", choices=["month", "quarter", "year"],
                    default="month", help="기간 균등 배분 단위")
    ap.add_argument("--resolve-factor", type=int, default=4,
                    help="URL 해석 대상 = limit × 이 값. 대형사 실측 생존율이 "
                         "27%라 3으로는 상한을 못 채운다(SK 240→193건)")
    ap.add_argument("--dry-run", action="store_true", help="목록만 보고 끝")
    args = ap.parse_args()

    if args.recollect:
        names = [n for _, n in nonstandard_runs(args.recollect)]
        if names:
            print(f"■ 표준이 아닌 설정으로 모은 기업 {len(names)}곳을 다시 모읍니다")
            print("   (이미 뽑은 기사는 title_hash로 건너뜁니다 — 추가분만 과금)\n")
    else:
        names = args.companies or (pending_by_priority(args.plan) if args.plan else [])
    if not names:
        print("돌릴 기업이 없습니다. 기업명을 주거나 --plan N / --recollect 를 쓰세요.")
        return 1

    std = (args.years == 5 and args.limit == 240 and args.month_split)
    print(f"대상 {len(names)}개사 · {args.years}년 · 월별분할={args.month_split} · "
          f"상한 {args.limit}건" + ("  [표준]" if std else "  ⚠ 표준이 아닙니다"))
    if not std:
        print("   ※ 표준은 5년 · 월별분할 · 상한 240입니다. 다르게 모으면 이 기업만")
        print("     연결 밀도가 달라져 **다른 기업과 위험도를 비교할 수 없습니다**.")
    for i, n in enumerate(names, 1):
        print(f"  {i:>2}. {n}")

    # ★상한과 예상치를 **함께** 찍는다. 전에는 상한만 찍어서, 그 값을 예상 비용으로
    #   읽고 예산을 3배로 잡은 적이 있다(2026-08-02). 상한은 전 기업이 CAP을
    #   채웠을 때인데, 29개사 중 채운 건 3곳뿐이었다.
    cap_cost = len(names) * args.limit * _EXTRACT_KRW
    est = _estimate_cost(names)
    if args.recollect:
        # 재수집은 **이미 뽑은 기사를 건너뛴다**. 옛 설정 평균 37건 → 표준 74건이
        # 실측이므로 추가분은 절반쯤이다. 아래 예상치는 그래서 상한에 가깝다.
        print(f"\n예상 {est:,.0f}원의 **절반 이하**입니다 — 이미 뽑은 기사는 건너뜁니다")
        print(f"  (실측: 옛 설정 평균 37건 → 표준 74건, 추가분 37건 ≈ 545원/사)")
    print(f"\n예상 {est:,.0f}원  (실측 단가: 대형 약 3,000원 · 그 외 약 700원)")
    print(f"비용 상한 {cap_cost:,.0f}원 — 전 기업이 상한 {args.limit}건을 채울 경우")
    print(f"  추출량은 상한이 아니라 **관련 판정을 통과한 기사 수**가 정합니다.")
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
