"""표기만 다른 해외 기업 노드를 찾아 **모델이 판정**하고 합친다.

★왜 필요한가 (2026-08-14)

해외 기업은 DART에 없어 이름이 곧 노드 식별자다. 표기가 갈리면 한 회사가
여러 노드로 쪼개지고, 그 회사를 지나는 공급망 경로가 끊긴다. 실측:

    마이크론(78) ⟷ 마이크론테크놀러지(0) ⟷ 마이크론 테크놀로지(10)
    보스턴다이내믹스(41) ⟷ 보스톤 다이나믹스(1) ⟷ 보스턴다이나믹스(4)

★왜 2단인가 — **열쇠는 확인에 좋고 제안에 나쁘다**

  자음 골격도 정식명도 후보를 모으는 데는 쓸 만하지만, 그것만으로 합치면
  엉뚱한 걸 묶는다. 실측(음차 열쇠로 뽑은 후보):

      npt   엔비디아 ⟷ 윈보드 ⟷ 에너베이트     ← 전부 다른 회사
      ntr   인텔 ⟷ 유니트리
      rstm  알스톰 ⟷ 로사톰

  그래서 규칙은 **후보만** 만들고(무료·전수), 판정은 모델이 한다(후보만·소액).
  개체 해소의 표준 구조 그대로다 — 블로킹 → 비교 → 판정.

★확정된 것만 합친다. 애매하면 **합치지 않고 표시만** 한다.
  노드를 잘못 합치면 되돌리기 어렵다 — 두 회사의 관계가 한 노드에 뒤섞인다.

    python -m batch.repair.foreign_merge --dry-run
    python -m batch.repair.foreign_merge
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.llm import ask_json
from pipeline.normalizer.canonical_name import canonical_names
from pipeline.normalizer.company_registry import ensure, record
from pipeline.normalizer.translit import MIN_SKELETON, skeleton

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_MODEL_COST = 0.25          # 판정 1건당 대략 (gpt-4o-mini)

_CREATE = """
CREATE TABLE IF NOT EXISTS name_merge_verdicts (
    key_a      TEXT NOT NULL,
    key_b      TEXT NOT NULL,
    same       BOOLEAN NOT NULL,
    reason     TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key_a, key_b)
)
"""
_LOAD = "SELECT key_a, key_b, same FROM name_merge_verdicts"
_SAVE = """
INSERT INTO name_merge_verdicts (key_a, key_b, same, reason) VALUES (%s,%s,%s,%s)
ON CONFLICT (key_a, key_b) DO UPDATE SET same = EXCLUDED.same,
                                         reason = EXCLUDED.reason
"""

_SYSTEM = """당신은 기업 지식그래프에서 **같은 회사가 두 이름으로 갈린 것**을
가려내는 도구입니다.

각 쌍이 **같은 회사**인지 판정하세요. 두 이름은 표기·음차가 비슷하다는 이유로
후보에 올랐을 뿐, 실제로는 전혀 다른 회사인 경우가 많습니다.

【같은 회사 (same=true)】
   표기 차이      "보스턴다이내믹스" / "보스톤 다이나믹스" / "보스턴다이나믹스"
   한글↔영문      "엔비디아" / "NVIDIA"
   법인격·접미어   "마이크론" / "마이크론 테크놀로지"   ← 같은 회사의 정식명
   약어           "TCL" / "TCL그룹"

【다른 회사 (same=false)】 ★이쪽이 더 많습니다
   "엔비디아" / "윈보드"        음차 골격만 우연히 같음
   "인텔" / "유니트리"
   "알스톰" / "로사톰"
   "키옥시아" / "코쿠사이"
   ★**자회사·지역법인은 다른 회사입니다** —
     "마이크론" / "마이크론 메모리 말레이시아"  → false
     "HD현대" / "HD현대미포조선"              → false

【판단이 어려울 때】
   확신이 없으면 **false**로 하세요. 잘못 합치면 두 회사의 관계가 한 노드에
   뒤섞여 되돌리기 어렵습니다. 못 합친 건 나중에 다시 볼 수 있습니다.

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
                    "same": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["a", "b", "same", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_NODES = """
MATCH (c:Company) WHERE c.corp_code IS NULL AND c.name IS NOT NULL
RETURN c.name AS name, c.norm_name AS key, size([(c)-[]-() | 1]) AS deg
ORDER BY deg DESC
"""
_MERGE = """
MATCH (keep:Company {norm_name:$keep})
MATCH (drop:Company {norm_name:$drop})
CALL apoc.refactor.mergeNodes([keep, drop], {properties:'discard', mergeRels:true})
YIELD node RETURN node.name AS name
"""
_MARK = """
MATCH (c:Company {norm_name:$key})
SET c.also_names = coalesce(c.also_names, []) + $alias
"""


def _group(nodes: list[dict], keyfn) -> list[tuple[dict, dict]]:
    """같은 열쇠를 쓰는 노드끼리 짝짓는다. 연결 많은 쪽이 기준.

    무리 안에서 **모든 쌍**을 만들지 않는다 — 10곳이면 45쌍이 되어 판정비가
    폭증한다. 기준 하나와 나머지만 짝지으면 9쌍이고, 기준과 같다고 판정되면
    이행적으로 다 이어진다.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        k = keyfn(n)
        if k:
            buckets[k].append(n)
    out: list[tuple[dict, dict]] = []
    for group in buckets.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: -x["deg"])
        head = group[0]
        for other in group[1:]:
            if head["key"] != other["key"]:
                out.append((head, other))
    return out


def _pairs(conn, nodes: list[dict], *, use_canon: bool) -> list[tuple[dict, dict]]:
    """후보 생성 — 음차 골격(무료)과 정식명 열쇠를 **둘 다** 쓴다.

    서로 다른 것을 놓치므로 겹쳐 쓴다. 재현율 비교는 `canonical_name.py` 참고.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[dict, dict]] = []

    def add(pairs):
        for a, b in pairs:
            k = tuple(sorted((a["key"], b["key"])))
            if k not in seen:
                seen.add(k)
                out.append((a, b))

    add(_group(nodes, lambda n: (lambda sk: sk if len(sk) >= MIN_SKELETON else "")(
        skeleton(n["name"]))))
    if not use_canon:
        return out

    # 정식명 → 열쇠. 한 번 물으면 레지스트리에 남아 다음엔 무료다.
    names = [n["name"] for n in nodes]
    keys = {n["name"]: n["key"] for n in nodes}
    canon = canonical_names(conn, names, keys)
    for n in nodes:
        cn, bk = canon.get(n["name"], (n["name"], ""))
        n["canon_name"], n["block"] = cn, bk
        record(conn, n["key"], n["key"], source="first_seen",
               canon_name=cn, note="노드 자신 — 열쇠 보관")
    add(_group(nodes, lambda n: n.get("block") or ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="판정할 쌍 수 상한(비용 통제)")
    ap.add_argument("--no-canon", action="store_true",
                    help="정식명 묻기를 건너뛴다(음차만·무료)")
    args = ap.parse_args()

    with neo4j_session() as s:
        nodes = [dict(r) for r in s.run(_NODES)]

    with postgres_connection() as conn:
        ensure(conn)
        pairs = _pairs(conn, nodes, use_canon=not args.no_canon)
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(_LOAD)
            cached = {(a, b): v for a, b, v in cur.fetchall()}

        todo = [p for p in pairs if (p[0]["key"], p[1]["key"]) not in cached]
        if args.limit:
            todo = todo[:args.limit]

        print("=" * 72)
        print(f"  표기가 갈린 해외 기업 후보 — 노드 {len(nodes):,}곳에서 "
              f"{len(pairs)}쌍")
        print(f"  이미 판정 {len(cached)}쌍 · 이번에 물을 것 {len(todo)}쌍 "
              f"(약 {len(todo) * _MODEL_COST:.0f}원)")
        print("=" * 72)

        if todo and not args.dry_run:
            for i in range(0, len(todo), 20):
                chunk = todo[i:i + 20]
                body = "\n".join(f"- {a['name']} | {b['name']}" for a, b in chunk)
                got = ask_json(_SYSTEM, body, schema=_SCHEMA,
                               name="foreign_merge", fallback={"items": []})
                by_name = {(a["name"], b["name"]): (a, b) for a, b in chunk}
                with conn.cursor() as cur:
                    for it in got.get("items", []):
                        pair = by_name.get((it["a"], it["b"]))
                        if not pair:
                            continue
                        ka, kb = pair[0]["key"], pair[1]["key"]
                        cached[(ka, kb)] = bool(it["same"])
                        cur.execute(_SAVE, (ka, kb, bool(it["same"]),
                                            (it.get("reason") or "")[:60]))
                print(f"     … {min(i + 20, len(todo))}/{len(todo)}")

        same = [(a, b) for a, b in pairs if cached.get((a["key"], b["key"]))]
        diff = len(pairs) - len(same)
        print(f"\n  판정: 같은 회사 {len(same)}쌍 · 다른 회사 {diff}쌍")
        for a, b in same[:15]:
            print(f"     {a['name'][:22]:<24}(연결 {a['deg']:>3})  ←  "
                  f"{b['name'][:22]:<24}(연결 {b['deg']:>2})")

        if args.dry_run:
            print("\n[dry-run] 합치지 않았습니다.")
            return 0
        if not same:
            print("\n· 합칠 것이 없습니다.")
            return 0

        merged = 0
        with neo4j_session() as s:
            for a, b in same:
                # 사라지는 표기를 살아남는 노드에 남긴다 — 옛 링크가 계속 찾아지게
                s.run(_MARK, key=a["key"], alias=[b["name"], b["key"]])
                s.run(_MERGE, keep=a["key"], drop=b["key"])
                merged += 1
        print(f"\n✅ {merged}쌍 병합 · 사라진 표기는 `also_names`에 보관")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
