"""기업 조회 — **Neo4j 와 PostgreSQL 을 합쳐 한 덩어리로.**

★이 계층이 하는 일은 「무엇을 감출지」다

  조회 결과에는 내부 검사 흔적이 붙어 있다. 그대로 내보내면 화면에 샌다.

      grounding_suspect      그 관계를 **아예 응답에서 뺀다**
      confidence × 신선도     `score` 하나로 합쳐서
      revenue_trusted=false  금액 빼고 비중만
      listed_shares.suspect  시총·PER·PBR·PSR 을 안 보낸다

  백엔드가 Cypher 를 직접 짜면 이 판단을 매번 다시 구현해야 하고,
  **그러면 화면마다 결과가 달라진다.** 그래서 통로를 하나로 둔다.

★비율은 계산해서 준다

  ROE·ROA·영업이익률·부채비율을 저장하지 않는다. 저장하면 원본이 갱신될 때
  어긋나고, 화면마다 다르게 계산하면 **같은 회사가 화면마다 다른 ROE 를 갖는다.**

★없는 것과 모르는 것을 가른다

  `detail_level` 하나로는 부족해서 `blocks` 로 블록마다 표시한다.
  「재무는 있는데 공시가 없다」가 흔한데 통짜 등급으로는 표현이 안 된다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.database import neo4j_session, postgres_connection
from pipeline.freshness import assess
from pipeline.normalizer.ksic import label_of

# 근거 검증에서 이 판정이 난 관계는 **응답에서 아예 뺀다.**
# `wrong_type` 은 빼지 않는다 — 관계 자체는 실재하므로 점수만 깎는다.
_HIDE = frozenset({"unfounded", "insufficient"})
_WRONG_TYPE_PENALTY = 0.5

# 관계 목록에서 제외할 신선도. 끝난 관계는 현재 질의의 답이 아니다.
_DROP_STATUS = frozenset({"expired"})

_SYMMETRIC = frozenset({"PARTNERS_WITH", "COMPETES_WITH"})

# 그래프 범례를 가르는 값 — `label` 만으로는 Company 가 거래처인지 주주인지 모른다
_KIND = {
    "OWNS_STAKE_IN": "ownership", "ACQUIRES": "ownership",
    "IS_EXECUTIVE_OF": "person",
    "HAS_EVENT": "event", "IMPACTS": "event",
    "DEVELOPS": "product", "DEPENDS_ON": "product",
    "REGULATES": "org",
}


# ══════════════════════════════════════════════════════════════════
#  공통
# ══════════════════════════════════════════════════════════════════


def _node(session, key: str) -> Optional[dict]:
    """`key` 는 `corp_code` 이거나 `norm_name` 이다. 둘 다로 찾는다."""
    row = session.run(
        """MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
           RETURN properties(c) AS p LIMIT 1""", k=key).single()
    return dict(row["p"]) if row else None


def _verdict(p: dict) -> str:
    if not p.get("grounding_suspect"):
        return "supported"
    return p.get("grounding_stage1") or "unfounded"


def _relation(src: dict, tgt: dict, etype: str, p: dict,
              today: Optional[date] = None, *, eid: str = "") -> Optional[dict]:
    """엣지 속성 → API 관계. **감출 것은 여기서 감춘다.** 빼야 하면 None."""
    # ★자기 자신을 잇는 엣지는 **그릴 수 없다.** 힘기반 레이아웃이 깨지고
    #   「A가 A에게 공급」은 화면에서 뜻이 없다.
    #   실측(2026-08-17): 로보티즈가 자기 자신의 자회사·피인수로 들어가 있다
    #   (전체 2건). **데이터 오류지만 API 가 방어한다** — 노드 병합이 또 만들 수 있다.
    if src.get("key") and src["key"] == tgt.get("key"):
        return None
    if _verdict(p) in _HIDE:
        return None
    fr = assess(p, today=today)
    if fr.status in _DROP_STATUS:
        return None

    conf = float(p.get("confidence") or 0.7)
    corr = int(p.get("corroboration") or 1)
    corr_boost = 1.0 + min(max(corr - 1, 0), 4) * 0.05
    pen = _WRONG_TYPE_PENALTY if _verdict(p) == "wrong_type" else 1.0
    score = round(min(conf * fr.confidence_factor * corr_boost * pen, 1.0), 3)

    cycle = p.get("refresh_cycle_days")
    left = None
    if cycle is not None and fr.days_since is not None:
        left = int(cycle) - int(fr.days_since)

    return {
        # ★`edge_id` 는 **엣지 자체의 유일한 id**(Neo4j elementId)다.
        #   `evidence_id` 를 쓰면 안 된다 — 한 근거가 여러 관계를 뒷받침해서
        #   유일하지 않다(11,060 엣지에 근거 9,228개).
        "edge_id": eid or p.get("evidence_id") or "",
        "evidence_id": p.get("evidence_id"),
        "type": etype,
        "subtype": p.get("subtype") or None,
        "source": {"key": src["key"], "name": src["name"], "label": src.get("label", "Company")},
        "target": {"key": tgt["key"], "name": tgt["name"], "label": tgt.get("label", "Company")},
        "symmetric": etype in _SYMMETRIC,
        "amount": int(p["amount"]) if p.get("amount") is not None else None,
        "ratio": float(p["ratio"]) if p.get("ratio") is not None else None,
        "freshness": fr.status,
        "last_seen": str(p.get("last_seen"))[:10] if p.get("last_seen") else None,
        "valid_from": str(p.get("valid_from"))[:10] if p.get("valid_from") else None,
        "valid_until": str(p.get("valid_until"))[:10] if p.get("valid_until") else None,
        "score": score,
        "corroboration": corr,
        "source_type": p.get("source_type") or "news",
        "refresh_cycle_days": int(cycle) if cycle is not None else None,
        "days_since": fr.days_since,
        "days_until_refresh": left,
    }


def _key_of(n: dict) -> str:
    return n.get("corp_code") or n.get("norm_name") or n.get("name") or ""


# ══════════════════════════════════════════════════════════════════
#  재무 — 비율은 계산해서 준다
# ══════════════════════════════════════════════════════════════════


def _pct(num, den) -> Optional[float]:
    if num is None or not den:
        return None
    return round(float(num) / float(den) * 100, 2)


def _financials(cur, corp_code: str, limit: int = 3) -> list[dict]:
    cur.execute("""SELECT bsns_year, fs_div, revenue, operating_profit, net_profit,
                          total_assets, total_liabilities, total_equity
                   FROM financials WHERE corp_code = %s
                   ORDER BY bsns_year DESC LIMIT %s""", (corp_code, limit))
    out = []
    for y, fd, rev, op, np_, ta, tl, te in cur.fetchall():
        out.append({
            "bsns_year": y, "fs_div": fd or "CFS",
            "revenue": rev, "operating_profit": op, "net_profit": np_,
            "total_assets": ta, "total_liabilities": tl, "total_equity": te,
            "debt_ratio": _pct(tl, te), "roe": _pct(np_, te), "roa": _pct(np_, ta),
            "operating_margin": _pct(op, rev),
        })
    return out


# ══════════════════════════════════════════════════════════════════
#  시장 — 저장하지 않고 조회할 때 계산한 값을 읽는다
# ══════════════════════════════════════════════════════════════════


def _market(cur, corp_code: Optional[str], stock_code: Optional[str]) -> Optional[dict]:
    """`market_metrics` 뷰의 최근 한 줄. 없으면 None."""
    if not corp_code:
        return None
    cur.execute("""SELECT trade_date, close_price, change_pct, volume, listed_shares,
                          market_cap, per, pbr, psr, fin_year, fs_div
                   FROM market_metrics WHERE corp_code = %s
                   ORDER BY trade_date DESC LIMIT 1""", (corp_code,))
    row = cur.fetchone()
    if not row:
        return None
    d, cp, ch, vol, ls, mc, per, pbr, psr, fy, fd = row
    return {
        "trade_date": str(d), "close_price": cp,
        "change_pct": float(ch) if ch is not None else 0.0,
        "volume": vol, "listed_shares": ls, "market_cap": int(mc),
        # ★적자면 PER 이 null 이다. 음수 PER 을 만들면 화면이 「저평가」로 오독한다
        "per": float(per) if per is not None else None,
        "pbr": float(pbr) if pbr is not None else None,
        "psr": float(psr) if psr is not None else None,
        "fin_year": fy, "fs_div": fd or "CFS",
    }


def market_of(key: str, days: int = 30) -> dict:
    """시세와 지표. **상장사에만 있고, 없으면 왜 없는지 함께 보낸다.**"""
    with neo4j_session() as s:
        node = _node(s, key)
    if node is None:
        return {"key": key, "listed": False, "unavailable_reason": "unlisted",
                "latest": None, "series": []}

    stock = node.get("stock_code")
    corp = node.get("corp_code")
    if not stock:
        return {"key": key, "listed": False, "stock_code": None,
                "unavailable_reason": "unlisted", "latest": None, "series": []}

    with postgres_connection() as conn, conn.cursor() as cur:
        latest = _market(cur, corp, stock)
        cur.execute("""SELECT trade_date, close_price, change_pct, volume
                       FROM market_data WHERE stock_code = %s
                       ORDER BY trade_date DESC LIMIT %s""", (stock, days))
        series = [{"trade_date": str(d), "close_price": cp,
                   "change_pct": float(ch) if ch is not None else 0.0, "volume": v}
                  for d, cp, ch, v in reversed(cur.fetchall())]
        reason = None
        if latest is None:
            # ★상장인데 지표가 없다 — 시세 자체가 없나, 주식수를 못 믿나
            cur.execute("SELECT suspect FROM listed_shares WHERE corp_code = %s", (corp,))
            r = cur.fetchone()
            reason = "unreliable_shares" if (r and r[0]) else "not_collected"

    return {"key": key, "listed": True, "stock_code": stock,
            "unavailable_reason": reason, "latest": latest, "series": series}


# ══════════════════════════════════════════════════════════════════
#  관계 · 사건 · 뉴스 · 공시
# ══════════════════════════════════════════════════════════════════

# ★상세의 관계 목록에 나가는 유형과 상한. **그래프도 이걸 그대로 쓴다** —
#   목록과 그림이 각자 기준으로 뽑으면 어긋난다.
_REL_TYPES = ('SUPPLIES_TO', 'PARTNERS_WITH', 'COMPETES_WITH', 'ACQUIRES',
              'SUES', 'DEPENDS_ON', 'OWNS_STAKE_IN', 'REGULATES')
_REL_LIMIT = 30

_REL_Q = f"""
MATCH (c:Company)-[r]-(o)
WHERE (c.corp_code = $k OR c.norm_name = $k)
  AND type(r) IN {list(_REL_TYPES)!r}
RETURN type(r) AS t, properties(r) AS p, elementId(r) AS eid,
       startNode(r) = c AS outgoing,
       coalesce(o.name,'?') AS oname,
       coalesce(o.corp_code, o.norm_name, o.name) AS okey,
       labels(o)[0] AS olabel,
       c.name AS cname, coalesce(c.corp_code, c.norm_name) AS ckey
"""


def relations_of(key: str, *, limit: Optional[int] = None,
                 rows: Optional[list[dict]] = None) -> list[dict]:
    """관계 목록. **근거 검증에서 걸린 것과 종료된 것은 안 나온다.**

    `rows` 를 주면 질의를 **다시 하지 않는다.** 상세는 1홉을 한 번만 읽어
    관계 목록과 그래프를 **같은 목록에서** 뽑는다 — 그래야 어긋날 수가 없다.
    """
    if rows is None:
        with neo4j_session() as s:
            rows = [dict(r) for r in s.run(_REL_Q, k=key)]
    else:
        rows = [r for r in rows if r["t"] in _REL_TYPES]
    out = []
    for r in rows:
        me = {"key": r["ckey"], "name": r["cname"], "label": "Company"}
        other = {"key": r["okey"], "name": r["oname"], "label": r["olabel"]}
        src, tgt = (me, other) if r["outgoing"] else (other, me)
        rel = _relation(src, tgt, r["t"], r["p"] or {}, eid=r["eid"])
        if rel:
            out.append(rel)
    out.sort(key=lambda x: -x["score"])
    return out[:limit] if limit else out


_EVENT_Q = """
MATCH (c:Company)-[h:HAS_EVENT]->(e:Event)
WHERE c.corp_code = $k OR c.norm_name = $k
RETURN properties(e) AS e, properties(h) AS h
ORDER BY coalesce(h.occurred_at, e.last_seen) DESC
"""


def _own_evidence_ids(h: dict) -> list[str]:
    """★기업별 근거는 **엣지에 있다.** Event 노드의 `evidence_ids` 를 쓰면 안 된다.

    하나의 Event 를 여러 기업이 공유하는데(실측 2026-08-23: 사건 938건 중 85건),
    노드의 `evidence_ids` 는 그 사건에 엮인 **모든 기업의 근거 합집합**이다.
    그걸 기업별 조회가 그대로 돌려주는 바람에 「SK하이닉스」 질의의 /ask 근거에
    현대오토에버 노조 기사가 섞였다(ev_14df4ce056904b8b).

        Event '노조 설립'  e.evidence_ids = [현대오토에버 ×2, SK하이닉스, 신세계]
          SK하이닉스   -[HAS_EVENT {evidence_id: ev_47b007…}]->
          현대오토에버 -[HAS_EVENT {evidence_id: ev_14df4c…}]->

    `role`·`occurred_at` 은 이미 엣지에서 가져오고 있었다 — 「날짜는 사건 노드가
    아니라 관계에 있다」(schemas.py `Event`). `evidence_ids` 만 그 원칙에서
    빠져 있었을 뿐이다.

    ★못 찾아도 노드 합집합으로 메우지 않는다. 없는 것과 남의 것은 다르다 —
      메우면 이 사고가 그대로 되돌아온다(실측: 1,062개 HAS_EVENT 엣지 전부가
      `evidence_id` 를 들고 있어 메울 일도 없다).

    단수·복수를 둘 다 모은다 — `relation_service._evidence()`·
    `graph_searcher._evidence_refs()` 와 같은 규약이다(실측: 복수 11건).
    """
    ids: list[str] = []
    for value in (h.get("evidence_id"), *(h.get("evidence_ids") or [])):
        if value and value not in ids:
            ids.append(str(value))
    return ids


def events_of(key: str) -> list[dict]:
    """사건 목록. `timeline` 은 **펴서** 준다 — 화면이 문자열을 쪼개게 하지 않는다.

    근거는 **이 기업의 엣지 것만** 나간다(`_own_evidence_ids`). 사건 자체는
    공유 구조 그대로 — 같은 Event 를 여러 기업이 들고 있어도 사건은 사건이다.
    """
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_EVENT_Q, k=key)]
    out = []
    for row in rows:
        e, h = dict(row["e"]), dict(row["h"] or {})
        tl = []
        for item in (e.get("timeline") or []):
            parts = str(item).split("|")
            if len(parts) >= 2:
                tl.append({"period": parts[0], "name": parts[1]})
        out.append({
            "event_id": e.get("event_id") or "",
            "name": e.get("name") or "",
            "event_type": e.get("event_type") or "기타",
            "is_risk": bool(e.get("is_risk")),
            "role": h.get("role") or "subject",
            "occurred_at": str(h.get("occurred_at"))[:10] if h.get("occurred_at") else None,
            "article_count": int(e.get("article_count") or 1),
            "timeline": tl,
            "evidence_ids": _own_evidence_ids(h),
        })
    return out


def news_of(key: str, limit: int = 20) -> list[dict]:
    """관련 기사. ★**본문은 저장하지 않는다** — 제목·언론사·날짜·링크까지."""
    with neo4j_session() as s:
        node = _node(s, key)
        if node is None:
            return []
        urls = [r["u"] for r in s.run(
            """MATCH (c:Company)-[r]-() WHERE c.corp_code=$k OR c.norm_name=$k
               WITH r WHERE r.source_type = 'news' AND r.source_doc STARTS WITH 'http'
               RETURN DISTINCT r.source_doc AS u LIMIT $n""", k=key, n=limit * 3)]
    if not urls:
        return []
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT url, title, press, published_at FROM news_articles
                       WHERE url = ANY(%s) ORDER BY published_at DESC NULLS LAST
                       LIMIT %s""", (urls, limit))
        return [{"url": u, "title": t, "press": p,
                 "published_at": str(d)[:10] if d else None}
                for u, t, p, d in cur.fetchall()]


def _news_count(key: str) -> int:
    """관련 기사 **전체 수.** 목록은 잘려도 이 숫자는 안 잘린다."""
    with neo4j_session() as s:
        return s.run(
            """MATCH (c:Company)-[r]-() WHERE c.corp_code=$k OR c.norm_name=$k
               WITH r WHERE r.source_type='news' AND r.source_doc STARTS WITH 'http'
               RETURN count(DISTINCT r.source_doc) AS n""", k=key).single()["n"]


def filings_of(key: str, limit: int = 20) -> list[dict]:
    with neo4j_session() as s:
        node = _node(s, key)
    corp = (node or {}).get("corp_code")
    if not corp:
        return []
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT rcept_no, doc_type, title, rcept_dt FROM documents
                       WHERE corp_code = %s ORDER BY rcept_dt DESC LIMIT %s""",
                    (corp, limit))
        return [{"rcept_no": r, "doc_type": dt, "title": t, "rcept_dt": str(d)[:10],
                 "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r}"}
                for r, dt, t, d in cur.fetchall()]


# ══════════════════════════════════════════════════════════════════
#  기업 상세
# ══════════════════════════════════════════════════════════════════

# ★`OPTIONAL MATCH` 를 연달아 쓰면 **카테시안 곱**이 된다.
#
#   처음엔 이렇게 썼다가 삼성전자 조회가 **영영 안 끝났다**(2026-08-16):
#
#       OPTIONAL MATCH (c)-[r]-()            1,169행
#       OPTIONAL MATCH (c)-[]-(x:Company)    × 443
#       OPTIONAL MATCH (c)-[:HAS_EVENT]->(e) × 148
#       OPTIONAL MATCH (c)-[:HAS_EVENT]->(rk)× 69
#                                            = 52억 행을 만들고 나서 센다
#
#   심텍(44 × 13 × 4 × 2 = 4,576행)으로 테스트해서 못 잡았다.
#   **작은 노드로만 테스트하면 이 종류의 버그는 안 보인다.**
#
#   `COUNT {}` 서브쿼리는 각각 따로 세므로 곱이 안 생긴다 — 1,169관계짜리도 0.01초.
_DETAIL_Q = """
MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
RETURN properties(c) AS p,
       COUNT { (c)-[]-() }                                  AS rel,
       COUNT { MATCH (c)-[]-(x:Company) RETURN DISTINCT x }  AS comp,
       COUNT { (c)-[:HAS_EVENT]->(:Event) }                  AS ev,
       COUNT { (c)-[:HAS_EVENT]->(:Event {is_risk:true}) }   AS risk
"""

# ★자기 자신을 소유할 수 없다. 노드 병합이 만드는 자기 루프를 여기서도 막는다
#   (2026-08-17 실측: 로보티즈가 자기 자신의 자회사로 들어가 있다).
#   워크스페이스 그래프는 `_relation()` 이 막지만 **지배구조는 다른 경로**라
#   따로 걸러야 한다 — 방어는 한 곳에만 두면 새는 데가 생긴다.
_OWN_Q = """
MATCH (a:Company)-[r:OWNS_STAKE_IN]->(b:Company)
WHERE ((a.corp_code=$k OR a.norm_name=$k) OR (b.corp_code=$k OR b.norm_name=$k))
  AND a <> b
RETURN coalesce(a.corp_code,a.norm_name) AS akey, a.name AS aname,
       coalesce(b.corp_code,b.norm_name) AS bkey, b.name AS bname,
       r.ratio AS ratio, r.subtype AS subtype,
       (a.corp_code=$k OR a.norm_name=$k) AS outgoing
ORDER BY r.ratio DESC
"""

_PROD_Q = """
MATCH (c:Company)-[d:DEVELOPS]->(p:Product)
WHERE c.corp_code=$k OR c.norm_name=$k
RETURN p.norm_name AS key, p.name AS name, p.category AS cat,
       coalesce(d.source_type,'news') AS src
"""

# ★`LIMIT 30` 이 여기 박혀 있었다. 상세가 잘라 보내는 건 맞지만 **세는 것까지
#   잘리면** 화면이 「30명」이라고 쓰고 그게 전부인 줄 안다. 자르는 건 위에서 한다.
_EXEC_Q = """
MATCH (p:Person)-[r:IS_EXECUTIVE_OF]->(c:Company)
WHERE c.corp_code=$k OR c.norm_name=$k
RETURN p.person_key AS key, p.name AS name, r.subtype AS position
"""


def _fill(n: int, threshold: int = 1) -> str:
    return "full" if n >= threshold else "none"


# ══════════════════════════════════════════════════════════════════
#  「더보기」 — 상세는 블록마다 10건, 전체는 여기서
# ══════════════════════════════════════════════════════════════════
#
# ★상세가 전부 실어 보내면 삼성전자에서 제품 152 · 자회사 157 이 나간다.
#   화면은 상단 몇 개만 보여 주고 「더보기」로 펼치므로, 전체는 따로 받는다.


def products_of(key: str, limit: Optional[int] = None) -> list[dict]:
    with neo4j_session() as s:
        out = [{"key": r["key"], "name": r["name"], "category": r["cat"],
                "source": "dart" if r["src"] in ("dart", "dart_filing") else "news"}
               for r in s.run(_PROD_Q, k=key)]
    return out[:limit] if limit else out


def executives_of(key: str, limit: Optional[int] = None) -> list[dict]:
    with neo4j_session() as s:
        out = [{"key": r["key"], "name": r["name"], "position": r["position"]}
               for r in s.run(_EXEC_Q, k=key)]
    return out[:limit] if limit else out


def ownership_of(key: str, limit: Optional[int] = None) -> dict:
    """지배구조. **양방향을 갈라서 준다** — 같은 `OWNS_STAKE_IN` 이라도
    들어오는 방향과 나가는 방향은 화면에서 다른 뜻이다."""
    owns, owned_by = [], []
    with neo4j_session() as s:
        for r in s.run(_OWN_Q, k=key):
            other = ("b", "bname") if r["outgoing"] else ("a", "aname")
            item = {"key": r[other[0] + "key"], "name": r[other[1]], "label": "Company",
                    "ratio": float(r["ratio"]) if r["ratio"] is not None else None,
                    "subtype": r["subtype"] or None}
            (owns if r["outgoing"] else owned_by).append(item)
    return {"key": key, "owns_total": len(owns), "owned_by_total": len(owned_by),
            "owns": owns[:limit] if limit else owns,
            "owned_by": owned_by[:limit] if limit else owned_by}


def company_detail(key: str) -> Optional[dict]:
    """기업 상세 — **「이 회사 자체의 전부」.** 없으면 None."""
    with neo4j_session() as s:
        row = s.run(_DETAIL_Q, k=key).single()
        if row is None:
            return None
        p = dict(row["p"])
        counts = {"relations": row["rel"], "related_companies": row["comp"],
                  "events": row["ev"], "risk_events": row["risk"]}
        owns, owned_by = [], []
        for r in s.run(_OWN_Q, k=key):
            item = {"key": r["bkey"] if r["outgoing"] else r["akey"],
                    "name": r["bname"] if r["outgoing"] else r["aname"],
                    "label": "Company",
                    "ratio": float(r["ratio"]) if r["ratio"] is not None else None,
                    "subtype": r["subtype"] or None}
            (owns if r["outgoing"] else owned_by).append(item)
        products = [{"key": r["key"], "name": r["name"], "category": r["cat"],
                     "source": "dart" if r["src"] in ("dart", "dart_filing") else "news"}
                    for r in s.run(_PROD_Q, k=key)]
        executives = [{"key": r["key"], "name": r["name"], "position": r["position"]}
                      for r in s.run(_EXEC_Q, k=key)]

    # ★상세는 **블록마다 상위 몇 건만** 준다. 화면이 「더보기」로 펼치는 구조라
    #   전부 실어 보낼 이유가 없다. 전체는 서브 라우트가 준다.
    #
    #   실측(2026-08-18) 상한을 안 걸었을 때 — 삼성전자 상세가 2.2초였다.
    #       events 148 · products 152 · owns 157 · executives 21 이 통째로 나갔다
    #
    #   ★`counts` 는 **자르기 전 실제 수**다. 그래야 화면이 「148건 중 10건」이라고
    #     쓸 수 있다. `len()` 으로 세면 10건이 전부인 줄 안다.
    counts["products"] = len(products)
    counts["executives"] = len(executives)
    counts["owns"] = len(owns)
    counts["owned_by"] = len(owned_by)
    products_all, executives_all = products, executives
    owns_all, owned_by_all = owns, owned_by
    products = products[:_BLOCK_LIMIT]
    executives = executives[:_BLOCK_LIMIT]
    owns = owns[:_BLOCK_LIMIT]
    owned_by = owned_by[:_BLOCK_LIMIT]

    corp = p.get("corp_code")
    node_key = _key_of(p)
    attrs, fins, segs, overview, biz = {}, [], [], None, None
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT ceo_nm, est_dt, name_en, induty, sector_label
                       FROM company_attributes WHERE node_key = %s""", (node_key,))
        r = cur.fetchone()
        if r:
            attrs = {"ceo": r[0], "established_at": str(r[1])[:10] if r[1] else None,
                     "name_en": r[2], "induty": r[3], "sector_label": r[4]}
        if corp:
            fins = _financials(cur, corp)
            cur.execute("""SELECT segment_name, revenue, revenue_ratio,
                                  revenue_trusted, ratio_trusted
                           FROM business_segments WHERE corp_code = %s
                           ORDER BY revenue DESC NULLS LAST""", (corp,))
            for nm, rev, ratio, rt, at in cur.fetchall():
                # ★못 믿는 값은 **아예 안 보낸다.** 사업보고서 표는 단위가 자주 틀린다
                segs.append({"name": nm,
                             "revenue": rev if rt else None,
                             "revenue_ratio": float(ratio) if (at and ratio is not None) else None})
            cur.execute("SELECT text FROM company_profiles WHERE corp_code=%s "
                        "ORDER BY version DESC LIMIT 1", (corp,))
            r = cur.fetchone()
            overview = r[0] if r else None
            cur.execute("SELECT overview_text FROM business_overview WHERE corp_code=%s "
                        "ORDER BY bsns_year DESC LIMIT 1", (corp,))
            r = cur.fetchone()
            biz = r[0] if r else None
        market = _market(cur, corp, p.get("stock_code"))
        cur.execute("SELECT count(*) FROM documents WHERE corp_code = %s", (corp or "",))
        n_filings = cur.fetchone()[0]

    overview = overview or attrs.get("sector_label")
    news = news_of(key, limit=10)
    # ★`counts` 는 **목록 길이가 아니라 실제 수**다. 목록은 상한에 걸려 잘리므로
    #   `len()` 으로 세면 화면이 「10건」이라고 쓰고 그게 전부인 줄 안다.
    counts["news"] = _news_count(key)
    counts["filings"] = n_filings

    # ★1홉을 **한 번만** 읽어 관계 목록과 그래프에 같이 쓴다.
    #   「기업 관계 그래프」는 상세 페이지의 블록이라(README 4-3) 페이지가
    #   열릴 때 어차피 필요하다 — 왕복을 두 번 할 이유가 없다.
    hop = one_hop(key)
    graph = company_graph(key, rows=hop)
    # ★관계 목록은 **그래프에 그려진 것과 같은 목록**이다.
    #   상한을 따로 두면 어긋난다 — 실측(2026-08-17): 목록 상한 30, 그래프 55 라서
    #   삼성전자에서 그래프엔 선이 있는데 목록엔 없는 관계가 25건 나왔다.
    #   그래프가 유형별로 균형을 잡아 뽑으니(경쟁·소송이 안 밀린다) 목록도 그걸 쓴다.
    drawn = {e["edge_id"] for e in graph["edges"]}
    related = [r for r in relations_of(key, rows=hop) if r["edge_id"] in drawn]
    events_all = events_of(key)
    events = events_all[:_BLOCK_LIMIT]

    blocks = {
        "overview": _fill(1 if overview else 0),
        "financials": _fill(len(fins)),
        "segments": _fill(len(segs)),
        "products": _fill(len(products_all)),
        "related": _fill(len(related)),
        "risk": "full" if counts["risk_events"] else ("partial" if counts["events"] else "none"),
        "news": _fill(len(news)),
        "filings": _fill(n_filings),
        "ownership": _fill(len(owns_all) + len(owned_by_all)),
        "market": _fill(1 if market else 0),
        "graph": _fill(len(graph["edges"])),
    }
    # ★블록 **개수**로 판정하면 안 된다. 블록은 무게가 서로 다르다 —
    #   `news`·`risk`·`graph` 는 관계만 있어도 채워져서, 재무도 공시도 없는
    #   외국 기업이 7개를 넘겨 `full` 로 나갔다(엔비디아 7/11 → full).
    #   프론트가 이 값으로 「상세 페이지로 가기」를 켜므로 **빈 페이지로 보낸다.**
    #
    #   무엇을 실제로 갖고 있느냐로 가른다. 검색·워크스페이스는 `is_stub` 으로
    #   이미 이렇게 판정하고 있었다 — 상세만 어긋나 있었다.
    #
    #       full             사업개요가 있다 (= is_stub=false = 시드 64곳)
    #                        사업보고서 본문을 파야 나오므로 재무·사업부문·공시가
    #                        함께 온다. 파이프라인이 끝까지 돈 증거다
    #       partial          재무나 시세가 있다 (416곳) — corp_code 가 있어야 한다
    #       none   그 외 (2,952곳) — 외국 기업이 여기 온다. 정상이다
    if p.get("is_stub") is False:
        detail_level = "full"
    elif fins or market:
        detail_level = "partial"
    else:
        detail_level = "none"

    return {
        "key": node_key, "name": p.get("name"),
        "detail_level": detail_level, "coverage": "complete",
        "collected_at": str(p.get("last_seen"))[:10] if p.get("last_seen") else None,
        "corp_code": corp, "stock_code": p.get("stock_code"),
        "market": p.get("market"), "entity_kind": p.get("entity_kind"),
        "ksic": p.get("ksic"), "ksic_label": label_of(p.get("ksic")) if p.get("ksic") else None,
        "also_names": list(p.get("also_names") or []),
        "blocks": blocks, "counts": counts,
        "overview": overview, "business_overview": biz,
        "ceo": attrs.get("ceo"), "established_at": attrs.get("established_at"),
        "name_en": attrs.get("name_en"), "induty": attrs.get("induty"),
        "market_metrics": market, "financials": fins, "segments": segs,
        "products": products, "executives": executives,
        "owned_by": owned_by, "owns": owns, "related": related,
        "events": events, "news": news, "filings": filings_of(key, limit=10),
        # ★상세 페이지의 「기업 관계 그래프」 블록. `depth`·`max_nodes` 를 바꿔
        #   다시 그리려면 `/companies/{key}/graph` 를 부른다.
        "graph": graph,
    }


# ══════════════════════════════════════════════════════════════════
#  기업 요약 — 워크스페이스 문맥
# ══════════════════════════════════════════════════════════════════

# 직접 관계는 없는데 **같은 고객에게 판다** — 관계가 아니라 근접성이다.
# 엣지로 만들면 안 된다. 근거 없는 관계를 그리는 것이 되기 때문이다.
_SHARED_Q = """
MATCH (m:Company)-[:SUPPLIES_TO]->(cust:Company)<-[:SUPPLIES_TO]-(peer:Company)
WHERE (m.corp_code=$k OR m.norm_name=$k) AND peer <> m
  AND coalesce(peer.corp_code, peer.norm_name) IN $ws
  AND NOT (m)--(peer)
RETURN coalesce(peer.corp_code,peer.norm_name) AS key, peer.name AS name,
       count(DISTINCT cust) AS n, collect(DISTINCT cust.name)[0..5] AS customers
ORDER BY n DESC LIMIT 5
"""


# ★워크스페이스 관계는 **따로 질의한다.** 상세의 `related` 를 걸러 쓰면 안 된다.
#
#   실측(2026-08-16): 삼성전자 상세의 `related` 상위 30개가 전부 점수 1.0 짜리
#   DART 관계(삼성복지재단·삼성생명 …)라, 정작 워크스페이스에 담긴 심텍(0.945)·
#   SK하이닉스(0.648)·한미반도체(0.48)가 **잘려 나가 빈 배열이 됐다.**
#
#   **자른 뒤에 거르면 안 되고, 걸러서 가져와야 한다.**
_WS_REL_Q = """
MATCH (c:Company)-[r]-(o:Company)
WHERE (c.corp_code = $k OR c.norm_name = $k)
  AND coalesce(o.corp_code, o.norm_name) IN $ws
  AND type(r) IN ['SUPPLIES_TO','PARTNERS_WITH','COMPETES_WITH','ACQUIRES',
                  'SUES','DEPENDS_ON','OWNS_STAKE_IN','REGULATES']
RETURN type(r) AS t, properties(r) AS p, elementId(r) AS eid,
       startNode(r) = c AS outgoing,
       coalesce(o.name,'?') AS oname, coalesce(o.corp_code,o.norm_name) AS okey,
       c.name AS cname, coalesce(c.corp_code,c.norm_name) AS ckey
"""


def company_summary(key: str, workspace_keys: list[str]) -> Optional[dict]:
    """워크스페이스 좌 패널 — **「지금 보는 그래프 안에서의 이 회사」.**

    ★`workspace_relations` 는 담아 둔 *다른* 기업들과의 관계만이다.
      기업 키만으로는 답이 안 나와서 이 함수가 목록을 함께 받는다.
    """
    detail = company_detail(key)
    if detail is None:
        return None
    ws = [k for k in workspace_keys if k != key]

    with neo4j_session() as s:
        in_ws = []
        for r in s.run(_WS_REL_Q, k=key, ws=ws):
            me = {"key": r["ckey"], "name": r["cname"], "label": "Company"}
            other = {"key": r["okey"], "name": r["oname"], "label": "Company"}
            src, tgt = (me, other) if r["outgoing"] else (other, me)
            rel = _relation(src, tgt, r["t"], dict(r["p"] or {}), eid=r["eid"])
            if rel:
                in_ws.append(rel)
        in_ws.sort(key=lambda x: -x["score"])

        shared = [{"key": r["key"], "name": r["name"], "shared_count": r["n"],
                   "customers": list(r["customers"])}
                  for r in s.run(_SHARED_Q, k=key, ws=ws)]

    risk_n = detail["counts"]["risk_events"]
    return {
        **{f: detail[f] for f in ("key", "name", "detail_level", "coverage",
                                  "collected_at", "corp_code", "stock_code", "market",
                                  "entity_kind", "ksic", "ksic_label", "also_names",
                                  "overview", "ceo", "established_at", "financials",
                                  "market_metrics")},
        "latest_financial": detail["financials"][0] if detail["financials"] else None,
        "risk_summary": (f"사건 {detail['counts']['events']}건 중 위험 {risk_n}건"
                         if detail["counts"]["events"] else None),
        "risk_event_count": risk_n,
        "workspace_relations": in_ws,
        "shared_customers": shared,
        "recent_news": detail["news"][:5],
    }


# ══════════════════════════════════════════════════════════════════
#  기업 중심 그래프 — 워크스페이스 캔버스와 **다른 그림**이다
# ══════════════════════════════════════════════════════════════════
#
# ★워크스페이스 그래프를 재사용하면 안 된다. 처음에 그렇게 했다가 **엣지가 0**으로
#   나왔다(2026-08-17). 그 함수는 「담긴 기업들끼리」를 전제로 짜여 있어서
#   기업 하나만 넣으면 그릴 게 없다.
#
# ★목업의 범례가 넷이다 — **거래 기업 · 주주/자회사 · 위험 사건 · 제품.**
#   그래서 Company 만이 아니라 Event·Product·Person 까지 1홉으로 가져오고
#   `kind` 로 가른다. `label` 만으로는 Company 가 거래처인지 주주인지 모른다.
#
# ★유형별로 상한을 둔다. 심텍 1홉이 13곳이라 상한이 거의 안 걸리지만,
#   삼성전자는 1,169개다 — 한 유형이 화면을 다 먹으면 나머지가 안 보인다.

# ★상한은 **엣지 유형별**이다. 갈래(kind)별로 걸면 안 된다 —
#   거래 갈래 하나에 공급·제휴·경쟁·소송·의존·인수가 다 들어 있어서
#   공급사가 8칸을 먼저 채우면 **경쟁사와 소송이 한 건도 안 그려진다.**
#   삼성전자가 실제로 그랬다: 경쟁 28·소송 73·의존 10 이 통째로 사라졌다.
#
# 그래서 두 번 담는다.
#   1차 — 유형마다 `_TYPE_FLOOR` 칸을 **먼저 떼어 준다.** 있는 유형은 반드시 보인다.
#   2차 — 남은 자리를 점수 높은 순으로 `_TYPE_CAP` 까지 채운다.
# 라벨별 키 속성 — **인덱스가 걸린 것만** 쓴다
_DEG_PROP = {"Company": ("corp_code", "norm_name"), "Person": ("person_key",),
             "Product": ("norm_name",), "Event": ("event_id",),
             "Organization": ("norm_name",)}
# ★상세의 블록별 상한. 화면이 「더보기」로 펼치는 구조라 상세는 앞부분만 준다.
#   전체는 `/companies/{key}/{events,products,relations,news,filings,ownership}`.
_BLOCK_LIMIT = 10
_TYPE_FLOOR = 3         # 유형별 최소 보장
_TYPE_CAP = 8           # 유형별 상한
_GRAPH_Q = """
MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
MATCH (c)-[r]-(o)
RETURN type(r) AS t, properties(r) AS p, elementId(r) AS eid,
       startNode(r) = c AS outgoing,
       coalesce(o.corp_code, o.norm_name, o.event_id, o.person_key, o.name) AS okey,
       coalesce(o.name, '?') AS oname, labels(o)[0] AS olabel,
       o.entity_kind AS okind, o.ksic AS oksic, o.corp_code AS occ,
       c.name AS cname, coalesce(c.corp_code, c.norm_name) AS ckey,
       c.entity_kind AS ckind, c.ksic AS cksic, c.corp_code AS ccc
"""


def one_hop(key: str) -> list[dict]:
    """이 기업에 붙은 관계 전부. **상세와 그래프가 이걸 나눠 쓴다.**

    ★같은 1홉을 두 번 읽으면 삼성전자에서 0.4~1.3초를 그냥 버린다.
      게다가 목록과 그래프가 **다른 읽기**에서 나오면 어긋날 여지가 생긴다.
    """
    with neo4j_session() as s:
        return [dict(r) for r in s.run(_GRAPH_Q, k=key)]


def company_graph(key: str, depth: int = 1, max_nodes: int = 60,
                  rows: Optional[list[dict]] = None) -> dict:
    """이 기업 중심 1홉 그래프. **사건·제품·주주가 섞여 나온다.**"""
    if rows is None:
        rows = one_hop(key)
    if not rows:
        with neo4j_session() as s:
            node = _node(s, key)
        if node is None:
            return {"nodes": [], "edges": [], "islands": [], "truncated": False,
                    "omitted": {}, "ref_candidates": 0}
        return {"nodes": [{"key": _key_of(node), "name": node.get("name"),
                           "label": "Company", "role": "pinned", "kind": "trade",
                           "entity_kind": node.get("entity_kind"),
                           "ksic_label": label_of(node["ksic"]) if node.get("ksic") else None,
                           "degree": 0, "is_island": True,
                           "can_collect": bool(node.get("corp_code"))}],
                "edges": [], "islands": [_key_of(node)], "truncated": False,
                "omitted": {}, "ref_candidates": 0}

    first = rows[0]
    me = {"key": first["ckey"], "name": first["cname"], "label": "Company",
          "role": "pinned", "kind": "trade", "entity_kind": first["ckind"],
          "ksic_label": label_of(first["cksic"]) if first["cksic"] else None,
          "degree": len(rows), "is_island": False,
          "members": None, "risk_weight": None, "risk_count": None,
          "can_collect": bool(first["ccc"])}
    nodes: dict[str, dict] = {me["key"]: me}
    edges: list[dict] = []
    omitted: dict[str, int] = {}
    per_type: dict[str, int] = {}

    # 점수 높은 것부터 담아야 상한에 걸려도 중요한 게 남는다
    prepared = []
    for r in rows:
        other = {"key": r["okey"], "name": r["oname"], "label": r["olabel"]}
        src, tgt = (me, other) if r["outgoing"] else (other, me)
        rel = _relation(src, tgt, r["t"], dict(r["p"] or {}), eid=r["eid"])
        if rel:
            prepared.append((rel, r))
    prepared.sort(key=lambda x: -x[0]["score"])

    # ★근거가 없거나 만료돼 **감춘 것도 세어 둔다.**
    #   안 세면 「관계 1,169」인데 그린 것 + 뺀 것이 998 이라 171 이 사라진다.
    hidden = len(rows) - len(prepared)

    # ★같은 사실이 두 번 그려지는 것을 막는다.
    #   HAS_EVENT(기업→사건) 와 IMPACTS(사건→기업) 는 근거가 같은 한 문장이다.
    #   둘 다 그리면 화면에 두 노드 사이 화살표가 두 개다.
    #   단 **남의 사건이 이 기업을 때린 IMPACTS 는 남긴다** — 전파는 다른 사실이다.
    own_event = {r["okey"] for _, r in prepared if r["t"] == "HAS_EVENT"}

    def _take(rel, r, cap: int) -> bool:
        """담았으면 True. 상한은 **노드를 새로 늘릴 때만** 본다.

        ★이미 그린 노드로 가는 엣지는 상한과 무관하게 담는다. 상한의 목적은
          노드 수를 줄이는 것이지 **관계를 지우는 것이 아니다.**
        """
        new_node = r["okey"] not in nodes
        if new_node and (per_type.get(r["t"], 0) >= cap or len(nodes) >= max_nodes):
            return False
        kind = _KIND.get(r["t"], "trade")
        if new_node:
            nodes[r["okey"]] = {
                "key": r["okey"], "name": r["oname"], "label": r["olabel"],
                "role": "neighbor", "kind": kind,
                "entity_kind": r["okind"],
                "ksic_label": label_of(r["oksic"]) if r["oksic"] else None,
                "degree": 0, "is_island": False,
                "members": None, "risk_weight": None, "risk_count": None,
                "can_collect": bool(r["occ"]) if r["olabel"] == "Company" else None,
            }
            per_type[r["t"]] = per_type.get(r["t"], 0) + 1
        edges.append({
            "edge_id": rel["edge_id"], "evidence_id": rel["evidence_id"],
            "type": rel["type"], "subtype": rel["subtype"],
            "source": rel["source"]["key"], "target": rel["target"]["key"],
            "symmetric": rel["symmetric"], "freshness": rel["freshness"],
            "score": rel["score"]})
        return True

    left, dup = [], 0
    for rel, r in prepared:
        if r["t"] == "IMPACTS" and r["okey"] in own_event:
            dup += 1                      # 같은 사실 — HAS_EVENT 로만 그린다
            continue
        left.append((rel, r))

    # ★상세의 관계 목록(`related`)에 나가는 것은 **반드시 그린다.**
    #   목록과 그래프가 각자 기준으로 뽑으면 어긋난다 — SK하이닉스에서 실제로
    #   목록엔 있고 그래프엔 없는 관계가 8건 나왔다. 목록에서 한 줄 눌렀는데
    #   그래프에 그 선이 없으면 화면이 강조할 대상이 없다.
    #   `relations_of` 와 **같은 기준**(같은 유형·점수순·같은 상한)으로 고른다.
    listed = [x for x in left if x[1]["t"] in _REL_TYPES][:_REL_LIMIT]
    must = {rel["edge_id"] for rel, _ in listed}
    for rel, r in listed:
        _take(rel, r, 10 ** 9)              # 상한을 보지 않는다
    left = [x for x in left if x[0]["edge_id"] not in must]

    for cap in (_TYPE_FLOOR, _TYPE_CAP):  # 1차 최소 보장 → 2차 채우기
        rest = []
        for rel, r in left:
            if not _take(rel, r, cap):
                rest.append((rel, r))
        left = rest
    for _, r in left:
        omitted[r["t"]] = omitted.get(r["t"], 0) + 1
    # ★아래 둘은 유형이 아니다. 그래야 「그린 것 + omitted = 관계 수」가 맞는다.
    if hidden:
        omitted["HIDDEN"] = hidden        # 근거 미흡·만료·자기 루프
    if dup:
        omitted["DUPLICATE"] = dup        # HAS_EVENT 와 겹치는 IMPACTS

    # ★이웃끼리의 관계는 **그리지 않는다.** 이건 「이 기업의」 관계 그래프다.
    #   양쯔메모리–화웨이 처럼 중심과 무관한 선이 붙으면 노이즈만 는다
    #   (실제로 붙여 봤더니 엣지가 82 → 122 로 늘고 읽기만 나빠졌다).
    #   이웃들끼리의 구조는 **워크스페이스 그래프가 볼 일이다.**

    # 이웃의 실제 연결 수를 채운다 — 화면이 허브를 흐리게 그릴 근거
    #
    # ★라벨을 붙여 **인덱스를 타게** 한다. 라벨 없이 `coalesce(...) IN $ks` 로
    #   찾으면 인덱스를 하나도 못 쓰고 7,755개 노드를 전부 훑는다 —
    #   키가 한 개여도 74ms 가 고정으로 붙었다.
    by_label: dict[str, list[str]] = {}
    for k, n in nodes.items():
        if k != me["key"]:
            by_label.setdefault(n["label"], []).append(k)
    if by_label:
        with neo4j_session() as s:
            for lab, ks in by_label.items():
                prop = _DEG_PROP.get(lab)
                if prop is None:
                    continue
                where = " OR ".join(f"o.{p} IN $ks" for p in prop)
                for r in s.run(
                        f"MATCH (o:{lab}) WHERE {where} "
                        f"RETURN coalesce({', '.join('o.'+p for p in prop)}) AS k, "
                        f"COUNT {{ (o)--() }} AS d", ks=ks):
                    if r["k"] in nodes:
                        nodes[r["k"]]["degree"] = r["d"]

    # ★자리는 담을 때 이미 지켰다(`_take`). 뒤에서 잘라내면 유형별 최소 보장이
    #   도로 무너진다 — 소송 상대는 연결 수가 적어 제일 먼저 잘린다.
    out = list(nodes.values())
    truncated = bool(left)

    linked: set[str] = set()
    for e in edges:
        linked.update((e["source"], e["target"]))
    islands = [n["key"] for n in out if n["key"] not in linked]
    for n in out:
        n["is_island"] = n["key"] in islands

    return {"nodes": out, "edges": edges, "islands": islands,
            "truncated": truncated, "omitted": omitted, "ref_candidates": 0}
