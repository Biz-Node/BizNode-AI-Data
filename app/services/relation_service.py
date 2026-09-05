"""관계 하나 — **근거 원문까지.**

이 파일이 있는 이유

  워크스페이스에서 **선을 클릭하면** 이게 뜬다. 그리고 여기서 보여 줄 것의
  핵심은 관계 자체가 아니라 **근거 원문**이다.

      관계   SFA반도체 ─SUPPLIES_TO→ 삼성전자
      근거   「공급 관계 — 공급자: SFA반도체 / 수요자: 삼성전자(주)
             계약유형: 공급계약 / 체결: 1999-01 …」
      출처   사업보고서 접수번호 20260323000826

  우리가 요약한 문장이 아니라 **기사·공시에 실제로 쓰여 있는 문장**이라,
  화면이 그대로 인용할 수 있다. 이게 GraphRAG 의 「근거」다.

원문은 세 곳에 흩어져 있고 id 가 같다

      Neo4j 엣지 속성   evidence_id · evidence_ids · source_doc
      ChromaDB         원문 텍스트 (evidence 컬렉션 10,510건)
      PostgreSQL       언론사·보도일(news_articles) · 공시 제목(documents)

  세 곳의 id 가 같은 값이라 이어 붙일 수 있다.

ChromaDB 가 죽어 있으면 **빈 배열을 주지 않는다**

  근거 없는 관계는 애초에 응답에서 빠진다. 그러니 `evidence: []` 는
  「근거가 없다」로 읽힌다 — 사실은 「우리가 못 꺼냈다」인데. 거짓말하지 않고
  503 을 낸다.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.config import CHROMA_HOST, CHROMA_PORT
from app.core.database import neo4j_session, postgres_connection
from app.services.company_service import relation_row

# 원문 안에 URL 이 섞여 있다 — 「… 이다.  — 「제목」 https://...」
_URL = re.compile(r"https?://\S+")

# ★파급은 **공급 관계에서만** 계산된다. `propagate_risk` 가 타는 엣지가
#   `SUPPLIES_TO` 하나이기 때문이다(graph_service._PROPAGATE).
#   지분·의존을 여기 넣어 뒀더니 **반드시 0건인데 1.7초를 썼다.**
#   통로를 늘리려면 먼저 `_PROPAGATE` 가 그 엣지를 타야 한다.
_CHANNEL = frozenset({"SUPPLIES_TO"})

_EDGE_Q = """
MATCH (a)-[r]->(b) WHERE elementId(r) = $eid
RETURN type(r) AS t, properties(r) AS p, elementId(r) AS eid,
       coalesce(a.corp_code, a.norm_name, a.event_id, a.person_key, a.name) AS akey,
       coalesce(a.name, '?') AS aname, labels(a)[0] AS alabel,
       coalesce(b.corp_code, b.norm_name, b.event_id, b.person_key, b.name) AS bkey,
       coalesce(b.name, '?') AS bname, labels(b)[0] AS blabel
"""

# 이 관계의 **출발점에 붙은 위험 사건.** 이 선을 타고 번질 수 있는 것들.
_RISK_AT = """
MATCH (c:Company)-[:HAS_EVENT]->(e:Event)
WHERE coalesce(c.corp_code, c.norm_name) = $k AND e.is_risk
RETURN e.name AS name, e.event_id AS id,
       coalesce(e.occurred_at, e.last_seen) AS at
ORDER BY at DESC LIMIT $n
"""


_COL: Any = None


def _chroma():
    """근거 컬렉션. **한 번만 맺는다.**

    ★실측(2026-08-18): `HttpClient()` 생성이 **2.2초**다. 요청마다 새로 맺어서
      선을 한 번 클릭하는 데 1~2.9초가 걸렸다 — 정작 원문 꺼내기는 48ms 다.
      임베딩은 필요 없으니 OpenAI 클라이언트는 만들지 않는다.
    """
    global _COL
    if _COL is None:
        import chromadb

        _COL = chromadb.HttpClient(host=CHROMA_HOST,
                                   port=CHROMA_PORT).get_collection("evidence")
    return _COL


def evidence_texts(ids: list[str]) -> dict[str, dict]:
    """`evidence_id` → {text, meta}. **못 꺼내면 예외를 낸다.**"""
    if not ids:
        return {}
    got = _chroma().get(ids=ids)
    out: dict[str, dict] = {}
    for i, doc, md in zip(got["ids"], got["documents"], got["metadatas"]):
        out[i] = {"text": (doc or "").strip(), "meta": dict(md or {})}
    return out


def _press_of(urls: list[str]) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """기사 URL → (언론사, 보도일). 원문에 URL 이 박혀 있어 되짚을 수 있다."""
    if not urls:
        return {}
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT url, press, published_at FROM news_articles WHERE url = ANY(%s)",
                    (urls,))
        return {u: (p, str(d)[:10] if d else None) for u, p, d in cur.fetchall()}


def _title_of(rcept_nos: list[str]) -> dict[str, str]:
    if not rcept_nos:
        return {}
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT rcept_no, title FROM documents WHERE rcept_no = ANY(%s)",
                    (rcept_nos,))
        return dict(cur.fetchall())


def _evidence(props: dict, etype: str) -> list[dict]:
    """엣지에 달린 근거 전부. **한 관계에 여러 근거가 붙을 수 있다**(875건)."""
    ids: list[str] = []
    for v in (props.get("evidence_id"), *(props.get("evidence_ids") or [])):
        if v and v not in ids:
            ids.append(str(v))
    return evidence_for_ids(
        ids,
        fallback_source_type=props.get("source_type"),
        fallback_source_doc=str(props.get("source_doc") or ""),
    )


def evidence_for_ids(
    ids: list[str], *,
    fallback_source_type: Optional[str] = None,
    fallback_source_doc: str = "",
) -> list[dict]:
    """`evidence_id` 목록 → `Evidence` 모양 dict 목록. **한 번에 모아 조회한다.**

    ★관계마다 부르면 근거 조회·언론사 조회·공시제목 조회가 관계 수만큼 반복된다.
      챗봇 재료는 여러 기업 × 여러 관계를 한 응답에 담으므로 그 반복이 그대로
      드러난다. 그래서 id 를 다 모아 **한 번**에 부르는 입구를 따로 둔다.

    `_evidence()`가 이 함수를 부른다 — 조립 규칙이 두 곳으로 갈라지지 않게 한다.

    ★못 꺼낸 근거를 **조용히 빼지 않는다.** `missing=True` 로 남긴다 —
      빼면 「근거가 없는 관계」로 읽힌다.
    """
    ids = [str(i) for i in dict.fromkeys(i for i in ids if i)]
    if not ids:
        return []

    texts = evidence_texts(ids)
    urls, rcepts = [], []
    for e in texts.values():
        m = _URL.search(e["text"])
        if m:
            urls.append(m.group(0).rstrip(")）」"))
        rn = str(e["meta"].get("rcept_no") or "")
        if rn:
            rcepts.append(rn)
    press = _press_of(urls)
    titles = _title_of(rcepts)

    out = []
    for eid in ids:
        e = texts.get(eid)
        if e is None:                       # 컬렉션에 없는 id — 숨기지 않고 알린다
            out.append({"evidence_id": eid, "text": "", "source_doc": "",
                        "source_type": fallback_source_type or "news",
                        "press": None, "published_at": None,
                        "missing": True})
            continue
        md = e["meta"]
        m = _URL.search(e["text"])
        url = m.group(0).rstrip(")）」") if m else None
        rn = str(md.get("rcept_no") or "")
        st = md.get("source_type") or fallback_source_type or ("dart" if rn else "news")
        p, d = press.get(url or "", (None, None))
        at = md.get("occurred_at")
        if not d and at and int(at) > 19000000:
            s = str(int(at))
            d = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        out.append({
            "evidence_id": eid,
            "text": e["text"],
            # ★출처는 **되짚을 수 있는 값**이어야 한다. 공시면 접수번호, 기사면 URL
            "source_doc": rn or url or fallback_source_doc,
            "source_type": "dart" if st.startswith("dart") else "news",
            "press": p or (titles.get(rn) if rn else None),
            "published_at": d,
            "missing": False,
        })
    return out


def _propagation(src: dict, tgt: dict, etype: str, *, max_events: int = 3) -> list[dict]:
    """이 선을 타고 닿는 위험.

    ★**양쪽 끝을 다 본다.** 한쪽만 보면 절반을 놓친다 —
      「한성크린텍 → 삼성전자」 공급 관계에서 한성크린텍에 사건이 없어도,
      **삼성전자의 사건은 한성크린텍의 매출을 때린다**(매출 상실).
      공급 관계는 위험을 양방향으로 실어 나른다.

    ★사건 전부를 돌리지 않는다. 삼성전자는 사건이 148건이라 화면이 못 기다린다.
      끝마다 최근 `max_events` 건만 본다.
    """
    if etype not in _CHANNEL:
        return []                            # 통로가 아닌 관계는 계산하지 않는다
    from app.services.graph_service import propagate_risk

    out, seen = [], set()
    for a, b in ((src, tgt), (tgt, src)):
        if a["label"] != "Company" or b["label"] != "Company":
            continue
        with neo4j_session() as s:
            events = [dict(r) for r in s.run(_RISK_AT, k=a["key"], n=max_events)]
        for ev in events:
            # ★`only` 로 **Cypher 에서** 걸러야 한다. 전부 계산한 뒤 파이썬에서
            #   버리면 계산은 이미 끝나 있다 — 사건 하나가 129곳을 낸다.
            for p in propagate_risk(ev["name"], only=b["name"]):
                if p.target != b["name"]:
                    continue                 # 이 선의 반대쪽 끝에 닿은 것만
                k = (ev["id"], p.target, p.hops)
                if k in seen:
                    continue
                seen.add(k)
                out.append({
                    "event_id": ev["id"], "event": ev["name"],
                    "target": p.target, "key": b["key"],
                    "score": round(p.score, 3), "hops": p.hops,
                    # ★기사가 말한 것인지 우리가 계산한 것인지 **반드시 가른다.**
                    "stated": p.stated, "channel": p.channel or None,
                    "path": list(p.path),
                })
    out.sort(key=lambda x: -x["score"])
    return out


_EVENT_NAME = """
MATCH (e:Event) WHERE e.event_id = $id OR e.name = $id
RETURN e.name AS name, e.event_id AS id, e.is_risk AS is_risk LIMIT 1
"""

# 이름 → 키. 파급 결과는 이름만 들고 오는데 화면은 **눌러서 넘어가야** 한다.
_KEY_OF = """
UNWIND $names AS nm
MATCH (c:Company) WHERE c.name = nm
RETURN nm AS name, coalesce(c.corp_code, c.norm_name) AS key
"""


def event_impact(event_id: str, max_hops: int = 3) -> Optional[list[dict]]:
    """사건 하나가 그래프를 타고 어디까지 번지나. 사건이 없으면 None.

    ★1홉은 **기사가 말한 것**, 2홉은 **우리가 공급망으로 계산한 것**이다.
      실측(모트라스 파업): 124곳 = 보도 10곳 + 계산 114곳. 섞어 보내면
      화면이 둘을 같은 무게로 그린다.
    """
    from app.services.graph_service import propagate_risk

    with neo4j_session() as s:
        row = s.run(_EVENT_NAME, id=event_id).single()
        if row is None:
            return None
        name = row["name"]

    got = [p for p in propagate_risk(name) if p.hops <= max_hops]
    with neo4j_session() as s:
        keys = {r["name"]: r["key"]
                for r in s.run(_KEY_OF, names=[p.target for p in got])}
    return [{
        "target": p.target, "key": keys.get(p.target),
        "score": round(p.score, 3), "hops": p.hops,
        "stated": p.stated, "channel": p.channel or None, "path": list(p.path),
        "edge_ids": list(p.edge_ids),
    } for p in got]


def relation_detail(edge_id: str) -> Optional[dict]:
    """선을 클릭했을 때. 없으면 None."""
    with neo4j_session() as s:
        row = s.run(_EDGE_Q, eid=edge_id).single()
    if row is None:
        return None
    r = dict(row)
    props = dict(r["p"] or {})
    src = {"key": r["akey"], "name": r["aname"], "label": r["alabel"]}
    tgt = {"key": r["bkey"], "name": r["bname"], "label": r["blabel"]}

    rel = relation_row(src, tgt, r["t"], props, eid=r["eid"])
    # ★근거 검증에서 걸렸거나 종료된 관계는 **목록에 안 나오니 상세도 없다.**
    #   여기서 통과시키면 화면이 감춘 관계를 열 수 있게 된다.
    if rel is None:
        return None

    return {
        "relation": rel,
        "evidence": _evidence(props, r["t"]),
        "propagation": _propagation(src, tgt, r["t"]),
    }
