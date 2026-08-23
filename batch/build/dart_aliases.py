"""DART 공식 영문명을 별칭 레지스트리에 넣는다. 비용 0 · 외부 의존 0.

왜 이게 최선인가 (2026-08-13)

한국 기업이 영문으로 쓰이면 노드가 갈린다. 실측:

    뉴로메카 (연결 150)  ≠  Neuromeka (연결 13)     13개 연결이 끊겨 있었다
    케이티  (연결 100)  ≠  ㈜KT (연결 52)
    로보티즈 (연결 115)  ≠  ROBOTIS Inc. (연결 2)

갈리면 그 회사를 지나는 공급망 경로가 끊긴다. 화면에서는 그냥 「연결이 적은
회사」로 보여 눈에 띄지도 않는다.

그런데 **DART가 이 쌍을 공식으로 주고 있었다.** 기업개황 API의 `corp_name_eng`를
`stub_profiles`가 이미 받아 `name_en`에 저장해 뒀는데, 별칭으로 안 쓰고 있었다.

    · **공식**  법인이 DART에 등록한 영문 상호다
    · **이미 있다**  네트워크 호출도 속도 제한도 없다
    · **같은 법인이 보장된다**  같은 corp_code에서 온 쌍이라 다른 회사가 못 섞인다

새 기업을 수집하면 `name_en`이 늘어난다. 그때 다시 돌리면 된다(멱등).

    python -m batch.build.dart_aliases --dry-run
    python -m batch.build.dart_aliases
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.company_registry import ensure, record

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROWS = """
MATCH (c:Company)
WHERE c.name_en IS NOT NULL AND c.norm_name IS NOT NULL
RETURN c.name AS name, c.norm_name AS norm, c.name_en AS name_en,
       c.corp_code AS corp_code, size([(c)-[]-() | 1]) AS deg
ORDER BY deg DESC
"""
_OWNERS = ("MATCH (c:Company) WHERE c.norm_name IS NOT NULL "
           "RETURN c.norm_name AS k, c.corp_code AS cc")


def _hand_aliases(conn) -> dict[str, str]:
    """사람이 정한 별칭. 자동 생성이 이 값을 덮으면 안 된다."""
    with conn.cursor() as cur:
        cur.execute("SELECT alias_key, canonical_key FROM company_aliases "
                    "WHERE source = 'hand'")
        return dict(cur.fetchall())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_ROWS)]
        owners: dict[str, set[str]] = {}
        for r in s.run(_OWNERS):
            owners.setdefault(r["k"], set()).add(r["cc"])

    with postgres_connection() as conn:
        ensure(conn)
        hand = _hand_aliases(conn)
        hand_canon = set(hand.values())

        mapping: dict[str, str] = {}
        cyclic = conflicts = 0
        for r in rows:
            key = normalize_company_name(r["name_en"] or "")
            if not key or key == r["norm"] or len(key) < 2:
                continue
            # ★순환 방지. `normalize_company_name`이 **이미 별칭을 적용해서**
            #   돌려주므로, 손 목록의 대표형이 새 별칭 키가 되면 뒤집힌다
            #   (실측: `퀄컴 → qualcommincorporated` 로 대표형이 영문 키가 됐다).
            if key in hand or key in hand_canon:
                cyclic += 1
                continue
            # 그 영문 키를 **다른 법인**이 쓰고 있으면 접지 않는다
            if {c for c in owners.get(key, set()) if c and c != r["corp_code"]}:
                conflicts += 1
                continue
            mapping[key] = r["norm"]

        # 고정점 검사 — 대표형이 다시 별칭 키가 되면 순환한다
        bad = [(k, v) for k, v in mapping.items() if v in mapping]

        print("=" * 68)
        print(f"  DART 영문명 → 별칭 — 대상 {len(rows)}곳 · 만들 별칭 {len(mapping)}개")
        print(f"  건너뜀: 손 목록과 순환 {cyclic}건 · 다른 법인과 충돌 {conflicts}건")
        print("=" * 68)
        if bad:
            print(f"\n🔴 순환 별칭 {len(bad)}건 — 기록하지 않습니다: {bad[:3]}")
            return 1

        if args.dry_run:
            print("\n[dry-run] 표에 쓰지 않았습니다.")
            return 0

        for alias, canon in mapping.items():
            record(conn, alias, canon, source="dart",
                   note="DART 기업개황 공식 영문명")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM company_aliases WHERE source='dart'")
            n = cur.fetchone()[0]
    print(f"\n✅ 레지스트리에 기록 · source='dart' {n}행")
    print("   갈려 있는 노드를 합치려면: python -m batch.repair.node_identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
