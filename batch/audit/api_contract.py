"""API 응답 계약과 스텁 예시를 **살아 있는 DB와 대조한다.**

★왜 필요한가 (2026-08-16)

라우트가 200을 돌려주는 것만 확인하고 **값이 맞는지는 아무도 안 봤다.**
그 결과:

    corp_code 8개가 지어낸 값이었다      `00121932` 를 한미반도체라고 썼는데 없는 노드
    하나는 다른 회사를 가리켰다           `00164645` → 실제로는 HMM
    counts 는 넷 중 셋이 틀렸다          연관기업 98 vs 실제 443
    SK하이닉스 매출도 틀렸다              66조 vs 실제 97조

**스모크 테스트는 「깨지지 않는다」만 증명하지 「맞다」를 증명하지 않는다.**
그래서 이 검사는 세 가지를 본다:

    ① 예시의 key 가 **실재하고 이름이 맞나**
    ② 예시의 수치가 **DB 를 다시 세어도 같나**
    ③ 라우트 전부가 **스키마에 맞는 응답**을 주나

★지어낸 값을 쓰면 안 되는 이유

프론트가 **현실에 없는 모양**을 전제로 화면을 만든다. 그리고 진짜 데이터를
붙이는 날 깨진다 — 그때는 이미 화면이 다 만들어진 뒤다.

실행:
    python -m batch.audit.api_contract
    python -m batch.audit.api_contract --routes-only   # DB 없이 스키마만
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_OK, _BAD = "  ✅", "  ❌"


def _fail(msgs: list[str], text: str) -> None:
    msgs.append(text)
    print(_BAD + " " + text)


# ══════════════════════════════════════════════════════════════════
#  ① 예시의 key 가 실재하고 이름이 맞나
# ══════════════════════════════════════════════════════════════════


def check_keys(problems: list[str]) -> None:
    """예시에 등장하는 모든 (key, name) 쌍을 그래프와 대조한다.

    ★`key` 는 `corp_code` 이거나 `norm_name` 이다. 둘 다로 찾아본다.
    """
    from app.api import examples as ex
    from app.core.database import neo4j_session

    pairs: set[tuple[str, str]] = set()

    def add(k, n):
        if k and n:
            pairs.add((str(k), str(n)))

    for obj in (ex.SIMMTECH, ex.HYNIX, ex.SAMSUNG, ex.HANMI, ex.NVIDIA, ex.KOHYOUNG):
        add(obj.key, obj.name)
    for r in ex.RELATIONS_OF:
        add(r.source.key, r.source.name)
        add(r.target.key, r.target.name)
    for c in (ex.COMPANY_FULL, ex.COMPANY_RELATIONS_ONLY, ex.COMPANY_SUMMARY):
        add(c.key, c.name)
        for o in list(getattr(c, "owned_by", [])) + list(getattr(c, "owns", [])):
            add(o.key, o.name)
    for s in ex.SUGGESTIONS:
        add(s.key, s.name)
    for s in ex.SHARED:
        add(s.key, s.name)
    for g in (ex.WORKSPACE_GRAPH, ex.COMPANY_GRAPH):
        for n in g.nodes:
            if n.label.value == "Company":
                add(n.key, n.name)
    for t in ex.TRENDING:
        add(t.key, t.name)

    print(f"■ 예시 key {len(pairs)}개 — 실재하고 이름이 맞나")
    with neo4j_session() as s:
        for key, name in sorted(pairs):
            row = s.run(
                """MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
                   RETURN c.name AS n LIMIT 1""", k=key).single()
            if row is None:
                _fail(problems, f"{key} — 그런 노드가 없다 (예시는 「{name}」이라고 주장)")
            elif row["n"] != name:
                _fail(problems, f"{key} — 예시는 「{name}」, 실제는 「{row['n']}」")
    print(f"{_OK} {len(pairs)}개 확인")


# ══════════════════════════════════════════════════════════════════
#  ② 수치가 DB 를 다시 세어도 같나
# ══════════════════════════════════════════════════════════════════


def check_schema_examples(problems: list[str]) -> None:
    """`schemas.py` 의 `Field(examples=...)` 에 박힌 키도 실재하나.

    ★이걸 안 봐서 놓쳤다(2026-08-16). `/docs` 의 **Try it out 기본값**이
      `00121932`(없는 노드)라, 사용자가 눌러 보면 빈 그래프가 나왔다.
      예시가 스텁 파일에만 있는 게 아니라 **계약 안에도 있다.**
    """
    import re
    from pathlib import Path
    from app.core.database import neo4j_session

    src = Path("app/api/schemas.py").read_text(encoding="utf-8")
    # ★`unknown_keys` 의 예시는 **일부러 없는 키**다. 「그래프에 없어서 못 그린
    #   키」를 보여 주는 필드라, 실재하면 오히려 예시가 틀린 것이다.
    keep, skip = [], False
    for line in src.splitlines():
        if "unknown_keys" in line:
            skip = True
        if not skip:
            keep.append(line)
        if skip and line.rstrip().endswith(")"):
            skip = False
    src = chr(10).join(keep)
    keys = sorted(set(re.findall(r'"(\d{8})"', src)))
    print(f"\n■ schemas.py 예시 키 {len(keys)}개 — /docs 의 Try it out 기본값")
    with neo4j_session() as s:
        for k in keys:
            row = s.run("MATCH (c:Company {corp_code:$k}) RETURN c.name AS n",
                        k=k).single()
            if row is None:
                _fail(problems, f"schemas.py 예시 {k} — 그런 노드가 없다. "
                                f"/docs 에서 눌러 보면 빈 응답이 나온다")
    print(f"{_OK} {len(keys)}개 확인")


def check_counts(problems: list[str]) -> None:
    """`counts` 와 `degree` 를 다시 센다. **가장 틀리기 쉬운 값**이다."""
    from app.api import examples as ex
    from app.core.database import neo4j_session

    print("\n■ counts · degree 재계산")
    with neo4j_session() as s:
        for co in (ex.COMPANY_FULL, ex.COMPANY_RELATIONS_ONLY):
            k = co.key
            got = s.run("""
                MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
                OPTIONAL MATCH (c)-[r]-()
                OPTIONAL MATCH (c)-[]-(x:Company)
                OPTIONAL MATCH (c)-[:HAS_EVENT]->(e:Event)
                RETURN count(DISTINCT r) AS rel, count(DISTINCT x) AS comp,
                       count(DISTINCT e) AS ev""", k=k).single()
            risk = s.run("""
                MATCH (c:Company)-[:HAS_EVENT]->(e:Event {is_risk:true})
                WHERE c.corp_code = $k OR c.norm_name = $k
                RETURN count(e) AS n""", k=k).single()["n"]
            for label, claim, real in [
                ("relations", co.counts.relations, got["rel"]),
                ("related_companies", co.counts.related_companies, got["comp"]),
                ("events", co.counts.events, got["ev"]),
                ("risk_events", co.counts.risk_events, risk),
            ]:
                if claim != real:
                    _fail(problems, f"{co.name}.counts.{label} — 예시 {claim} · 실제 {real}")

        # 그래프 노드의 degree
        for g in (ex.WORKSPACE_GRAPH, ex.COMPANY_GRAPH):
            for n in g.nodes:
                if n.label.value != "Company":
                    continue
                row = s.run("""
                    MATCH (c:Company) WHERE c.corp_code = $k OR c.norm_name = $k
                    OPTIONAL MATCH (c)-[r]-() RETURN count(r) AS d""", k=n.key).single()
                if row and n.degree != row["d"]:
                    _fail(problems, f"그래프 {n.name}.degree — 예시 {n.degree} · 실제 {row['d']}")
    print(f"{_OK} 재계산 끝")


def check_facts(problems: list[str]) -> None:
    """재무·시세·지분율처럼 **원본이 하나뿐인 값**을 대조한다."""
    from app.api import examples as ex
    from app.core.database import neo4j_session, postgres_connection

    print("\n■ 재무 · 시세 · 지분율")
    with postgres_connection() as conn, conn.cursor() as cur:
        for fy in ex.COMPANY_FULL.financials:
            cur.execute("""SELECT revenue, operating_profit, net_profit, total_equity
                           FROM financials
                           WHERE corp_code=%s AND bsns_year=%s AND fs_div=%s""",
                        (ex.COMPANY_FULL.key, fy.bsns_year, fy.fs_div))
            row = cur.fetchone()
            if not row:
                _fail(problems, f"financials {fy.bsns_year} {fy.fs_div} — DB 에 없다")
                continue
            for label, claim, real in zip(
                    ("revenue", "operating_profit", "net_profit", "total_equity"),
                    (fy.revenue, fy.operating_profit, fy.net_profit, fy.total_equity), row):
                if claim != real:
                    _fail(problems,
                          f"financials {fy.bsns_year}.{label} — 예시 {claim:,} · 실제 {real:,}")

        m = ex.MARKET_SIMMTECH
        cur.execute("""SELECT close_price, volume, listed_shares, per, pbr, psr
                       FROM market_metrics WHERE corp_code=%s AND trade_date=%s""",
                    (ex.MARKET_RESPONSE.key, m.trade_date))
        row = cur.fetchone()
        if not row:
            _fail(problems, f"market_metrics {m.trade_date} — DB 에 없다")
        else:
            cp, vol, ls, per, pbr, psr = row
            if cp != m.close_price:
                _fail(problems, f"종가 — 예시 {m.close_price:,} · 실제 {cp:,}")
            if vol != m.volume:
                _fail(problems, f"거래량 — 예시 {m.volume:,} · 실제 {vol:,}")
            if ls != m.listed_shares:
                _fail(problems, f"유통주식수 — 예시 {m.listed_shares:,} · 실제 {ls:,}")
            # ★적자면 PER 이 null 이어야 한다
            if (per is None) != (m.per is None):
                _fail(problems, f"PER — 예시 {m.per} · 실제 {per}")

    with neo4j_session() as s:
        for o in ex.COMPANY_FULL.owned_by + ex.COMPANY_FULL.owns:
            row = s.run("""
                MATCH (a:Company)-[r:OWNS_STAKE_IN]->(b:Company)
                WHERE (a.corp_code=$a OR a.norm_name=$a)
                  AND (b.corp_code=$b OR b.norm_name=$b)
                RETURN r.ratio AS ratio LIMIT 1""",
                a=o.key if o in ex.COMPANY_FULL.owned_by else ex.COMPANY_FULL.key,
                b=ex.COMPANY_FULL.key if o in ex.COMPANY_FULL.owned_by else o.key).single()
            if row is None:
                _fail(problems, f"지분 관계 {o.name} — 그런 엣지가 없다")
            elif o.ratio is not None and row["ratio"] is not None \
                    and abs(row["ratio"] - o.ratio) > 0.05:
                _fail(problems, f"{o.name} 지분율 — 예시 {o.ratio} · 실제 {row['ratio']}")
    print(f"{_OK} 대조 끝")


def check_refs(problems: list[str]) -> None:
    """`evidence_id` · `event_id` 가 실재하나."""
    from app.api import examples as ex
    from app.core.database import neo4j_session

    print("\n■ evidence_id · event_id")
    ev_ids = {r.edge_id for r in ex.RELATIONS_OF} | {e.evidence_id for e in
                                                     ex.RELATION_DETAIL.evidence}
    event_ids = {e.event_id for e in ex.EVENTS_OF} | {r.event_id for r in ex.RISK_EVENTS}

    with neo4j_session() as s:
        for eid in sorted(ev_ids):
            n = s.run("MATCH ()-[r]->() WHERE r.evidence_id=$e RETURN count(r) AS n",
                      e=eid).single()["n"]
            if not n:
                _fail(problems, f"evidence_id {eid} — 그런 엣지가 없다")
        for eid in sorted(event_ids):
            n = s.run("MATCH (e:Event {event_id:$e}) RETURN count(e) AS n",
                      e=eid).single()["n"]
            if not n:
                _fail(problems, f"event_id {eid} — 그런 사건이 없다")
    print(f"{_OK} {len(ev_ids)}개 근거 · {len(event_ids)}개 사건 확인")


# ══════════════════════════════════════════════════════════════════
#  ③ 라우트가 스키마에 맞는 응답을 주나
# ══════════════════════════════════════════════════════════════════


def check_routes(problems: list[str]) -> None:
    """전 라우트를 호출해 **응답이 선언한 모델로 검증되는지** 본다.

    ★FastAPI 는 `response_model` 로 이미 검증하지만, 그건 **응답을 만들 때**다.
      여기서는 실제로 호출해 500 이 안 나는지, 필수 필드가 빠지지 않았는지 본다.
    """
    from fastapi.testclient import TestClient
    from app.api.main import app

    calls = [
        ("GET", "/health", None), ("GET", "/search?q=심텍", None),
        ("GET", "/companies/01095722", None), ("GET", "/companies/엔비디아", None),
        ("GET", "/companies/없는키", None),
        ("GET", "/companies/01095722/graph", None),
        ("GET", "/companies/01095722/market", None),
        ("GET", "/companies/엔비디아/market", None),
        ("GET", "/companies/01095722/events", None),
        ("GET", "/companies/01095722/news", None),
        ("GET", "/companies/01095722/filings", None),
        ("GET", "/companies/01095722/relations", None),
        ("GET", "/companies/01095722/products", None),
        ("GET", "/companies/01095722/executives", None),
        ("GET", "/companies/01095722/ownership", None),
        ("GET", "/events/evt_news_1664a8f17eed/impact", None),
        ("GET", "/news", None),
        ("POST", "/insights", {"keys": ["01095722", "00164779", "00126380"]}),
        ("POST", "/workspace/summary", {"key": "01095722", "workspace_keys": ["01095722"]}),
        ("POST", "/workspace/graph", {"keys": ["01095722", "00579999"]}),
        ("POST", "/workspace/suggest", {"keys": ["01095722"]}),
        ("POST", "/workspace/changes", {"keys": ["01095722"], "since": "2026-08-09"}),
        ("POST", "/retrieve", {"question": "심텍에 무슨 일이 있었나?"}),
    ]
    print(f"\n■ 라우트 {len(calls)}개 호출")
    c = TestClient(app)
    for method, url, body in calls:
        r = c.post(url, json=body) if method == "POST" else c.get(url)
        if r.status_code not in (200, 404):
            _fail(problems, f"{method} {url} → {r.status_code} {r.text[:120]}")
    print(f"{_OK} 전부 응답")

    # 스키마에만 있고 아무도 안 쓰는 모델 찾기 — 계약이 부풀지 않게
    spec = c.get("/openapi.json").json()
    declared = set(spec["components"]["schemas"])
    used = set()
    blob = str(spec["paths"])
    for name in declared:
        if f"/{name}" in blob:
            used.add(name)
    orphan = declared - used
    # 중첩으로만 쓰이는 모델은 paths 에 안 나온다 — 전체 문자열로 다시 확인
    whole = str(spec)
    orphan = {o for o in orphan if whole.count(f'"{o}"') <= 1}
    if orphan:
        print(f"  🟡 아무 라우트도 안 쓰는 모델 {len(orphan)}개: {', '.join(sorted(orphan))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes-only", action="store_true", help="DB 없이 스키마·라우트만")
    args = ap.parse_args()

    problems: list[str] = []
    check_routes(problems)
    if not args.routes_only:
        check_keys(problems)
        check_schema_examples(problems)
        check_counts(problems)
        check_facts(problems)
        check_refs(problems)

    print()
    if problems:
        print(f"❌ 어긋난 것 {len(problems)}건 — 예시가 DB 와 맞지 않습니다")
        return 1
    print("✅ 계약과 예시가 DB 와 맞습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
