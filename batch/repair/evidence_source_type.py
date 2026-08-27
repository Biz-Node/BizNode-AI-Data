"""evidence 청크 메타에 `source_type` 을 채운다 — **재임베딩 없이.**

ChromaDB `evidence` 컬렉션(실측 2026-08-27: 10,510청크)의 메타는
`edge_type · subtype · source_corp · target_corp · rcept_no · occurred_at`
여섯 개뿐이라 **출처 종류가 없다.** `news_loader` 만 이 키를 넣었고
`disclosure_loader`·`business_report_loader` 는 안 넣어서, 청크만 보고는
「공시인가 보도인가」를 알 수 없다.

그런데 `Evidence.source_type` 은 챗봇 답변의 신뢰도를 가르는 값이다 —
`dart` 는 확정 사실, `news` 는 보도다. 지금은 `relation_service` 가
엣지 속성으로 메워 주고 있지만, 그러려면 **근거를 항상 엣지에서 출발해
읽어야 한다.** 의미검색으로 청크를 먼저 잡는 경로에는 메울 것이 없다.

진실 출처 — 엣지의 **스칼라** `evidence_id`
────────────────────────────────────────────────────────────────
Neo4j 엣지는 `(evidence_id, source_type)` 이 100% 채워져 있다(실측:
`evidence_id` 보유 엣지 11,060건 전부에 `source_type` 이 있다).

★`evidence_ids` **배열은 쓰지 않는다.** 배열은 「이 청크의 출처」가 아니라
  「이 관계를 뒷받침하는 근거들」이다. `batch/repair/edges.py` 의 `_CLUSTER`
  가 같은 두 노드의 엣지를 **`source_type` 구분 없이** 접으면서 배열을
  합집합으로 만들기 때문이다. 실측(2026-08-27)으로 확인된 오염:

      스칼라는 news 인데 배열은 dart 인 id      39건
      배열끼리도 값이 갈리는 id                  5건

  배열까지 쓰면 10,510청크를 100% 덮을 수 있지만(1,282건이 배열에만 있다),
  그 1,282건은 **출처가 따로 증언된 적이 없는** id다. 덮는 대신 개수만
  리포트한다 — `rcept_no` 로 추측하지도 않는다. 없는 것과 모르는 것은 다르다.

두 가지 불일치를 어떻게 다루나
────────────────────────────────────────────────────────────────
① **`evidence_id` 는 엣지에서 유일하지 않다.** 엣지 11,060건에 유일 id 는
   9,228개 — 한 근거가 여러 관계를 뒷받침한다. 같은 id 에 붙은 엣지들의
   `source_type` 이 **서로 다르면 경고를 남기고 건너뛴다.** 임의로 하나를
   고르면 「공시에서 나온 사실」과 「보도」가 조용히 뒤바뀐다.
   (실측 현재 충돌 0건 — 그래도 규칙은 둔다. 배열 오염이 스칼라로 번지면
    여기서 걸려야 한다.)

② **청크가 유일 id 보다 많다.** 10,510 > 9,228. Neo4j 스칼라에서 못 찾은
   청크는 **건드리지 않고** 개수만 리포트한다.

Neo4j 는 **읽기만** 한다. 임베딩도 다시 만들지 않는다
(`ChromaStore.update_metadata` → `collection.update(ids=, metadatas=)`).

    python -m batch.repair.evidence_source_type --dry-run
    python -m batch.repair.evidence_source_type
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from app.core.database import neo4j_session
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 한 번에 보낼 update 크기. `batch/repair/evidence.py` 의 delete 와 같은 폭.
_UPDATE_BATCH = 500

# 경고를 몇 건까지 펼쳐 보일지. 전부 찍으면 요약이 묻힌다.
_SHOW = 10

META_KEY = "source_type"

# ★스칼라만 읽는다. `evidence_ids` 배열은 위 docstring 의 이유로 제외한다.
_EDGE_Q = """
MATCH ()-[r]->()
WHERE r.evidence_id IS NOT NULL AND r.source_type IS NOT NULL
RETURN r.evidence_id AS eid, r.source_type AS st, type(r) AS etype
"""


def load_edge_source_types() -> tuple[dict[str, str], dict[str, set[str]]]:
    """Neo4j 엣지 → `(합의된 매핑, 충돌한 매핑)`.

    한 `evidence_id` 에 붙은 엣지들의 `source_type` 이 하나로 모이면 매핑에,
    갈리면 충돌에 담는다. 충돌은 **건너뛴다** — 고르지 않는다.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    edges = 0
    with neo4j_session() as s:
        for row in s.run(_EDGE_Q):
            seen[str(row["eid"])].add(str(row["st"]))
            edges += 1

    agreed = {eid: next(iter(sts)) for eid, sts in seen.items() if len(sts) == 1}
    conflicts = {eid: sts for eid, sts in seen.items() if len(sts) > 1}
    print(f"■ Neo4j 엣지 읽기 — 엣지 {edges:,}건 · 유일 evidence_id {len(seen):,}개")
    print(f"   합의 {len(agreed):,}개 · 충돌 {len(conflicts):,}개")
    for eid, sts in list(conflicts.items())[:_SHOW]:
        print(f"     ⚠ {eid}  source_type 이 갈림: {sorted(sts)} — 건너뜀")
    if len(conflicts) > _SHOW:
        print(f"     … 외 {len(conflicts) - _SHOW}건 (전부 건너뜀)")
    return agreed, conflicts


def plan(agreed: dict[str, str], conflicts: dict[str, set[str]]) -> tuple[
        list[str], list[dict], Counter]:
    """청크를 훑어 **채울 것**만 고른다. `(ids, metadatas, 집계)`."""
    got = get_store()._col(EVIDENCE_COLLECTION).get(include=["metadatas"])
    ids, metas = got["ids"], got["metadatas"]
    stat = Counter()
    stat["chunks"] = len(ids)

    upd_ids: list[str] = []
    upd_meta: list[dict] = []
    disagree: list[tuple[str, str, str]] = []
    for cid, md in zip(ids, metas):
        md = dict(md or {})
        want = agreed.get(cid)
        if want is None:
            stat["conflict_skipped" if cid in conflicts else "unmatched"] += 1
            continue
        have = md.get(META_KEY)
        if have == want:
            stat["already"] += 1
            continue
        if have:
            # 청크에 이미 값이 있는데 엣지와 다르다 — 덮지 않고 남긴다.
            # `news_loader` 만 이 키를 넣었으니 여기 걸리면 로더가 어긋난 것이다.
            disagree.append((cid, str(have), want))
            stat["disagree_skipped"] += 1
            continue
        upd_ids.append(cid)
        upd_meta.append({META_KEY: want})
        stat[f"fill_{want}"] += 1

    stat["fill"] = len(upd_ids)
    print(f"\n■ Chroma `{EVIDENCE_COLLECTION}` 훑기 — 청크 {stat['chunks']:,}개")
    print(f"   채울 것          {stat['fill']:,}")
    for st in sorted(k[5:] for k in stat if k.startswith("fill_")):
        print(f"       {st:12} {stat['fill_' + st]:,}")
    print(f"   이미 같은 값      {stat['already']:,}")
    print(f"   엣지와 값이 다름   {stat['disagree_skipped']:,}  (덮지 않는다)")
    for cid, have, want in disagree[:_SHOW]:
        print(f"     ⚠ {cid}  청크={have!r} vs 엣지={want!r} — 건너뜀")
    if len(disagree) > _SHOW:
        print(f"     … 외 {len(disagree) - _SHOW}건")
    print(f"   충돌로 건너뜀      {stat['conflict_skipped']:,}")
    print(f"   Neo4j 에서 못 찾음 {stat['unmatched']:,}  (건드리지 않는다)")
    return upd_ids, upd_meta, stat


def apply(ids: list[str], metas: list[dict]) -> int:
    """메타만 갱신한다 — 임베딩은 다시 만들지 않는다."""
    store = get_store()
    for i in range(0, len(ids), _UPDATE_BATCH):
        store.update_metadata(
            EVIDENCE_COLLECTION, ids[i:i + _UPDATE_BATCH], metas[i:i + _UPDATE_BATCH])
    return len(ids)


def verify() -> dict[str, int]:
    """백필 뒤 실측 — `where` 로 값별 청크 수를 센다."""
    col = get_store()._col(EVIDENCE_COLLECTION)
    total = col.count()
    counts = {}
    for st in ("news", "dart", "dart_filing"):
        counts[st] = len(col.get(where={META_KEY: st}, include=[])["ids"])
    counts["_total"] = total
    counts["_none"] = total - sum(counts[k] for k in ("news", "dart", "dart_filing"))
    return counts


def report(counts: dict[str, int], agreed: dict[str, str]) -> None:
    """실측 요약 — **엣지 수와 청크 수는 다른 것을 센다.** 그걸 밝힌다."""
    with neo4j_session() as s:
        edge = {r["st"]: r["n"] for r in s.run(
            "MATCH ()-[r]->() WHERE r.source_type IS NOT NULL "
            "RETURN r.source_type AS st, count(*) AS n")}
    uniq = Counter(agreed.values())

    print(f"\n■ 검증 — Chroma `{EVIDENCE_COLLECTION}` 청크 {counts['_total']:,}개")
    print(f"   {'source_type':14}{'청크':>9}{'유일 id':>10}{'엣지':>9}")
    for st in ("news", "dart", "dart_filing"):
        print(f"   {st:14}{counts[st]:>9,}{uniq.get(st, 0):>10,}{edge.get(st, 0):>9,}")
    print(f"   {'(값 없음)':14}{counts['_none']:>9,}")
    print("\n   ★「청크」와 「엣지」가 다른 것은 정상이다 — 세는 대상이 다르다.")
    print(f"     엣지 {sum(edge.values()):,}건이 유일 evidence_id "
          f"{len(agreed):,}개를 가리킨다(한 근거가 여러 관계를 뒷받침).")
    print("     청크는 id 하나에 하나뿐이라 **유일 id 수**와 맞아야 한다.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    agreed, conflicts = load_edge_source_types()
    ids, metas, _ = plan(agreed, conflicts)

    if args.dry_run:
        print(f"\n   [dry-run] 청크 {len(ids):,}개의 메타를 채울 예정 — 변경 없음")
        return 0
    if ids:
        print(f"\n   메타 갱신 {apply(ids, metas):,}건 (재임베딩 없음)")
    else:
        print("\n   채울 것이 없습니다.")

    report(verify(), agreed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
