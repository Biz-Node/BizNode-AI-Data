"""이름으로 기업 찾기 — **그래프에 있는 것과 DART 명부에만 있는 것을 함께.**

왜 두 곳을 다 뒤지나 (2026-08-16)

  우리 그래프    3,432곳    관계·사건·재무가 있다
  DART 명부    118,535곳   이름과 번호만 있다

  사용자가 「한화오션」을 찾는데 우리가 아직 안 모았다면, **없다고 답하면
  안 된다.** 실재하는 회사이고 DART 명부에 있다. 「있는데 아직 자료가 없다」와
  「그런 회사가 없다」는 화면에서 완전히 다른 말이다.

  그래서 명부 것도 돌려주되 `in_graph=false` 로 표시한다. 프론트는 이걸 보고
  **「수집되지 않은 기업입니다」**라고 알리면 된다.

부분 일치를 쓰는 이유

  실측(2026-08-16): **우리 그래프 안에 이름이 겹치는 노드는 0건**이다.
  정규화·병합이 이미 처리했다. 그래서 완전 일치만 받으면 「삼성」을 쳐도
  아무것도 안 나온다 — 사용자는 회사 이름 전체를 정확히 외우고 있지 않다.

  ★11.3% 동명은 **명부 이야기**다. 그래프가 아니라 개체해소용 사전에서 나는
    충돌이고, 명부 검색을 켜면 그때 실제로 보인다(「신우」 11곳).

순위는 「얼마나 정확히 맞았나」 → 「얼마나 아는가」 순이다

    1  이름이 정확히 같다
    2  이름이 그 말로 시작한다        「삼성전자」 ← 「삼성」
    3  이름 안에 들어 있다           「제일모직」 ← 「모직」
    4  옛 표기(별칭)로 걸렸다
    (같은 등급이면) 그래프 노드 먼저, 그 다음 관계 수(degree) 많은 순

  명부 것은 **언제나 그래프 것보다 뒤**다. 자료가 있는 쪽을 먼저 보여준다.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.ksic import label_of

# 명부는 118,535건이라 이름 두 글자로도 수백 건이 걸린다. 가져오는 건 상한을
# 두되, **몇 건인지는 따로 세어 정직하게 알린다** — 「50건」이라고 쓰면
# 사용자가 그게 전부인 줄 안다.
_REGISTRY_CAP = 50

# ★그래프 검색은 CONTAINS 라 인덱스를 못 탄다. 노드가 3,432곳이라 전체를 훑어도
#   빠르지만(실측 20ms), 노드가 10만 건이 되면 full-text 인덱스가 필요하다.
_GRAPH = """
MATCH (c:Company)
WITH c, [c.name] + coalesce(c.also_names, []) AS names
WHERE any(n IN names WHERE toLower(n) CONTAINS toLower($q))
OPTIONAL MATCH (c)-[r]-()
WITH c, names, count(r) AS degree
RETURN c.corp_code           AS corp_code,
       c.norm_name           AS norm_name,
       c.name                AS name,
       c.entity_kind         AS entity_kind,
       c.market              AS market,
       c.ksic                AS ksic,
       c.stock_code          AS stock_code,
       c.is_stub             AS is_stub,
       names                 AS names,
       degree
ORDER BY degree DESC
"""

# 명부에서 **그래프에 없는 것만.** 이미 노드가 있으면 위에서 나왔다.
_REGISTRY = """
SELECT corp_code, corp_name, stock_code, modify_date
FROM corp_code_master
WHERE corp_name ILIKE %s
  AND corp_code <> ALL(%s)
ORDER BY (stock_code IS NOT NULL) DESC, modify_date DESC
LIMIT %s
"""


def _tier(q: str, name: str, names: list[str]) -> tuple[int, str]:
    """맞은 정도. 낮을수록 위. 어디서 맞았는지도 같이 돌려준다."""
    ql, nl = q.lower(), (name or "").lower()
    if nl == ql:
        return 0, "name"
    if nl.startswith(ql):
        return 1, "name"
    if ql in nl:
        return 2, "name"
    # 본이름이 아니라 옛 표기로 걸린 경우
    return 3, "alias"


def _detail_level(row: dict, has_fin: bool) -> str:
    """이 기업에 대해 얼마나 아는가. **상세와 같은 기준으로 판정한다.**

    ★검색 결과에는 `blocks` 가 없다. 사용자가 목록에서 「볼 게 있나」를 판단할
      단서가 이 값뿐이라, 「재무·시세는 있음」(416곳)을 「관계만」으로 뭉개면
      HD현대중공업 같은 곳이 실제보다 빈약해 보인다.

        full             사업개요까지 (시드 64곳) — 상세 페이지가 꽉 찬다
        partial          재무나 시세가 있다 (416곳)
        none   그 외 — 외국 기업이 여기 온다. 정상이다
    """
    if row.get("is_stub") is False:
        return "full"
    return "partial" if has_fin else "none"


def search(q: str, limit: int = 20, *, include_registry: bool = True) -> dict[str, Any]:
    """이름·별칭 부분일치로 찾는다.

    반환 `hits[]` 의 각 항목:
        key           이후 모든 조회에 쓰는 키
        in_graph      **false 면 DART 명부에만 있는 회사** — 자료가 없다
        detail_level  full / partial / none · 명부 결과는 null
        ksic_label    업종 이름. 코드(`ksic`)도 함께 준다
    """
    q = (q or "").strip()
    if not q:
        return {"query": q, "total": 0, "hits": []}

    # ★상한을 걸지 않는다. 노드가 3,432곳이라 전부 훑어도 빠르고(실측 0.1초),
    #   무엇보다 **몇 건인지 정확히 세야** 화면이 「27건 중 20건」을 말할 수 있다.
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_GRAPH, q=q)]

    # ★`partial` 을 가르려면 **재무·시세 보유 여부**를 봐야 한다.
    #   전에는 `company_attributes`(대표·설립일)를 읽고 있었는데, 그건
    #   「이름 말고 뭐라도 있나」지 「숫자가 있나」가 아니다.
    corps = [r["corp_code"] for r in rows if r["corp_code"]]
    has_num: set[str] = set()
    if corps:
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT corp_code FROM financials WHERE corp_code = ANY(%s)
                           UNION
                           SELECT corp_code FROM market_metrics WHERE corp_code = ANY(%s)""",
                        (corps, corps))
            has_num = {k.strip() for (k,) in cur.fetchall()}

    hits: list[dict[str, Any]] = []
    for r in rows:
        key = r["corp_code"] or r["norm_name"]
        tier, matched = _tier(q, r["name"], r["names"])
        hits.append({
            "key": key,
            "name": r["name"],
            "label": "Company",
            "in_graph": True,
            "entity_kind": r["entity_kind"],
            "market": r["market"],
            "stock_code": r["stock_code"],
            "ksic": r["ksic"],
            "ksic_label": label_of(r["ksic"]) if r["ksic"] else None,
            "detail_level": _detail_level(r, key in has_num),
            "matched_on": matched,
            "degree": r["degree"] or 0,
            "_tier": tier,
        })

    # ── DART 명부 — 그래프에 없는 회사 ───────────────────────────
    registry_total = 0
    if include_registry:
        seen = [h["key"] for h in hits if h["key"] and len(h["key"]) == 8]
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT count(*) FROM corp_code_master
                           WHERE corp_name ILIKE %s AND corp_code <> ALL(%s)""",
                        (f"%{q}%", seen or [""]))
            registry_total = cur.fetchone()[0]
            cur.execute(_REGISTRY, (f"%{q}%", seen or [""], _REGISTRY_CAP))
            for cc, nm, sc, md in cur.fetchall():
                tier, _ = _tier(q, nm, [nm])
                hits.append({
                    "key": cc.strip(),
                    "name": nm,
                    "label": "Company",
                    "in_graph": False,          # ★수집되지 않은 회사
                    "entity_kind": None,
                    "market": None,
                    "stock_code": sc or None,
                    "ksic": None,
                    "ksic_label": None,
                    # 명부에만 있는 회사는 등급이 없다. `in_graph=false` 가 말해 준다
                    "detail_level": None,
                    "matched_on": "name",
                    "degree": 0,
                    "_tier": tier,
                    "_modify_date": str(md) if md else None,
                })

    # 맞은 정도 → 그래프 우선 → 관계 수
    hits.sort(key=lambda h: (h["_tier"], not h["in_graph"], -h["degree"]))
    for h in hits:
        h.pop("_tier", None)
        h.pop("_modify_date", None)

    graph_total = sum(1 for h in hits if h["in_graph"])
    return {
        "query": q,
        "total": graph_total + registry_total,   # ★가져온 수가 아니라 **있는 수**
        "graph_total": graph_total,
        "registry_total": registry_total,
        "hits": hits[:limit],
    }


def ksic_label(code: Optional[str]) -> Optional[str]:
    """업종 코드 → 이름. 화면이 숫자를 보여주지 않게 한다."""
    return label_of(code) if code else None
