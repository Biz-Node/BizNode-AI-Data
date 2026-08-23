"""이미 접힌 클러스터의 **대표 subtype을 다시 고르고** 낡은 검증 판정을 걷는다.

왜 소급이 필요한가 (2026-08-11)

`repair/edges`의 클러스터링은 「같은 두 노드·같은 엣지 타입」이 **2개 이상**일 때만
돈다. 한 번 접히고 나면 엣지가 하나라 조건에 안 걸리므로, 대표 뽑기 규칙을 고쳐도
**이미 접힌 것에는 적용되지 않는다.** 그 규칙이 오늘 세 번 바뀌었다:

    ① 타입 이름 제외   「공급」·「협력」이 최빈값으로 이기던 것을 막음
    ② 숫자 제외        「지분 12.42%」 — `ratio` 필드에 이미 있는 값
    ③ 한글 우선        「defamation」이 「명예훼손」을 이기던 것

  실측: 클러스터 500건 중 **229건의 대표가 바뀐다.**

검증 도장도 함께 걷는다

접힌 엣지의 `evidence_ids`는 여러 근거의 합집합인데, `grounding_verdict`는 대표
엣지 **하나**를 보고 내린 것이다. 대표가 `unfounded`였으면 합쳐진 다른 근거가
뒷받침해도 물리쳐진 채로 남는다. 도장을 걷어 2차 검증이 다시 보게 한다.
(1차 토큰 대조는 매번 전수라 이미 합집합을 본다 — 2차만 도장으로 걸러진다)

★앞으로는 `repair/edges`가 접을 때 같이 한다(같은 날 수정). 이 스크립트는
  **이미 쌓인 것**을 정리한다. 멱등이라 여러 번 돌려도 안전하다.

    python -m batch.repair.cluster_reps --dry-run
    python -m batch.repair.cluster_reps
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session
from pipeline.normalizer.relations import (
    _DEFAULT_SUBTYPE, _UNKNOWN_SUBTYPE, canonical_forms)

# `repair/edges._CLUSTER`와 **같은 목록**이어야 한다 — 한쪽만 고치면 다음
# 클러스터링이 도로 되돌린다.
_CANON_BY_TYPE = {t: sorted(canonical_forms(t)) for t in _DEFAULT_SUBTYPE}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# `repair/edges._CLUSTER`의 대표 뽑기와 **같은 규칙**이어야 한다.
# 여기서만 고치면 다음 클러스터링이 도로 되돌린다.
_PICK = r"""
MATCH (a)-[r]->(b)
WHERE r.subtypes IS NOT NULL AND size(r.subtypes) > 1
WITH a, b, r, type(r) AS t, r.subtypes AS all_subs, coalesce(r.subtype,'') AS now
// ★B군(DEVELOPS·IMPACTS·HAS_EVENT)은 **비우는 것이 정답**이라 후보를 만들지 않는다.
//   「무엇을」을 Product·Event 노드와 `sign`·`event_type`이 이미 말한다.
//   빼지 않으면 「사건 | OTHER」에서 쓰레기값 「OTHER」가 대표로 올라온다(실측).
// ★`_UNKNOWN_SUBTYPE`류(OTHER·기타·test…)도 후보에서 뺀다 — 정규화가 이미
//   버리기로 한 값인데 배열에는 남아 있다.
WITH a, b, r, t, all_subs, now,
     CASE WHEN t IN ['DEVELOPS','IMPACTS','HAS_EVENT'] THEN []
          ELSE [s IN all_subs
                WHERE s <> coalesce($defaults[t], $sentinel)
                  AND NOT toLower(s) IN $junk] END AS c0
WITH a, b, r, t, all_subs, now, c0,
     [s IN c0 WHERE NOT s =~ '.*\\d+(\\.\\d+)?\\s*(%|퍼센트|억|조|만원|억원).*'] AS c1
WITH a, b, r, t, all_subs, now, CASE WHEN size(c1) > 0 THEN c1 ELSE c0 END AS c2
WITH a, b, r, t, all_subs, now, c2, [s IN c2 WHERE s =~ '.*[가-힣].*'] AS c3
WITH a, b, r, t, all_subs, now, CASE WHEN size(c3) > 0 THEN c3 ELSE c2 END AS c4
WITH a, b, r, all_subs, now, c4 AS cand
WITH a, b, r, all_subs, now,
     reduce(best = '', s IN cand |
        CASE WHEN size([x IN cand WHERE x = s]) > size([x IN cand WHERE x = best])
                  OR (size([x IN cand WHERE x = s]) = size([x IN cand WHERE x = best])
                      AND size(s) > size(best))
             THEN s ELSE best END) AS newrep
RETURN elementId(r) AS eid, coalesce(a.name,'?') AS a, type(r) AS t,
       coalesce(b.name,'?') AS b, now, newrep, all_subs
"""

_APPLY = """
UNWIND $rows AS row
MATCH ()-[r]->() WHERE elementId(r) = row.eid
SET r.subtype = row.rep, r.cluster_rep_fixed = true
RETURN count(*) AS n
"""

# 접힌 엣지 전부 — 대표가 바뀌었든 아니든 판정은 낡았다(근거 묶음이 커졌으므로).
_CLEAR_STAMPS = """
MATCH ()-[r]->() WHERE r.subtypes IS NOT NULL AND size(r.subtypes) > 1
  AND (r.grounding_checked_at IS NOT NULL OR r.grounding_verdict IS NOT NULL)
SET r.grounding_checked_at = NULL, r.grounding_verdict = NULL,
    r.grounding_verdict_why = NULL, r.grounding_reason = NULL,
    r.grounding_stage1 = NULL, r.grounding_suspect = NULL
RETURN count(*) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-verdicts", action="store_true",
                    help="검증 판정은 건드리지 않는다 (대표만 고침)")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_PICK, defaults=_DEFAULT_SUBTYPE,
                                       canon=_CANON_BY_TYPE, junk=sorted(_UNKNOWN_SUBTYPE), sentinel="~~none~~")]

    changed = [r for r in rows if r["now"] != r["newrep"]]
    print("=" * 70)
    print(f"  접힌 클러스터 {len(rows)}건 · 대표가 바뀌는 것 {len(changed)}건")
    print("=" * 70)
    for r in changed[:14]:
        print(f"   {r['a'][:13]:<14}-[{r['t']:<13}]-> {r['b'][:13]:<14}")
        print(f"        「{(r['now'] or '(빈값)')[:24]}」 → "
              f"「{(r['newrep'] or '(빈값)')[:24]}」")
        print(f"        [{' | '.join(x[:13] for x in r['all_subs'])}]")
    if len(changed) > 14:
        print(f"   … 외 {len(changed) - 14}건")

    still_empty = [r for r in rows if not r["newrep"]]
    if still_empty:
        print(f"\n   ⚠ 새 규칙으로도 대표가 빈 것 {len(still_empty)}건 "
              f"(후보가 전부 타입 이름)")

    if args.dry_run:
        print("\n[dry-run] 실제로 바뀐 것은 없습니다.")
        return 0

    with neo4j_session() as s:
        n = 0
        if changed:
            n = s.run(_APPLY, rows=[{"eid": r["eid"], "rep": r["newrep"]}
                                    for r in changed]).single()["n"]
        cleared = 0
        if not args.keep_verdicts:
            cleared = s.run(_CLEAR_STAMPS).single()["n"]

    print(f"\n✅ 대표 {n}건 교체 (`cluster_rep_fixed=true`)")
    if cleared:
        print(f"   검증 판정 {cleared}건 걷음 — 다음 finalize가 "
              f"**합쳐진 근거 전부**로 다시 봅니다")
        print(f"   예상 비용: 2차 검증에 걸리는 만큼만 (1차는 무료 전수)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
