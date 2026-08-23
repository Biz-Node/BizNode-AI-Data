"""엣지가 하나도 없는 노드를 **표시**하고 검색에서 뺀다. 비용 0.

왜 생기나 (2026-08-03 추적)

고아 34건을 하나씩 되짚었더니 **원인이 하나였다.** 전부 뉴스에서 온
`PARTNERS_WITH`인데, `audit/relations.check_symmetric`이 「관계가 아니라
나란히 언급됐을 뿐」이라고 판정해 엣지를 지운 자리들이다:

    삼성전자 -PARTNERS_WITH-> 하이얼      ← 같은 기사에 이름이 같이 나왔을 뿐
    DL이앤씨 -PARTNERS_WITH-> 호반건설     ← 「건설사들이」로 묶여 언급
    00164788 -PARTNERS_WITH-> 글로벌테크놀로지

엣지를 지운 건 **맞다.** 없는 관계다. 문제는 그 노드가 그 엣지 하나만
붙들고 있었다는 것 — 엣지가 사라지자 아무 데도 안 닿는 노드만 남았다.

왜 지우지 않고 표시하나

빈 노드는 그래프에 안 그려지고 위험 전파에도 안 잡히니 화면에는 원래 안
보인다. 딱 하나 새는 곳이 **벡터 검색**이었다 — `company` 컬렉션에 코인원·
키움증권·두산건설이 들어 있어서, 검색으로 찾아 들어가면 **관계가 하나도 없는
빈 화면**이 열린다. 그것만 막으면 된다.

지우면 되돌릴 수 없고, 나중에 다른 기사에서 진짜 관계가 나오면 다시 만들어야
한다. 표시해 두면 엣지가 붙는 순간 이 도구가 알아서 표시를 뗀다.

    python -m batch.repair.orphan_nodes --dry-run
    python -m batch.repair.orphan_nodes

★시드 기업은 절대 표시하지 않는다. 시드가 고아라면 그건 수집이 실패한 것이지
  버릴 노드가 아니다 — 경고를 띄우고 멈춘다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_FIND_ORPHAN = """
MATCH (n) WHERE NOT (n)-[]-()
RETURN elementId(n) AS eid, labels(n)[0] AS lb, n.name AS name,
       coalesce(n.corp_code, '') AS cc,
       coalesce(n.is_orphan, false) AS marked
ORDER BY lb, name
"""

# 표시해 뒀는데 엣지가 다시 붙은 것 — 표시를 뗀다
_FIND_REVIVED = """
MATCH (n) WHERE n.is_orphan = true AND (n)-[]-()
RETURN elementId(n) AS eid, labels(n)[0] AS lb, n.name AS name,
       size([(n)-[]-()|1]) AS deg
"""

_MARK = """
MATCH (n) WHERE elementId(n) = $eid
SET n.is_orphan = true,
    n.orphan_reason = $why,
    n.orphan_since = coalesce(n.orphan_since, date())
"""

_UNMARK = """
MATCH (n) WHERE elementId(n) = $eid
REMOVE n.is_orphan, n.orphan_reason, n.orphan_since
"""

_WHY = "엣지 0 — 관계 검사에서 「나란한 언급」으로 판정돼 마지막 엣지가 삭제됨"


def _seed_keys() -> tuple[set[str], set[str]]:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]
    return ({c["companyName"] for c in seed}, {c["corpCode"] for c in seed})


def _prune_vectors(names: set[str], dry_run: bool) -> int:
    """`company` 컬렉션에서 고아를 뺀다 — 검색으로 빈 화면이 열리지 않게."""
    if not names:
        return 0
    from pipeline.vectorstore.chroma_store import ChromaStore

    store = ChromaStore()
    try:
        col = store._client.get_collection("company")
    except Exception as exc:                       # 컬렉션이 아직 없을 수 있다
        print(f"   · company 컬렉션을 열지 못했습니다 ({exc!r}) — 건너뜁니다")
        return 0
    got = col.get(include=["metadatas"])
    hit = [i for i, m in zip(got["ids"], got["metadatas"])
           if (m or {}).get("name") in names]
    if hit and not dry_run:
        col.delete(ids=hit)
    return len(hit)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seed_names, seed_codes = _seed_keys()

    with neo4j_session() as session:
        # ① 엣지가 다시 붙은 것 — 표시를 뗀다 (먼저 한다: 아래 집계가 깨끗해진다)
        revived = [dict(r) for r in session.run(_FIND_REVIVED)]
        if revived:
            print(f"■ 엣지가 다시 붙은 노드 {len(revived)}건 — 표시를 뗍니다")
            for r in revived[:8]:
                print(f"   ↩ {r['lb']:12}{str(r['name'])[:26]:28}연결 {r['deg']}")
            if not args.dry_run:
                for r in revived:
                    session.run(_UNMARK, eid=r["eid"])
            print()

        rows = [dict(r) for r in session.run(_FIND_ORPHAN)]

        # ② 시드가 고아면 표시가 아니라 **수집 실패**다 — 멈춘다
        bad = [r for r in rows
               if r["name"] in seed_names or (r["cc"] and r["cc"] in seed_codes)]
        if bad:
            print(f"🔴 시드 기업이 고아입니다 {len(bad)}건 — 표시하지 않습니다. 수집을 확인하세요")
            for r in bad:
                print(f"   {r['lb']:12}{str(r['name'])[:26]:28}{r['cc']}")
            return 2

        if not rows:
            print("고아 노드가 없습니다.")
            return 0

        tally = Counter(r["lb"] for r in rows)
        fresh = [r for r in rows if not r["marked"]]
        print(f"■ 고아 노드 {len(rows)}건 (새로 생긴 것 {len(fresh)}건)")
        for lb, n in tally.most_common():
            print(f"   {lb:14}{n:>4}건")
        print()
        for r in fresh[:12]:
            print(f"   ✎ {r['lb']:12}{str(r['name'])[:30]:32}{r['cc']}")
        if len(fresh) > 12:
            print(f"   … 외 {len(fresh) - 12}건")

        if not args.dry_run:
            for r in fresh:
                session.run(_MARK, eid=r["eid"], why=_WHY)

    # ③ 벡터 검색에서 뺀다 — 여기가 유일하게 새던 곳이다
    pruned = _prune_vectors({r["name"] for r in rows if r["name"]}, args.dry_run)
    print(f"\n   벡터(company 컬렉션)에서 뺄 고아: {pruned}건")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0
    print(f"\n✅ 표시 {len(fresh)}건 · 표시 해제 {len(revived)}건 · 벡터 제거 {pruned}건")
    print("   삭제가 아닙니다 — 엣지가 다시 붙으면 이 도구가 표시를 뗍니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
