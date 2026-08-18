"""표본 심층검사 — **아직 아무도 생각 못 한 실패 유형**을 찾는 자리.

자동 검사는 "정해둔 실패 방식"만 잡는다. 정의되지 않은 실패는 사람이 근거를 읽어야
드러난다. 실제로 2026-07-29에 삼성전자↔SK하이닉스 한 쌍을 정독한 것만으로
결함 4건이 나왔고, 그 중 3건은 영구 검사가 되어 3,900개 엣지 전체에 적용됐다.

**그런데 그 방식엔 편향이 있다.** 대기업 쌍을 골랐으니 나온 결함도 「대형사 · 대칭
엣지」 유형이었다. 같은 데를 또 보면 같은 것만 나온다. 그래서 이 도구는 **층화
무작위**로 뽑는다 — 엣지 유형마다 골고루, 그 안에서는 무작위로.

    python -m batch.audit.spot_check                    # 유형별 2건씩
    python -m batch.audit.spot_check --per-type 3       # 더 많이
    python -m batch.audit.spot_check --source news      # 뉴스만 (추론이 들어간 쪽)
    python -m batch.audit.spot_check --suspect          # 의심 표시된 것만 재검토
    python -m batch.audit.spot_check --seed 7           # 같은 표본 다시 (재현)

출력은 사람이 읽으라고 만든 것이다. 각 표본에 대해 이렇게 물어보면 된다:

    1. 이 근거가 정말 이 관계를 말하는가?
    2. 방향이 맞는가?
    3. 엣지 유형이 맞는가? (협력인가 거래인가)
    4. 노드 이름이 이 근거에서 나온 게 맞는가?
    5. **자동 검사 중 무엇도 이걸 못 잡았다면, 어떤 검사가 있어야 했나?**

5번이 이 도구의 존재 이유다. 발견을 영구 검사로 바꾸지 않으면 다음에 또 놓친다.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict

from app.core.database import neo4j_session
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.vectorstore.chroma_store import get_store

_FIND = """
MATCH (a)-[r]->(b)
WHERE ($source = 'any' OR r.source_type = $source)
  AND (NOT $suspect_only OR r.grounding_suspect OR r.retype_suspect)
RETURN elementId(r) AS eid, type(r) AS edge,
       labels(a)[0] AS a_label, coalesce(a.name,'') AS a_name,
       labels(b)[0] AS b_label, coalesce(b.name,'') AS b_name,
       coalesce(r.subtype,'') AS subtype,
       coalesce(r.subtypes,[]) AS subtypes,
       coalesce(r.source_type,'') AS src,
       coalesce(r.confidence,0) AS conf,
       coalesce(r.corroboration,1) AS corr,
       coalesce([r.evidence_id],[]) + coalesce(r.evidence_ids,[]) AS ids,
       coalesce(r.source_docs, [r.source_doc]) AS docs,
       r.grounding_suspect IS NOT NULL AS g_susp,
       coalesce(r.grounding_reason,'') AS g_why,
       r.retype_suspect IS NOT NULL AS r_susp
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-type", type=int, default=2,
                    help="엣지 유형마다 뽑을 표본 수 (기본 2)")
    ap.add_argument("--source", choices=["news", "dart", "any"], default="any")
    ap.add_argument("--suspect", action="store_true",
                    help="의심 표시된 것만 (자동 판정이 맞았는지 재검토)")
    ap.add_argument("--seed", type=int, default=None,
                    help="표본 고정 — 같은 표본을 다시 보고 싶을 때")
    args = ap.parse_args()

    rng = random.Random(args.seed)

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(
            _FIND, source=args.source, suspect_only=args.suspect)]

    if not rows:
        print("표본이 없습니다.")
        return 0

    # ── 층화: 엣지 유형별로 균등하게 ──────────────────────────
    # 그냥 무작위로 뽑으면 개수 많은 유형(IMPACTS·HAS_EVENT)만 나온다.
    # 드문 유형(SUES·REGULATES)일수록 검사를 덜 받았을 가능성이 높다.
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["edge"]].append(r)

    picked: list[dict] = []
    for edge in sorted(by_type):
        pool = by_type[edge]
        picked.extend(rng.sample(pool, min(args.per_type, len(pool))))

    store = get_store()
    scope = f"{args.source} · {'의심분만' if args.suspect else '전체'}"
    print("=" * 72)
    print(f"  표본 심층검사   {len(picked)}건 / 모집단 {len(rows):,}건   ({scope})")
    if args.seed is not None:
        print(f"  seed={args.seed} — 같은 표본을 다시 보려면 이 값을 쓰세요")
    print("=" * 72)

    for i, r in enumerate(picked, 1):
        flags = []
        if r["g_susp"]:
            flags.append("근거의심")
        if r["r_susp"]:
            flags.append("유형의심")
        flag_txt = f"  [{' · '.join(flags)}]" if flags else ""

        subs = r["subtypes"] or ([r["subtype"]] if r["subtype"] else [])
        print(f"\n{'─'*72}")
        print(f"[{i}/{len(picked)}] ({r['a_label']}) {r['a_name'][:26]}")
        print(f"        -[{r['edge']}]->")
        print(f"        ({r['b_label']}) {r['b_name'][:26]}{flag_txt}")
        print(f"   subtype: {' | '.join(subs) or '-'}")
        print(f"   출처: {r['src']} · 확신 {r['conf']:.2f} · 뒷받침 {r['corr']}건")
        if r["g_why"]:
            print(f"   자동판정: {r['g_why'][:110]}")

        ids = list(dict.fromkeys([x for x in r["ids"] if x]))
        if not ids:
            print("   ⚠ 근거 없음")
        else:
            try:
                got = store.get(EVIDENCE_COLLECTION, ids)
                for doc in got.get("documents", []):
                    if doc:
                        print(f"   · {doc.split(chr(10))[0][:180]}")
            except Exception as exc:
                print(f"   ⚠ 근거 조회 실패: {exc!r}")

        for d in [x for x in (r["docs"] or []) if x][:2]:
            print(f"   ↗ {d[:96]}")

    print(f"\n{'='*72}")
    print("  각 표본에 물어보세요:")
    print("    1. 이 근거가 정말 이 관계를 말하는가?")
    print("    2. 방향은? 3. 엣지 유형은? 4. 노드 이름은 근거에서 나왔나?")
    print("    5. ★자동 검사가 못 잡았다면, 어떤 검사가 있어야 했나?")
    print("  5번에서 나온 답을 batch/audit/relations.py 에 검사로 추가하세요.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
