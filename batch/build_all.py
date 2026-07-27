"""[온보딩 한 방] 전 경로 파이프라인을 순차 실행하고 마지막에 감사한다.

시드에 기업을 추가한 뒤 이 명령 하나로 전체를 최신화한다. 각 단계는 멱등
(staged_edges source_doc 단위 삭제·재삽입, evidence·MERGE 멱등)이라 반복 안전.

  경로 A(지분·임원) → 재무 → 경로 B(공급·사건) → 경로 C(제품) → 감사

실행:
  python -m batch.build_all            # 증분 최신화
  python -m batch.build_all --reset    # 그래프 초기화 후 전체 재구축
"""

from __future__ import annotations

import argparse
import sys
import time

from batch import (
    audit_graph,
    build_business_reports,
    build_disclosures,
    build_graph,
    build_major_reports,
    import_financials,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# (제목, 실행 콜러블) — 순서 중요: 노드 먼저, 관계 나중
STEPS = [
    ("경로 A — 지분·임원 (build_graph)", None),  # reset 인자 필요 → 별도 처리
    ("재무 (import_financials)", import_financials.main),
    ("경로 B 공급계약 (build_disclosures)", build_disclosures.main),
    ("경로 B 사건 (build_major_reports)", build_major_reports.main),
    ("경로 C 제품 (build_business_reports)", build_business_reports.main),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="그래프 초기화 후 전체 재구축")
    parser.add_argument("--skip-audit", action="store_true", help="마지막 감사 생략")
    args = parser.parse_args()

    started = time.monotonic()
    total = len(STEPS)
    for i, (title, fn) in enumerate(STEPS, 1):
        print("\n" + "=" * 64)
        print(f"[단계 {i}/{total}] {title}")
        print("=" * 64)
        rc = build_graph.run_path_a(reset=args.reset) if fn is None else fn()
        if rc != 0:
            print(f"\n✗ 단계 실패({title}), 중단합니다.")
            return rc

    if not args.skip_audit:
        print("\n" + "=" * 64)
        print("[감사] 데이터 품질 검증")
        print("=" * 64)
        audit_graph.main()

    print(f"\n✅ 전체 파이프라인 완료 ({time.monotonic() - started:.0f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
