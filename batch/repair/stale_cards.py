"""그래프에 없는 **기업 카드**를 검색에서 뺀다. 비용 0.

★왜 생기나 (2026-08-07)

통합 검색은 `company` 컬렉션의 카드를 찾는다. 그런데 카드를 만든 뒤 그래프에서
노드가 사라지면, **카드만 남아 검색으로 들어갈 수 있는 빈 화면**이 된다.
실측 7건의 사유는 둘이었다:

    도쿄일렉트론(Tokyo Electron Limited) → 「도쿄일렉트론」에 **병합**됨
    램리서치(LAM Research)              → 「램리서치」에 병합됨
    삼성전자 시스템LSI                     → 「삼성전자 시스템LSI사업부」에 병합됨
    현대모비스, 현대케피코, 현대트랜시스          → 세 회사로 **쪼개고 삭제**함
    00115852 · 00296290 · 01263378      → 엣지가 0이라 `is_orphan` 표시됨

앞의 넷은 `repair.name_overlap`이, 뒤의 셋은 `repair.orphan_nodes`가 만든
결과다. 둘 다 그래프는 옳게 고쳤는데 **벡터를 안 건드렸다.**

★왜 `orphan_nodes`로 안 되나

그 도구는 「엣지가 0인 노드」만 본다. 병합·삭제된 노드는 **노드 자체가 없어서**
찾지 못한다. 그래서 별도로 「카드는 있는데 그래프에 없는 것」을 본다.

    python -m batch.repair.stale_cards --dry-run
    python -m batch.repair.stale_cards
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 카드의 owner_key는 `company_vectors`가 `corp_code or name`으로 넣는다.
# 그래프에서도 같은 규칙으로 뽑아야 맞춰진다.
_LIVE = """
MATCH (c:Company) WHERE NOT coalesce(c.is_orphan, false)
RETURN coalesce(c.corp_code, c.name) AS k
"""


def _why(session, key: str) -> str:
    r = session.run("MATCH (c:Company) WHERE coalesce(c.corp_code, c.name) = $k "
                    "RETURN coalesce(c.is_orphan, false) AS orph", k=key).single()
    if r:
        return "엣지 0으로 표시됨" if r["orph"] else "?"
    m = session.run("MATCH (c:Company) WHERE $k IN coalesce(c.merged_names, []) "
                    "RETURN c.name AS n", k=key).single()
    return f"「{m['n']}」에 병합됨" if m else "노드가 삭제됨"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("SELECT chunk_id, owner_key FROM vector_chunks "
                    "WHERE collection = 'company'")
        cards = {r[1]: r[0] for r in cur.fetchall()}

    with neo4j_session() as session:
        live = {r["k"] for r in session.run(_LIVE)}
        stale = {k: cid for k, cid in cards.items() if k not in live}
        if not stale:
            print(f"모든 기업 카드가 그래프와 맞습니다 ({len(cards)}건).")
            return 0
        print(f"■ 그래프에 없는 기업 카드 {len(stale)}건 / 전체 {len(cards)}건\n")
        reasons = {k: _why(session, k) for k in stale}
        for k in sorted(stale):
            print(f"   {str(k)[:30]:32}{reasons[k]}")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0

    # ChromaDB에서 빼고, 대장(vector_chunks)에서도 지운다 — 둘이 어긋나면
    # 다음 검사가 「대장엔 있는데 실물이 없다」로 또 걸린다.
    ids = list(stale.values())
    try:
        from pipeline.vectorstore.chroma_store import ChromaStore
        col = ChromaStore()._client.get_collection("company")
        col.delete(ids=ids)
    except Exception as exc:
        print(f"\n⚠ ChromaDB 삭제 실패 — 대장도 건드리지 않습니다: {exc!r}")
        return 1
    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("DELETE FROM vector_chunks WHERE chunk_id = ANY(%s)", (ids,))

    print(f"\n✅ 검색에서 {len(ids)}건 제거 (ChromaDB + vector_chunks)")
    print("   그래프 노드는 건드리지 않았습니다 — 카드만 정리한 것입니다.")
    print("   병합된 회사는 **남은 쪽 카드로 검색됩니다**(도쿄일렉트론 등).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
