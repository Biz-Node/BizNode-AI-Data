"""한 Event 노드에 **여러 사건이 섞였는지** 전수로 찾아 모델에 묻는다.

왜 필요한가 (2026-08-29)

Event 노드는 **전파 구조**다 — 한 사건이 어디까지 번졌는지가 한 노드에 모여야
「이 화재가 어디까지 영향을 주나」에 답할 수 있다. 그래서 이름만 다른 같은
사건을 합친다(`batch/repair/event_merge.py`).

그런데 그 합치기가 **이름만 보고** 판정하고 있었다. `_FIND`가 `corps`·`dates`를
가져와 놓고도 모델에 넘기는 것은 이름 두 개뿐이다:

    body = "- {a['name']} | {b['name']}"

회사도 날짜도 안 보여 주니 모델이 가를 방법이 없다. 결과가 두 종류로 나온다:

    기업 혼재   「자사주 소각」이 한미반도체·NAVER·삼성전자에 동시에 걸렸다.
                서로 다른 회사의 서로 다른 사건인데 이름이 같아 합쳐졌다.
    반복 융합   2024년 사망사고와 2026년 사망사고가 한 노드가 됐다.
                「최근 리스크」 검색에서 2년 전 사고가 최근 것으로 딸려 나온다.

★사람이 표본 64건을 봐서 40건(62%)이 결함이었다. 하지만 그 64건은 「4개월 이상
  날짜 gap」 후보 121건에서 뽑은 표본이고, **명단이 남아 있지 않다.** 그래서
  표본이 아니라 **전수로 다시 훑는다** — 신호를 5종으로 늘리니 240건이 걸린다.

두 단으로 나눈다

    1단  구조 신호 5종으로 의심 노드를 고른다                        무료
    2단  기사 제목·발행일·주체 기업을 **보여 주고** 모델이 판정한다   유료

  ★1단에서 쓰는 신호. 하나만 걸려도 의심에 넣되, 겹칠수록 확실하다.

      S1 다중주체       subject 기업이 둘 이상        기업 혼재의 직접 신호
      S2 시점폭 4M+     사건 날짜가 4개월 이상 벌어짐  반복 융합의 직접 신호
      S3 timeline·기사1 합친 흔적은 있는데 기사가 1개  앞뒤가 안 맞는다
      S4 출처불일치     노드의 source_doc이 자기 기사 목록에 없다
      S5 발행연도 2+    기사 발행 연도가 둘 이상

  ★2단에서 **기사 제목을 보여 주는 것**이 핵심이다. 이름만으로는 「자사주 소각」
    둘을 가를 수 없지만, 「한미반도체, 자사주 200억 소각」과 「네이버, 자사주
    소각 결정」은 사람이든 모델이든 한눈에 갈린다.

★판정만 한다. 노드를 건드리지 않는다 — `batch/audit/`의 규칙이다.
  실제 분리는 `batch/repair/event_split.py`가 이 표를 읽어 수행한다.

    python -m batch.audit.event_merge --dry-run      1단만 (무료)
    python -m batch.audit.event_merge --limit 30     2단 30건까지
    python -m batch.audit.event_merge                전체
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 판정 품질이 그래프를 좌우한다 — 추출과 같은 등급을 쓴다(mini는 근거 표를 못 읽는다).
_MODEL = "gpt-4o"
_MAX_ARTICLES = 14          # 토큰 상한. 넘으면 시점이 고르게 퍼지도록 솎는다.

_CREATE = """
CREATE TABLE IF NOT EXISTS event_mix_verdicts (
    event_id   TEXT PRIMARY KEY,
    verdict    TEXT NOT NULL,          -- single | mixed
    mix_kind   TEXT NOT NULL,          -- none | company | time | both
    groups     JSONB,                  -- [{label, subject, occurred_at, docs:[]}]
    reason     TEXT,
    signals    TEXT,
    model      TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_LOAD = "SELECT event_id FROM event_mix_verdicts"
_SAVE = """
INSERT INTO event_mix_verdicts (event_id, verdict, mix_kind, groups, reason, signals, model)
VALUES (%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (event_id) DO UPDATE SET
    verdict=EXCLUDED.verdict, mix_kind=EXCLUDED.mix_kind, groups=EXCLUDED.groups,
    reason=EXCLUDED.reason, signals=EXCLUDED.signals, model=EXCLUDED.model,
    decided_at=now()
"""

_FIND = """
MATCH (e:Event)
OPTIONAL MATCH (c:Company)-[r]-(e)
RETURN e.event_id AS id, e.name AS name, e.event_type AS etype,
       e.article_count AS ac, e.source_doc AS sd,
       coalesce(e.source_docs, []) AS sds, e.timeline AS tl,
       collect(DISTINCT {corp:c.norm_name, role:r.role, rel:type(r),
                         at:r.occurred_at, doc:r.source_doc}) AS links
"""

_SYSTEM = """당신은 기업 지식그래프에서 **한 노드에 여러 사건이 섞였는지**를
가려내는 도구입니다.

Event 노드 하나는 **현실의 사건 하나**여야 합니다. 그런데 이름만 보고 합치는
바람에 서로 다른 사건이 한 노드에 들어간 경우가 있습니다. 두 종류입니다.

【company — 기업이 섞였다】
   서로 다른 회사의 **각자 별개인** 사건이 이름이 같아 합쳐진 것.
   예) 「자사주 소각」에 한미반도체 기사와 네이버 기사가 함께 걸려 있다.
       → 두 회사가 각자 자기 자사주를 소각한 것이지, 하나의 사건이 아닙니다.

   ★주의 — **정상적인 다기업 사건과 헷갈리지 마십시오.**
       담합·입찰담합: 여러 회사가 **함께 저지른 하나의 사건**입니다 → single
       공급계약·수주: 파는 쪽과 사는 쪽 **둘 다 당사자**입니다 → single
       화재·사고: 한 회사에서 났고 협력사가 **영향받은** 것입니다 → single
       소송: 원고와 피고 둘 다 당사자입니다 → single
   기업이 여럿이라는 사실만으로는 섞인 게 아닙니다. **각자 따로 일어난
   같은 종류의 일**일 때만 company 입니다.

【time — 시점이 섞였다】
   해마다 되풀이되는 **별개의** 사건이 한 노드가 된 것.
   예) 2024년 사망사고와 2026년 사망사고 / 2023년 임단협과 2025년 임단협
       → 같은 종류지만 서로 다른 사건입니다.

   ★주의 — **길게 이어지는 하나의 사건과 헷갈리지 마십시오.**
       착공(2022) → 공사 지연(2024) → 준공(2025)   → single (한 프로젝트)
       투자 발표 → 장비 반입 → 양산 개시             → single
       제소 → 1심 → 항소심 → 확정                   → single
       파업 예고 → 총파업 → 유보 → 복귀              → single (한 번의 쟁의)
   몇 년에 걸쳐도 **하나의 일이 전개된 것**이면 single 입니다.
   ★반대로 파업·사고·임단협이 **해를 건너뛰어 다시** 일어난 것이면 time 입니다.
     연도가 끊겨 있고(2022년 기사·2026년 기사, 그 사이 없음) 중간 국면을 잇는
     기사가 없다면 되풀이된 별개 사건일 가능성이 높습니다.

【single — 하나의 사건】 ★판단이 어려우면 이쪽입니다
   섞였다고 잘못 말하면 멀쩡한 전파 구조가 쪼개져 「이 사건이 어디까지 번졌나」에
   답할 수 없게 됩니다. 확신이 있을 때만 mixed 로 하십시오.

mixed 로 판정하면 `groups`에 **기사 번호를 나눠** 담으십시오. 모든 기사 번호가
정확히 한 그룹에 들어가야 합니다. single 이면 groups 는 빈 배열입니다.

groups 의 각 항목:
  label       갈라 낸 사건의 이름. **한국어**로, 기업명을 빼고 8~20자.
              그대로 노드 이름이 되므로 "Group 1"·"2024 Contract" 같은
              자리표시자를 쓰면 안 됩니다. 예) "메모리 70% 장기 계약"
  subject     그 사건의 **주체 기업 이름 하나**. 반드시 위 「연결된 기사」에
              나온 기업 중에서 고르십시오. 사건 이름을 넣으면 안 됩니다.
  occurred_at 그 사건이 일어난 날 (YYYY-MM-DD).

reason 은 40자 이내로 짧게."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["single", "mixed"]},
        "mix_kind": {"type": "string", "enum": ["none", "company", "time", "both"]},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "subject": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "articles": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "subject", "occurred_at", "articles"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["verdict", "mix_kind", "groups", "reason"],
    "additionalProperties": False,
}


def _mk(p):
    """YYYY-MM → 월 일련번호."""
    try:
        return int(str(p)[:4]) * 12 + int(str(p)[5:7])
    except (ValueError, IndexError, TypeError):
        return None


def _url(doc: str) -> str:
    """`news:https://...` → `https://...`"""
    return doc.split("news:", 1)[-1]


def _signals(e: dict, arts: dict) -> tuple[list[str], dict]:
    """구조 신호 5종. 하나라도 걸리면 2단 후보."""
    links = [l for l in e["links"] if l.get("corp")]
    subs = sorted({l["corp"] for l in links if l.get("role") == "subject"})

    months = [m for m in (_mk(l["at"]) for l in links) if m]
    months += [m for m in (_mk(t.split("|")[0]) for t in (e["tl"] or [])) if m]
    span = (max(months) - min(months)) if months else 0

    docs = sorted({l["doc"] for l in links if l.get("doc")})
    years = sorted({str(arts[_url(d)][1])[:4] for d in docs
                    if _url(d) in arts and arts[_url(d)][1]})

    hits = []
    if len(subs) >= 2:
        hits.append("S1")
    if span >= 4:
        hits.append("S2")
    if e["tl"] and (e["ac"] or 0) <= 1:
        hits.append("S3")
    if e["sd"] and e["sd"] not in (e["sds"] or []):
        hits.append("S4")
    if len(years) >= 2:
        hits.append("S5")
    return hits, {"subs": subs, "span": span, "docs": docs, "years": years,
                  "links": links}


def _thin(docs: list[str], arts: dict) -> list[str]:
    """기사가 많으면 **시점이 고르게 퍼지도록** 솎는다.

    앞에서 잘라 버리면 한 시점만 남아 「시점이 섞였나」를 물을 수 없게 된다.
    """
    if len(docs) <= _MAX_ARTICLES:
        return docs
    keyed = sorted(docs, key=lambda d: str(arts.get(_url(d), ("", ""))[1]))
    step = len(keyed) / _MAX_ARTICLES
    return [keyed[int(i * step)] for i in range(_MAX_ARTICLES)]


def _pack(e: dict, ctx: dict, arts: dict) -> tuple[str, list[str]]:
    """모델에 보여 줄 근거 표. **기사 제목이 여기서 결정적이다.**"""
    docs = _thin(ctx["docs"], arts)
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for l in ctx["links"]:
        if l.get("doc"):
            by_doc[l["doc"]].append(l)

    lines = [f"사건 이름: {e['name']}",
             f"사건 유형: {e.get('etype') or '-'}",
             f"주체 기업: {', '.join(ctx['subs']) or '-'}",
             f"사건 날짜 폭: {ctx['span']}개월"]
    if e["tl"]:
        lines.append("병합 이력: " + " / ".join(
            t.split("|")[0] + " " + t.split("|")[1]
            for t in e["tl"][:8] if "|" in t))
    lines += ["", "연결된 기사:"]
    for i, d in enumerate(docs, 1):
        title, pub = arts.get(_url(d), ("(제목 없음)", None))
        who = sorted({f"{l['corp']}({l.get('role') or l['rel']})"
                      for l in by_doc.get(d, [])})
        at = sorted({str(l["at"])[:10] for l in by_doc.get(d, []) if l.get("at")})
        lines.append(f"  [{i}] {str(pub)[:10]} 「{title}」")
        lines.append(f"      기업: {', '.join(who) or '-'}  "
                     f"사건일: {', '.join(at) or '-'}")
    return "\n".join(lines), docs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="1단 신호만 세고 끝낸다")
    ap.add_argument("--limit", type=int, help="이번에 물을 노드 수 상한")
    ap.add_argument("--min-signals", type=int, default=1, help="신호 N개 이상만")
    ap.add_argument("--recheck", action="store_true", help="이미 판정한 것도 다시")
    ap.add_argument("--model", default=_MODEL)
    args = ap.parse_args()

    with neo4j_session() as s:
        evs = [dict(r) for r in s.run(_FIND)]
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT url, title, published_at FROM news_articles")
        arts = {u: (t, p) for u, t, p in cur.fetchall()}

    suspects = []
    tally: dict[str, int] = defaultdict(int)
    for e in evs:
        hits, ctx = _signals(e, arts)
        for h in hits:
            tally[h] += 1
        if hits and len(hits) >= args.min_signals:
            suspects.append((e, hits, ctx))
    suspects.sort(key=lambda x: (-len(x[1]), -x[2]["span"]))

    print("=" * 74)
    print(f"  1단 구조 탐지 — Event {len(evs):,}건 중 의심 {len(suspects)}건")
    print("=" * 74)
    names = {"S1": "다중주체", "S2": "시점폭 4M+", "S3": "timeline·기사1",
             "S4": "출처불일치", "S5": "발행연도 2+"}
    for k in sorted(tally):
        print(f"    {k} {names[k]:<16} {tally[k]:>4}건")

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(_LOAD)
            done = {r[0] for r in cur.fetchall()}

        todo = (suspects if args.recheck
                else [s for s in suspects if s[0]["id"] not in done])
        if args.limit:
            todo = todo[:args.limit]
        print(f"\n  이미 판정 {len(done)}건 · 이번에 물을 것 {len(todo)}건 "
              f"(모델 {args.model})")

        if args.dry_run or not todo:
            print("\n[dry-run] 묻지 않았습니다." if args.dry_run
                  else "\n· 물을 것이 없습니다.")
            return 0

        mixed = 0
        for n, (e, hits, ctx) in enumerate(todo, 1):
            body, docs = _pack(e, ctx, arts)
            got = ask_json(_SYSTEM, body, schema=_SCHEMA, name="event_mix",
                           fallback={"verdict": "single", "mix_kind": "none",
                                     "groups": [], "reason": ""},
                           model=args.model)
            if got.get("failed"):
                print(f"  [{n}/{len(todo)}] ✗ {e['name'][:28]} — {got['reason'][:60]}")
                continue

            # 기사 번호 → 실제 URL 로 되돌린다(분리 단계가 그대로 쓴다).
            groups = []
            for g in got.get("groups", []):
                groups.append({**g, "docs": [docs[i - 1] for i in g.get("articles", [])
                                             if 1 <= i <= len(docs)]})
            with conn.cursor() as cur:
                cur.execute(_SAVE, (e["id"], got["verdict"], got["mix_kind"],
                                    json.dumps(groups, ensure_ascii=False),
                                    (got.get("reason") or "")[:200],
                                    "+".join(hits), args.model))
            conn.commit()

            if got["verdict"] == "mixed":
                mixed += 1
                print(f"  [{n}/{len(todo)}] ★{got['mix_kind']:<8} "
                      f"{e['name'][:26]:<28} → {len(groups)}개로 갈림 · "
                      f"{got.get('reason', '')[:30]}")
            elif n % 20 == 0:
                print(f"  [{n}/{len(todo)}] …")

        print(f"\n  판정 완료 — 섞인 것 {mixed}건 / 물어본 것 {len(todo)}건")

        with conn.cursor() as cur:
            cur.execute("SELECT mix_kind, count(*) FROM event_mix_verdicts "
                        "GROUP BY 1 ORDER BY 2 DESC")
            print("\n  누적 판정:")
            for k, c in cur.fetchall():
                print(f"    {k:<10} {c:>4}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
