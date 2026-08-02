"""근거(evidence) 탐색 CLI — ChromaDB 원문 + Neo4j 엣지 + 원문 파일 경로.

ChromaDB는 전용 UI가 없어 이 CLI로 조회한다.

사용법:
  python -m batch.ops.lookup ev_599ae4f46bf15b7c     # id로 직접 조회(팩트체크)
  python -m batch.ops.lookup --search "파운드리 공급"   # 의미 검색
  python -m batch.ops.lookup --corp 00126380         # 기업의 근거 전체
  python -m batch.ops.lookup --edge SUPPLIES_TO -n 5 # 엣지타입별
  python -m batch.ops.lookup --stats                 # 컬렉션 통계
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _print_chunk(cid: str, doc: str, meta: dict, distance: float | None = None) -> None:
    head = f"[{cid}]"
    if distance is not None:
        head += f"  (거리 {distance:.3f})"
    print(f"\n{head}")
    print(f"  {doc}")
    print(f"  ├ 엣지: {meta.get('edge_type')} / {meta.get('subtype')}")
    print(f"  ├ 관계: {meta.get('source_corp')} → {meta.get('target_corp')}")
    print(f"  └ 공시: {meta.get('rcept_no')}  (발생 {meta.get('occurred_at')})")


def show_by_id(evidence_id: str) -> None:
    """팩트체크 — id로 근거 + 엣지 + 원문파일까지 추적."""
    got = get_store().get(EVIDENCE_COLLECTION, [evidence_id])
    docs, metas = got.get("documents") or [], got.get("metadatas") or []
    if not docs:
        print(f"✗ {evidence_id}: ChromaDB에 없음")
        return
    print("=" * 68)
    _print_chunk(evidence_id, docs[0], metas[0])

    with neo4j_session() as s:
        rows = list(s.run(
            "MATCH (a)-[r]->(b) WHERE r.evidence_id = $ev RETURN "
            "coalesce(a.name, a.person_key) AS an, type(r) AS rel, "
            "coalesce(b.name, b.corp_code) AS bn, r.confidence AS conf, r.source_type AS st",
            ev=evidence_id))
    print("\n  ▸ Neo4j 엣지")
    for r in rows or []:
        print(f"    ({r['an']}) -[{r['rel']}]-> ({r['bn']})  conf={r['conf']} src={r['st']}")
    if not rows:
        print("    (참조 엣지 없음 — 고아 청크)")

    rcept = (metas[0] or {}).get("rcept_no")
    if rcept:
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT doc_type, title, rcept_dt, raw_path FROM documents "
                        "WHERE rcept_no = %s", (rcept,))
            d = cur.fetchone()
        print("\n  ▸ 원문 (PostgreSQL documents)")
        if d:
            print(f"    {d[0]} | {d[1].strip()} | {d[2]}")
            print(f"    파일: {d[3]}")
        else:
            print("    (API 기반 엣지라 원문 파일 없음 — rcept_no만 기록)")
    print("=" * 68)


def search(query: str, n: int, where: dict | None) -> None:
    """의미 검색 — '어떤 근거가 있나' 탐색용."""
    res = get_store().query(EVIDENCE_COLLECTION, query, n_results=n, where=where)
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    print(f"'{query}' 검색 결과 {len(ids)}건" + (f" (필터 {where})" if where else ""))
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        _print_chunk(cid, doc, meta, dist)


def browse(where: dict, n: int) -> None:
    """필터로 목록 조회 (검색 아님)."""
    col = get_store()._col(EVIDENCE_COLLECTION)
    got = col.get(where=where, limit=n, include=["documents", "metadatas"])
    ids = got.get("ids") or []
    print(f"필터 {where} → {len(ids)}건")
    for cid, doc, meta in zip(ids, got["documents"], got["metadatas"]):
        _print_chunk(cid, doc, meta)


def stats() -> None:
    col = get_store()._col(EVIDENCE_COLLECTION)
    total = col.count()
    got = col.get(include=["metadatas"])
    by_edge = Counter(m.get("edge_type") for m in got["metadatas"])
    print(f"evidence 컬렉션: 총 {total:,}개 청크\n")
    print("엣지 타입별:")
    for edge, cnt in by_edge.most_common():
        print(f"  {edge:18} {cnt:>6,}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("evidence_id", nargs="?", help="조회할 evidence_id")
    p.add_argument("--search", help="의미 검색어")
    p.add_argument("--corp", help="기업 corp_code로 필터 (source 기준)")
    p.add_argument("--target", help="기업 corp_code로 필터 (target 기준 — inbound)")
    p.add_argument("--edge", help="엣지 타입으로 필터 (SUPPLIES_TO 등)")
    p.add_argument("-n", type=int, default=5, help="결과 수 (기본 5)")
    p.add_argument("--stats", action="store_true", help="컬렉션 통계")
    args = p.parse_args()

    # 메타데이터 필터 조립 (Chroma는 조건 2개 이상이면 $and 필요)
    conds = []
    if args.corp:
        conds.append({"source_corp": args.corp})
    if args.target:
        conds.append({"target_corp": args.target})
    if args.edge:
        conds.append({"edge_type": args.edge})
    where = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)

    if args.stats:
        stats()
    elif args.evidence_id:
        show_by_id(args.evidence_id)
    elif args.search:
        search(args.search, args.n, where)
    elif where:
        browse(where, args.n)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
