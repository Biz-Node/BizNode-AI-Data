"""리스크 추론 검증 — 실제 질의를 던져 답이 나오는지 본다.

지표(엣지 수·브리지 수)가 아니라 **질의가 되는가**로 판정한다.
확장 전에 "이 데이터로 서비스가 되는가"를 확인하는 것이 목적이다.

각 질의마다:
  · 결과가 나오는가
  · 근거를 제시할 수 있는가
  · 결과가 상식에 부합하는가 (사람이 눈으로 확인)

실행:
  python -m batch.audit.queries
  python -m batch.audit.queries --company 한미반도체
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _evidence_snippet(ev_id: str, width: int = 84) -> str:
    if not ev_id:
        return ""
    try:
        got = get_store().get(EVIDENCE_COLLECTION, [ev_id])
        docs = got.get("documents") or []
        if not docs or not docs[0]:
            return ""
        return docs[0].split("\n")[0][:width]
    except Exception:
        return ""


# ── 질의 정의 ────────────────────────────────────────────────
QUERIES = [
    {
        "id": "Q1",
        "question": "{c}에 어떤 사건이 있었나? (부정적 사건 우선)",
        "why": "리스크 이력 조회 — 가장 기본",
        "cypher": """
            MATCH (c:Company {name:$name})-[:HAS_EVENT]->(e:Event)
            OPTIONAL MATCH (e)-[i:IMPACTS]->(t)
            RETURN e.name AS 사건,
                   collect(DISTINCT coalesce(t.name,'') + '(' + coalesce(i.sign,'?') + ')')[0..4] AS 영향,
                   head(collect(i.occurred_at)) AS 시점,
                   head(collect(coalesce(i.evidence_id, e.source_doc))) AS ev
            ORDER BY 시점 DESC LIMIT 8
        """,
    },
    {
        "id": "Q2",
        "question": "{c}에 납품하는 공급사는?",
        "why": "공급망 1홉 — 방향이 맞아야 성립",
        "cypher": """
            MATCH (s:Company)-[r:SUPPLIES_TO]->(c:Company {name:$name})
            RETURN s.name AS 공급사, r.subtype AS 유형, r.source_type AS 출처,
                   coalesce(r.evidence_id, head(coalesce(r.evidence_ids,[]))) AS ev
            LIMIT 10
        """,
    },
    {
        "id": "Q3",
        "question": "{c} 공급사가 흔들리면 어느 기업이 함께 영향받나? (2홉)",
        "why": "★연쇄 파급 — BizNode의 존재 이유",
        "cypher": """
            MATCH (c:Company {name:$name})<-[:SUPPLIES_TO]-(s:Company)-[:SUPPLIES_TO]->(o:Company)
            WHERE o.name <> $name
            RETURN s.name AS 공유공급사, collect(DISTINCT o.name)[0..5] AS 함께영향받는기업,
                   '' AS ev
            LIMIT 8
        """,
    },
    {
        "id": "Q4",
        "question": "{c}의 법적·규제 리스크는?",
        "why": "SUES·REGULATES — 뉴스에서만 나오는 정보",
        "cypher": """
            MATCH (a)-[r:SUES|REGULATES]-(c:Company {name:$name})
            RETURN type(r) AS 종류, r.subtype AS 유형,
                   CASE WHEN startNode(r).name = $name THEN '→ ' + coalesce(endNode(r).name,'?')
                        ELSE '← ' + coalesce(startNode(r).name,'?') END AS 상대,
                   coalesce(r.evidence_id, head(coalesce(r.evidence_ids,[]))) AS ev
            LIMIT 10
        """,
    },
    {
        "id": "Q5",
        "question": "{c}와 같은 사건에 얽힌 다른 기업은? (사건 브리지)",
        "why": "하나의 사건이 여러 기업으로 번지는 구조",
        "cypher": """
            MATCH (c:Company {name:$name})-[]-(e:Event)-[i]-(o:Company)
            WHERE o.name <> $name
            RETURN e.name AS 사건, collect(DISTINCT o.name + '(' + coalesce(i.sign,'?') + ')')[0..5] AS 관련기업,
                   '' AS ev
            LIMIT 8
        """,
    },
    {
        "id": "Q6",
        "question": "{c}의 경쟁사는?",
        "why": "COMPETES_WITH — 공시에 없는 정보",
        "cypher": """
            MATCH (c:Company {name:$name})-[r:COMPETES_WITH]-(o)
            RETURN DISTINCT coalesce(o.name,'?') AS 경쟁사, r.subtype AS 유형,
                   coalesce(r.evidence_id, head(coalesce(r.evidence_ids,[]))) AS ev
            LIMIT 8
        """,
    },
]


def run(session, company: str) -> tuple[int, int]:
    ok = 0
    print(f"\n{'█'*74}\n█ {company}\n{'█'*74}")
    for q in QUERIES:
        question = q["question"].format(c=company)
        print(f"\n[{q['id']}] {question}")
        print(f"      ({q['why']})")
        rows = [dict(r) for r in session.run(q["cypher"], name=company)]
        if not rows:
            print("      ✗ 결과 없음")
            continue
        ok += 1
        for row in rows:
            fields = {k: v for k, v in row.items() if k != "ev"}
            body = " · ".join(
                f"{v}" if not isinstance(v, list) else f"{v}"
                for v in fields.values())
            print(f"      • {body[:110]}")
        snippet = _evidence_snippet(rows[0].get("ev") or "")
        if snippet:
            print(f"      └ 근거: {snippet}")
    return ok, len(QUERIES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", action="append",
                    help="대상 기업 (여러 번 지정 가능)")
    args = ap.parse_args()
    companies = args.company or ["SK하이닉스", "한미반도체", "레인보우로보틱스"]

    total_ok = total = 0
    with neo4j_session() as session:
        for c in companies:
            ok, n = run(session, c)
            total_ok += ok
            total += n

    print(f"\n{'='*74}")
    print(f"질의 성공 {total_ok}/{total} "
          f"({total_ok/max(total,1)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
