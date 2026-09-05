"""무작위 기업 조합으로 API 를 **수백 번 두들겨** 이상한 것을 찾는다.

왜 필요한가 (2026-08-17)

손으로 고른 워크스페이스 대여섯 개로 버그 14개를 잡았다. 그런데 고른 조합은
**내가 아는 조합**이라 내가 생각 못 한 상황은 안 나온다. 실제로 늦게 잡힌 것들이
그랬다 —

    삼성전자(연결 1,169)로 조회해야만 카테시안 곱이 드러났다
    소재·부품처럼 **서로 거래 안 하는** 조합이라야 섬 재계산 버그가 보였다
    이웃이 적은 워크스페이스라야 두 축 붕괴가 보였다

그래서 **그래프에서 무작위로 뽑아** 돌린다. 규모·업종·연결 수를 섞어서.

두 종류를 본다

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
import re
import sys
import time
from collections import Counter

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
    # ★보낸 키는 **노드가 되거나 `unknown_keys` 에 있거나** 둘 중 하나다.
    #   조용히 사라지면 화면이 「담았는데 왜 안 보이지」를 설명할 수 없다.
    lost = set(keys) - pinned_keys - set(g.get("unknown_keys") or [])
    if lost and not g.get("truncated"):
        out.append(f"보낸 키가 노드도 unknown_keys 도 아니고 사라짐: {sorted(lost)}")

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


def check_events(rows: list, d: dict) -> list[str]:
    """`/companies/{key}/events` — **사건 목록.**"""
    out: list[str] = []
    seen = set()
    for e in rows:
        if not e.get("event_id"):
            out.append("event_id 가 비었다")
        if e["event_id"] in seen:
            out.append(f"event_id 중복: {e['event_id']}")
        seen.add(e["event_id"])
        if e["role"] not in ("subject", "counterparty", "mentioned"):
            out.append(f"role 이 {e['role']}")
        if e.get("article_count", 1) < 1:
            out.append(f"article_count 가 {e['article_count']}")
        for ph in e.get("timeline") or []:
            # ★`timeline` 은 **펴서** 줘야 한다. 화면이 문자열을 쪼개게 하지 않는다
            if not ph.get("period") or not ph.get("name"):
                out.append(f"timeline 국면이 덜 펴짐: {ph}")
            if ph.get("period") and not re.fullmatch(r"\d{4}-\d{2}", ph["period"]):
                out.append(f"timeline period 형식이 이상: {ph['period']}")
        if e.get("occurred_at") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                    e["occurred_at"]):
            out.append(f"occurred_at 형식이 이상: {e['occurred_at']}")
    # 상세의 사건 수와 어긋나면 화면이 헷갈린다
    if d and len(rows) > d["counts"]["events"]:
        out.append(f"events {len(rows)} > counts.events {d['counts']['events']}")
    risk = sum(1 for e in rows if e["is_risk"])
    if d and risk > d["counts"]["risk_events"]:
        out.append(f"위험 {risk} > counts.risk_events {d['counts']['risk_events']}")
    return out


def check_news(rows: list) -> list[str]:
    """`/companies/{key}/news` — ★**본문은 없어야 한다**(저작권)."""
    out: list[str] = []
    urls = set()
    for n in rows:
        if not str(n.get("url", "")).startswith("http"):
            out.append(f"url 이 http 로 시작 안 함: {str(n.get('url'))[:40]}")
        if n["url"] in urls:
            out.append(f"기사 중복: {n['url'][:50]}")
        urls.add(n["url"])
        if not n.get("title"):
            out.append("제목이 비었다")
        if "body" in n or "content" in n:
            out.append("본문이 응답에 실렸다 — 저작권상 나가면 안 된다")
    dates = [n["published_at"] for n in rows if n.get("published_at")]
    if dates != sorted(dates, reverse=True):
        out.append("최신순이 아니다")
    return out


def check_filings(rows: list) -> list[str]:
    out: list[str] = []
    seen = set()
    for f in rows:
        if not re.fullmatch(r"\d{14}", f.get("rcept_no", "")):
            out.append(f"접수번호가 14자리가 아님: {f.get('rcept_no')}")
        if f["rcept_no"] in seen:
            out.append(f"공시 중복: {f['rcept_no']}")
        seen.add(f["rcept_no"])
        if f["rcept_no"] not in (f.get("url") or ""):
            out.append("url 에 접수번호가 안 들어감")
    dates = [f["rcept_dt"] for f in rows]
    if dates != sorted(dates, reverse=True):
        out.append("최신순이 아니다")
    return out


def check_company_graph(g: dict, key: str, d: dict) -> list[str]:
    """`/companies/{key}/graph` — **이 기업 중심 그래프.**"""
    out: list[str] = []
    N = {n["key"]: n for n in g["nodes"]}
    if key not in N and d["key"] not in N:
        out.append("중심 기업이 노드에 없다")
    me = N.get(key) or N.get(d["key"])
    if me and me["role"] != "pinned":
        out.append(f"중심 기업의 role 이 {me['role']}")
    for e in g["edges"]:
        if e["source"] not in N or e["target"] not in N:
            out.append("엣지 끝이 nodes 에 없다")
        if e["source"] == e["target"]:
            out.append("자기 자신을 잇는 엣지")
    ids = [e["edge_id"] for e in g["edges"]]
    if len(ids) != len(set(ids)):
        out.append("edge_id 중복")
    # ★관계가 있는 기업인데 그래프에 선이 없으면 화면이 못 그린다
    if not g["edges"] and (d.get("related") or d.get("events") or d.get("products")):
        out.append(f"상세엔 관계 {len(d.get('related') or [])}·사건 "
                   f"{len(d.get('events') or [])}·제품 {len(d.get('products') or [])}"
                   f" 가 있는데 그래프 엣지가 0")
    # ★목업의 범례는 거래·주주·사건·제품 넷이다. 전부 trade 면 못 가른다
    kinds = {n.get("kind") for n in g["nodes"] if n["role"] != "pinned"}
    if len(g["nodes"]) > 3 and kinds and kinds == {"trade"} and (
            d.get("events") or d.get("products") or d.get("owned_by")):
        out.append(f"노드 kind 가 전부 trade — 사건·제품·주주를 못 가린다 {kinds}")

    om = g.get("omitted") or {}
    # ★그린 것 + 뺀 것 = 상세가 말한 관계 수. 안 맞으면 관계가 어딘가로 샜다
    total = (d.get("counts") or {}).get("relations")
    if total is not None and len(g["edges"]) + sum(om.values()) != total:
        out.append(f"그린 {len(g['edges'])} + 뺀 {sum(om.values())} ≠ 상세 관계 {total}")
    # ★유형이 있는데 한 건도 안 그려지면 그 관계는 화면에서 통째로 사라진다
    drawn = {e["type"] for e in g["edges"]}
    gone = [t for t, n in om.items()
            if n > 0 and t not in drawn and t not in ("HIDDEN", "DUPLICATE")]
    if gone:
        out.append(f"유형이 통째로 안 그려졌다 {gone}")
    # ★같은 두 노드를 **같은 유형·같은 근거로** 두 번 잇지 않는다.
    #   유형이 다르면 중복이 아니다 — 지분과 제휴가 한 문장에서 같이 나올 수 있고
    #   성격이 달라 둘 다 그려야 한다(LG이노텍–AOE일렉트로닉스 합작 설립).
    pair = [(frozenset((e["source"], e["target"])), e["type"], e.get("evidence_id"))
            for e in g["edges"] if e.get("evidence_id")]
    if len(pair) != len(set(pair)):
        out.append("같은 유형·같은 근거로 같은 두 노드를 두 번 이었다")
    # ★HAS_EVENT 와 IMPACTS 는 **같은 사실의 앞뒤면**이다. 둘 다 그리면 화살표가 겹친다
    ev = {frozenset((e["source"], e["target"])) for e in g["edges"]
          if e["type"] == "HAS_EVENT"}
    if any(frozenset((e["source"], e["target"])) in ev
           for e in g["edges"] if e["type"] == "IMPACTS"):
        out.append("HAS_EVENT 와 IMPACTS 가 같은 두 노드를 겹쳐 이었다")
    if len(g["nodes"]) > 60:
        out.append(f"노드가 상한 60 을 넘었다 {len(g['nodes'])}")

    # ★목록과 그림은 **같은 목록이어야 한다.** 양방향으로 본다.
    #   목록에만 있으면 → 한 줄 눌렀을 때 강조할 선이 없다
    #   그림에만 있으면 → 선을 봤는데 목록에서 못 찾는다
    rel = {r["edge_id"] for r in (d.get("related") or []) if r.get("edge_id")}
    if rel - drawn_ids(g):
        out.append(f"관계 목록에만 있고 그래프엔 없는 관계 {len(rel - drawn_ids(g))}건")
    _BLOCK_OF = {"IS_EXECUTIVE_OF": "executives", "DEVELOPS": "products",
                 "HAS_EVENT": "events", "IMPACTS": "events"}
    orphan = [e["type"] for e in g["edges"]
              if e["edge_id"] not in rel and e["type"] not in _BLOCK_OF]
    if orphan:
        out.append(f"그래프엔 그렸는데 상세 어느 블록에도 없는 관계 {len(orphan)}건 "
                   f"{sorted(set(orphan))}")
    # 상세 응답에 그래프 블록이 함께 와야 한다 — 상세 페이지의 블록이니까
    if d.get("graph") is None:
        out.append("상세 응답에 graph 블록이 없다")
    elif {e["edge_id"] for e in d["graph"]["edges"]} != drawn_ids(g):
        out.append("상세의 graph 블록과 /graph 라우트가 다르다")
    return out


def drawn_ids(g: dict) -> set:
    return {e["edge_id"] for e in g["edges"]}


def _fuzz_company(cli, pool, rng, args) -> int:
    """기업 라우트 6개를 무작위 기업으로 두들긴다."""
    bugs: list[tuple[list[str], str]] = []
    slow: list[tuple[float, str]] = []
    t0 = time.time()
    stat = Counter()

    for i in range(args.n):
        c = rng.choice(pool)
        key, label = c["key"], f"{c['name']}({c['key']}, deg {c['deg']})"
        t = time.time()
        r = cli.get(f"/companies/{key}")
        dt = time.time() - t
        if dt > 3.0:
            slow.append((dt, label))
        if r.status_code == 404:
            bugs.append((["존재하는 노드인데 404"], label))
            continue
        if r.status_code != 200:
            bugs.append(([f"HTTP {r.status_code}: {r.text[:120]}"], label))
            continue
        d = r.json()
        bad = check_company(d, key) + check_detail_level(d)
        stat[d["detail_level"]] += 1

        for sub, fn in (("market", lambda j: check_market(j)),
                        ("events", lambda j: check_events(j, d)),
                        ("news", lambda j: check_news(j)),
                        ("filings", lambda j: check_filings(j)),
                        ("graph", lambda j: check_company_graph(j, key, d)),
                        ("relations", lambda j: [])):
            rr = cli.get(f"/companies/{key}/{sub}")
            if rr.status_code != 200:
                bad.append(f"{sub} → {rr.status_code}")
                continue
            bad += [f"[{sub}] {x}" for x in fn(rr.json())]
        if bad:
            bugs.append((bad, label))
        if (i + 1) % 50 == 0:
            print(f"  … {i+1}/{args.n}  버그 {len(bugs)}")

    print(f"\n{args.n}곳 · {time.time()-t0:.0f}초 · "
          f"{' · '.join(f'{k} {v}' for k, v in stat.most_common())}")
    print(f"\n■ 불변식 위반 {len(bugs)}건")
    for bad, label in bugs[:20]:
        print(f"   ❌ {label}")
        for b in bad[:5]:
            print(f"      {b}")
    if slow:
        print(f"\n■ 3초 넘음 {len(slow)}건")
        for dt, label in sorted(slow, reverse=True)[:5]:
            print(f"   {dt:.1f}초  {label}")
    return 1 if bugs else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="시도 횟수")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--show", action="store_true", help="낌새를 조합과 함께 자세히")
    ap.add_argument("--what", choices=["ws", "company"], default="ws")
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

    if args.what == "company":
        return _fuzz_company(cli, pool, rng, args)

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


# ══════════════════════════════════════════════════════════════════
#  기업 라우트 — 무작위 기업으로 두들긴다
# ══════════════════════════════════════════════════════════════════


def check_detail_level(d: dict) -> list[str]:
    """`detail_level` 이 **실제 보유 자료와 맞나.**

    프론트가 이 값으로 「상세 페이지로 가기」를 켠다. 전에는 블록 **개수**로
    판정해서 재무도 공시도 없는 외국 기업이 `full` 로 나갔고, 눌러 보면
    빈 페이지였다.
    """
    out, B, lv = [], d.get("blocks") or {}, d.get("detail_level")
    has_num = B.get("financials") != "none" or B.get("market") != "none"
    if lv == "full" and B.get("overview") == "none":
        out.append("detail_level=full 인데 사업개요가 없다")
    if lv == "partial" and not has_num:
        out.append("detail_level=partial 인데 재무도 시세도 없다")
    if lv == "none" and has_num:
        out.append("detail_level=none 인데 재무나 시세가 있다")
    # ★`none` 은 「빈 노드」가 아니다. 관계·사건·뉴스는 있어야 한다 —
    #   화면이 좌 패널을 그려야 하므로.
    if lv == "none" and all(v == "none" for v in B.values()):
        out.append("detail_level=none 인데 채워진 블록이 하나도 없다")
    if lv not in ("full", "partial", "none"):
        out.append(f"detail_level 값이 {lv!r}")
    return out


def check_company(d: dict, key: str) -> list[str]:
    """`GET /companies/{key}` 의 불변식."""
    out: list[str] = []
    if d["key"] != key and d.get("corp_code") != key:
        out.append(f"요청 키 {key} 와 응답 키 {d['key']} 가 다르다")

    # ★`counts` 는 **목록 길이가 아니라 실제 수**다. 목록이 더 많으면 모순이다
    if len(d.get("financials") or []) > 3:
        out.append(f"financials 가 {len(d['financials'])}개 (최근 3개년이어야)")
    if len(d.get("related") or []) > d["counts"]["relations"]:
        out.append(f"related {len(d['related'])} > counts.relations {d['counts']['relations']}")
    if len(d.get("events") or []) > d["counts"]["events"]:
        out.append(f"events {len(d['events'])} > counts.events {d['counts']['events']}")

    # ★`blocks` 와 실제 내용이 어긋나면 화면이 빈 블록을 편다
    for block, field in [("financials", "financials"), ("segments", "segments"),
                         ("products", "products"), ("related", "related")]:
        has = bool(d.get(field))
        said = d["blocks"][block] != "none"
        if has != said:
            out.append(f"blocks.{block}={d['blocks'][block]} 인데 {field} 는 "
                       f"{'있음' if has else '없음'}")
    if bool(d.get("market_metrics")) != (d["blocks"]["market"] != "none"):
        out.append(f"blocks.market={d['blocks']['market']} 인데 market_metrics 는 "
                   f"{'있음' if d.get('market_metrics') else 'null'}")

    for f in d.get("financials") or []:
        if f["fs_div"] not in ("CFS", "OFS"):
            out.append(f"fs_div 가 {f['fs_div']}")
        # 비율은 계산된 값이라 원본과 맞아야 한다
        if f.get("total_equity") and f.get("net_profit") is not None:
            want = round(f["net_profit"] / f["total_equity"] * 100, 2)
            if f.get("roe") is not None and abs(f["roe"] - want) > 0.02:
                out.append(f"{f['bsns_year']} ROE {f['roe']} ≠ 계산값 {want}")

    for s in d.get("segments") or []:
        if s.get("revenue_ratio") is not None and not (0 <= s["revenue_ratio"] <= 100):
            out.append(f"사업부문 비중이 {s['revenue_ratio']}%")

    for o in (d.get("owned_by") or []) + (d.get("owns") or []):
        if o.get("ratio") is not None and not (0 <= o["ratio"] <= 100):
            out.append(f"지분율이 {o['ratio']}% ({o['name']})")
    # ★자기 자신을 소유할 수 없다
    for o in (d.get("owned_by") or []) + (d.get("owns") or []):
        if o["key"] == d["key"]:
            out.append(f"자기 자신을 소유: {o['name']}")

    for r in d.get("related") or []:
        if r["source"]["key"] != d["key"] and r["target"]["key"] != d["key"]:
            out.append(f"내 관계가 아닌 것이 섞임: {r['source']['name']}→{r['target']['name']}")
        if r["freshness"] == "expired":
            out.append("expired 관계가 응답에 있다")
        if r.get("ratio") is not None and not (0 <= r["ratio"] <= 100):
            out.append(f"관계 ratio 가 {r['ratio']}")

    for e in d.get("events") or []:
        if e["role"] not in ("subject", "counterparty", "mentioned"):
            out.append(f"event role 이 {e['role']}")

    m = d.get("market_metrics")
    if m:
        if m.get("per") is not None and m["per"] < 0:
            out.append(f"PER 이 음수: {m['per']}")
        if m["market_cap"] <= 0:
            out.append(f"시가총액이 {m['market_cap']}")
        # 시총 = 종가 × 유통주식수 여야 한다
        want = m["close_price"] * m["listed_shares"]
        if abs(m["market_cap"] - want) > max(want * 0.001, 1):
            out.append(f"시총 {m['market_cap']:,} ≠ 종가×주식수 {want:,}")
    return out


def check_market(d: dict) -> list[str]:
    out: list[str] = []
    if d["listed"] and d.get("stock_code") is None:
        out.append("listed=true 인데 stock_code 가 null")
    if not d["listed"] and d.get("latest"):
        out.append("listed=false 인데 시세가 있다")
    if d.get("latest") is None and d.get("unavailable_reason") is None:
        out.append("시세가 없는데 사유가 없다")
    if d.get("latest") and d.get("unavailable_reason"):
        out.append(f"시세가 있는데 사유가 붙음: {d['unavailable_reason']}")
    dates = [p["trade_date"] for p in d.get("series") or []]
    if dates != sorted(dates):
        out.append("series 가 날짜순이 아니다")
    if d.get("latest") and dates and d["latest"]["trade_date"] < dates[-1]:
        out.append("latest 가 series 의 마지막보다 과거")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
