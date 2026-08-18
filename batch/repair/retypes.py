"""유형오류로 표시된 엣지를 실제로 고친다 — **매트릭스로 걸러서**.

`recheck_suspects`가 남긴 `retype_hint`를 그대로 적용하면 안 된다. 실측(2026-07-29)
28건 중 20건이 **매트릭스 위반**이었다:

    한미반도체 -DEVELOPS-> HBM4 본딩 장비   제안: SUPPLIES_TO
      → `SUPPLIES_TO`는 Company→Company다. Product를 target으로 못 받는다.
      → 그리고 한미반도체는 **실제로 TC 본더를 만든다**. DEVELOPS가 맞다.
      → LLM이 기사의 「수주」라는 낱말에 끌려 공급이라 답한 것이다.

즉 제안이 유효한지 **먼저 확인**해야 한다. 세 갈래로 가른다:

  ① 제안 == 현재 유형     → 유형은 맞고 **방향**이 틀렸다는 뜻
                            (프롬프트가 "방향 오류는 mistyped + 사유 '방향'"이라 지시)
                            → 반전. 단 반전 결과도 매트릭스를 통과해야 한다.
  ② 제안 != 현재, 유효    → 재분류.
  ③ 제안 != 현재, 무효    → **아무것도 하지 않는다.** 원래 유형이 맞을 가능성이 높다.
                            판정을 무효로 기록하고 의심도 해제한다.

    python -m batch.repair.retypes --dry-run
    python -m batch.repair.retypes
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from app.core.database import neo4j_session
from pipeline.importer.evidence import fetch_texts
from pipeline.validators.matrix import validate_edge

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_FIND = """
MATCH (a)-[r]->(b)
WHERE r.retype_hint IS NOT NULL AND r.retype_hint <> ''
RETURN elementId(r) AS eid, type(r) AS edge, r.retype_hint AS hint,
       labels(a)[0] AS a_label, coalesce(a.name,'') AS a_name,
       labels(b)[0] AS b_label, coalesce(b.name,'') AS b_name,
       coalesce(r.grounding_verdict_why, r.grounding_reason, '') AS why,
       coalesce([r.evidence_id], []) + coalesce(r.evidence_ids, []) AS ev_ids
"""

# 유형 변경 — 새 타입으로 엣지를 다시 만들고 속성을 옮긴다.
# Cypher는 타입을 동적으로 못 쓰므로 APOC을 쓴다.
#
# ★`direction_checked_at`을 지우는 것이 핵심이다.
#   PARTNERS_WITH·COMPETES_WITH는 **대칭**이라 저장된 방향에 뜻이 없다.
#   이것을 SUPPLIES_TO 같은 방향 있는 유형으로 바꾸면 **무의미한 방향을 물려받는다**:
#       삼성전자 -PARTNERS_WITH-> 세메스  →  삼성전자 -SUPPLIES_TO-> 세메스
#       그런데 실제로는 세메스가 삼성전자에 장비를 공급한다. 거꾸로다.
#   그래서 표시를 걷어 방향 검사가 이들을 **다시 보게** 한다.
_RETYPE = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
CALL apoc.refactor.setType(r, $new) YIELD output
SET output.retyped_from = $old, output.retype_suspect = NULL,
    output.retype_hint = NULL, output.grounding_suspect = NULL,
    output.direction_checked_at = NULL, output.parallel_checked_at = NULL
RETURN 1 AS ok
"""

_INVERT = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
CALL apoc.refactor.invert(r) YIELD output
SET output.direction_corrected = true, output.retype_suspect = NULL,
    output.retype_hint = NULL, output.grounding_suspect = NULL
RETURN 1 AS ok
"""

# 제안이 무효 — 재분류 표시는 걷는다.
#
# ★`grounding_suspect`를 **자동으로 걷지 않는다.** 매트릭스 기각은 「제안이
#   틀렸다」는 뜻이지 「원래 유형이 맞다」는 뜻이 아니다. 실측(2026-08-01)
#   기각된 DEVELOPS 104건을 근거별로 갈라 보니 둘이 섞여 있었다:
#
#     공동개발형 69건  "한미반도체 역시 SK하이닉스와 협력하며 하이브리드본더를
#                     **개발 중이다**"          → DEVELOPS가 맞다. 의심 해제.
#     공급계약형 17건  "SK하이닉스가 HBM 핵심 제조장비 **발주**에 나섰다"
#                     → SK하이닉스는 **사는 쪽**이다. DEVELOPS가 틀렸다.
#                        여기서 의심을 걷으면 진짜 오류가 묻힌다.
#
#   근거에 **만든다는 말이 있는지**로 가른다. 없으면 표시를 남겨 전문 재검증이
#   다시 보게 한다.
_REJECT = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.retype_rejected = $reason, r.retype_suspect = NULL, r.retype_hint = NULL,
    r.grounding_suspect = CASE WHEN $clear_suspect THEN NULL
                               ELSE r.grounding_suspect END
RETURN 1 AS ok
"""

# 「이 회사가 만든다」를 근거가 말하는가. 있으면 원래 유형(DEVELOPS)을 믿는다.
_MAKES_RE = re.compile(
    r"개발|만들|제조|생산|양산|출시|선보|내놓|공급하[는기]|납품하[는기]"
    r"|위탁\s*생산|파운드리|공정을?\s*활용")

# 반대로 **사는 쪽**임을 말하는 표현. 이게 있으면 의심을 유지한다.
_BUYS_RE = re.compile(r"발주|구매|도입|사들|들여|공급받|납품받|수주했|수주하")

# 자동으로 못 고치는 것 — 표시를 남겨 사람이 보게 한다. 손대지 않는다.
_HOLD = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.needs_human_review = true, r.retype_hint = NULL
RETURN 1 AS ok
"""


def _load_evidence(rows: list[dict]) -> dict[str, str]:
    """evidence_id → 본문."""
    return fetch_texts([e for r in rows for e in (r.get("ev_ids") or []) if e])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]
        print(f"유형오류 표시 {len(rows)}건\n")
        docs = _load_evidence(rows)
        tally: Counter = Counter()

        for r in rows:
            cur, hint = r["edge"], (r["hint"] or "").strip().upper()
            pair = f"({r['a_name'][:16]}) -[{cur}]-> ({r['b_name'][:20]})"

            # ── ① 같은 유형 = 방향 오류 ────────────────────────
            if hint == cur:
                # ★사유에 '방향'이 있는지 확인한다. 「같은 유형 = 방향 오류」로
                #   단정하면 안 된다 — LLM이 다른 이유로도 mistyped를 고른다.
                #   실측 3건 중 2건이 방향과 무관했다:
                #     「두산 -ACQUIRES-> DLS」 근거는 **매각**. 반전하면 「DLS가
                #       두산을 인수」가 되어 더 틀린다. 지울 일이지 뒤집을 일이 아니다.
                #     「플랙트 -SUPPLIES_TO-> 삼성전자」 사유는 "관계 자체가
                #       기사에 없다" — 방향 문제가 아니다.
                if "방향" not in (r["why"] or ""):
                    print(f"  ? 보류 {pair}  (같은 유형인데 사유가 방향이 아님: "
                          f"{(r['why'] or '')[:44]})")
                    tally["보류(사람검토)"] += 1
                    if not args.dry_run:
                        session.run(_HOLD, eid=r["eid"])
                    continue
                ok, why = validate_edge(r["b_label"], cur, r["a_label"])
                if not ok:
                    print(f"  · 반전불가 {pair}  ({r['b_label']}→{r['a_label']} "
                          f"매트릭스 위반)")
                    tally["반전불가"] += 1
                    if not args.dry_run:
                        # 반전도 못 하면 방향을 고칠 길이 없다 — 의심을 남긴다.
                        session.run(_REJECT, eid=r["eid"], clear_suspect=False,
                                    reason=f"반전 시 매트릭스 위반: {why}")
                    continue
                print(f"  ↻ 방향반전 {pair}")
                tally["방향반전"] += 1
                if not args.dry_run:
                    session.run(_INVERT, eid=r["eid"])
                continue

            # ── ②③ 다른 유형 — 매트릭스로 유효성 확인 ──────────
            ok, why = validate_edge(r["a_label"], hint, r["b_label"])
            if not ok:
                # ★여기가 핵심 방어선이다. 무효한 제안은 **버린다.**
                #   다만 「제안이 틀렸다」가 「원래 유형이 맞다」는 뜻은 아니라,
                #   근거가 **만든다고 말하는지**를 따로 본다(위 _MAKES_RE 주석 참조).
                ev = " ".join(docs.get(e, "") for e in (r["ev_ids"] or []))
                makes = bool(_MAKES_RE.search(ev)) and not _BUYS_RE.search(ev)
                mark = "✗ 제안기각" if makes else "⚠ 제안기각·의심유지"
                print(f"  {mark} {pair} → {hint}  "
                      f"({r['a_label']}→{r['b_label']} 매트릭스 위반)")
                if not makes:
                    print(f"       근거에 「만든다」가 없음 → 원래 유형도 못 믿는다: "
                          f"{' '.join(ev.split())[:56]}")
                tally["제안기각" if makes else "제안기각·의심유지"] += 1
                if not args.dry_run:
                    session.run(_REJECT, eid=r["eid"], clear_suspect=makes,
                                reason=f"{hint} 제안이 매트릭스 위반: {why}")
                continue

            print(f"  ⇄ 재분류 {pair} → {hint}")
            tally["재분류"] += 1
            if not args.dry_run:
                session.run(_RETYPE, eid=r["eid"], new=hint, old=cur)

    print(f"\n{'[dry-run] ' if args.dry_run else '✅ '}"
          + " · ".join(f"{k} {v}건" for k, v in tally.most_common()))
    if tally.get("제안기각"):
        print(f"  ※ 기각분은 원래 유형을 유지합니다 — "
              f"`retype_rejected` 속성에 사유가 남습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
