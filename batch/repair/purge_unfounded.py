"""2단 검사를 **둘 다** 실패한 엣지를 지운다 — 근거는 남기고.

「삭제보다 표시」의 예외를 여기 둔다

이 저장소의 기본은 표시다. 판정이 틀릴 수 있으니 지우지 않고 조회에서 빼는 쪽이
안전하기 때문이다. 그런데 표시만 하면 **걷어낼 길이 없어 계속 쌓인다**:

    전체 엣지 9,100 · 의심 표시 547건(6.0%) · 걷어내는 경로 **없음**

남은 19곳을 넣으면 800~900건이 된다. 조회마다 필터로 걸러야 하는 짐이고, 「의심」이
많아질수록 진짜 봐야 할 것이 묻힌다.

그래서 **2단을 둘 다 실패한 것만** 지운다

    1차  근거 문장과 주장의 토큰 대조   무료·전수
    2차  기사 **전문**으로 LLM 재판정   유료·1차 의심분만

  1차만 걸린 것은 전문을 보면 풀리는 경우가 많아 손대지 않는다. 둘 다 실패했다면
  「저장된 문장에도 없고 기사 전문에도 없다」는 뜻이라, 추출기가 만들어낸 것이다.

  실측(2026-08-11): 547건 중 485건이 2단 실패. 판정 사유가 구체적이다 —
      한미반도체 -SUES-> 아워홈       "계약 해지 내용만 있고 소송 언급 없음"
      삼성전자 -DEVELOPS-> Z 플립5    "판매 중단 내용뿐, 개발 언급 없음"
      쏘카 -ACQUIRES-> 에이펙스 모빌리티 "신설 법인 설립 계획일 뿐 인수가 아님"

★`wrong_type`은 지우지 않는다
  「유형이 틀렸다」는 관계 자체는 있다는 뜻이다. `repair/retypes`가 고칠 몫이라
  여기서 지우면 고칠 기회를 없앤다.

★근거는 지우지 않는다
  Chroma의 evidence 청크는 그대로 둔다. `repair/evidence --only prune`이 어느
  엣지도 안 가리키는 근거를 따로 치운다 — 그때 정리된다.

★무엇을 지웠는지 남긴다
  PostgreSQL `purged_edges`에 적재한다. 되짚을 수 없는 삭제는 하지 않는다.

    python -m batch.repair.purge_unfounded --dry-run
    python -m batch.repair.purge_unfounded
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_CREATE = """
CREATE TABLE IF NOT EXISTS purged_edges (
    id          BIGSERIAL PRIMARY KEY,
    purged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    src_name    TEXT, edge_type TEXT, tgt_name TEXT,
    subtype     TEXT, source_type TEXT, source_doc TEXT,
    evidence_id TEXT, stage1 TEXT, verdict TEXT, verdict_why TEXT
)
"""

_INSERT = """
INSERT INTO purged_edges
  (src_name, edge_type, tgt_name, subtype, source_type, source_doc,
   evidence_id, stage1, verdict, verdict_why)
VALUES (%(a)s, %(t)s, %(b)s, %(st)s, %(src)s, %(doc)s,
        %(ev)s, %(s1)s, %(v)s, %(why)s)
"""

# 2단을 **둘 다** 실패. `wrong_type`은 유형만 틀린 것이라 뺀다.
_FIND = """
MATCH (a)-[r]->(b)
WHERE coalesce(r.grounding_suspect, false)
  AND r.grounding_verdict = 'unfounded'
  AND coalesce(r.grounding_stage1, '') <> 'wrong_type'
RETURN elementId(r) AS eid, coalesce(a.name,'?') AS a, type(r) AS t,
       coalesce(b.name,'?') AS b, coalesce(r.subtype,'') AS st,
       coalesce(r.source_type,'') AS src, toString(coalesce(r.source_doc,'')) AS doc,
       coalesce(r.evidence_id,'') AS ev, coalesce(r.grounding_stage1,'') AS s1,
       coalesce(r.grounding_verdict,'') AS v,
       toString(coalesce(r.grounding_verdict_why,'')) AS why
"""

_DELETE = """
UNWIND $eids AS eid
MATCH ()-[r]->() WHERE elementId(r) = eid
DELETE r
RETURN count(*) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="한 번에 지울 최대 건수")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND)]
    if args.limit:
        rows = rows[:args.limit]

    if not rows:
        print("2단 검사를 둘 다 실패한 엣지가 없습니다.")
        return 0

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["t"]] = by_type.get(r["t"], 0) + 1

    print("=" * 70)
    print(f"  2단 검사 둘 다 실패 — {len(rows)}건")
    print("=" * 70)
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"   {t:<18}{n:>5}건")

    print("\n  예시")
    for r in rows[:8]:
        print(f"   {r['a'][:15]:<16}-[{r['t']:<13}]-> {r['b'][:15]}")
        print(f"      {r['why'][:96]}")

    if args.dry_run:
        print(f"\n[dry-run] {len(rows)}건을 지웁니다. "
              f"근거 청크와 `purged_edges` 기록은 남습니다.")
        return 0

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            for r in rows:
                cur.execute(_INSERT, {
                    "a": r["a"][:200], "t": r["t"], "b": r["b"][:200],
                    "st": r["st"][:200], "src": r["src"], "doc": r["doc"][:500],
                    "ev": r["ev"], "s1": r["s1"], "v": r["v"],
                    "why": r["why"][:2000],
                })
        conn.commit()

    with neo4j_session() as s:
        n = s.run(_DELETE, eids=[r["eid"] for r in rows]).single()["n"]

    print(f"\n✅ {n}건 삭제 — PostgreSQL `purged_edges`에 전부 기록했습니다.")
    print("   되짚기: SELECT * FROM purged_edges ORDER BY purged_at DESC;")
    print("   근거 청크는 그대로입니다 — `repair/evidence --only prune`이 "
          "고아가 된 것을 따로 치웁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
