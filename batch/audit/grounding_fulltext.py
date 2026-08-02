"""근거의심 엣지를 **기사 전문**으로 다시 본다 — 지우기 전에.

`verify_evidence_grounding`은 **저장된 근거 문장 한두 개**만 보고 판정한다.
그래서 `supported=false`는 「이 관계가 거짓이다」가 아니라
**「저장된 문장만으로는 확인이 안 된다」**는 뜻이다. 둘은 전혀 다르다.

문장만 봐서 확인이 안 되는 데는 **정상적인 이유**가 여럿 있다:

  ① 근거는 한 문장만 저장한다   기사 전체엔 있는데 그 문장엔 없다
       "CLT 인터페이스 보드는 …핵심 부품이다"  ← 삼성전자는 앞 문단에 있었다
  ② 대명사·약칭               "회사는" "양사는" "이 회사" "동사(同社)"
  ③ 관계가 여러 문장에 걸침     "A는 …했다. 이에 따라 B는 …"
  ④ 유형만 틀림               관계는 실재. 협력이 아니라 거래였을 뿐
  ⑤ 판정 자체가 오류           실측: 「SK하이닉스가 ASMPT과 손을 잡으면서」를
                              협력이 아니라고 판정한 사례가 있었다

①②③은 **엣지가 맞는데 근거 저장이 좁았을 뿐**이다. 여기서 지우면 참인 관계를
잃는다. 그래서 지우기 전에 **기사 전문**을 다시 받아 대조한다.

    python -m batch.audit.grounding_fulltext --dry-run     # 판정만, 그래프 변경 없음
    python -m batch.audit.grounding_fulltext               # 판정 결과를 속성으로 기록

★이 도구는 **아무것도 삭제하지 않는다.** 판정을 `grounding_verdict`에 남길 뿐이다:
    confirmed  전문에서 관계가 확인됐다        → 의심 해제
    mistyped   관계는 있는데 유형이 다르다      → 재분류 대상
    unfounded  전문을 봐도 근거가 없다          → 사람이 볼 것
    unreadable 기사를 못 받았다(삭제·차단)      → 판단 보류
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline.llm import ask_json
from pipeline.extractors.news.crawler import fetch_body
from pipeline.importer.evidence import fetch_texts
from pipeline.ontology import EDGE_DEFINITIONS, VERIFY_FAILURES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_WORKERS = 8
_CRAWL_WORKERS = 6

_FIND = """
MATCH (a)-[r]->(b)
WHERE r.grounding_suspect AND r.source_type = 'news'
      AND ($full OR r.grounding_verdict IS NULL)
RETURN elementId(r) AS eid, type(r) AS edge,
       labels(a)[0] AS a_label, coalesce(a.name,'') AS a_name,
       labels(b)[0] AS b_label, coalesce(b.name,'') AS b_name,
       coalesce(r.subtype,'') AS subtype,
       coalesce([r.evidence_id],[]) + coalesce(r.evidence_ids,[]) AS ev_ids,
       coalesce(r.source_docs, [r.source_doc]) AS docs
"""

_MARK = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.grounding_verdict = $verdict,
    r.grounding_verdict_why = $why,
    r.grounding_suspect = CASE WHEN $verdict = 'confirmed' THEN NULL
                               ELSE r.grounding_suspect END,
    r.retype_suspect = CASE WHEN $verdict = 'mistyped' THEN true
                            ELSE r.retype_suspect END,
    r.retype_hint = CASE WHEN $verdict = 'mistyped' THEN $actual
                         ELSE r.retype_hint END
"""

_SYSTEM = f"""기사 **전문**을 읽고, 주어진 관계가 기사에 실제로 있는지 판정하세요.

앞선 검사에서 「근거 문장 한두 개」만 보고 확인이 안 됐던 관계입니다.
문장 하나에 안 담겼을 뿐 기사 전체엔 있는 경우가 많으니 **전문 기준으로** 다시 보세요.

【판정 — 넷 중 하나】
· confirmed  기사에 이 관계가 있다. 방향·유형 모두 맞다.
             ※ 대명사·약칭도 인정하세요. 「회사는」「양사는」「동사」가 문맥상
                그 기업을 가리키면 언급된 것으로 봅니다.
             ※ 관계가 여러 문장에 나뉘어 있어도 종합해서 성립하면 confirmed입니다.
· mistyped   관계는 기사에 있는데 **엣지 유형이 다르다**.
             (협력으로 돼 있으나 실제로는 납품, 개발로 돼 있으나 실제로는 공급 등)
             어떤 유형이 맞는지 `actual`에 쓰세요. 12종 중 하나:
             OWNS_STAKE_IN IS_EXECUTIVE_OF SUPPLIES_TO PARTNERS_WITH ACQUIRES
             SUES COMPETES_WITH REGULATES DEVELOPS DEPENDS_ON HAS_EVENT IMPACTS
· unfounded  기사 전문을 봐도 이 관계가 없다. 다른 기업 얘기이거나 추측이다.
· unclear    기사가 짧거나 잘려서 판단할 수 없다.

【★엣지 유형의 뜻 — 이 정의로만 판단하세요】
일상적인 낱말 뜻이 아니라 **이 그래프의 정의**를 씁니다. 실제로 이걸 안 알려줬더니
「검찰이 기업을 수사한다」를 두고 "'조사'는 '규제'가 아니다"라며 관계를 부정한
사례가 있었습니다.

{EDGE_DEFINITIONS}

{VERIFY_FAILURES}

【중요】
· **애매하면 confirmed 쪽으로 기울이세요.** 이 판정으로 관계를 지울 수 있고,
  참인 관계를 잃는 손해가 거짓 관계를 남기는 손해보다 큽니다.
  ★단 「애매하면 confirmed」가 **틀린 관계까지 통과시키라는 뜻은 아닙니다.**
    근거가 관계를 말하지 않으면(수익률 비교·나란한 언급) unfounded입니다.
· 방향이 반대이면 unfounded가 아니라 mistyped로 하고 사유에 '방향'이라 쓰세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["confirmed", "mistyped", "unfounded", "unclear"]},
        "actual": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "actual", "reason"],
    "additionalProperties": False,
}


def _judge(item: tuple[dict, str, str]) -> tuple[dict, dict]:
    row, body, stored = item
    user = (f"관계: ({row['a_label']}) 「{row['a_name']}」 "
            f"-[{row['edge']}{'/' + row['subtype'] if row['subtype'] else ''}]-> "
            f"({row['b_label']}) 「{row['b_name']}」\n\n"
            f"[앞선 검사가 본 근거 문장]\n{stored or '(없음)'}\n\n"
            f"[기사 전문]\n{body[:7000]}")
    # 실패 fallback은 unclear — 「판단 보류」라 관계를 지우지도 살리지도 않는다
    return row, ask_json(_SYSTEM, user, schema=_SCHEMA, name="recheck",
                         fallback={"verdict": "unclear", "actual": "", "reason": ""})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full", action="store_true", help="이미 재판정한 것도 다시")
    ap.add_argument("--limit", type=int, default=600)
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND, full=args.full)]
    print(f"근거의심 엣지 {len(rows)}건")
    if not rows:
        return 0
    rows = rows[: args.limit]

    # ── 기사 전문 재수집 (URL 중복 제거) ──────────────────────
    urls = {d for r in rows for d in (r["docs"] or []) if d}
    print(f"고유 기사 {len(urls)}건 재수집 중…")
    bodies: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=_CRAWL_WORKERS) as pool:
        for url, body in zip(urls, pool.map(fetch_body, urls)):
            if body:
                bodies[url] = body
    print(f"  → 본문 확보 {len(bodies)}/{len(urls)}건 "
          f"({len(bodies)/max(len(urls),1)*100:.0f}%)")

    # ── 저장된 근거 문장도 같이 보여준다 ─────────────────────
    texts = fetch_texts([e for r in rows for e in (r["ev_ids"] or []) if e])

    targets, unreadable = [], []
    for r in rows:
        body = next((bodies[d] for d in (r["docs"] or []) if d in bodies), "")
        if not body:
            unreadable.append(r)
            continue
        stored = "\n".join(
            texts.get(e, "").split("\n")[0]
            for e in (r["ev_ids"] or []) if texts.get(e))[:600]
        targets.append((r, body, stored))

    print(f"\n[재판정] 전문 대조 {len(targets)}건 "
          f"(기사 못 받음 {len(unreadable)}건은 보류)")
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(_judge, targets))

    tally = Counter(v["verdict"] for _, v in results)
    print()
    labels = {"confirmed": "✅ 전문에서 확인됨 (의심 해제)",
              "mistyped": "⇄ 관계는 있으나 유형이 다름",
              "unfounded": "✗ 전문을 봐도 근거 없음",
              "unclear": "· 판단 보류"}
    for key, label in labels.items():
        n = tally.get(key, 0)
        pct = n / max(len(results), 1) * 100
        print(f"  {label:34} {n:>4}건 ({pct:4.1f}%)")

    for verdict, mark in (("unfounded", "✗"), ("mistyped", "⇄")):
        picked = [(r, v) for r, v in results if v["verdict"] == verdict][:8]
        if picked:
            print(f"\n  ── {labels[verdict]} 예시 ──")
            for r, v in picked:
                extra = f" → {v.get('actual','')}" if verdict == "mistyped" else ""
                print(f"  {mark} ({r['a_name'][:18]}) -[{r['edge']}]-> "
                      f"({r['b_name'][:20]}){extra}")
                print(f"      {v.get('reason','')[:88]}")

    if not args.dry_run:
        with neo4j_session() as session:
            for r, v in results:
                if v.get("failed"):
                    continue          # 실패는 기록하지 않는다 (다음에 재시도)
                session.run(_MARK, eid=r["eid"], verdict=v["verdict"],
                            why=v.get("reason", "")[:200],
                            actual=v.get("actual", "")[:32])
        print(f"\n✅ {len(results)}건 재판정 기록 "
              f"(confirmed는 grounding_suspect 해제 · **삭제 없음**)")
    else:
        print("\n[dry-run] 그래프를 바꾸지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
