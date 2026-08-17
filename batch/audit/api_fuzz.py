"""무작위 기업 조합으로 API 를 **수백 번 두들겨** 이상한 것을 찾는다.

★왜 필요한가 (2026-08-17)

손으로 고른 워크스페이스 대여섯 개로 버그 14개를 잡았다. 그런데 고른 조합은
**내가 아는 조합**이라 내가 생각 못 한 상황은 안 나온다. 실제로 늦게 잡힌 것들이
그랬다 —

    삼성전자(연결 1,169)로 조회해야만 카테시안 곱이 드러났다
    소재·부품처럼 **서로 거래 안 하는** 조합이라야 섬 재계산 버그가 보였다
    이웃이 적은 워크스페이스라야 두 축 붕괴가 보였다

그래서 **그래프에서 무작위로 뽑아** 돌린다. 규모·업종·연결 수를 섞어서.

★두 종류를 본다

    불변식   깨지면 무조건 버그          엣지 끝이 없다 · id 중복 · 섬인데 선이 있다
    낌새     맞을 수도 있지만 봐야 한다   두 축 완전 중복 · 참조 0곳 · 점수 전부 동일

  낌새는 **자동으로 판정하지 않는다.** 「이런 조합에서 이런 게 보였다」를 모아
  사람이 보게 한다 — 실제로 두 축 붕괴가 그렇게 발견됐다.

실행:
    python -m batch.audit.api_fuzz                  # 200회
    python -m batch.audit.api_fuzz --n 500
    python -m batch.audit.api_fuzz --seed 7 --show  # 낌새 상세까지
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _pool() -> list[dict]:
    """기업 후보 — **연결 수를 골고루** 섞어 뽑는다.

    ★연결이 많은 것만 뽑으면 섬·빈 그래프가 안 나오고, 적은 것만 뽑으면
      허브 관련 문제가 안 나온다.
    """
    from app.core.database import neo4j_session
    with neo4j_session() as s:
        return [dict(r) for r in s.run("""
            MATCH (c:Company)
            OPTIONAL MATCH (c)-[e]-()
            WITH c, count(e) AS deg
            WHERE deg > 0
            RETURN coalesce(c.corp_code, c.norm_name) AS key, c.name AS name,
                   c.ksic AS ksic, deg
        """)]


def _sample(pool: list[dict], rng: random.Random) -> list[str]:
    """워크스페이스 하나를 만든다. 크기·성격을 섞는다."""
    n = rng.choice([1, 2, 3, 3, 4, 5, 5, 6, 8, 12])
    mode = rng.choice(["random", "same_ksic", "big", "small", "mixed"])
    if mode == "same_ksic":
        by = {}
        for c in pool:
            by.setdefault(c.get("ksic"), []).append(c)
        big = [v for v in by.values() if len(v) >= n]
        cand = rng.choice(big) if big else pool
    elif mode == "big":
        cand = [c for c in pool if c["deg"] >= 100] or pool
    elif mode == "small":
        cand = [c for c in pool if c["deg"] <= 5] or pool
    else:
        cand = pool
    return [c["key"] for c in rng.sample(cand, min(n, len(cand)))]


# ══════════════════════════════════════════════════════════════════
#  불변식 — 깨지면 무조건 버그
# ══════════════════════════════════════════════════════════════════


def check_invariants(g: dict, keys: list[str], body: dict) -> list[str]:
    out: list[str] = []
    N = {n["key"]: n for n in g["nodes"]}

    for e in g["edges"]:
        if e["source"] not in N:
            out.append(f"엣지 source `{e['source']}` 가 nodes 에 없다")
        if e["target"] not in N:
            out.append(f"엣지 target `{e['target']}` 가 nodes 에 없다")
        if not (0 <= e["score"] <= 1):
            out.append(f"score 범위 밖: {e['score']}")
        if e["source"] == e["target"]:
            out.append(f"자기 자신을 잇는 엣지: {e['source']}")

    ids = [e["edge_id"] for e in g["edges"]]
    if len(ids) != len(set(ids)):
        out.append(f"edge_id 중복 {len(ids)-len(set(ids))}건")
    if any(not i for i in ids):
        out.append("edge_id 가 빈 문자열")

    for k, n in N.items():
        pinned = n["role"] == "pinned"
        if pinned and (n["members"] is not None or n["risk_weight"] is not None):
            out.append(f"담은 기업 {n['name']} 에 참조 지표가 채워짐")
        if not pinned and n["risk_weight"] is None:
            out.append(f"참조 {n['name']} 의 risk_weight 가 null")
        if n["is_island"] != (k in g["islands"]):
            out.append(f"{n['name']} 의 is_island 와 islands 목록이 어긋남")

    for isl in g["islands"]:
        if isl not in N:
            out.append(f"islands 에 nodes 없는 키 `{isl}`")
        if any(isl in (e["source"], e["target"]) for e in g["edges"]):
            out.append(f"{isl} 는 섬이라는데 엣지가 있다")

    if len(g["nodes"]) > body.get("max_nodes", 150):
        out.append(f"max_nodes 초과: {len(g['nodes'])}")
    if g.get("truncated") and not g.get("omitted"):
        out.append("잘렸는데 omitted 가 비었다")
    if not body.get("refs") and g.get("ref_candidates"):
        out.append("refs=false 인데 ref_candidates 가 있다")

    # 요청한 키 중 실재하는 것은 노드로 나와야 한다
    pinned_keys = {n["key"] for n in g["nodes"] if n["role"] == "pinned"}
    if pinned_keys - set(keys):
        out.append(f"요청에 없는 키가 pinned 로: {pinned_keys - set(keys)}")

    # ── 여기부터는 「응답 안에서 서로 맞는가」 ──────────────────
    touch: dict[str, int] = {}
    for e in g["edges"]:
        for side in (e["source"], e["target"]):
            touch[side] = touch.get(side, 0) + 1

    for k, n in N.items():
        # ★`degree` 는 **그래프 전체의 연결 수**다. 응답에 그려진 선보다 작을 수 없다.
        if n.get("degree") is not None and touch.get(k, 0) > n["degree"]:
            out.append(f"{n['name']} degree={n['degree']} 인데 응답의 선이 {touch[k]}개")
        # ★참조의 `members` 는 **실제로 담은 기업과 이어진 수**여야 한다.
        #   잘린 응답은 뺀다 — 엣지가 사라졌으니 셀 수가 없다.
        if (n["role"] != "pinned" and n.get("members") is not None
                and not g.get("truncated")):
            real = len({(e["target"] if e["source"] == k else e["source"])
                        for e in g["edges"] if k in (e["source"], e["target"])}
                       & pinned_keys)
            if real != n["members"]:
                out.append(f"{n['name']} members={n['members']} 인데 실제 연결 {real}곳")

    for e in g["edges"]:
        # ★종료된 관계는 응답에 오면 안 된다
        if e["freshness"] == "expired":
            out.append(f"expired 엣지가 응답에 있다: {e['edge_id']}")
        # ★대칭 여부는 **유형으로 정해진다.** 값이 따로 놀면 화면이 화살표를 틀리게 그린다
        want = e["type"] in ("PARTNERS_WITH", "COMPETES_WITH")
        if e["symmetric"] != want:
            out.append(f"{e['type']} 의 symmetric={e['symmetric']} (기대 {want})")

    # ★후보 수는 실제로 붙은 참조보다 적을 수 없다
    nrefs = sum(1 for n in g["nodes"] if n["role"] == "neighbor")
    if body.get("refs") and not g.get("truncated") and g.get("ref_candidates", 0) < nrefs:
        out.append(f"ref_candidates={g['ref_candidates']} 인데 참조가 {nrefs}곳")

    # ★다리 노드는 **섬을 잇기 위해** 붙은 것이다. 담은 기업과 안 이어졌으면 이유가 없다
    for n in g["nodes"]:
        if n["role"] == "bridge" and not g.get("truncated"):
            linked = {(e["target"] if e["source"] == n["key"] else e["source"])
                      for e in g["edges"] if n["key"] in (e["source"], e["target"])}
            if len(linked & pinned_keys) < 2:
                out.append(f"다리 {n['name']} 가 담은 기업 {len(linked & pinned_keys)}곳에만 닿음"
                           " (다리는 둘 이상을 이어야 한다)")
    return out


# ══════════════════════════════════════════════════════════════════
#  낌새 — 맞을 수도 있지만 사람이 봐야 한다
# ══════════════════════════════════════════════════════════════════


def check_smells(g: dict, body: dict) -> list[str]:
    out: list[str] = []
    N = {n["key"]: n for n in g["nodes"]}
    refs = [n for n in g["nodes"] if n["role"] != "pinned"]
    pinned = [n for n in g["nodes"] if n["role"] == "pinned"]

    # ★잘린 응답은 낌새 판정에서 뺀다. `max_nodes` 에 걸려 참조가 없는 것은
    #   정상이고 `omitted` 가 이미 말하고 있다 — 안 빼면 거짓 경보만 쌓인다.
    if body.get("refs") and not g.get("truncated"):
        if not refs and g.get("ref_candidates", 0) > 0:
            out.append(f"참조 0곳인데 후보는 {g['ref_candidates']}곳")
        # ★두 축이 같은 답을 내면 축이 하나다 — 이렇게 버그 14번을 찾았다
        if refs and len(refs) < 2 * 5 and g.get("ref_candidates", 0) >= 2 * 5:
            out.append(f"후보 {g['ref_candidates']}곳인데 참조가 {len(refs)}곳뿐"
                       " (두 축이 겹쳤을 수 있음)")
    if body.get("refs") and not g.get("truncated"):
        risky = [n for n in refs if (n.get("risk_weight") or 0) > 0]
        if refs and not risky:
            out.append("참조 전부가 위험 0 — 위험 축이 아무것도 못 뽑았다")

    if g["edges"]:
        fresh = Counter(e["freshness"] for e in g["edges"])
        if fresh.get("stale", 0) == len(g["edges"]) and len(g["edges"]) >= 5:
            out.append(f"엣지 {len(g['edges'])}개가 전부 stale")
        scores = {e["score"] for e in g["edges"]}
        if len(scores) == 1 and len(g["edges"]) >= 5:
            out.append(f"엣지 {len(g['edges'])}개의 점수가 전부 {scores.pop()}")
        # 같은 쌍에 엣지가 너무 많으면 화면이 못 그린다
        pair = Counter(tuple(sorted((e["source"], e["target"]))) for e in g["edges"])
        worst, cnt = pair.most_common(1)[0]
        if cnt >= 6:
            a = N.get(worst[0], {}).get("name", worst[0])
            b = N.get(worst[1], {}).get("name", worst[1])
            out.append(f"{a}↔{b} 사이에 엣지 {cnt}개")
        # subtype 이 비정상적으로 길면 화면이 깨진다
        for e in g["edges"]:
            if e.get("subtype") and len(e["subtype"]) > 40:
                out.append(f"subtype 이 {len(e['subtype'])}자: {e['subtype'][:45]}…")
                break

    if pinned and len(g["islands"]) == len(pinned) and len(pinned) >= 3:
        out.append(f"담은 {len(pinned)}곳이 전부 섬")
    for n in refs:
        if n.get("members") == 0:
            out.append(f"참조 {n['name']} 의 members 가 0")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="시도 횟수")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--show", action="store_true", help="낌새를 조합과 함께 자세히")
    args = ap.parse_args()

    from fastapi.testclient import TestClient
    from app.api.main import app

    rng = random.Random(args.seed)
    pool = _pool()
    cli = TestClient(app)
    print(f"기업 후보 {len(pool):,}곳에서 무작위 조합 {args.n}회\n")

    bugs: list[tuple[list[str], str]] = []
    smells: list[tuple[str, str]] = []
    slow: list[tuple[float, str]] = []
    stat = Counter()
    t0 = time.time()

    for i in range(args.n):
        keys = _sample(pool, rng)
        body = {
            "keys": keys,
            "refs": rng.random() < 0.5,
            "expand": rng.random() < 0.8,
            "max_nodes": rng.choice([4, 10, 150, 150]),
        }
        label = f"{len(keys)}곳 refs={body['refs']} max={body['max_nodes']} {keys[:3]}"
        t = time.time()
        r = cli.post("/workspace/graph", json=body)
        dt = time.time() - t
        if r.status_code != 200:
            bugs.append(([f"HTTP {r.status_code}: {r.text[:150]}"], label))
            continue
        g = r.json()
        if dt > 3.0:
            slow.append((dt, label))

        bad = check_invariants(g, keys, body)
        if bad:
            bugs.append((bad, label))
        for sm in check_smells(g, body):
            smells.append((sm, label))

        stat["노드"] += len(g["nodes"])
        stat["엣지"] += len(g["edges"])
        stat["섬"] += len(g["islands"])
        stat["잘림"] += bool(g.get("truncated"))
        if (i + 1) % 50 == 0:
            print(f"  … {i+1}/{args.n}  버그 {len(bugs)} · 낌새 {len(smells)}")

    print(f"\n{args.n}회 · {time.time()-t0:.0f}초 · "
          f"노드 누적 {stat['노드']:,} 엣지 {stat['엣지']:,} 섬 {stat['섬']:,} "
          f"잘림 {stat['잘림']}회")

    print(f"\n■ 불변식 위반 {len(bugs)}건")
    for bad, label in bugs[:15]:
        print(f"   ❌ {label}")
        for b in bad[:4]:
            print(f"      {b}")

    print(f"\n■ 낌새 {len(smells)}건")
    for kind, cnt in Counter(s for s, _ in smells).most_common():
        print(f"   {cnt:>4}회  {kind if args.show else kind[:70]}")
    if args.show:
        seen: set[str] = set()
        print("\n   ── 조합 예시 ──")
        for kind, label in smells:
            k = kind[:40]
            if k in seen:
                continue
            seen.add(k)
            print(f"   {kind}\n      ↳ {label}")

    if slow:
        print(f"\n■ 3초 넘는 요청 {len(slow)}건")
        for dt, label in sorted(slow, reverse=True)[:5]:
            print(f"   {dt:.1f}초  {label}")

    return 1 if bugs else 0


if __name__ == "__main__":
    raise SystemExit(main())
