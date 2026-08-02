"""[품질 감사] 그래프/DB의 이상을 6개 범주로 체계 스캔한다.

A 노드무결성 · B 엣지무결성 · C 값정합성 · D 크로스-DB · E 구조 · F 통계
"이미 아는 문제"뿐 아니라 무결성·참조·범위·분포 이상까지 능동 탐지한다.

실행: python -m batch.audit_graph
"""

from __future__ import annotations

import sys
from datetime import date

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TODAY = date.today().isoformat()
_ALLOWED_MARKETS = ["KOSPI", "KOSDAQ", "KONEX", "비상장", "해외", "펀드"]
_STATE_EDGES = ["OWNS_STAKE_IN", "IS_EXECUTIVE_OF", "SUPPLIES_TO"]

# (심각도, 범주, 제목, cypher, 이상임계) — 결과행 > 임계면 플래그
# 심각도: E=ERROR(무결성 위반) W=WARN(의심)
CHECKS: list[tuple] = [
    # ── A. 노드 무결성 ──────────────────────────────────────
    ("E", "A-노드", "이름 null/빈값",
     "MATCH (n) WHERE NOT n:Event AND (n.name IS NULL OR trim(n.name)='') RETURN labels(n)[0]+':'+coalesce(n.corp_code,n.person_key,'?') AS v LIMIT 10", 0),
    ("E", "A-노드", "Event title/event_id 누락",
     "MATCH (e:Event) WHERE e.event_id IS NULL OR e.title IS NULL RETURN e.event_id AS v LIMIT 10", 0),
    ("E", "A-노드", "Company norm_name 누락",
     "MATCH (c:Company) WHERE c.norm_name IS NULL RETURN c.name AS v LIMIT 10", 0),
    ("E", "A-노드", "resolution 불일치(resolved인데 corp_code 없음)",
     "MATCH (c:Company) WHERE c.resolution_status='resolved' AND c.corp_code IS NULL RETURN c.name AS v LIMIT 10", 0),
    ("E", "A-노드", "corp_code 형식 오류(8자리 아님)",
     "MATCH (c:Company) WHERE c.corp_code IS NOT NULL AND NOT c.corp_code =~ '\\\\d{8}' RETURN c.corp_code+'/'+c.name AS v LIMIT 10", 0),
    ("W", "A-노드", "market 허용셋 밖",
     f"MATCH (c:Company) WHERE c.market IS NOT NULL AND NOT c.market IN {_ALLOWED_MARKETS} RETURN DISTINCT c.market AS v LIMIT 10", 0),
    ("W", "A-노드", "이름 1자/글자없음(숫자·특수문자만)",
     "MATCH (n) WHERE n.name IS NOT NULL AND (size(trim(n.name))<=1 OR NOT n.name =~ '.*\\\\p{L}.*') RETURN labels(n)[0]+':'+n.name AS v LIMIT 10", 0),

    # ── B. 엣지 무결성 ──────────────────────────────────────
    ("E", "B-엣지", "표준메타 누락(source_type/confidence/is_current)",
     "MATCH ()-[r]->() WHERE r.source_type IS NULL OR r.confidence IS NULL OR r.is_current IS NULL RETURN type(r) AS v LIMIT 10", 0),
    ("E", "B-엣지", "confidence 범위 밖[0,1]",
     "MATCH ()-[r]->() WHERE r.confidence<0 OR r.confidence>1 RETURN type(r)+':'+toString(r.confidence) AS v LIMIT 10", 0),
    ("E", "B-엣지", "계약기간 역전(valid_until<valid_from)",
     "MATCH ()-[r]->() WHERE r.valid_from IS NOT NULL AND r.valid_until IS NOT NULL AND r.valid_until<r.valid_from RETURN type(r)+' '+r.valid_from+'~'+r.valid_until AS v LIMIT 10", 0),
    ("W", "B-엣지", "미래 발생일(occurred_at > 오늘)",
     f"MATCH ()-[r]->() WHERE r.occurred_at>'{TODAY}' RETURN type(r)+' '+r.occurred_at AS v LIMIT 10", 0),
    ("W", "B-엣지", "OWNS_STAKE_IN ratio·subtype 둘다 null",
     "MATCH ()-[r:OWNS_STAKE_IN]->() WHERE r.ratio IS NULL AND r.subtype IS NULL RETURN 'both null' AS v LIMIT 10", 0),
    ("W", "B-엣지", "IS_EXECUTIVE_OF subtype 없음",
     "MATCH ()-[r:IS_EXECUTIVE_OF]->() WHERE r.subtype IS NULL OR trim(r.subtype)='' RETURN 'no subtype' AS v LIMIT 10", 0),

    # ── C. 값 정합성 ────────────────────────────────────────
    ("E", "C-값", "지분율 범위 밖(>100/<0)",
     "MATCH ()-[r:OWNS_STAKE_IN]->() WHERE r.ratio>100 OR r.ratio<0 RETURN r.subtype+':'+toString(r.ratio) AS v LIMIT 10", 0),
    ("W", "C-값", "매출대비 100% 초과(검토 필요)",
     "MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE r.revenue_ratio>1 RETURN a.name+'→'+b.name+' '+toString(round(r.revenue_ratio*100))+'%' AS v LIMIT 10", 0),
    ("W", "C-값", "self-loop",
     "MATCH (a)-[r]->(a) RETURN type(r)+':'+a.name AS v LIMIT 10", 0),
    ("W", "C-값", "중복 엣지(같은 src,tgt,type,subtype)",
     "MATCH (a)-[r]->(b) WITH a,b,type(r) AS t,r.subtype AS s,count(*) AS c WHERE c>1 RETURN t+' '+a.name+'→'+b.name+' ×'+toString(c) AS v LIMIT 10", 0),
    ("W", "C-값", "문장/오추출 노드명(Company)",
     "MATCH (n:Company) WHERE any(w IN ['매출','기재','요청','영업비밀','중에서','선주','제조사','대형기업','공시유보'] WHERE n.name CONTAINS w) RETURN n.name AS v LIMIT 10", 0),

    # ── F. 커버리지(조용한 실패 탐지) ──────────────────────
    # ★근거(팩트체크) 누락 — BizNode 정체성(XAI)에 직결. 엣지는 있는데 근거가 없으면
    # "왜 이 관계가 있는가"를 답할 수 없다.
    ("E", "F-근거", "엣지에 source_doc 없음(출처 불명)",
     "MATCH ()-[r]->() WHERE r.source_doc IS NULL RETURN type(r) + ':' + coalesce(r.subtype,'') AS v LIMIT 10", 0),
    ("W", "F-근거", "엣지에 evidence_id 없음(근거 스니펫 없음)",
     "MATCH ()-[r]->() WHERE r.evidence_id IS NULL RETURN type(r) + ':' + coalesce(r.subtype,'') AS v LIMIT 10", 0),
    ("W", "F-신선도", "엣지에 last_seen 없음(신선도 판정 불가)",
     "MATCH ()-[r]->() WHERE r.last_seen IS NULL RETURN type(r) + ':' + coalesce(r.subtype,'') AS v LIMIT 10", 0),

    # 배치가 파싱·추출 실패를 조용히 스킵하면 무결성 위반 없이 데이터만 빔.
    # "있어야 할 게 없다"를 능동 탐지한다.
    ("W", "F-커버리지", "상장 시드인데 제품(DEVELOPS) 0개 (파싱/추출 의심)",
     "MATCH (c:Company {is_seed:true}) WHERE c.market IS NOT NULL AND c.market<>'비상장' "
     "AND NOT (c)-[:DEVELOPS]->() RETURN c.name AS v LIMIT 15", 0),
    ("W", "F-커버리지", "상장 시드인데 지분(OWNS_STAKE_IN) 유입 0 (수집 의심)",
     "MATCH (c:Company {is_seed:true}) WHERE c.market IS NOT NULL AND c.market<>'비상장' "
     "AND NOT ()-[:OWNS_STAKE_IN]->(c) RETURN c.name AS v LIMIT 15", 0),

    # ── E. 구조 이상 ────────────────────────────────────────
    ("W", "E-구조", "슈퍼노드(degree>150)",
     "MATCH (n) WITH n, size([(n)-[]-()|1]) AS d WHERE d>150 RETURN labels(n)[0]+':'+coalesce(n.name,'?')+' deg='+toString(d) AS v ORDER BY d DESC LIMIT 10", 0),
    ("W", "E-구조", "고아 노드(엣지 0)",
     "MATCH (n) WHERE NOT (n)-[]-() RETURN labels(n)[0]+':'+coalesce(n.name,'?') AS v LIMIT 10", 5),
]


def _run_graph_checks(session) -> list[str]:
    flags = []
    print("\n[이상 스캔]")
    for sev, cat, title, cypher, threshold in CHECKS:
        rows = [r["v"] for r in session.run(cypher)]
        over = len(rows) > threshold
        icon = ("🔴" if sev == "E" else "🟡") if over else "✓"
        line = f"  {icon} [{cat}] {title}: {len(rows)}건"
        print(line)
        if over:
            flags.append(f"{icon} {cat} {title} ({len(rows)})")
            for v in rows[:5]:
                print(f"        - {v}")
    return flags


def _special_checks(session) -> list[str]:
    """단일 쿼리로 안 되는 집계·통계 이상."""
    flags = []
    print("\n[집계·통계 이상]")

    # C: 최대주주 특수관계인 지분합 > 100 (기업별)
    q = ("MATCH (o)-[r:OWNS_STAKE_IN {subtype:'최대주주'}]->(c:Company) "
         "WITH c, sum(r.ratio) AS total WHERE total > 100.5 "
         "RETURN c.name AS name, round(total,1) AS total ORDER BY total DESC LIMIT 8")
    rows = list(session.run(q))
    icon = "🔴" if rows else "✓"
    print(f"  {icon} [C-값] 최대주주 지분합>100%: {len(rows)}건")
    for r in rows:
        print(f"        - {r['name']}: {r['total']}%")
    if rows:
        flags.append(f"🔴 최대주주 지분합>100 ({len(rows)})")

    # F: 동일 이름 Person이 여러 person_key로 분산(겸직 미병합 의심)
    q = ("MATCH (p:Person) WITH p.name AS nm, collect(DISTINCT p.person_key) AS keys "
         "WHERE size(keys)>1 RETURN nm, size(keys) AS k ORDER BY k DESC LIMIT 8")
    rows = list(session.run(q))
    icon = "🟡" if rows else "✓"
    print(f"  {icon} [F-통계] 동명이인/겸직 미병합 의심: {len(rows)}건")
    for r in rows:
        print(f"        - {r['nm']}: {r['k']}개 키")
    if rows:
        flags.append(f"🟡 동명 Person 분산 ({len(rows)})")

    return flags


def _cross_db_checks(session) -> list[str]:
    """Neo4j ↔ PostgreSQL ↔ ChromaDB 참조 정합성 (§5-5)."""
    flags = []
    print("\n[크로스-DB 참조 정합성]")

    # Neo4j 엣지의 evidence_id / corp_code 수집
    edge_ev = {r["ev"] for r in session.run(
        "MATCH ()-[r]->() WHERE r.evidence_id IS NOT NULL RETURN DISTINCT r.evidence_id AS ev")}
    node_corp = {r["cc"] for r in session.run(
        "MATCH (c:Company) WHERE c.corp_code IS NOT NULL RETURN DISTINCT c.corp_code AS cc")}

    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("SELECT chunk_id FROM vector_chunks")
        vchunks = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT corp_code FROM corp_code_master")
        master = {r[0] for r in cur.fetchall()}

    # D1: 엣지 evidence_id ∉ vector_chunks (근거 참조 끊김)
    dangling_ev = edge_ev - vchunks
    icon = "🔴" if dangling_ev else "✓"
    print(f"  {icon} [D-크로스] 엣지 evidence_id ∉ vector_chunks: {len(dangling_ev)}건")
    for v in list(dangling_ev)[:5]:
        print(f"        - {v}")
    if dangling_ev:
        flags.append(f"🔴 dangling evidence_id ({len(dangling_ev)})")

    # D2: vector_chunks ∉ 엣지 참조 (고아 청크)
    orphan_chunks = vchunks - edge_ev
    icon = "🟡" if orphan_chunks else "✓"
    print(f"  {icon} [D-크로스] 고아 evidence 청크(엣지 미참조): {len(orphan_chunks)}건")
    if orphan_chunks:
        flags.append(f"🟡 고아 청크 ({len(orphan_chunks)})")

    # D3: Neo4j corp_code ∉ corp_code_master (유령 노드)
    ghost = node_corp - master
    icon = "🔴" if ghost else "✓"
    print(f"  {icon} [D-크로스] Company corp_code ∉ master: {len(ghost)}건")
    for v in list(ghost)[:5]:
        print(f"        - {v}")
    if ghost:
        flags.append(f"🔴 유령 corp_code ({len(ghost)})")

    return flags


def _profile(session) -> None:
    print("[노드/엣지 분포]")
    for row in session.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC"):
        print(f"  노드 {row['l']}: {row['c']}")
    for row in session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"):
        print(f"  엣지 {row['t']}: {row['c']}")
    # Company 해소 상태
    print("[Company 해소 상태]")
    for row in session.run(
        "MATCH (c:Company) RETURN coalesce(c.is_stub,false) AS s, coalesce(c.resolution_status,'seed') AS r, count(*) AS c ORDER BY c DESC"):
        print(f"  stub={row['s']} res={row['r']}: {row['c']}")


def main() -> int:
    print("=" * 62)
    print(f"BizNode 데이터 품질 감사  (기준일 {TODAY})")
    print("=" * 62)

    flags: list[str] = []
    with neo4j_session() as session:
        _profile(session)
        flags += _run_graph_checks(session)
        flags += _special_checks(session)
        flags += _cross_db_checks(session)

    print("\n" + "=" * 62)
    if flags:
        print(f"⚠️  플래그 {len(flags)}건:")
        for f in flags:
            print(f"   {f}")
    else:
        print("✅ 모든 검사 통과 — 이상 없음")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
