"""근거 청크 정리 — **중복 병합**과 **고아 삭제**. 둘 다 벡터DB 위생 작업이다.

★① 중복 병합 (2026-08-01 실측)

의미 검색 상위 결과에 같은 문장이 두 번씩 나왔다:

    질의: 「노조가 파업을 예고하며 임금 협상이 결렬됐다」
      [1.062] 노조에 따르면 지난 23일 부서별 릴레이 파업이 시작된 이후 …
      [1.062] 노조에 따르면 지난 23일 부서별 릴레이 파업이 시작된 이후 …   ← 같은 문장

`evidence_id`는 `(출처, 출발키, 도착키, 엣지, subtype)` 해시라, **같은 문장이
여러 관계의 근거면 id가 따로 생긴다.** 검색은 문장만 보므로 중복으로 보인다.

· **내용이 완전히 같은 것만** 합친다. 비슷한 것은 손대지 않는다 — 실측 54건을
  보니 대부분 서로 다른 내용이었다:
      「공급 관계 — 수요자: JCET」 vs 「… JCET스태츠칩팩코리아」  ← 다른 회사다
· 대표 id 하나만 남기고, 나머지를 **가리키던 엣지들을 대표 id로 옮긴다.**
  그래야 근거를 잃지 않는다.

★② 고아 삭제

`evidence_id`가 해시라 **구성요소 하나만 바뀌어도 새 id가 생긴다.** 정규화
규칙을 고치거나 엣지를 재분류하면 옛 청크가 아무 엣지도 참조하지 않는 채 남는다.

그냥 두면 안 되는 이유: 고아 청크는 **의미검색에 계속 걸린다.** 옛 형식으로 쓰인
근거나 잘못 분류된 관계의 근거가 결과에 섞이면 근거를 신뢰할 수 없게 된다.

★순서가 있다 — **병합을 먼저, 삭제를 나중에.** 병합이 엣지 참조를 대표 id로
  옮기므로, 순서를 바꾸면 아직 옮기지 않은 참조가 고아로 보여 지워진다.

    python -m batch.repair.evidence --dry-run
    python -m batch.repair.evidence                # 병합 → 삭제
    python -m batch.repair.evidence --only prune   # 하나만
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_DELETE_CHUNKS = "DELETE FROM vector_chunks WHERE chunk_id = ANY(%s)"

# ── ① 중복 병합 ──────────────────────────────────────────────
# 엣지의 evidence_id / evidence_ids 에서 버릴 id를 대표 id로 갈아끼운다.
_REMAP = """
UNWIND $pairs AS p
MATCH ()-[r]->()
WHERE r.evidence_id = p.old OR p.old IN coalesce(r.evidence_ids, [])
SET r.evidence_id = CASE WHEN r.evidence_id = p.old THEN p.keep
                         ELSE r.evidence_id END,
    r.evidence_ids = CASE WHEN r.evidence_ids IS NULL THEN r.evidence_ids
                          ELSE [x IN r.evidence_ids |
                                CASE WHEN x = p.old THEN p.keep ELSE x END]
                     END
RETURN count(*) AS n
"""

_DEDUP_ARRAY = """
MATCH ()-[r]->() WHERE r.evidence_ids IS NOT NULL
SET r.evidence_ids = apoc.coll.toSet(r.evidence_ids)
RETURN count(*) AS n
"""

# ── ② 고아 삭제 ──────────────────────────────────────────────
# 엣지가 참조하는 모든 evidence_id — 스칼라 + 병합으로 보존된 목록
_REFERENCED = """
MATCH ()-[r]->()
WHERE r.evidence_id IS NOT NULL OR r.evidence_ids IS NOT NULL
RETURN collect(DISTINCT r.evidence_id) AS single,
       collect(r.evidence_ids) AS lists
"""


def dedup(dry_run: bool) -> int:
    """내용이 같은 청크를 하나로. 합친 개수를 반환."""
    store = get_store()
    got = store._col(EVIDENCE_COLLECTION).get(include=["documents"])
    ids, docs = got["ids"], got["documents"]
    print(f"■ 중복 병합 — 근거 청크 {len(ids):,}개")

    by_text: dict[str, list[str]] = defaultdict(list)
    for cid, doc in zip(ids, docs):
        if doc:
            by_text[doc.strip()].append(cid)
    groups = {t: v for t, v in by_text.items() if len(v) > 1}
    drop_total = sum(len(v) - 1 for v in groups.values())
    print(f"   내용이 같은 그룹 {len(groups):,}개 · 합칠 청크 {drop_total:,}개 "
          f"({drop_total / max(len(ids), 1) * 100:.1f}%)")
    if not groups:
        print("   중복이 없습니다.")
        return 0

    for t, v in list(groups.items())[:5]:
        print(f"     ×{len(v)}  {t.splitlines()[0][:74]}")
    if len(groups) > 5:
        print(f"     … 외 {len(groups) - 5}그룹")
    if dry_run:
        print("   [dry-run] 변경 없음")
        return 0

    # 대표 = 사전순 첫 id (재현 가능하게)
    pairs, drop_ids = [], []
    for cids in groups.values():
        keep = sorted(cids)[0]
        for old in cids:
            if old != keep:
                pairs.append({"old": old, "keep": keep})
                drop_ids.append(old)

    # ★엣지 참조를 **먼저** 옮긴다. 청크를 먼저 지우면 근거를 잃는다.
    with neo4j_session() as session:
        for i in range(0, len(pairs), 500):
            session.run(_REMAP, pairs=pairs[i:i + 500])
        session.run(_DEDUP_ARRAY)
    print(f"   엣지 참조 {len(pairs):,}건을 대표 id로 이전")

    for i in range(0, len(drop_ids), 500):
        store.delete(EVIDENCE_COLLECTION, drop_ids[i:i + 500])
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_DELETE_CHUNKS, (drop_ids,))
    print(f"   청크 {len(drop_ids):,}개 삭제 → {len(ids):,} → {len(ids)-len(drop_ids):,}개")
    return len(drop_ids)


def prune(dry_run: bool) -> int:
    """아무 엣지도 참조하지 않는 청크를 삭제. 지운 개수를 반환."""
    with neo4j_session() as session:
        row = session.run(_REFERENCED).single()
    referenced = {e for e in (row["single"] or []) if e}
    for lst in row["lists"] or []:
        referenced.update(e for e in (lst or []) if e)

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chunk_id FROM vector_chunks WHERE chunk_type='evidence'")
            all_chunks = {r[0] for r in cur.fetchall()}
        orphans = sorted(all_chunks - referenced)
        print(f"\n■ 고아 삭제 — 엣지가 참조 {len(referenced):,}건 · "
              f"등록 청크 {len(all_chunks):,}건 → 고아 {len(orphans):,}건")
        if not orphans:
            print("   정리할 고아 청크가 없습니다.")
            return 0

        # 무엇을 지우는지 보여준다 — 지운 뒤엔 되돌릴 수 없다
        sample = get_store().get(EVIDENCE_COLLECTION, orphans[:5])
        for cid, doc in zip(sample.get("ids", []), sample.get("documents", [])):
            print(f"     {cid}  {(doc or '').splitlines()[0][:74]}")
        if len(orphans) > 5:
            print(f"     … 외 {len(orphans) - 5}건")
        if dry_run:
            print(f"   [dry-run] 고아 청크 {len(orphans):,}건 삭제 예정")
            return 0

        get_store().delete(EVIDENCE_COLLECTION, orphans)
        with conn.cursor() as cur:
            cur.execute(_DELETE_CHUNKS, (orphans,))
        print(f"   ✅ 고아 청크 {len(orphans):,}건 삭제 (ChromaDB + vector_chunks)")
    return len(orphans)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["dedup", "prune"],
                    help="하나만 실행 (기본은 병합 → 삭제 순서로 둘 다)")
    args = ap.parse_args()

    if args.only in (None, "dedup"):
        dedup(args.dry_run)
    if args.only in (None, "prune"):
        prune(args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
