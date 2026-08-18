"""`norm_name` 에 든 슬래시를 없앤다 — **키가 URL 에 들어가기 때문**이다.

★무슨 일이 있었나 (2026-08-17)

`corp_code` 가 없는 회사는 `norm_name` 이 키다. 그 키가 `/companies/{key}` 의
경로에 그대로 들어가는데, 슬래시가 있으면 **경로가 갈라져 조회가 안 된다.**

    GET /companies/한국s/w공제조합        → 404
    GET /companies/한국s%2Fw공제조합      → 404  (서버가 %2F 를 정규화한다)

12곳이 이 상태였다. 하위 경로(`/market`·`/events` …)는 `{key:path}` 로도 못
받는다 — `:path` 는 **마지막 세그먼트**여야 해서다. 그래서 **키에서 슬래시를
빼는 것**이 유일한 해법이다.

★이름 자체는 안 건드린다. 「한국S/W공제조합」은 화면에 그대로 나온다.
  바뀌는 건 **키뿐**이다.

★충돌은 미리 확인했다(2026-08-17: 12곳 전부 충돌 없음). 혹시 생기면 그 노드는
  건너뛴다 — 키가 겹치면 서로 다른 회사가 한 노드가 된다.

실행:
    python -m batch.repair.slash_keys --dry-run
    python -m batch.repair.slash_keys
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(
            "MATCH (c:Company) WHERE c.norm_name CONTAINS '/' "
            "RETURN c.name AS name, c.norm_name AS nn")]
        taken = {r["nn"] for r in s.run(
            "MATCH (c:Company) WHERE c.norm_name IS NOT NULL RETURN c.norm_name AS nn")}

    print(f"■ 키에 슬래시가 든 노드 {len(rows)}곳")
    plan, skip = [], []
    for r in rows:
        new = r["nn"].replace("/", "")
        (skip if new in taken else plan).append((r, new))
    for r, new in plan:
        print(f"   {r['name'][:34]:<36}{r['nn'][:30]:<32}→ {new[:30]}")
    for r, _ in skip:
        print(f"   ⏭ {r['name'][:34]:<34}키가 겹쳐 건너뜀")

    if args.dry_run or not plan:
        if args.dry_run:
            print("\n[dry-run] 고치지 않았습니다.")
        return 0

    with neo4j_session() as s:
        for r, new in plan:
            s.run("MATCH (c:Company {norm_name: $old}) "
                  "SET c.norm_name = $new, c.key_fixed_at = date()",
                  old=r["nn"], new=new)
        left = s.run("MATCH (c:Company) WHERE c.norm_name CONTAINS '/' "
                     "RETURN count(*) AS n").single()["n"]
    print(f"\n✅ {len(plan)}곳의 키에서 슬래시를 뺐습니다 · 남은 것 {left}곳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
