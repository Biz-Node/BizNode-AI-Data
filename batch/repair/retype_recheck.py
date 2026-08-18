"""재분류된 엣지의 **낡은 근거 판정**을 걷어 다시 검증받게 한다.

★무슨 일이 있었나 (2026-08-11)

`repair/retypes`가 엣지 타입을 고칠 때 `grounding_suspect`(의심 표시)만 지우고
`grounding_checked_at`(검사 도장)은 남겼다. 그런데 `audit/grounding`은 **도장으로
대상을 고른다**:

    pool_rows = [s for s in base if args.full or not s[0]["checked"]]

그래서 재분류된 엣지가 **영영 재검증되지 않았다.** 세 가지가 동시에 일어났다:

    · 낡은 `grounding_verdict`(재분류 **전** 판정)가 그대로 남는다
    · 의심 표시는 지워져 **조회에 그대로 노출**된다
    · 도장 때문에 다시 볼 기회도 없다

  실측: 재분류 123건 중 57건이 「판정 없이 도장만」, 그중 8건은 `unfounded`
  판정을 달고도 조회에 보였다. 그중엔 미국 집단소송 로펌이 규제기관으로
  들어와 있는 것도 있었다(`Kahn Swick & Foti -REGULATES-> SK하이닉스`).

★왜 「의심으로 표시」가 아니라 「판정 지우기」인가

낡은 판정으로 숨기는 것도 **판정이다.** 「협력이 아니다」라는 판정으로
「공급이다」를 막을 수는 없다 — 타입이 바뀌었으니 그 판정은 무효다.
「모른다」를 「없다」로 위장하지 않는 쪽이 맞다. 판정을 지우면 다음
`finalize`의 근거 검증이 재분류된 상태로 처음부터 본다.

★원인은 `repair/retypes`에서 고쳤다(같은 날). 이 스크립트는 **이미 쌓인 것**을
  정리한다. 멱등이라 여러 번 돌려도 안전하다.

    python -m batch.repair.retype_recheck --dry-run
    python -m batch.repair.retype_recheck
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session
from pipeline.normalizer.relations import _DEFAULT_SUBTYPE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 재분류됐는데 **재분류 전 판정·도장**이 남아 있는 것.
# `retyped_from`이 있고 grounding 흔적이 하나라도 있으면 대상이다.
_FIND = """
MATCH (a)-[r]->(b)
WHERE r.retyped_from IS NOT NULL
  AND (r.grounding_checked_at IS NOT NULL OR r.grounding_verdict IS NOT NULL
       OR r.grounding_stage1 IS NOT NULL OR r.grounding_suspect IS NOT NULL)
RETURN elementId(r) AS eid, r.retyped_from AS was, type(r) AS now,
       coalesce(a.name,'?') AS a, coalesce(b.name,'?') AS b,
       coalesce(r.subtype,'') AS st,
       coalesce(r.grounding_verdict,'(판정없음)') AS verdict,
       coalesce(r.grounding_suspect,false) AS susp
ORDER BY r.grounding_verdict
"""

# 판정·도장을 전부 걷는다. 다음 `audit/grounding`이 처음 보는 엣지처럼 다룬다.
# ★subtype이 **옛 타입 이름**이면 같이 비운다 — 「공급 관계인데 협력이라 적힘」.
_CLEAR = """
UNWIND $rows AS row
MATCH ()-[r]->() WHERE elementId(r) = row.eid
SET r.grounding_checked_at = NULL,
    r.grounding_verdict = NULL, r.grounding_verdict_why = NULL,
    r.grounding_reason = NULL, r.grounding_stage1 = NULL,
    r.grounding_suspect = NULL,
    r.retype_rechecked = true,
    r.subtype = CASE WHEN r.subtype = row.old_name THEN '' ELSE r.subtype END
RETURN count(*) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="무엇이 바뀔지만 출력")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]

    if not rows:
        print("재분류 후 낡은 판정이 남은 엣지가 없습니다.")
        return 0

    # 조회에 **보이는데 근거가 없다고 판정된** 것 — 가장 나쁜 상태
    leaking = [r for r in rows
               if r["verdict"] == "unfounded" and not r["susp"]]
    stale_sub = [r for r in rows if r["st"] == _DEFAULT_SUBTYPE.get(r["was"])]

    print("=" * 70)
    print(f"  재분류 후 낡은 판정 정리 — {len(rows)}건")
    print("=" * 70)
    print(f"  ⚠ 근거없음 판정인데 조회에 보이는 것   {len(leaking):>4}건")
    print(f"  · subtype이 옛 타입 이름인 것         {len(stale_sub):>4}건")

    by_v: dict[str, int] = {}
    for r in rows:
        by_v[r["verdict"]] = by_v.get(r["verdict"], 0) + 1
    print("\n  현재 판정 분포")
    for v, n in sorted(by_v.items(), key=lambda x: -x[1]):
        print(f"     {v:<14}{n:>5}건")

    if leaking:
        print("\n  조회에 새고 있던 것")
        for r in leaking[:10]:
            print(f"     {r['a'][:16]:<17}-[{r['now']:<13}]-> {r['b'][:16]:<17}"
                  f"({r['was']}에서 바뀜)")

    if args.dry_run:
        print("\n[dry-run] 위 엣지의 판정·도장을 걷습니다 — "
              "다음 finalize의 근거 검증이 다시 봅니다.")
        return 0

    payload = [{"eid": r["eid"],
                "old_name": _DEFAULT_SUBTYPE.get(r["was"]) or chr(1)}
               for r in rows]
    with neo4j_session() as session:
        n = session.run(_CLEAR, rows=payload).single()["n"]

    print(f"\n✅ {n}건의 근거 판정을 걷었습니다 (`retype_rechecked=true` 표시).")
    print("   다음 `batch.ops.finalize`의 근거 검증이 재분류된 상태로 다시 봅니다.")
    print(f"   예상 비용: 엣지당 3~4원 → 약 {n*3.5:,.0f}원")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
