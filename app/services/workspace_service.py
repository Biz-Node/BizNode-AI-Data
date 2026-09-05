"""워크스페이스 — **담은 기업들을 하나로 놓고 보는 것.**

그래프를 그리는 규칙 셋

    ① 담긴 기업끼리 직접 이어진 엣지       언제나 포함
    ② 섬이 된 기업만 한 칸 건너 잇기        **허브는 다리로 안 씀**
    ③ 그래도 못 이으면 섬으로 두되 표시     **억지로 잇지 않음**

  ②가 필요한 이유를 실측으로 확인했다. 「고영」을 나머지와 이어 주는 중간 노드가
  **삼성자산운용**(연결 32)이었는데, 자산운용사가 **양쪽에 지분이 있을 뿐**이지
  두 회사가 관계있다는 뜻이 아니다. 다리로 쓰면 펀드 하나가 모든 회사를 이어버린다.

  ③이 더 중요하다. **없는 관계를 그리는 것보다 없다고 말하는 게 낫다.**

참조 기업은 두 축으로 뽑는다

    구조 축   담은 기업 **몇 곳과** 이어지나       `members`
    위험 축   그 노드를 통해 들어오는 위험의 크기    `risk_weight`

  「그 회사가 겪은 사건 수」가 아니다. 현대모비스는 사건이 19건이지만 메모리
  워크스페이스와는 약하게 이어져 있어 들어올 게 없다.

`can_collect` 의 진짜 뜻

  「담을 수 없음」의 이유는 해외라서가 아니라 **DART 에 없어서 더 받을 데가
  없다**는 것이다. `corp_code` 유무로 판정한다 — `entity_kind='해외'` 는
  9곳뿐이라 쓸 수 없다.
"""

from __future__ import annotations


from app.core.database import neo4j_session
from app.core.trace import trace_logger
from app.services import company_service
from app.services.company_service import relation_row
from pipeline.normalizer.ksic import label_of

log = trace_logger(__name__)

# 다리로 쓰지 않을 노드의 차수. 삼성전자(1,169)를 다리로 허용하면 거의 모든
# 쌍이 이어져 **의미가 0인 그래프**가 된다.
_HUB = 150

# 축마다 뽑을 참조 기업 수. 두 축 합쳐 최대 10곳이 붙는다.
_PER_AXIS = 5

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


def names_of(keys: list[str]) -> dict[str, str]:
    """워크스페이스 key → **표시용 이름**(설계서 §16-3).

    `workspace_keys` 는 키만 온다. 그런데 답변 문구(§14-4)·앵커 2차 대조(§14-7)·
    워크스페이스 소속 표기(§12)는 이름을 쓴다. 그 이름을 **우리가 조회한다** —
    백엔드가 밀어 주지 않는다.

    ★**경계에서 한 번만 부른다.** flow ①b 는 이 결과를 메모리에서 대조만 해야
      한다(설계서 §10 ①b 의 금지사항 「새 검색을 하지 않는다」).

    ★**못 찾은 key 를 조용히 지우지 않는다.** 그래프에 없는 기업이 워크스페이스에
      담겨 있을 수 있다 — 이름 자리에 key 를 그대로 두고 로그에 남긴다
      ([규칙 2](../../docs/BizNode_Search_설계.md)).

    ★**이름은 식별 기준이 아니다.** 식별은 key 이고(`corp_code` → `norm_name`),
      이름은 표시·해석용이다(설계서 §16-1).

    ★조회는 `company_service.names_by_keys()` 가 한다 — 같은 질의를 두 벌 두면
      key 판별 규약이 갈린다. 여기서 얹는 것은 **못 찾은 key 의 처리**뿐이다.
      `_nodes()` 를 안 쓰는 이유는 비용이다 — 저쪽은 `OPTIONAL MATCH (c)-[e]-()`
      로 차수까지 세서 3.6배 비싸다(실측 2026-08-25: 8.7ms → 2.4ms).
    """
    unique = list(dict.fromkeys(k for k in keys if k))
    if not unique:
        return {}
    found = company_service.names_by_keys(unique)

    missing = [k for k in unique if k not in found]
    if missing:
        log.info("workspace.names unnamed=%d keys=%s", len(missing), missing)
    return {k: found.get(k) or k for k in unique}


_DIRECT = """
MATCH (a:Company)-[r]->(b:Company)
WHERE coalesce(a.corp_code,a.norm_name) IN $k
  AND coalesce(b.corp_code,b.norm_name) IN $k
  AND type(r) IN $types
RETURN coalesce(a.corp_code,a.norm_name) AS ak, a.name AS an,
       coalesce(b.corp_code,b.norm_name) AS bk, b.name AS bn,
       type(r) AS t, properties(r) AS p, elementId(r) AS eid
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
  // ★고를 때도 **그릴 때와 같은 기준**으로 걸러야 한다. 근거 없는 관계·종료된
  //   관계는 응답의 엣지에서 빠지므로, 그런 엣지로 다리를 고르면 화면에
  //   **한쪽에만 붙은 다리**가 생긴다 — 다리인데 잇지를 못한다
  //   (2026-08-17 실측: ASE코리아·삼성전기·지멘스·큐렉소).
  AND coalesce(r1.grounding_suspect,false) = false
  AND coalesce(r2.grounding_suspect,false) = false
  AND coalesce(r1.is_current, true) = true
  AND coalesce(r2.is_current, true) = true
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
#
# ★위험도 **함께 잰다.** 구조 축으로 뽑혔다고 위험을 안 재면 화면에서
#   `risk_weight: null` 이 「위험 없음」으로 오독된다. **안 잰 것과 없는 것은 다르다.**
#
# ★**거래 관계로만 센다**(`_BRIDGE_TYPES`). 지분으로 이어진 것을 세면 실측처럼
#   **삼성자산운용(members=5)·국민연금공단(members=3)** 이 참조 기업 상위에 올라온다
#   (2026-08-16). 담긴 5곳 전부의 5%이상주주라 「가장 많이 이어진 곳」이 되지만,
#   **주주는 밖에서 영향을 주는 상대가 아니다.** 다리에서 막은 것과 같은 이유다.
#
#   삼성전자처럼 지분도 있고 거래도 있는 곳은 거래로 잡히므로 안 빠진다.
_REF_STRUCT = """
MATCH (m:Company)-[r]-(x:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(x.corp_code,x.norm_name) IN $k
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
  // ★엣지 목록과 **같은 기준**으로 걸러야 한다. 종료된 관계(is_current=false,
  //   valid_until 경과)는 응답의 엣지에서 빠지는데 여기서 세면 `members` 가
  //   실제 그려지는 선보다 커진다 — 실측(2026-08-16): SFA반도체가 members=3 인데
  //   선은 2개였다(한미반도체와의 관계가 종료 상태).
  AND coalesce(r.is_current, true) = true
  AND (r.valid_until IS NULL OR r.valid_until >= date())
WITH x, count(DISTINCT m) AS members
OPTIONAL MATCH (x)-[e]-()
WITH x, members, count(e) AS degree
RETURN coalesce(x.corp_code,x.norm_name) AS key, x.name AS name,
       x.entity_kind AS kind, x.ksic AS ksic, x.corp_code AS cc,
       members, degree
ORDER BY members DESC, degree DESC LIMIT $n
"""

# 참조 **후보가 몇 곳인지.** 상위 5곳씩만 보내므로 「몇 곳 중 몇 곳」을 알려야 한다.
_REF_COUNT = """
MATCH (m:Company)-[r]-(x:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(x.corp_code,x.norm_name) IN $k
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
  AND coalesce(r.is_current, true) = true
  AND (r.valid_until IS NULL OR r.valid_until >= date())
RETURN count(DISTINCT x) AS n
"""

# 참조·다리 노드가 **담은 기업 몇 곳과 이어지나** — 다시 센다.
#
# ★뽑을 때 쓴 유형(거래만)과 **그릴 때 쓴 유형(거래+지분+소송)이 달라서**
#   `members` 가 화면의 선 개수와 어긋났다(2026-08-17 실측: 파두 members=1 인데
#   선은 2개, 삼성전자 members=4 인데 5개). 뽑는 기준은 그대로 두고
#   **세는 기준만 그리는 것과 맞춘다.**
_MEMBER_FILL = """
MATCH (x:Company)-[r]-(m:Company)
WHERE coalesce(x.corp_code,x.norm_name) IN $refs
  AND coalesce(m.corp_code,m.norm_name) IN $pinned
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
  AND coalesce(r.is_current, true) = true
  AND (r.valid_until IS NULL OR r.valid_until >= date())
RETURN coalesce(x.corp_code,x.norm_name) AS key, count(DISTINCT m) AS members
"""

# 뽑힌 참조 기업들의 **위험을 한 번에 잰다.** 어느 축으로 뽑혔든 채운다.
_REF_RISK_FILL = """
MATCH (x:Company)-[:HAS_EVENT]->(e:Event {is_risk:true})
WHERE coalesce(x.corp_code,x.norm_name) IN $refs
RETURN coalesce(x.corp_code,x.norm_name) AS key,
       count(e) AS risks, sum(coalesce(e.article_count,1)) AS raw
"""

# 참조 기업 — 위험 축.
#
# ★「그 회사가 겪은 사건 수」로 세면 안 된다. 처음엔 그렇게 짰다가 반도체
#   워크스페이스에 **현대모비스(사건 19건)·현대자동차·LG전자**가 상위로 올라왔다.
#   담긴 기업과 한 가닥으로만 이어져 있어 **들어올 게 없는데도** 사건이 많다는
#   이유로 뽑힌 것이다.
#
#   그런데 **`members` 를 곱하면 안 된다.** 한 번 그렇게 고쳤다가 두 축이
#   같은 답을 냈다(2026-08-17 실측: 소재 워크스페이스에서 **5/5 완전 중복**,
#   로봇 4/5). 연결이 많은 곳이 위험도 커 보이니 구조 축과 순서가 같아진다 —
#   **축을 둘로 나눈 의미가 없어진다.**
#
#   연결은 **이미 조건으로 걸려 있다**(담긴 기업과 거래 관계가 있어야 후보다).
#   그 위에서는 **위험 자체로만** 줄 세운다.
#
# ★거래 관계로만 센다. 지분(OWNS_STAKE_IN)으로 이어진 것은 위험이 흐르는 길이
#   아니다 — 주주가 사고를 내도 그 회사의 공급이 끊기지 않는다.
_RISK_TYPES = ["SUPPLIES_TO", "PARTNERS_WITH", "COMPETES_WITH", "DEPENDS_ON", "ACQUIRES"]

_REF_RISK = """
MATCH (m:Company)-[r]-(x:Company)
WHERE coalesce(m.corp_code,m.norm_name) IN $k
  AND NOT coalesce(x.corp_code,x.norm_name) IN $k
  AND type(r) IN $types
  AND coalesce(r.grounding_suspect,false) = false
  // ★엣지 목록과 **같은 기준**으로 걸러야 한다. 종료된 관계(is_current=false,
  //   valid_until 경과)는 응답의 엣지에서 빠지는데 여기서 세면 `members` 가
  //   실제 그려지는 선보다 커진다 — 실측(2026-08-16): SFA반도체가 members=3 인데
  //   선은 2개였다(한미반도체와의 관계가 종료 상태).
  AND coalesce(r.is_current, true) = true
  AND (r.valid_until IS NULL OR r.valid_until >= date())
WITH DISTINCT x, count(DISTINCT m) AS members
MATCH (x)-[:HAS_EVENT]->(e:Event {is_risk:true})
WITH x, members, count(e) AS risks, sum(coalesce(e.article_count,1)) AS raw
OPTIONAL MATCH (x)-[o]-()
RETURN coalesce(x.corp_code,x.norm_name) AS key, x.name AS name,
       x.entity_kind AS kind, x.ksic AS ksic, x.corp_code AS cc,
       members, risks, raw AS weight, count(o) AS degree
ORDER BY weight DESC, members DESC LIMIT $n
"""


# 참조 기업이 **담긴 기업과 어떻게 이어지는지**. 참조끼리의 엣지는 안 가져온다 —
# 화면이 복잡해지기만 하고 「밖에서 오는 영향」을 보는 데 도움이 안 된다.
_REF_EDGES = """
MATCH (a:Company)-[r]->(b:Company)
WHERE type(r) IN $types
  AND ((coalesce(a.corp_code,a.norm_name) IN $refs AND
        coalesce(b.corp_code,b.norm_name) IN $pinned)
    OR (coalesce(a.corp_code,a.norm_name) IN $pinned AND
        coalesce(b.corp_code,b.norm_name) IN $refs))
RETURN coalesce(a.corp_code,a.norm_name) AS ak, a.name AS an,
       coalesce(b.corp_code,b.norm_name) AS bk, b.name AS bn,
       type(r) AS t, properties(r) AS p, elementId(r) AS eid
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
        return {"nodes": [], "edges": [], "islands": [], "truncated": False,
                "omitted": {}, "ref_candidates": 0, "unknown_keys": []}

    with neo4j_session() as s:
        base = _nodes(s, keys)
        nodes = {k: _to_node(v, "pinned") for k, v in base.items()}
        edges, connected = [], set()

        # ① 담긴 기업끼리 직접 이어진 엣지
        for r in s.run(_DIRECT, k=keys, types=_TRADE):
            rel = relation_row({"key": r["ak"], "name": r["an"]},
                            {"key": r["bk"], "name": r["bn"]}, r["t"],
                            dict(r["p"] or {}), eid=r["eid"])
            if rel is None:
                continue
            edges.append({
                "edge_id": rel["edge_id"], "evidence_id": rel["evidence_id"],
                "type": rel["type"], "subtype": rel["subtype"],
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
                                   type(r) AS t, properties(r) AS p,
                                   elementId(r) AS eid""",
                            mid=r["key"], ks=keys + [r["key"]], types=_BRIDGE_TYPES):
                        rel = relation_row({"key": e["ak"], "name": e["an"]},
                                        {"key": e["bk"], "name": e["bn"]},
                                        e["t"], dict(e["p"] or {}), eid=e["eid"])
                        if rel is None:
                            continue
                        edges.append({
                            "edge_id": rel["edge_id"], "evidence_id": rel["evidence_id"],
                            "type": rel["type"],
                            "subtype": rel["subtype"], "source": e["ak"], "target": e["bk"],
                            "symmetric": rel["symmetric"], "freshness": rel["freshness"],
                            "score": rel["score"]})
                        connected.update((e["ak"], e["bk"]))
            islands = [k for k in keys if k not in connected]

        # 참조 기업 — 두 축에서 각각 상위 5곳
        ref_keys: list[str] = []
        ref_candidates = 0
        if refs:
            ref_candidates = s.run(_REF_COUNT, k=keys,
                                   types=_BRIDGE_TYPES).single()["n"]
            # ★두 축이 겹치면 **다음 순위로 채운다.**
            #   명세는 「각 축 상위 5곳 → 7~11곳이 붙는다」인데, 겹치는 걸 그냥
            #   두면 소재 워크스페이스처럼 **5곳만** 나온다(5/5 중복).
            #   그래서 축마다 넉넉히 가져와 **아직 안 뽑힌 것으로 5곳을 채운다.**
            for q, tp in ((_REF_STRUCT, _BRIDGE_TYPES), (_REF_RISK, _RISK_TYPES)):
                taken = 0
                for r in s.run(q, k=keys, types=tp, n=_PER_AXIS * 4):
                    if taken >= _PER_AXIS:
                        break
                    if r["key"] in nodes:
                        # 이미 있는 노드면 다른 축의 지표만 채우고 **자리는 안 쓴다**
                        n = nodes[r["key"]]
                        if n["role"] != "pinned":
                            n["members"] = n.get("members") or r.get("members")
                        continue
                    nodes[r["key"]] = _to_node(dict(r), "neighbor")
                    ref_keys.append(r["key"])
                    taken += 1

            # ★참조 기업의 **엣지도 가져와야 한다.**
            #
            #   처음엔 노드만 붙이고 끝냈다가, 화면에 **떠 있는 점 10개**가 됐다
            #   (2026-08-16). 마이크론이 `members=4` 라고 말하면서 정작 그 4곳과
            #   잇는 선이 하나도 없었다 — 「몇 곳과 이어졌나」를 세어 놓고
            #   **어디로 이어졌는지는 안 준** 셈이다.
            if ref_keys:
                for e in s.run(_REF_EDGES, refs=ref_keys, pinned=keys, types=_TRADE):
                    rel = relation_row({"key": e["ak"], "name": e["an"]},
                                    {"key": e["bk"], "name": e["bn"]}, e["t"],
                                    dict(e["p"] or {}), eid=e["eid"])
                    if rel is None:
                        continue
                    edges.append({
                        "edge_id": rel["edge_id"], "evidence_id": rel["evidence_id"],
                        "type": rel["type"], "subtype": rel["subtype"],
                        "source": e["ak"], "target": e["bk"],
                        "symmetric": rel["symmetric"], "freshness": rel["freshness"],
                        "score": rel["score"]})

        # ★담은 기업이 아닌 노드(참조·다리)는 **전부 위험을 채운다.**
        #   `refs` 여부와 무관하다 — 다리 노드도 화면에 그려지므로 위험을 알아야 하고,
        #   `null`(안 쟀다)과 `0`(없다)이 모드에 따라 달라지면 안 된다.
        others = [k for k, n in nodes.items() if n["role"] != "pinned"]
        if others:
            for r in s.run(_REF_RISK_FILL, refs=others):
                n = nodes.get(r["key"])
                if not n:
                    continue
                n["risk_count"] = r["risks"]
                # 위험 = 리스크 사건의 기사 수 합. **연결 수를 곱하지 않는다**(위 주석)
                n["risk_weight"] = int(r["raw"])
            for k in others:
                if nodes[k].get("risk_weight") is None:
                    nodes[k]["risk_weight"] = 0
                    nodes[k]["risk_count"] = 0

    # 같은 엣지가 두 번 담기는 경우를 지운다 — 다리 노드를 채울 때 겹칠 수 있다
    seen_ids: set[str] = set()
    uniq = []
    for e in edges:
        if e["edge_id"] in seen_ids:
            continue
        seen_ids.add(e["edge_id"])
        uniq.append(e)
    edges = uniq

    out = list(nodes.values())
    omitted: dict[str, int] = {}

    truncated = len(out) > max_nodes
    if truncated:
        # 담은 기업이 먼저, 그다음 연결이 많은 순으로 남긴다
        out.sort(key=lambda n: (n["role"] != "pinned", -(n["degree"] or 0)))
        keep = {n["key"] for n in out[:max_nodes]}
        dropped = [n for n in out[max_nodes:]]
        out = out[:max_nodes]

        # ★무엇이 잘렸는지 **말한다.** 조용히 자르면 화면은 그게 전부인 줄 안다.
        #   노드는 역할별로, 엣지는 유형별로 센다.
        for n in dropped:
            k = f"node:{n['role']}"
            omitted[k] = omitted.get(k, 0) + 1
        kept_edges = []
        for e in edges:
            if e["source"] in keep and e["target"] in keep:
                kept_edges.append(e)
            else:
                omitted[e["type"]] = omitted.get(e["type"], 0) + 1
        edges = kept_edges

    # ③ 못 이은 것은 섬으로 두되 **표시한다**
    #
    # ★섬은 **맨 마지막에** 센다. 응답에 실제로 나가는 노드·엣지로만 판정해야 한다.
    #
    #   두 번 틀렸다(2026-08-16):
    #     ㄱ. 참조 기업을 붙이기 **전**에 세어, 참조가 이어 준 노드가 여전히
    #         섬으로 남았다(소재·부품 5곳이 엣지 20개로 이어졌는데도 섬 5).
    #     ㄴ. `max_nodes` 로 자르기 **전**에 세어, **잘려 나간 노드**를 섬이라고
    #         가리켰다(노드 3개인데 islands 가 없는 키를 담고 있었다).
    #
    #   뜻은 하나로 못 박는다 — **「응답에 있으면서 선이 하나도 없는 노드」**.
    #   담은 기업끼리 직접 연결이 없는지는 화면이 엣지 양 끝이 모두 `pinned` 인
    #   것을 세어 알 수 있다.
    # ★`members` 는 **응답에 실제로 실린 엣지에서 센다.** 별도 질의로 세면
    #   기준이 갈려 계속 어긋난다 — 두 번 겪었다(2026-08-17):
    #     ㄱ. 뽑을 땐 거래 유형만, 그릴 땐 지분·소송까지 → 세는 게 더 적었다
    #     ㄴ. `grounding_suspect` 인데 `wrong_type` 인 관계는 **그리되 안 셌다**
    #   질의를 아무리 맞춰도 필터가 하나 늘 때마다 또 어긋난다.
    #   **셀 것과 그릴 것이 같은 목록이면 어긋날 수가 없다.**
    pinned_keys = {n["key"] for n in out if n["role"] == "pinned"}
    for n in out:
        if n["role"] == "pinned":
            continue
        n["members"] = len({
            (e["target"] if e["source"] == n["key"] else e["source"])
            for e in edges if n["key"] in (e["source"], e["target"])
        } & pinned_keys)

    linked: set[str] = set()
    for e in edges:
        linked.update((e["source"], e["target"]))
    islands = [n["key"] for n in out if n["key"] not in linked]
    island_set = set(islands)
    for n in out:
        n["is_island"] = n["key"] in island_set

    # ★그래프에 없는 키를 **조용히 버리지 않는다.** 검색은 DART 명부까지
    #   보여 주므로(`in_graph=false`) 아직 수집 안 한 회사가 담겨 올 수 있다.
    #   말없이 빼면 화면이 「담았는데 왜 안 보이지」를 설명할 수 없다.
    return {"nodes": out, "edges": edges, "islands": islands,
            "truncated": truncated, "omitted": omitted,
            "ref_candidates": ref_candidates,
            "unknown_keys": [k for k in keys if k not in base]}


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
            # 추천 목록에서는 재무 보유 여부를 안 읽는다 — `partial` 을 가르려면
            # PG 를 한 번 더 쳐야 하고, 추천은 「담을까 말까」라 그만큼이 필요 없다.
            "detail_level": "full" if r["stub"] is False else "none",
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
