"""프롬프트 예시가 유출된 Event 이름을 **근거에서 다시 짓는다**.

추출 프롬프트에 「평택 공장 화재」·「HBM4 양산 개시」를 형태 예시로 적었더니
LLM이 사건 이름을 못 정할 때 그대로 복사했다(실측 2026-07-29):

    근거: "한미반도체는 TC본더 가격을 인상했으며… 유지·보수 인력을 철수"
    이름: 「평택 공장 화재」            ← 기사와 완전히 무관

근거 문장은 기사에서 정확히 인용됐으므로 **정보 자체는 살아 있다.**
삭제하면 그 관계를 통째로 잃으니, 근거를 읽어 이름만 다시 짓는다.
사건이라 부를 게 없으면 그때 삭제한다.

실행:
  python -m batch.repair.event_names --dry-run
  python -m batch.repair.event_names
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline.llm import ask_json
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.normalizer.generic_names import PROMPT_EXAMPLE_NAMES
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── 기사 제목이 그대로 사건 이름이 된 경우 ────────────────────
# 「세계시장 휩쓰는 中 로봇청소기…보안·위생 앞세워 추격 나선 삼성·LG전자」
# 「[특징주]디아이, SK하이닉스 공급계약 체결에도 5%대 하락」
# 사건 이름은 **무슨 일이 있었는가**여야 하는데, 제목은 편집자의 서술이라
# 같은 사건이 매체마다 다른 이름을 갖게 돼 ER(사건 병합)이 깨진다.
_HEADLINE_MARKS = ("…", "...", "”", "“", '"')


def is_headline(name: str | None) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return (len(n) > 34
            or any(m in n for m in _HEADLINE_MARKS)
            or n.count(",") >= 2
            or bool(re.match(r"^\s*\[[^\]]{1,14}\]", n)))     # [특징주] [뉴스브리핑]


_FIND = """
MATCH (e:Event)
OPTIONAL MATCH (e)-[r]-(c)
WITH e, collect(DISTINCT r.evidence_id) AS evs,
     collect(DISTINCT coalesce(c.name, ''))[0..4] AS linked,
     count(r) AS deg
RETURN e.event_id AS eid, e.name AS name, evs, linked, deg,
       coalesce(e.eventness_suspect, false) AS suspect,
       coalesce(e.eventness_why, '') AS suspect_why
"""

_CLEAR_SUSPECT = ("MATCH (e:Event {event_id: $eid}) "
                  "SET e.eventness_suspect = NULL, e.eventness_why = NULL")

_SYSTEM = """근거 문장을 읽고 **이 사건의 이름**을 다시 지으세요.

기존 이름이 **사건 이름 구실을 못 합니다.** 둘 중 하나입니다:
  ① 프롬프트 예시가 그대로 복사됐다 (기사와 완전히 무관)
  ② 기사 제목이 그대로 들어갔다
     예) 「[특징주]디아이, SK하이닉스 공급계약 체결에도 5%대 하락」
         → 제목은 편집자의 서술이라 **같은 사건이 매체마다 다른 이름**이 됩니다.
            사건 이름은 「무슨 일이 있었는가」여야 합니다 → 「SK하이닉스 공급계약 체결」
기존 이름은 무시하고 **근거만 보고** 판단하세요.

【이름 규칙】
· 짧은 명사구(30자 이내). 근거에 실제로 나온 고유명사(회사·지역·제품)만 사용.
· 근거에 없는 지역·설비를 지어내지 마세요.
· 기사 제목처럼 쓰지 마세요 — 매체 말머리([특징주]), 줄임표(…), 인용부호,
  주가 반응("5%대 하락"), 수식어("세계시장 휩쓰는")를 넣지 마세요.

【사건이 아니면】
근거가 특정 시점에 벌어진 일이 아니라 **전망·추세·시장 상황·의견**이면
is_event=false로 하세요. (그 경우 name은 빈 문자열)"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_event": {"type": "boolean"},
        "name": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_event", "name", "reason"],
    "additionalProperties": False,
}


def _evidence(evs: list[str]) -> str:
    ids = [e for e in evs if e]
    if not ids:
        return ""
    try:
        got = get_store().get(EVIDENCE_COLLECTION, ids[:3])
        return "\n---\n".join(d for d in got.get("documents", []) if d)[:1400]
    except Exception:
        return ""


_RENAME = ("MATCH (e:Event {event_id:$eid}) "
           "SET e.name=$name, e.title=$name, e.renamed_from=$old")
_DELETE = "MATCH (e:Event {event_id:$eid}) DETACH DELETE e"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]

    leaked = [r for r in rows
              if (r["name"] or "").replace(" ", "").lower() in PROMPT_EXAMPLE_NAMES]
    seen = {id(r) for r in leaked}
    heads = [r for r in rows if id(r) not in seen and is_headline(r["name"])]
    seen |= {id(r) for r in heads}
    # `audit_relation_quality --scope event`가 「행위가 없는 이름」으로 표시한 것.
    # 거기서 지우지 않고 넘긴 이유는 상당수가 **진짜 사건인데 이름만 나쁜 것**이라서다.
    susp = [r for r in rows if id(r) not in seen and r["suspect"]]
    targets = leaked + heads + susp
    print(f"이름을 다시 지을 Event {len(targets)}건 (전체 {len(rows)}건 중)\n"
          f"   프롬프트 예시 유출 {len(leaked)}건 · 기사 제목이 그대로 {len(heads)}건 · "
          f"행위 없는 이름 {len(susp)}건\n")
    for r in heads[:6]:
        print(f"     [제목형] {str(r['name'])[:64]}")
    for r in susp[:6]:
        print(f"     [행위없음] {str(r['name'])[:38]:40}{str(r['suspect_why'])[:34]}")
    if not targets:
        print("정리할 대상이 없습니다.")
        return 0

    def judge(row: dict) -> tuple[dict, dict, str]:
        ev = _evidence(row["evs"])
        user = (f"기존 이름(무시하세요): {row['name']}\n"
                f"연결된 기업: {', '.join(c for c in row['linked'] if c)}\n\n"
                f"근거:\n{ev or '(근거 없음)'}")
        # 실패 fallback: is_event=True + 새 이름 없음 → **삭제도 개명도 안 한다**
        return row, ask_json(_SYSTEM, user, schema=_SCHEMA, name="rename",
                             fallback={"is_event": True, "name": "",
                                       "reason": ""}), ev

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(judge, targets))

    renamed = deleted = kept = 0
    with neo4j_session() as session:
        for row, v, ev in results:
            snippet = ev.split("\n")[0][:56]
            if not v.get("is_event"):
                print(f"  ✗ 삭제 [{row['name']}] 연결 {row['deg']}\n"
                      f"       근거: {snippet}\n"
                      f"       ({v.get('reason','')[:50]})")
                deleted += 1
                if not args.dry_run:
                    session.run(_DELETE, eid=row["eid"])
                continue
            new = (v.get("name") or "").strip()
            if not new or new == row["name"]:
                kept += 1
                if row["suspect"] and not args.dry_run:
                    # 사건이라고 판정됐는데 더 나은 이름이 없다 — 의심만 푼다.
                    # 안 풀면 다음 실행이 같은 건을 또 LLM에 보낸다.
                    session.run(_CLEAR_SUSPECT, eid=row["eid"])
                continue
            print(f"  ✎ [{row['name']}] → 「{new}」  (연결 {row['deg']})\n"
                  f"       근거: {snippet}")
            renamed += 1
            if not args.dry_run:
                session.run(_RENAME, eid=row["eid"], name=new, old=row["name"])
                session.run(_CLEAR_SUSPECT, eid=row["eid"])

    print(f"\n{'[dry-run] ' if args.dry_run else '✅ '}"
          f"개명 {renamed} · 삭제 {deleted} · 유지 {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
