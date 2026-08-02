"""오분류 엣지 재배정 — **근거를 읽어 방향까지 판단**한다.

`build_subtype_taxonomy`가 `suspect_edge_type`으로 표시한 엣지를 실제로 옮긴다.
자동으로 못 옮기는 이유는 **방향** 때문이다:

    PARTNERS_WITH 는 대칭 엣지라 저장된 방향이 임의다(키 사전순).
    OWNS_STAKE_IN 은 방향이 의미다 — "A가 B의 지분을 보유".
    그냥 타입만 바꾸면 **누가 누구의 주주인지가 뒤집힐 수 있다.**

그래서 엣지의 **근거 문장(evidence)** 을 LLM에 주고 방향을 판단하게 한다.
근거가 없거나 판단이 애매하면 옮기지 않고 남긴다(틀린 방향보다 낫다).

실행:
  python -m batch.repair.misclassified_edges --dry-run
  python -m batch.repair.misclassified_edges
"""

from __future__ import annotations

import argparse
import sys


from app.core.database import neo4j_session
from pipeline.llm import ask_json
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.validators.matrix import validate_edge
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# 대칭 엣지 — 저장 방향이 임의라 옮길 때 방향을 새로 정해야 한다
_SYMMETRIC = {"PARTNERS_WITH", "COMPETES_WITH"}

_FIND = """
MATCH (a)-[r]->(b)
WHERE r.suspect_edge_type IS NOT NULL AND r.suspect_edge_type <> 'DROP'
      AND r.suspect_edge_type <> type(r)
RETURN elementId(r) AS eid, type(r) AS cur, r.suspect_edge_type AS target,
       r.subtype AS subtype, coalesce(a.name, '?') AS a_name,
       coalesce(b.name, '?') AS b_name,
       labels(a)[0] AS a_label, labels(b)[0] AS b_label,
       coalesce(r.evidence_id, '') AS ev,
       coalesce(r.evidence_ids, []) AS evs
"""

_SYSTEM = """관계의 **방향**을 판단하세요.

지식그래프의 엣지를 다른 유형으로 재분류하려는데, 원래 엣지가 대칭이라
방향 정보가 없습니다. 근거 문장을 읽고 누가 주체인지 정하세요.

【엣지별 방향 규칙】
· OWNS_STAKE_IN : 주주 → 피투자사   ("A가 B 지분을 보유")
· SUPPLIES_TO   : 공급자 → 수요자   ("A가 B에 납품")
· ACQUIRES      : 인수자 → 피인수사
· SUES          : 원고 → 피고
· REGULATES     : 규제기관 → 대상기업
· HAS_EVENT     : 기업 → 사건

【판단】
· 근거로 방향을 확정할 수 있으면 source/target을 정하세요.
· **확신이 없으면 confident=false** 로 두세요. 틀린 방향은 없는 것보다 나쁩니다.
· 근거가 재분류 자체를 지지하지 않으면 confident=false."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "confident": {"type": "boolean"},
        "source": {"type": "string"},
        "target": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["confident", "source", "target", "reason"],
    "additionalProperties": False,
}


def _evidence_text(row: dict) -> str:
    ids = [e for e in ([row["ev"]] + list(row["evs"])) if e]
    if not ids:
        return ""
    try:
        got = get_store().get(EVIDENCE_COLLECTION, ids[:3])
        return "\n---\n".join(d for d in got.get("documents", []) if d)[:1500]
    except Exception:
        return ""


def judge(row: dict, evidence: str) -> dict:
    user = (f"현재 엣지: {row['a_name']} -[{row['cur']}/{row['subtype']}]-> {row['b_name']}\n"
            f"재분류 대상: {row['target']}\n\n"
            f"근거:\n{evidence or '(근거 없음)'}")
    # confident=False면 아무것도 바꾸지 않는다 (실패 시 안전한 쪽)
    return ask_json(_SYSTEM, user, schema=_SCHEMA, name="direction",
                    fallback={"confident": False, "reason": ""})


_RETYPE = """
MATCH (a)-[r]->(b) WHERE elementId(r) = $eid
CALL apoc.refactor.setType(r, $new) YIELD output
SET output.suspect_edge_type = NULL, output.retyped_from = $old
RETURN elementId(output) AS eid
"""
_INVERT = "MATCH ()-[r]->() WHERE elementId(r)=$eid CALL apoc.refactor.invert(r) " \
          "YIELD output RETURN elementId(output) AS eid"
_CLEAR = "MATCH ()-[r]->() WHERE elementId(r)=$eid SET r.suspect_edge_type=NULL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    moved = kept = 0
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]
        print(f"재배정 후보 {len(rows)}건\n")

        for row in rows:
            # ★매트릭스 검증 — 옮긴 뒤에도 허용 조합이어야 한다.
            #   이걸 안 하면 「HD현대 -PARTNERS_WITH-> FCC 촉매 분해 공정」(Product)
            #   같은 위반이 생긴다. 방향을 뒤집는 경우도 함께 본다.
            ok_fwd, _ = validate_edge(row["a_label"], row["target"], row["b_label"])
            ok_rev, _ = validate_edge(row["b_label"], row["target"], row["a_label"])
            if not (ok_fwd or ok_rev):
                print(f"  ✗ 불가 {row['cur']}→{row['target']:14} "
                      f"{row['a_label']}→{row['b_label']} 조합이 매트릭스 위반")
                kept += 1
                continue

            evidence = _evidence_text(row)
            need_direction = row["cur"] in _SYMMETRIC and row["target"] not in _SYMMETRIC
            verdict = judge(row, evidence) if need_direction else {
                "confident": True, "source": row["a_name"], "target": row["b_name"],
                "reason": "방향 엣지 → 방향 엣지 (기존 방향 유지)",
            }

            arrow = f"{row['a_name']} → {row['b_name']}"
            if not verdict.get("confident"):
                print(f"  · 보류 {row['cur']}→{row['target']:14} {arrow:40} "
                      f"({verdict.get('reason','')[:40]})")
                kept += 1
                continue

            flip = need_direction and verdict.get("source") == row["b_name"]
            print(f"  ✓ 이동 {row['cur']}→{row['target']:14} "
                  f"{verdict.get('source')} → {verdict.get('target')}"
                  f"{'  [방향 반전]' if flip else ''}")
            moved += 1
            if args.dry_run:
                continue

            session.run(_RETYPE, eid=row["eid"], new=row["target"], old=row["cur"])
            if flip:
                session.run(_INVERT, eid=row["eid"])

        if not args.dry_run:
            for row in rows:
                session.run(_CLEAR, eid=row["eid"])

    verb = "예정" if args.dry_run else "완료"
    print(f"\n{'[dry-run] ' if args.dry_run else '✅ '}"
          f"이동 {moved}건 · 보류 {kept}건 {verb}")
    if kept:
        print("   보류분은 근거로 방향을 확정할 수 없는 것들입니다 "
              "(틀린 방향보다 그대로 두는 편이 낫습니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
