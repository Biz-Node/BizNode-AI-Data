"""같은 사건이 이름만 달리해 갈린 것을 **모델이 판정해** 합치고, 국면을 남긴다.

★왜 필요한가 (2026-08-14)

Event 노드가 있는 이유는 **전파 구조**다.

    「청주 공장 화재」 ─IMPACTS→ SK하이닉스 · 한미반도체 · 원익IPS

한 사건이 어디까지 번졌는지가 한 노드에 모여야 「이 화재가 어디까지 영향을
주나」에 답할 수 있다. 그런데 LLM은 기사마다 이름을 다르게 붙인다:

    「청주 SK하이닉스 화재」 「청주 공장 화재」 「청주4캠퍼스 화재」
    → 노드 3개 · 영향 기업이 셋으로 나뉨 → 답이 틀린다

★기존 `importer/event_er.py`는 **유형어 목록**으로 이 일을 한다. 그런데
  실측(2026-08-14): Event 1,352개 중 **631개(46%)가 유형어 목록에 없어
  후보에도 못 오른다.**

      「메모리 반도체 생산 확대」 「D램 사후정산제 도입」 「테일러 팹」
      「첨단 패키징 공장 건설」 「HBM4 생산라인 전환 일부 연기」

  사건 어휘는 열려 있어서 목록으로는 못 따라간다. 파일 주석에 두 번 실패한
  기록이 남아 있다 — 목록을 늘리면 연쇄 병합, 줄이면 46% 누락.

★그래서 **구조로 후보를 만들고 모델이 판정한다.**

    1단  R1 같은 기업에 붙음 + 이름 **어근** 1개 이상 공유          무료
    2단  모델이 「같은 사건인가」 판정                            0.25원/쌍
    3단  합치되 사라지는 이름을 `timeline`에 남김

  ★이름 겹침에서 **회사명 토큰을 뺀다**(2026-08-14). R1이 이미 같은 기업임을
    보장하므로 중복이고, 안 빼면 「삼성전자」 하나만 겹쳐도 후보가 되어
    서로 무관한 사건이 전부 올라온다(실측: 208 → 191쌍, 잡음이 크게 줄었다).

  ★★2026-08-15에 후보 규칙을 갈았다. 기존 `timeline` 59건을 정답지(99쌍)로
    놓고 겨뤄 보니 **옛 규칙이 62%를 놓치고 있었다**(`_roots` 주석 참고).
    「같은 연월」 조건도 뺐다 — 사건은 몇 달~몇 년에 걸쳐 전개된다.

★★`timeline` — 합치되 **국면을 잃지 않는다**

  「파업 예고」→「총파업」→「파업 유보」→「현업 복귀」는 한 사건의 국면이다.
  전에는 병합이 `properties:'discard'`라 사라지는 이름이 버려졌다.

      timeline: [{at:"2026-04", label:"파업 예고", event_id:"..."},
                 {at:"2026-07", label:"총파업 돌입", ...}]

  전파 구조는 한 노드에 모으고, 시점·국면은 배열에 남긴다. 엣지 12종을
  건드리지 않고도 「언제 시작해 언제 끝났나」에 답할 수 있다.

★확신 없으면 합치지 않는다. 서로 다른 사건을 합치면 영향 기업이 뒤섞이고
  `timeline`으로도 되돌릴 수 없다.

    python -m batch.repair.event_merge --dry-run
    python -m batch.repair.event_merge
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.event_er import _name_tokens
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_COST = 0.25

_CREATE = """
CREATE TABLE IF NOT EXISTS event_merge_verdicts (
    id_a       TEXT NOT NULL,
    id_b       TEXT NOT NULL,
    verdict    TEXT NOT NULL,      -- same | phase | different
    reason     TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id_a, id_b)
)
"""
_LOAD = "SELECT id_a, id_b, verdict FROM event_merge_verdicts"
_SAVE = """
INSERT INTO event_merge_verdicts (id_a, id_b, verdict, reason) VALUES (%s,%s,%s,%s)
ON CONFLICT (id_a, id_b) DO UPDATE SET verdict = EXCLUDED.verdict,
                                       reason = EXCLUDED.reason
"""

_SYSTEM = """당신은 기업 지식그래프에서 **같은 사건이 두 이름으로 갈린 것**을
가려내는 도구입니다.

두 사건 이름은 「같은 기업에 붙어 있고 낱말이 하나라도 겹친다」는 이유로 후보에
올랐을 뿐, 실제로는 다른 사건인 경우가 많습니다.

★**시점이 멀어도 같은 사건일 수 있습니다.** 후보에 시간 제한이 없습니다.
   "HBM4 생산 투자"(2025-09) / "HBM4 양산 일정 연기"(2026-06)
   → 9개월 떨어져 있어도 한 사업의 국면입니다 → phase

【same — 같은 사건을 다르게 부른 것】
   "삼성전자 본사 압수수색" / "삼성전자 압수수색"
   "평택캠퍼스 방문" / "평택 반도체 공장 방문"
   "22조8000억원 규모 파운드리 계약" / "22조7648억 원 규모 반도체 위탁생산 계약"
   "반도체 공정 등 제어감시시스템 입찰 담합 적발" / "반도체 공정 제어감시시스템 입찰 담합"

【phase — 한 사건의 다른 국면】 ★같은 사건으로 봅니다
   "파업 예고" / "총파업 돌입" / "파업 유보" / "현업 복귀"
   "세종 신사옥 착공" / "세종 신사옥 준공 지연" / "세종 신사옥 준공"
   → 하나의 일이 시간에 따라 전개된 것입니다. 시작·중간·끝을 나눠 부른 것.

【different — 다른 사건】 ★이쪽이 가장 많습니다
   "삼성전자 지분 매각" / "삼성전자 반도체 적자"
   "HBM4 생산 투자" / "NAND 생산 확대"
   "삼성 파운드리 포럼 2024" / "파운드리 웨이퍼 결함"
   ★같은 회사에서 낱말이 겹친다는 것만으로는 같은 사건이 아닙니다.
   ★유형이 같아도 대상이 다르면 다른 사건입니다
       "즉시연금 소송" / "특허 소송 배상 평결"   → different

【판단이 어려울 때】
   확신이 없으면 **different**로 하세요. 다른 사건을 합치면 영향받은 기업이
   한 노드에 뒤섞여 되돌릴 수 없습니다. 못 합친 건 나중에 다시 볼 수 있습니다.

reason은 5~20자로 짧게."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "verdict": {"type": "string",
                                "enum": ["same", "phase", "different"]},
                    "reason": {"type": "string"},
                },
                "required": ["a", "b", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_FIND = """
MATCH (e:Event)
OPTIONAL MATCH (e)-[r]-(c:Company)
WITH e, collect(DISTINCT c.norm_name) AS corps,
     collect(DISTINCT r.occurred_at) + collect(DISTINCT r.observed_at) AS dates
RETURN e.name AS name, e.event_id AS id, corps, dates,
       size([(e)-[]-() | 1]) AS deg
"""

# 합치면서 국면을 남긴다. `timeline`은 문자열 배열로 둔다 —
# Neo4j 속성은 중첩 map을 못 담아서 "연월|이름|event_id" 형태로 적는다.
_TIMELINE = """
MATCH (keep:Event {event_id:$keep})
SET keep.timeline = coalesce(keep.timeline, []) + $entries
"""
_MERGE = """
MATCH (a:Event {event_id:$keep}), (b:Event {event_id:$drop})
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.event_id AS id
"""


def _period(dates) -> str:
    v = sorted(str(d)[:7] for d in (dates or []) if d and len(str(d)) >= 7)
    return v[0] if v else ""


def _roots(name: str) -> set[str]:
    """비교용 **어근**. 조사·접미가 붙어도 같은 낱말로 보이게 한다.

    ★왜 토큰 그대로 쓰면 안 되나(2026-08-15 실측). 기존 `timeline` 59건을
      정답지(99쌍)로 놓고 규칙을 겨뤄 봤다:

          토큰 겹침 ≥30% (구 규칙)    38/99   38%   ← 62%를 놓치고 있었다
          토큰 1개 공유               85/99   86%
          ★어근 1개 공유             97/99   98%

      놓친 쌍이 왜 안 걸렸는지가 원인을 그대로 보여 준다:
          「442억원 규모 HBM4용 TC 본더 수주」 ↔ 「HBM4」
            → 「HBM4용」과 「HBM4」가 다른 토큰이라 겹침이 0이었다
    """
    out: set[str] = set()
    for t in _name_tokens(name):
        out.add(t)
        m = re.match(r"^([0-9A-Za-z]+)", t)          # HBM4용 → HBM4
        if m and len(m.group(1)) > 1:
            out.add(m.group(1))
        if len(t) > 3 and re.match(r"^[가-힣]+$", t):  # 한글은 앞 3글자
            out.add(t[:3])
    return out


def _candidates(evs: list[dict]) -> list[tuple[dict, dict]]:
    """R1(같은 기업) + 어근 1개 이상 공유. 전부 구조 조건이라 무료.

    ★**시간 제한을 두지 않는다**(2026-08-15). 전에는 「같은 연월」을 요구했는데,
      사건은 몇 달~몇 년에 걸쳐 전개된다:
          「HBM4 생산 투자」(2025-09) → 「HBM4 양산 일정 연기」(2026-06)
      어근 규칙만으로 후보가 36,264쌍 → 2,433쌍(93% 감축)이라 시간으로 더 조일
      이유가 없다. 판정은 어차피 모델이 한다.

    ★회사명 낱말은 뺀다. R1이 이미 같은 기업임을 보장하므로 중복이고, 안 빼면
      「삼성전자」 하나만 겹쳐도 후보가 되어 무관한 사건이 전부 올라온다.
    """
    corp_tokens: set[str] = set()
    for e in evs:
        for c in e["corps"]:
            if c:
                corp_tokens |= _roots(c)

    by_corp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        e["period"] = _period(e["dates"])
        e["toks"] = _roots(e["name"]) - corp_tokens
        for c in e["corps"]:
            if c:
                by_corp[c].append(e)

    seen: set[tuple[str, str]] = set()
    out: list[tuple[dict, dict]] = []
    for group in by_corp.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["id"] == b["id"]:
                    continue
                key = tuple(sorted((a["id"], b["id"])))
                if key in seen or not (a["toks"] & b["toks"]):
                    continue
                seen.add(key)
                out.append((a, b) if a["deg"] >= b["deg"] else (b, a))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="판정할 쌍 수 상한(비용 통제)")
    args = ap.parse_args()

    with neo4j_session() as s:
        evs = [dict(r) for r in s.run(_FIND)]
    pairs = _candidates(evs)

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(_LOAD)
            cached = {(a, b): v for a, b, v in cur.fetchall()}

        todo = [p for p in pairs if (p[0]["id"], p[1]["id"]) not in cached]
        if args.limit:
            todo = todo[:args.limit]

        print("=" * 72)
        print(f"  이름만 다른 같은 사건 찾기 — Event {len(evs):,}개에서 후보 {len(pairs)}쌍")
        print(f"  이미 판정 {len(cached)}쌍 · 이번에 물을 것 {len(todo)}쌍 "
              f"(약 {len(todo) * _COST:.0f}원)")
        print("=" * 72)

        if todo and not args.dry_run:
            for i in range(0, len(todo), 20):
                chunk = todo[i:i + 20]
                body = "\n".join(f"- {a['name']} | {b['name']}" for a, b in chunk)
                got = ask_json(_SYSTEM, body, schema=_SCHEMA,
                               name="event_merge", fallback={"items": []})
                by_name = {(a["name"], b["name"]): (a, b) for a, b in chunk}
                with conn.cursor() as cur:
                    for it in got.get("items", []):
                        pair = by_name.get((it["a"], it["b"]))
                        if not pair:
                            continue
                        ia, ib = pair[0]["id"], pair[1]["id"]
                        cached[(ia, ib)] = it["verdict"]
                        cur.execute(_SAVE, (ia, ib, it["verdict"],
                                            (it.get("reason") or "")[:60]))
                print(f"     … {min(i + 20, len(todo))}/{len(todo)}")

        merge = [(a, b, cached.get((a["id"], b["id"])))
                 for a, b in pairs
                 if cached.get((a["id"], b["id"])) in ("same", "phase")]
        diff = len(pairs) - len(merge)
        print(f"\n  판정: 합칠 것 {len(merge)}쌍 "
              f"(같은 사건 {sum(1 for *_, v in merge if v == 'same')} · "
              f"국면 {sum(1 for *_, v in merge if v == 'phase')}) "
              f"· 다른 사건 {diff}쌍")
        for a, b, v in merge[:15]:
            tag = "국면" if v == "phase" else "동일"
            print(f"     [{tag}] {a['name'][:26]:<28}(연결 {a['deg']:>2})  ←  "
                  f"{b['name'][:26]}")

        if args.dry_run:
            print("\n[dry-run] 합치지 않았습니다.")
            return 0
        if not merge:
            print("\n· 합칠 것이 없습니다.")
            return 0

        done = 0
        with neo4j_session() as s:
            for a, b, verdict in merge:
                entry = f"{b['period'] or '?'}|{b['name']}|{b['id']}"
                s.run(_TIMELINE, keep=a["id"], entries=[entry])
                s.run(_MERGE, keep=a["id"], drop=b["id"])
                done += 1
        print(f"\n✅ {done}쌍 병합 · 사라진 이름은 `timeline`에 "
              f"「연월|이름|id」로 보관")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
