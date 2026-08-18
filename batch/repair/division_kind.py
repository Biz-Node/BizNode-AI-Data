"""`entity_kind='사업부문'`인데 **실제 법인**인 노드를 되돌린다.

★무슨 일이 있었나 (2026-08-15)

`schema_slim` 2단계가 사업부문 노드 12곳을 부모 기업으로 병합했다. 이때
`apoc.refactor.mergeNodes(..., {properties:'discard'})`를 썼는데, 남은 노드의
`entity_kind`가 **자식 값(`사업부문`)으로 굳어졌다.** 그래서:

    삼성전자   entity_kind = 사업부문   ← 시드 기업인데
    LG전자     entity_kind = 사업부문   ← 역시 시드

**판별은 DART 명부로 한다.** `corp_code`가 있고 그 번호가 `corp_code_master`에
등재돼 있으면 **독립 법인**이지 사업부문이 아니다. 실측으로 12곳 전부 등재돼
있었다(삼성메디슨·삼성전자판매·SK하이닉스시스템아이씨 …).

★왜 이름으로 판단하지 않나

「삼성전자 DS부문」처럼 이름에 부문이 들어가면 사업부문이라고 볼 수도 있지만,
**「삼성전자판매」는 이름만 보면 부문 같아도 실제 법인**이다. 반대로 법인이
아닌데 이름이 멀쩡한 경우도 있다. 이름은 근거가 못 된다 — **명부가 근거다.**

★corp_code 가 없는 71곳은 건드리지 않는다. 그쪽은 진짜 사업부문일 수 있고,
  아니라는 근거가 없다. 근거 없이 값을 바꾸지 않는다.

실행:
    python -m batch.repair.division_kind --dry-run
    python -m batch.repair.division_kind
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_FIND = """
MATCH (c:Company {entity_kind: '사업부문'})
WHERE c.corp_code IS NOT NULL
RETURN c.corp_code AS cc, c.name AS name, c.is_stub AS is_stub
ORDER BY c.is_stub, c.name
"""

_FIX = """
MATCH (c:Company {corp_code: $cc})
SET c.entity_kind = '기업', c.entity_kind_fixed_at = date()
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND)]
    if not rows:
        print("사업부문으로 남은 법인이 없습니다.")
        return 0

    # DART 명부에 있는지가 유일한 근거다
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT corp_code FROM corp_code_master WHERE corp_code = ANY(%s)",
                    ([r["cc"] for r in rows],))
        listed = {c.strip() for (c,) in cur.fetchall()}

    fix = [r for r in rows if r["cc"] in listed]
    skip = [r for r in rows if r["cc"] not in listed]

    print(f"■ entity_kind='사업부문' + corp_code 보유 {len(rows)}곳")
    for r in fix:
        mark = " ★시드" if not r["is_stub"] else ""
        print(f"   → 기업   {r['name'][:22]:<24}{r['cc']}{mark}")
    for r in skip:
        print(f"   · 그대로 {r['name'][:22]:<24}{r['cc']}  (명부에 없음)")

    if args.dry_run or not fix:
        if args.dry_run:
            print("\n[dry-run] 고치지 않았습니다.")
        return 0

    with neo4j_session() as s:
        for r in fix:
            s.run(_FIX, cc=r["cc"])
        left = s.run("MATCH (c:Company {entity_kind:'사업부문'}) RETURN count(*) AS n").single()["n"]

    print(f"\n✅ {len(fix)}곳을 '기업'으로 되돌렸습니다")
    print(f"   남은 '사업부문' {left}곳 — corp_code 가 없어 판단 근거가 없는 것들입니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
