"""워크스페이스 — **담은 기업들을 하나로 놓고 보는 것.**

★그래프를 그리는 규칙 셋

    ① 담긴 기업끼리 직접 이어진 엣지       언제나 포함
    ② 섬이 된 기업만 한 칸 건너 잇기        **허브는 다리로 안 씀**
    ③ 그래도 못 이으면 섬으로 두되 표시     **억지로 잇지 않음**

  ②가 필요한 이유를 실측으로 확인했다. 「고영」을 나머지와 이어 주는 중간 노드가
  **삼성자산운용**(연결 32)이었는데, 자산운용사가 **양쪽에 지분이 있을 뿐**이지
  두 회사가 관계있다는 뜻이 아니다. 다리로 쓰면 펀드 하나가 모든 회사를 이어버린다.

  ③이 더 중요하다. **없는 관계를 그리는 것보다 없다고 말하는 게 낫다.**

★참조 기업은 두 축으로 뽑는다

    구조 축   담은 기업 **몇 곳과** 이어지나       `members`
    위험 축   그 노드를 통해 들어오는 위험의 크기    `risk_weight`

  「그 회사가 겪은 사건 수」가 아니다. 현대모비스는 사건이 19건이지만 메모리
  워크스페이스와는 약하게 이어져 있어 들어올 게 없다.

★`can_collect` 의 진짜 뜻

  「담을 수 없음」의 이유는 해외라서가 아니라 **DART 에 없어서 더 받을 데가
  없다**는 것이다. `corp_code` 유무로 판정한다 — `entity_kind='해외'` 는
  9곳뿐이라 쓸 수 없다.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.database import neo4j_session
from app.services.company_service import _relation
from pipeline.normalizer.ksic import label_of

# 다리로 쓰지 않을 노드의 차수. 삼성전자(1,169)를 다리로 허용하면 거의 모든
# 쌍이 이어져 **의미가 0인 그래프**가 된다.
_HUB = 150

# 관계로 볼 엣지 — 사건·제품은 워크스페이스 캔버스에 안 그린다
_TRADE = ["SUPPLIES_TO", "PARTNERS_WITH", "COMPETES_WITH", "ACQUIRES",
          "SUES", "DEPENDS_ON", "OWNS_STAKE_IN"]

_KIND = {"OWNS_STAKE_IN": "ownership"}


def _nodes(session, keys: list[str]) -> dict[str, dict]:
    rows = session.run("""
        MATCH (c:Company) WHERE c.corp_code IN $k OR c.norm_name IN $k
        OPTIONAL MATCH (c)-[e]-()
        RETURN coalesce(c.corp_code, c.norm_name) AS key, c.name AS name,
               c.entity_kind AS kind, c.ksic AS ksic, c.corp_code AS cc,
               count(e) AS degree""", k=keys)
    return {r["key"]: dict(r) for r in rows}


_DIRECT = """
MATCH (a:Company)-[r]->(b:Company)
WHERE coalesce(a.corp_code,a.norm_name) IN $k
  AND coalesce(b.corp_code,b.norm_name) IN $k
  AND type(r) IN $types
RETURN coalesce(a.corp_code,a.norm_name) AS ak, a.name AS an,
       coalesce(b.corp_code,b.norm_name) AS bk, b.name AS bn,
       type(r) AS t, properties(r) AS p
"""

# ★다리로 못 쓰는 것 둘 — 차수와 **종류**.
#
#   차수가 큰 노드(허브)를 다리로 허용하면 거의 모든 쌍이 이어져 의미가 0이 된다.
#   그런데 차수만으로는 부족하다. 실측(2026-08-16): 「고영」을 나머지와 이어 주는
#   다리로 **삼성자산운용**(차수 32 — 허브 기준 아래)이 뽑혔다. 자산운용사가
#   **양쪽에 지분이 있을 뿐**이지 두 회사가 관계있다는 뜻이 아니다.
#   펀드 하나가 모든 회사를 이어버린다.
#
#   그래서 **종류로도 막는다.** 금융기관·펀드·조합은 다리가 될 수 없다.
_NOT_BRIDGE_KIND = ["금융기관", "펀드·조합"]

# ★지분만으로 이어진 것도 다리로 안 쓴다. 「같은 주주가 있다」는 두 회사의
#   관계가 아니라 **주주의 사정**이다. 거래·경쟁만 다리가 된다.
_BRIDGE_TYPES = ["SUPPLIES_TO", "PARTNERS_WITH", "COMPETES_WITH", "ACQUIRES", "DEPENDS_ON"]

_BRIDGE = """
MATCH (island:Company)-[r1]-(mid:Company)-[r2]-(other:Company)
WHERE coalesce(island.corp_code,island.norm_name) = $island
  AND coalesce(other.corp_code,other.norm_name) IN $others
  AND NOT coalesce(mid.corp_code,mid.norm_name) IN $all
  AND type(r1) IN $types AND type(r2) IN $types
  AND NOT coalesce(mid.entity_kind,'') IN $bad_kinds
WITH mid, count(DISTINCT other) AS reach
MATCH (mid)-[e]-()
WITH mid, reach, count(e) AS degree
WHERE degree <= $hub
RETURN coalesce(mid.corp_code,mid.norm_name) AS key, mid.name AS name,
       mid.entity_kind AS kind, mid.ksic AS ksic, mid.corp_code AS cc,
       degree, reach
ORDER BY reach DESC, degree ASC LIMIT 3
"""

# 참조 기업 — 구조 축
_REF_STRUCT = """
MATCH (m:Company)-[r]-(x:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(x.corp_code,x.norm_name) IN $k
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
WITH x, count(DISTINCT m) AS members
OPTIONAL MATCH (x)-[e]-()
WITH x, members, count(e) AS degree
RETURN coalesce(x.corp_code,x.norm_name) AS key, x.name AS name,
       x.entity_kind AS kind, x.ksic AS ksic, x.corp_code AS cc,
       members, degree
ORDER BY members DESC, degree DESC LIMIT $n
"""

# 참조 기업 — 위험 축. **그 노드를 통해 워크스페이스로 들어오는** 위험만 센다
_REF_RISK = """
MATCH (m:Company)-[r]-(x:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(x.corp_code,x.norm_name) IN $k
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
WITH DISTINCT x, count(DISTINCT m) AS members
MATCH (x)-[:HAS_EVENT]->(e:Event {is_risk:true})
WITH x, members, count(e) AS risks, sum(coalesce(e.article_count,1)) AS weight
OPTIONAL MATCH (x)-[o]-()
RETURN coalesce(x.corp_code,x.norm_name) AS key, x.name AS name,
       x.entity_kind AS kind, x.ksic AS ksic, x.corp_code AS cc,
       members, risks, weight, count(o) AS degree
ORDER BY weight DESC, risks DESC LIMIT $n
"""


def _to_node(r: dict, role: str, *, island: bool = False) -> dict:
    return {
        "key": r["key"], "name": r["name"], "label": "Company",
        "role": role, "kind": "trade",
        "entity_kind": r.get("kind"),
        "ksic_label": label_of(r["ksic"]) if r.get("ksic") else None,
        "degree": r.get("degree") or 0,
        "is_island": island,
        "members": r.get("members"),
        "risk_weight": r.get("weight"),
        # ★더 받을 데가 있나 — corp_code 가 없으면 DART 로 받을 데가 없다
        "can_collect": bool(r.get("cc")),
    }


def workspace_graph(keys: list[str], *, expand: bool = True,
                    max_nodes: int = 150, refs: bool = False) -> dict:
    """담긴 기업들의 그래프.

    `refs=True` 면 **참조 기업을 두 축으로** 뽑아 붙인다(기본은 꺼짐 —
    담은 기업만 보는 게 시작점이다).
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {"nodes": [], "edges": [], "islands": [], "truncated": False, "omitted": {}}

    with neo4j_session() as s:
        base = _nodes(s, keys)
        nodes = {k: _to_node(v, "pinned") for k, v in base.items()}
        edges, connected = [], set()

        # ① 담긴 기업끼리 직접 이어진 엣지
        for r in s.run(_DIRECT, k=keys, types=_TRADE):
            rel = _relation({"key": r["ak"], "name": r["an"]},
                            {"key": r["bk"], "name": r["bn"]}, r["t"], dict(r["p"] or {}))
            if rel is None:
                continue
            edges.append({
                "edge_id": rel["edge_id"], "type": rel["type"], "subtype": rel["subtype"],
                "source": r["ak"], "target": r["bk"], "symmetric": rel["symmetric"],
                "freshness": rel["freshness"], "score": rel["score"],
            })
            connected.update((r["ak"], r["bk"]))

        islands = [k for k in keys if k not in connected]

        # ② 섬이 된 기업만 한 칸 건너 잇기 — 허브는 다리로 안 쓴다
        if expand and islands:
            for isl in list(islands):
                others = [k for k in keys if k != isl]
                for r in s.run(_BRIDGE, island=isl, others=others, all=keys,
                               types=_BRIDGE_TYPES, hub=_HUB,
                               bad_kinds=_NOT_BRIDGE_KIND):
                    if r["key"] in nodes:
                        continue
                    nodes[r["key"]] = _to_node(dict(r), "bridge")
                    # 다리 노드가 실제로 잇는 엣지를 채운다
                    for e in s.run("""
                            MATCH (m:Company)-[r]-(o:Company)
                            WHERE coalesce(m.corp_code,m.norm_name) = $mid
                              AND coalesce(o.corp_code,o.norm_name) IN $ks
                              AND type(r) IN $types
                            RETURN coalesce(startNode(r).corp_code,startNode(r).norm_name) AS ak,
                                   startNode(r).name AS an,
                                   coalesce(endNode(r).corp_code,endNode(r).norm_name) AS bk,
                                   endNode(r).name AS bn,
                                   type(r) AS t, properties(r) AS p""",
                            mid=r["key"], ks=keys + [r["key"]], types=_BRIDGE_TYPES):
                        rel = _relation({"key": e["ak"], "name": e["an"]},
                                        {"key": e["bk"], "name": e["bn"]},
                                        e["t"], dict(e["p"] or {}))
                        if rel is None:
                            continue
                        edges.append({
                            "edge_id": rel["edge_id"], "type": rel["type"],
                            "subtype": rel["subtype"], "source": e["ak"], "target": e["bk"],
                            "symmetric": rel["symmetric"], "freshness": rel["freshness"],
                            "score": rel["score"]})
                        connected.update((e["ak"], e["bk"]))
            islands = [k for k in keys if k not in connected]

        # 참조 기업 — 두 축에서 각각 상위 5곳
        if refs:
            for q in (_REF_STRUCT, _REF_RISK):
                for r in s.run(q, k=keys, types=_TRADE, n=5):
                    if r["key"] not in nodes:
                        nodes[r["key"]] = _to_node(dict(r), "neighbor")
                    else:
                        n = nodes[r["key"]]
                        n["members"] = n.get("members") or r.get("members")
                        n["risk_weight"] = n.get("risk_weight") or r.get("weight")

    # ③ 못 이은 것은 섬으로 두되 **표시한다**
    for k in islands:
        if k in nodes:
            nodes[k]["is_island"] = True

    out = list(nodes.values())
    truncated = len(out) > max_nodes
    if truncated:
        out.sort(key=lambda n: (n["role"] != "pinned", -(n["degree"] or 0)))
        keep = {n["key"] for n in out[:max_nodes]}
        out = out[:max_nodes]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]

    # 유형별로 몇 개를 뺐는지 — 조용히 자르면 화면은 그게 전부인 줄 안다
    omitted: dict[str, int] = {}
    seen = {(e["source"], e["target"], e["type"], e["edge_id"]) for e in edges}
    if len(seen) != len(edges):
        edges = [dict(t) for t in {tuple(sorted(e.items())) for e in edges}]

    return {"nodes": out, "edges": edges, "islands": islands,
            "truncated": truncated, "omitted": omitted}


# ══════════════════════════════════════════════════════════════════
#  추천 — 「같이 담을 만한 기업」
# ══════════════════════════════════════════════════════════════════

_SUGGEST = """
MATCH (m:Company)-[:SUPPLIES_TO]->(cust:Company)<-[:SUPPLIES_TO]-(peer:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(peer.corp_code,peer.norm_name) IN $k
WITH peer, count(DISTINCT cust) AS overlap, collect(DISTINCT cust.name)[0..5] AS via
OPTIONAL MATCH (peer)-[e]-()
RETURN coalesce(peer.corp_code,peer.norm_name) AS key, peer.name AS name,
       peer.ksic AS ksic, peer.corp_code AS cc, peer.is_stub AS stub,
       overlap, via, count(e) AS degree
ORDER BY overlap DESC, degree DESC LIMIT $n
"""


def suggest(keys: list[str], limit: int = 5) -> list[dict]:
    """**왜 추천인지를 반드시 함께 준다.**

    「한미반도체를 추천합니다」만으로는 담을 이유를 모른다.
    「공통 고객 4곳 — 삼성전자·SK하이닉스·마이크론·엔비디아」까지 줘야 판단이 된다.
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        return []
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_SUGGEST, k=keys, n=limit)]
    out = []
    for r in rows:
        via = list(r["via"])
        out.append({
            "key": r["key"], "name": r["name"], "reason": "shared_customer",
            "reason_text": f"공통 고객 {r['overlap']}곳 — {' · '.join(via)}",
            "overlap": r["overlap"], "via": via, "in_graph": True,
            "detail_level": "full" if r["stub"] is False else "relations_only",
            "ksic_label": label_of(r["ksic"]) if r["ksic"] else None,
        })
    return out


# ══════════════════════════════════════════════════════════════════
#  알림 — 그동안 무엇이 바뀌었나
# ══════════════════════════════════════════════════════════════════

_NEW_RISK = """
MATCH (c:Company)-[h:HAS_EVENT]->(e:Event {is_risk:true})
WHERE coalesce(c.corp_code,c.norm_name) IN $k
  AND e.first_seen >= date($since)
OPTIONAL MATCH (e)-[:IMPACTS]->(x:Company)
RETURN coalesce(c.corp_code,c.norm_name) AS ckey, c.name AS cname,
       e.event_id AS eid, e.name AS ename, e.event_type AS etype,
       toString(coalesce(h.occurred_at, e.first_seen)) AS at,
       count(DISTINCT x) AS affected
ORDER BY at DESC LIMIT 50
"""

_NEW_REL = """
MATCH (a:Company)-[r]->(b:Company)
WHERE (coalesce(a.corp_code,a.norm_name) IN $k OR coalesce(b.corp_code,b.norm_name) IN $k)
  AND type(r) IN $types
  AND r.first_seen >= date($since)
  AND coalesce(r.grounding_suspect,false) = false
RETURN coalesce(a.corp_code,a.norm_name) AS akey, a.name AS an, b.name AS bn,
       type(r) AS t, r.subtype AS st, toString(r.first_seen) AS at,
       r.evidence_id AS ev
ORDER BY at DESC LIMIT 50
"""


def changes(keys: list[str], since: str) -> dict:
    """마이페이지 알림 셋. **누구에게 보낼지는 우리가 모른다.**

    ★`relation_ended` 는 지금 **언제나 빈 배열**이다. 「이번 재적재에서 빠진
      관계 = 종료됨」으로 판정하는데 `loaded_at` 이 2026-07-31 도입이라
      비교 대상이 없다. 다음 DART 재적재 이후에 채워진다.
    """
    keys = list(dict.fromkeys(keys))
    out: list[dict] = []
    if not keys:
        return {"since": since, "total": 0, "changes": []}

    with neo4j_session() as s:
        for r in s.run(_NEW_RISK, k=keys, since=since):
            out.append({
                "kind": "new_risk_event", "company_key": r["ckey"],
                "company_name": r["cname"], "title": r["ename"],
                "detail": f"{r['etype']} · 영향 {r['affected']}곳" if r["affected"]
                          else r["etype"],
                "occurred_at": (r["at"] or "")[:10] or None, "ref_id": r["eid"],
            })
        for r in s.run(_NEW_REL, k=keys, since=since, types=_TRADE):
            sub = f" · {r['st']}" if r["st"] else ""
            out.append({
                "kind": "new_relation", "company_key": r["akey"],
                "company_name": r["an"],
                "title": f"{r['an']} → {r['bn']} 관계가 새로 확인됨",
                "detail": f"{r['t']}{sub}",
                "occurred_at": (r["at"] or "")[:10] or None, "ref_id": r["ev"],
            })

    out.sort(key=lambda c: c["occurred_at"] or "", reverse=True)
    return {"since": since, "total": len(out), "changes": out}
