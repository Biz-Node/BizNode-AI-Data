"""매일 도는 파이프라인 — **크론이 부르는 한 줄.**

단계마다 성격이 다르다

  어느 DB 를 건드리느냐로 「서비스 중에 돌려도 되나」가 갈린다. Neo4j 를 쓰는
  단계가 조회 중에 노드를 병합하면, 그 노드를 보던 사용자는 없는 키를 보게 된다.

      ① 뉴스 수집    PG    무료    서비스 중 가능    매일
      ② 시세        PG    무료    서비스 중 가능    평일 장 마감 후
      ③ 관계 추출    Neo4j  유료    야간만          매일 (상한 안에서)
      ④ 근거 검증    Neo4j  1차 무료 · 2차 유료    ③ 뒤에 바로

  ③④는 붙어 있어야 한다 — 새로 만든 엣지를 검증 없이 두면 근거 없는 관계가
  화면에 나간다.

비용이 드는 곳은 ③ 하나다

  실측(2026-08-18): 라우터 0.25원/기사 · 관계 추출 14.7원/기사.
  라우터가 이미 72%를 걸러서, 미처리 6,403건 중 실제 추출 대상은 1,493건이었다.
  그래도 상한을 둔다 — 아껴서가 아니라 **최악이 얼마인지 알기 위해서**다.
  대형주에 사건이 터지면 기사가 쏟아진다.

실행:
    python -m batch.ops.daily                  # 전부
    python -m batch.ops.daily --dry-run        # 대상 수와 예상 비용만
    python -m batch.ops.daily --skip-extract   # 무료 구간만 (①②)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_PY = sys.executable


def _run(title: str, args: list[str], *, paid: bool = False) -> int:
    print(f"\n{'=' * 66}\n■ {title}{'   ★비용 발생' if paid else ''}\n{'=' * 66}")
    t = time.time()
    code = subprocess.call([_PY, "-m", *args])
    print(f"  ({time.time() - t:.0f}초, 종료코드 {code})")
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="대상 수와 예상 비용만")
    ap.add_argument("--skip-extract", action="store_true", help="무료 구간만 (수집·시세)")
    ap.add_argument("--extract-limit", type=int, default=300,
                    help="관계 추출 상한. 넘으면 다음 날로 밀린다")
    ap.add_argument("--no-llm-verify", action="store_true",
                    help="근거 검증 2차(LLM)를 생략. 1차 토큰 대조는 늘 한다")
    args = ap.parse_args()

    fails: list[str] = []

    # ── ① 뉴스 수집 ─────────────────────────────────────────
    if _run("뉴스 수집 (PG · 무료)",
            ["batch.build.news_feed"] + (["--dry-run"] if args.dry_run else [])):
        fails.append("뉴스 수집")

    # ── ② 시세 ─────────────────────────────────────────────
    # 주말·공휴일에는 새 데이터가 없다. 그래도 돌려서 손해는 없다(멱등).
    if not args.dry_run and _run("시세 (PG · 무료)", ["batch.build.market_data"]):
        fails.append("시세")

    if args.skip_extract:
        print("\n--skip-extract — 관계 추출·근거 검증을 건너뜁니다.")
        return 1 if fails else 0

    # ── ③ 관계 추출 ─────────────────────────────────────────
    # ★PG 의 미처리 기사를 이어받는다. RSS 를 다시 받지 않는다 —
    #   그러면 ①이 저장한 기사를 못 보고 지나간다.
    ex = ["batch.build.news", "--limit", str(args.extract_limit)]
    if args.dry_run:
        ex.append("--dry-run")
    if _run("관계 추출 (Neo4j)", ex, paid=not args.dry_run):
        fails.append("관계 추출")
        # ★추출이 실패하면 검증도 건너뛴다. 검증할 새 엣지가 없다.
        print("  추출이 실패해 근거 검증을 건너뜁니다.")
        return 1

    if args.dry_run:
        print("\n--dry-run — 근거 검증은 실제 실행에서만 돕니다.")
        return 0

    # ── ④ 근거 검증 ─────────────────────────────────────────
    # 1차 토큰 대조는 무료라 전수로 돈다(정규화·개명이 뒤늦게 관계를 망가뜨리는
    # 것을 잡기 위함). 2차 LLM 은 미검사분만 본다.
    gr = ["batch.audit.grounding", "--apply"]
    if not args.no_llm_verify:
        gr.append("--llm")
    if _run("근거 검증 (Neo4j)", gr, paid=not args.no_llm_verify):
        fails.append("근거 검증")

    print(f"\n{'=' * 66}")
    if fails:
        print(f"❌ 실패: {' · '.join(fails)}")
        return 1
    print("✅ 매일 파이프라인 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
