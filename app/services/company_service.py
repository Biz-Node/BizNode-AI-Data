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
    "OWNS_STAKE_IN": "ownership", "IS_EXECUTIVE_OF": "person",
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
              today: Optional[date] = None) -> Optional[dict]:
    """엣지 속성 → API 관계. **감출 것은 여기서 감춘다.** 빼야 하면 None."""
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
        "edge_id": p.get("evidence_id") or "",
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

_REL_Q = """
MATCH (c:Company)-[r]-(o)
WHERE (c.corp_code = $k OR c.norm_name = $k)
  AND type(r) IN ['SUPPLIES_TO','PARTNERS_WITH','COMPETES_WITH','ACQUIRES',
                  'SUES','DEPENDS_ON','OWNS_STAKE_IN','REGULATES']
RETURN type(r) AS t, properties(r) AS p,
       startNode(r) = c AS outgoing,
       coalesce(o.name,'?') AS oname,
       coalesce(o.corp_code, o.norm_name, o.name) AS okey,
       labels(o)[0] AS olabel,
       c.name AS cname, coalesce(c.corp_code, c.norm_name) AS ckey
"""


def relations_of(key: str, *, limit: Optional[int] = None) -> list[dict]:
    """관계 목록. **근거 검증에서 걸린 것과 종료된 것은 안 나온다.**"""
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_REL_Q, k=key)]
    out = []
    for r in rows:
        me = {"key": r["ckey"], "name": r["cname"], "label": "Company"}
        other = {"key": r["okey"], "name": r["oname"], "label": r["olabel"]}
        src, tgt = (me, other) if r["outgoing"] else (other, me)
        rel = _relation(src, tgt, r["t"], r["p"] or {})
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


def events_of(key: str) -> list[dict]:
    """사건 목록. `timeline` 은 **펴서** 준다 — 화면이 문자열을 쪼개게 하지 않는다."""
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
            "evidence_ids": list(e.get("evidence_ids") or []),
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

_DETAIL_Q = """
MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
OPTIONAL MATCH (c)-[r]-()
OPTIONAL MATCH (c)-[]-(x:Company)
OPTIONAL MATCH (c)-[:HAS_EVENT]->(e:Event)
OPTIONAL MATCH (c)-[:HAS_EVENT]->(rk:Event {is_risk:true})
RETURN properties(c) AS p, count(DISTINCT r) AS rel, count(DISTINCT x) AS comp,
       count(DISTINCT e) AS ev, count(DISTINCT rk) AS risk
"""

_OWN_Q = """
MATCH (a:Company)-[r:OWNS_STAKE_IN]->(b:Company)
WHERE (a.corp_code=$k OR a.norm_name=$k) OR (b.corp_code=$k OR b.norm_name=$k)
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

_EXEC_Q = """
MATCH (p:Person)-[r:IS_EXECUTIVE_OF]->(c:Company)
WHERE c.corp_code=$k OR c.norm_name=$k
RETURN p.person_key AS key, p.name AS name, r.subtype AS position LIMIT 30
"""


def _fill(n: int, threshold: int = 1) -> str:
    return "full" if n >= threshold else "none"


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

    related = relations_of(key, limit=30)
    events = events_of(key)

    blocks = {
        "overview": _fill(1 if overview else 0),
        "financials": _fill(len(fins)),
        "segments": _fill(len(segs)),
        "products": _fill(len(products)),
        "related": _fill(len(related)),
        "risk": "full" if counts["risk_events"] else ("partial" if counts["events"] else "none"),
        "news": _fill(len(news)),
        "filings": _fill(n_filings),
        "ownership": _fill(len(owns) + len(owned_by)),
        "market": _fill(1 if market else 0),
    }
    filled = sum(1 for v in blocks.values() if v == "full")
    detail_level = "full" if filled >= 7 else ("relations_only" if related else "none")

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


def company_summary(key: str, workspace_keys: list[str]) -> Optional[dict]:
    """워크스페이스 좌 패널 — **「지금 보는 그래프 안에서의 이 회사」.**

    ★`workspace_relations` 는 담아 둔 *다른* 기업들과의 관계만이다.
      기업 키만으로는 답이 안 나와서 이 함수가 목록을 함께 받는다.
    """
    detail = company_detail(key)
    if detail is None:
        return None
    ws = [k for k in workspace_keys if k != key]
    in_ws = [r for r in detail["related"]
             if r["source"]["key"] in ws or r["target"]["key"] in ws]

    with neo4j_session() as s:
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
