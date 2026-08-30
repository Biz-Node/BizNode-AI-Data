"""섞인 Event 노드를 **판정대로 갈라 놓는다**. `batch/audit/event_merge.py`의 짝.

왜 이 파일이 따로 있나 (2026-08-29)

`batch/repair/event_merge.py`가 이름만 보고 합치는 바람에 서로 다른 사건이 한
노드에 들어갔다. 감사(`batch/audit/event_merge.py`)가 기사 제목·주체 기업·발생일을
모델에 보여 주고 「이 노드가 몇 개의 사건인가」를 판정해 `event_mix_verdicts`에
적어 뒀다. 여기서는 그 표를 읽어 **실제로 가른다.**

    「블랙웰 결함」  기사[1] 엔비디아 GPU 결함        (2024-08)
                    기사[2] 한미반도체→SK하이닉스 수주 (2025-01)
        → 두 노드로. 엣지도 각자 기사에 따라 따라간다.

가르는 방법 — **엣지를 옮긴다. 다시 만들지 않는다.**

  엣지에는 `evidence_id`·`confidence`·`first_seen`·`grounding_verdict` 등
  되살릴 수 없는 값이 붙어 있다. 지웠다 새로 만들면 전부 잃는다. 그래서
  `apoc.refactor.to`/`from`으로 **끝점만 갈아 끼운다** — 속성은 그대로 간다.

      HAS_EVENT   (Company)-[r]->(Event)   Event가 끝점  → apoc.refactor.to
      IMPACTS     (Event)-[r]->(Company)   Event가 시작점 → apoc.refactor.from

  기사가 가장 많은 그룹은 **원래 노드에 남긴다**(`event_id` 보존 — 다른 곳이
  이 id를 참조하고 있을 수 있다). 나머지 그룹만 새 노드가 된다.

★되돌릴 수 있어야 한다. 옮긴 엣지를 `event_splits`에 전부 적는다.

    python -m batch.repair.event_split --dry-run    무엇이 갈릴지만 본다
    python -m batch.repair.event_split              가른다
    python -m batch.repair.event_split --rollback   되돌린다
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ★`orig_name`·`orig_timeline`을 함께 적는다. 분리하면서 원본의 이름을 대표
#   그룹 이름으로 바꾸고 `timeline`을 비우는데, 그 값들이 여기 없으면 되돌려도
#   원래 노드가 원래대로 돌아오지 않는다 — 되돌릴 수 없는 롤백은 롤백이 아니다.
_CREATE = """
CREATE TABLE IF NOT EXISTS event_splits (
    id            BIGSERIAL PRIMARY KEY,
    orig_id       TEXT NOT NULL,
    new_id        TEXT NOT NULL,
    label         TEXT,
    subject       TEXT,
    occurred_at   TEXT,
    docs          JSONB NOT NULL,    -- 이 그룹으로 옮긴 기사 URL 목록
    moved         INT  NOT NULL,     -- 실제로 옮긴 엣지 수
    orig_name     TEXT,              -- 분리 전 원본 이름
    orig_timeline JSONB,             -- 분리 전 원본 timeline
    undone_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
# 표가 먼저 있었다면 컬럼만 보탠다 — `CREATE TABLE IF NOT EXISTS`는 안 고쳐 준다.
_ALTER = [
    "ALTER TABLE event_splits ADD COLUMN IF NOT EXISTS orig_name TEXT",
    "ALTER TABLE event_splits ADD COLUMN IF NOT EXISTS orig_timeline JSONB",
]

# ★그래프를 건드리기 **전에** 적는다(`moved = -1`). 중간에 죽으면 되돌릴 기록이
#   없는 노드가 그래프에 남는다 — 실제로 한 번 그렇게 됐다. 옮긴 뒤 `_MARK`로
#   실제 개수를 채운다. `moved = -1`이 남아 있으면 **미완결 분리**라는 뜻이다.
_SAVE = """
INSERT INTO event_splits (orig_id, new_id, label, subject, occurred_at, docs, moved,
                          orig_name, orig_timeline)
VALUES (%s,%s,%s,%s,%s,%s,-1,%s,%s) RETURNING id
"""
_MARK = "UPDATE event_splits SET moved = %s WHERE id = %s"
_PICK = """
SELECT event_id, mix_kind, groups, reason FROM event_mix_verdicts
WHERE verdict = 'mixed'
"""

# 원본 노드에서 물려받는 속성. 이름·출처·개수는 그룹마다 다시 센다.
_NEW_NODE = """
MATCH (o:Event {event_id:$orig})
CREATE (n:Event {event_id:$new})
SET n.name          = $label,
    n.event_type    = o.event_type,
    n.is_risk       = o.is_risk,
    n.classified_at = o.classified_at,
    n.source_doc    = $source_doc,
    n.source_docs   = $docs,
    n.article_count = size($docs),
    n.first_seen    = o.first_seen,
    n.last_seen     = o.last_seen,
    n.split_from    = $orig,
    n.split_at      = datetime()
RETURN n.event_id AS id
"""

# 엣지 옮기기 — 방향에 따라 갈아 끼우는 끝점이 다르다.
_MOVE_IN = """
MATCH (c:Company)-[r]->(o:Event {event_id:$orig})
WHERE r.source_doc IN $docs
MATCH (n:Event {event_id:$new})
CALL apoc.refactor.to(r, n) YIELD input
RETURN count(*) AS moved
"""
_MOVE_OUT = """
MATCH (o:Event {event_id:$orig})-[r]->(c:Company)
WHERE r.source_doc IN $docs
MATCH (n:Event {event_id:$new})
CALL apoc.refactor.from(r, n) YIELD input
RETURN count(*) AS moved
"""

# 원본에 남은 그룹의 이름·출처를 다시 맞춘다.
_RETOUCH = """
MATCH (e:Event {event_id:$id})
OPTIONAL MATCH (e)-[r]-(:Company)
WITH e, [d IN collect(DISTINCT r.source_doc) WHERE d IS NOT NULL] AS docs
SET e.name          = coalesce($label, e.name),
    e.source_docs   = docs,
    e.article_count = size(docs),
    e.source_doc    = CASE WHEN size(docs) > 0 THEN docs[0] ELSE e.source_doc END,
    e.timeline      = NULL,
    e.split_at      = datetime()
RETURN size(docs) AS n
"""

_RESTORE = """
MATCH (e:Event {event_id:$id})
OPTIONAL MATCH (e)-[r]-(:Company)
WITH e, [d IN collect(DISTINCT r.source_doc) WHERE d IS NOT NULL] AS docs
SET e.name          = $name,
    e.timeline      = $timeline,
    e.source_docs   = docs,
    e.article_count = size(docs),
    e.split_at      = NULL
RETURN size(docs) AS n
"""

# 되돌리기 — 옮긴 엣지를 원래 노드로 보내고 빈 노드를 지운다.
_BACK_IN = """
MATCH (c:Company)-[r]->(n:Event {event_id:$new})
MATCH (o:Event {event_id:$orig})
CALL apoc.refactor.to(r, o) YIELD input
RETURN count(*) AS moved
"""
_BACK_OUT = """
MATCH (n:Event {event_id:$new})-[r]->(c:Company)
MATCH (o:Event {event_id:$orig})
CALL apoc.refactor.from(r, o) YIELD input
RETURN count(*) AS moved
"""
_DROP = "MATCH (n:Event {event_id:$new}) DETACH DELETE n"


def _new_id(orig: str, label: str, docs: list[str]) -> str:
    """결정적 id — 같은 판정을 두 번 적용해도 같은 노드가 된다.

    ★기사 목록까지 넣는다. 모델이 두 그룹에 **같은 이름**을 붙이는 일이 있어
      (「삼성전자 LG전자 브라질 순손실」이 양쪽 다 「브라질 순손실」) 이름만으로는
      id가 겹친다. `event_id`에 유일성 제약이 걸려 있어 겹치면 적재가 멈춘다.
    """
    key = f"{orig}|{label}|{'|'.join(sorted(docs))}"
    return f"evt_split_{hashlib.sha1(key.encode()).hexdigest()[:12]}"


# 국면을 붙일 노드와 이만큼 넘게 떨어져 있으면 붙이지 않는다.
_FAR_MONTHS = 12

_FAMILY_DATES = """
MATCH (e:Event) WHERE e.event_id IN $ids
OPTIONAL MATCH (e)-[r]-(:Company)
RETURN e.event_id AS id, e.name AS name, coalesce(e.timeline, []) AS tl,
       [d IN collect(DISTINCT r.occurred_at) WHERE d IS NOT NULL] AS dates
"""
_SET_TIMELINE = """
MATCH (e:Event {event_id:$id}) SET e.timeline = $tl RETURN size($tl) AS n
"""


def _mon(p):
    try:
        return int(str(p)[:4]) * 12 + int(str(p)[5:7])
    except (ValueError, IndexError, TypeError):
        return None


def _restore_timeline(args) -> int:
    """분리하면서 비운 `timeline`을 **갈라진 노드들에 나눠** 되돌린다.

    왜 그냥 되돌리면 안 되나 — `timeline`은 「합쳐지며 사라진 이름」의 기록이다.
    노드를 가른 뒤 원본에 통째로 되돌리면, 갈라져 나간 사건의 국면까지 원본이
    갖게 된다. 그래서 각 항목의 **연월이 어느 노드의 기간에 맞는지** 보고 배분한다.

        「유니투스 무기한 파업」 → 램프사업부 파업(2026) · 자회사 출범 파업(2024)
        timeline 항목 「2024-11 …」 → 2024 쪽 노드로

    ★`timeline`은 답변에 직접 쓰인다(`app/graph/prompt.py` 의 「국면: …」).
      비워 두면 「이 사건 어떻게 진행됐나」에 답할 재료가 사라진다.

    ★병합(`event_merge`)이 나중에 붙인 항목은 **건드리지 않는다** — 이미 있는
      것에 더하고, 같은 항목은 한 번만 둔다.
    """
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT orig_id, new_id, orig_timeline FROM event_splits
                           WHERE undone_at IS NULL AND orig_timeline IS NOT NULL
                           ORDER BY id""")
            rows = cur.fetchall()

    fams: dict[str, dict] = {}
    for orig, new, tl in rows:
        f = fams.setdefault(orig, {"tl": [], "kids": []})
        f["kids"].append(new)
        for e in (tl or []):
            if e not in f["tl"]:
                f["tl"].append(e)

    print("=" * 74)
    print(f"  timeline 복원 — 분리한 원본 {len(fams)}건")
    print("=" * 74)

    touched = placed = dropped = 0
    with neo4j_session() as s:
        for orig, f in fams.items():
            ids = [orig] + f["kids"]
            nodes = {r["id"]: dict(r) for r in s.run(_FAMILY_DATES, ids=ids)}
            if not nodes:                     # 나중 병합으로 사라진 원본
                dropped += len(f["tl"])
                continue

            spans = {}
            for nid, n in nodes.items():
                ms = [m for m in (_mon(str(d)[:7]) for d in n["dates"]) if m]
                if ms:
                    spans[nid] = (min(ms), max(ms))
            if not spans:
                dropped += len(f["tl"])
                continue

            # ★분리 전 항목은 일단 **걷어내고** 다시 배분한다. 그래야 두 번
            #   돌려도 같은 결과가 나오고, 잘못 붙였던 것이 그대로 남지 않는다.
            #   병합이 나중에 붙인 항목은 `f["tl"]`에 없으므로 그대로 살아남는다.
            assign: dict[str, list[str]] = {
                nid: [e for e in nodes[nid]["tl"] if e not in f["tl"]] for nid in nodes}

            for entry in f["tl"]:
                m = _mon(entry.split("|")[0])
                if m is None:
                    dropped += 1
                    continue

                def _dist(k, m=m):
                    lo, hi = spans[k]
                    return 0 if lo <= m <= hi else min(abs(m - lo), abs(m - hi))

                best = min(spans, key=_dist)
                # ★어느 노드와도 1년 넘게 떨어진 국면은 **버린다**. 가장 가까운
                #   곳에 무조건 붙였더니 2024년 국면이 2026년 사건에 붙어
                #   시점폭 12개월 이상이 40 → 48로 되돌아갔다. 국면 기록을
                #   잃는 것보다 방금 없앤 반복 융합을 되살리는 쪽이 나쁘다.
                if _dist(best) > _FAR_MONTHS:
                    dropped += 1
                    continue
                if entry not in assign[best]:
                    assign[best].append(entry)
                    placed += 1

            for nid, tl in assign.items():
                if tl != nodes[nid]["tl"]:
                    if not args.dry_run:
                        s.run(_SET_TIMELINE, id=nid, tl=tl)
                    touched += 1
                    print(f"    「{nodes[nid]['name'][:30]:<32}」 국면 {len(tl)}개")

    print(f"\n{'[dry-run] ' if args.dry_run else '✅ '}노드 {touched}개에 국면 "
          f"{placed}개 복원 · 갈 곳이 없어 버린 항목 {dropped}개")
    return 0


def _rollback(args) -> int:
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            for stmt in _ALTER:
                cur.execute(stmt)
            cur.execute("SELECT id, orig_id, new_id, label, orig_name, orig_timeline "
                        "FROM event_splits WHERE undone_at IS NULL ORDER BY id DESC")
            rows = cur.fetchall()
        conn.commit()

        print(f"  되돌릴 분리 {len(rows)}건")
        if args.dry_run:
            for _, o, n, lb, *_ in rows[:20]:
                print(f"    {n} 「{lb}」 → {o}")
            print("\n[dry-run] 되돌리지 않았습니다.")
            return 0

        done = 0
        with neo4j_session() as s:
            for sid, orig, new, label, oname, otl in rows:
                back = (s.run(_BACK_IN, orig=orig, new=new).single()["moved"]
                        + s.run(_BACK_OUT, orig=orig, new=new).single()["moved"])
                s.run(_DROP, new=new)
                s.run(_RESTORE, id=orig, name=oname, timeline=otl)
                with conn.cursor() as cur:
                    cur.execute("UPDATE event_splits SET undone_at = now() WHERE id = %s",
                                (sid,))
                conn.commit()
                done += 1
                print(f"    ↩ 「{label}」 엣지 {back}개 복귀 · {new} 삭제")
        print(f"\n✅ {done}건 되돌렸습니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--kind", choices=["company", "time", "both"],
                    help="이 종류만 가른다")
    ap.add_argument("--rollback", action="store_true", help="적용한 분리를 되돌린다")
    ap.add_argument("--restore-timeline", action="store_true",
                    help="분리하며 비운 국면을 갈라진 노드들에 나눠 되돌린다")
    args = ap.parse_args()

    if args.rollback:
        return _rollback(args)
    if args.restore_timeline:
        return _restore_timeline(args)

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            for stmt in _ALTER:
                cur.execute(stmt)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(_PICK)
            verdicts = cur.fetchall()
            cur.execute("SELECT orig_id FROM event_splits WHERE undone_at IS NULL")
            applied = {r[0] for r in cur.fetchall()}

        todo = [v for v in verdicts
                if v[0] not in applied
                and len(v[2] or []) >= 2
                and (not args.kind or v[1] == args.kind)]
        if args.limit:
            todo = todo[:args.limit]

        print("=" * 74)
        print(f"  섞였다고 판정된 Event {len(verdicts)}건 · 이미 적용 {len(applied)}건 "
              f"· 이번에 가를 것 {len(todo)}건")
        print("=" * 74)

        with neo4j_session() as s:
            before = {r["id"]: (r["name"], r["tl"]) for r in
                      s.run("MATCH (e:Event) RETURN e.event_id AS id, e.name AS name, "
                            "e.timeline AS tl")}

            split_n = new_n = 0
            for orig, kind, groups, reason in todo:
                # ★모델이 **같은 기사를 두 그룹에** 넣는 일이 있다. 그대로 두면
                #   앞 그룹이 엣지를 가져가 버려 원본이 기사 0건인 빈 노드로
                #   남는다(실측: 64건 중 4건). 앞선 그룹이 갖고, 빈 그룹은 버린다.
                seen: set[str] = set()
                clean = []
                for g in groups:
                    docs = [d for d in (g.get("docs") or []) if d not in seen]
                    seen.update(docs)
                    if docs:
                        clean.append({**g, "docs": docs})
                if len(clean) < 2:
                    continue

                # 기사가 가장 많은 그룹이 원본 노드에 남는다(event_id 보존).
                ordered = sorted(clean, key=lambda g: -len(g.get("docs") or []))
                keep, rest = ordered[0], [g for g in ordered[1:] if g.get("docs")]
                if not rest:
                    continue

                oname, otl = before.get(orig, (orig, None))
                print(f"\n  「{oname}」 [{kind}] {reason[:34]}")
                print(f"    남김: 「{keep['label']}」 기사 {len(keep.get('docs') or [])}건")
                for g in rest:
                    docs = g["docs"]
                    nid = _new_id(orig, g["label"], docs)
                    print(f"    가름: 「{g['label']}」 기사 {len(docs)}건 "
                          f"→ {nid} (주체 {g.get('subject') or '-'})")
                    new_n += 1
                    if args.dry_run:
                        continue
                    with conn.cursor() as cur:
                        cur.execute(_SAVE, (orig, nid, g["label"], g.get("subject"),
                                            g.get("occurred_at"),
                                            json.dumps(docs, ensure_ascii=False),
                                            oname,
                                            json.dumps(otl, ensure_ascii=False)
                                            if otl else None))
                        sid = cur.fetchone()[0]
                    conn.commit()

                    s.run(_NEW_NODE, orig=orig, new=nid, label=g["label"],
                          docs=docs, source_doc=docs[0])
                    moved = (s.run(_MOVE_IN, orig=orig, new=nid, docs=docs)
                             .single()["moved"]
                             + s.run(_MOVE_OUT, orig=orig, new=nid, docs=docs)
                             .single()["moved"])
                    with conn.cursor() as cur:
                        cur.execute(_MARK, (moved, sid))
                    conn.commit()
                    print(f"          엣지 {moved}개 이동")
                if not args.dry_run:
                    left = s.run(_RETOUCH, id=orig, label=keep["label"]).single()["n"]
                    print(f"    원본 정리 — 남은 기사 {left}건, timeline 비움")
                split_n += 1

        if args.dry_run:
            print(f"\n[dry-run] Event {split_n}건이 갈리고 노드 {new_n}개가 생깁니다.")
        else:
            print(f"\n✅ Event {split_n}건을 갈라 새 노드 {new_n}개 생성 "
                  f"· 되돌리려면 --rollback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
