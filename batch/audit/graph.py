"""그래프/DB의 이상을 6개 범주로 체계 스캔한다.

A 노드무결성 · B 엣지무결성 · C 값정합성 · D 크로스-DB · E 구조 · F 통계
"이미 아는 문제"뿐 아니라 무결성·참조·범위·분포 이상까지 능동 탐지한다.

실행: python -m batch.audit.graph
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
    # 익명 이니셜("L사"·"H사")이 노드가 되면 서로 다른 회사가 한 노드로 뭉친다
    ("E", "C-값", "익명 이니셜 노드(L사·H사 형태)",
     "MATCH (n:Company) WHERE n.name =~ '(?:[A-Za-z]{1,2}|[가-힣])사' RETURN n.name AS v LIMIT 10", 0),
    # 같은 이름 + **같은 발생 연월**이면 갈린 것 — 이름만 같고 시점이 다르면
    # 각 기업이 각자 벌인 별개 사건이라 정상이다(삼성 2월 양산 ≠ SK 1월 양산).
    # event_id가 기사 URL 기반이던 시절의 검사를 시점까지 보도록 고쳤다.
    ("E", "C-값", "동명 Event 중복(같은 시점인데 노드가 갈림)",
     "MATCH (e:Event)-[r]-() WHERE e.name IS NOT NULL "
     "WITH e, e.name AS nm, left(coalesce(r.occurred_at, r.valid_from, ''),7) AS ym "
     "WHERE ym <> '' "
     "WITH nm, ym, count(DISTINCT e) AS c WHERE c>1 "
     "RETURN nm+' ('+ym+') ×'+toString(c) AS v LIMIT 10", 0),
    ("W", "C-값", "시황성 Event(상한가·급등 등 — 사건 아님)",
     "MATCH (e:Event) WHERE any(w IN ['상한가','하한가','급등','급락','신고가','강세','주가 상승'] "
     "WHERE e.name CONTAINS w) RETURN e.name AS v LIMIT 10", 0),

    # ── D. 관계 의미 정합성 ─────────────────────────────────
    # 아래 3종은 2026-07-28 실측에서 실제로 터진 결함이다. 재발하면 여기서 잡힌다.
    ("E", "D-의미", "SUPPLIES_TO인데 공급 표지 없음(공사·매각·전환사채 오분류)",
     "MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE r.source_type<>'news' AND NOT any(w IN "
     "['공급','납품','수주','물품','거래기본','양산','OEM','ODM','위탁생산'] "
     "WHERE r.subtype CONTAINS w) RETURN a.name+'→'+b.name+' ['+r.subtype+']' AS v LIMIT 10", 0),
    ("E", "D-의미", "PARTNERS_WITH인데 공급계약(방향 소실 오분류)",
     "MATCH (a)-[r:PARTNERS_WITH]->(b) WHERE any(w IN ['공급계약','납품','OEM','ODM'] "
     "WHERE r.subtype CONTAINS w) RETURN a.name+'↔'+b.name+' ['+r.subtype+']' AS v LIMIT 10", 0),
    ("W", "D-의미", "subtype 미정규화(빈값·구두점만)",
     "MATCH ()-[r]->() WHERE r.subtype IS NULL OR trim(r.subtype) IN ['','.','·','-'] "
     "RETURN type(r) AS v LIMIT 10", 0),
    # ★표본 심층검사(2026-08-02)에서 나온 것. 처음엔 「모순」이라 보고 ERROR로 달았는데
    #   **12건을 실제로 읽어 보니 4건은 둘 다 맞았다**:
    #       LX세미콘 -DEVELOPS-> DDI      "DDI를 설계하는 회사다"
    #       LX세미콘 -DEPENDS_ON-> DDI    "매출의 약 90%가 DDI에서 발생"
    #     자기가 만드는 제품에 **매출을 의존**하는 것은 정상이고, 단일 제품 의존도는
    #     오히려 중요한 리스크 지표다.
    #   나머지 8건은 진짜 오류인데 **어느 쪽이 틀렸는지 제각각**이라(DEVELOPS 4 ·
    #   DEPENDS_ON 3 · 둘 다 1) 자동으로 고칠 수 없다. 그래서 WARN으로 두고
    #   사람이 보게 한다 — 「둘 중 하나가 틀렸을 수 있다」는 신호일 뿐이다.
    ("W", "D-의미", "같은 제품에 DEVELOPS·DEPENDS_ON 동시(매출의존이면 정상 — 확인 필요)",
     "MATCH (c)-[:DEVELOPS]->(p) WHERE EXISTS { MATCH (c)-[:DEPENDS_ON]->(p) } "
     "RETURN c.name+' → '+p.name AS v LIMIT 15", 0),
    # 같은 두 노드가 서로 제소 — 맞소송일 수 있으나 대개 「A와 B가 함께 원고」의 오독이다
    ("W", "D-의미", "양방향 SUES(맞소송이거나 공동원고 오독)",
     "MATCH (a)-[:SUES]->(b) WHERE EXISTS { MATCH (b)-[:SUES]->(a) } AND a.name < b.name "
     "RETURN a.name+' ↔ '+b.name AS v LIMIT 10", 0),

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
    # ── G. 라벨 정합성 ──────────────────────────────────────
    #
    # ★2026-08-02 추가. 라벨이 어긋난 노드를 **아무도 안 보고 있었다.** 실측으로
    #   `TOKAI CARBON CO.,LTD.`가 Person과 Company로 갈려 있었고(같은 회사가 두 자리),
    #   노조 조합원이 Company로 들어와 있었다. 위 33개 검사 중 어느 것도 못 잡는다.
    ("E", "G-라벨", "라벨 둘 이상(Company·Person·Organization 겹침)",
     "MATCH (n) WITH n, [l IN labels(n) WHERE l IN ['Company','Person','Organization']] AS ls "
     "WHERE size(ls) > 1 "
     "RETURN n.name + ' — ' + reduce(s='', l IN ls | s + l + ' ') AS v LIMIT 10", 0),
    ("E", "G-라벨", "법인격 표기인데 :Person(㈜·Inc.·Ltd. 등)",
     "MATCH (p:Person) WHERE p.name =~ '.*(㈜|\\\\(주\\\\)|주식회사|유한회사).*' "
     "OR toLower(p.name) =~ '.*[ ,]\\\\s*(inc|ltd|llc|corp|gmbh|plc|limited)\\\\.?$' "
     "RETURN p.name AS v LIMIT 10", 0),
    ("W", "G-라벨", "단체 표기인데 :Company(노조·조합원·위원회 등)",
     "MATCH (c:Company) WHERE c.name =~ '.*(조합원|노동조합|노조|위원회|협회|연맹).*' "
     "RETURN c.name AS v LIMIT 10", 0),

    # ── H. 검사 표시 정합성 ─────────────────────────────────
    #
    # ★이 저장소가 **세 번 데인 실패 방식**이다 — 낡은 표시를 안 지워서 생기는 모순.
    #   ① 통과했는데 `grounding_stage1`이 남음 → 보고서가 거짓말 (1,116건)
    #   ② 다시 걸었는데 옛 `grounding_verdict`가 남음 → **참인 관계가 숨겨짐** (44건)
    #   리스크 점수는 `grounding_suspect`에 직접 걸려 있어(파급 대상의 9.5%를 빼고
    #   64.5%의 점수를 바꾼다) 이 모순은 곧 **리스크 숫자의 재현성 문제**다.
    ("E", "H-표시", "통과인데 1차 판정만 남음(stage1 있고 suspect·verdict 없음)",
     "MATCH (a)-[r]->(b) WHERE r.grounding_stage1 IS NOT NULL "
     "AND r.grounding_suspect IS NULL AND r.grounding_verdict IS NULL "
     "RETURN a.name + ' -' + type(r) + '-> ' + b.name AS v LIMIT 10", 0),
    ("E", "H-표시", "숨겼는데 전문 재검증은 통과(suspect인데 verdict=confirmed)",
     "MATCH (a)-[r]->(b) WHERE coalesce(r.grounding_suspect,false) "
     "AND r.grounding_verdict = 'confirmed' "
     "RETURN a.name + ' -' + type(r) + '-> ' + b.name AS v LIMIT 10", 0),

    # ── I. 타입 정합성 ──────────────────────────────────────
    #
    # ★`apoc.refactor.mergeNodes(properties:'combine')`가 스칼라를 배열로 바꾼다.
    #   세 번 샜다 — 엣지에서, `n.name`에서, `r.occurred_at`에서. 그때마다
    #   `TypeError: unhashable type: 'list'`로 **다른 배치가 죽었다**.
    #   `node_identity.unlist_scalars`가 되돌리지만, 되돌렸는지 보는 눈이 없었다.
    ("E", "I-타입", "배열이 되면 안 되는 속성이 배열(노드)",
     "MATCH (x) UNWIND [k IN keys(x) WHERE valueType(x[k]) STARTS WITH 'LIST' "
     "  AND NOT k IN ['evidence_ids','source_docs','subtypes','sector','etf_list',"
     "'aliases','tags','products','sector_variants'] AND NOT k ENDS WITH '_variants'] AS k "
     "RETURN labels(x)[0] + '.' + k AS v LIMIT 10", 0),
    ("E", "I-타입", "배열이 되면 안 되는 속성이 배열(엣지)",
     "MATCH ()-[x]->() UNWIND [k IN keys(x) WHERE valueType(x[k]) STARTS WITH 'LIST' "
     "  AND NOT k IN ['evidence_ids','source_docs','subtypes'] "
     "  AND NOT k ENDS WITH '_variants'] AS k "
     "RETURN type(x) + '.' + k AS v LIMIT 10", 0),

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

    flags += _matrix_check(session)
    flags += _seed_merge_check(session)
    flags += _pending_judgement(session)
    return flags


def _pending_judgement(session) -> list[str]:
    """**고칠 도구는 있는데 검사 항목이 없던 것들.** 셋 다 사람 판단이 남는다.

    ★검사 없이 도구만 있으면, 도구를 돌렸는지 아무도 모른다. 실제로 이 셋은
      2026-08-02까지 "제가 기억하면 돌리는" 상태였다 — 그게 이 저장소에서
      문제가 계속 새로 나오던 이유다. 숫자로 띄워야 눈에 걸린다.
    """
    from app.core.database import postgres_connection
    flags = []

    # ① 언론사명 결측 — 「뉴스/이슈」 화면이 매체를 못 적는다
    with postgres_connection() as conn:
        tot, bad = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE press IS NULL OR press='' "
            "OR press LIKE '%.%') FROM news_articles").fetchone()
        untrusted = conn.execute(
            "SELECT count(*) FROM business_segments WHERE revenue_trusted IS false"
        ).fetchone()[0]
    ratio = bad * 100 // max(tot, 1)
    icon = "🟡" if ratio > 15 else "✓"
    print(f"  {icon} [F-표시] 기사 언론사명 결측: {bad}/{tot}건 ({ratio}%)")
    if ratio > 15:
        print("        → python -m batch.repair.press_names")
        flags.append(f"🟡 언론사명 결측 ({bad})")

    # ② 사업부문 매출 단위를 못 믿는 것 — 화면이 금액을 그대로 쓰면 안 된다
    icon = "🟡" if untrusted else "✓"
    print(f"  {icon} [C-값] 매출 단위 미검증 사업부문(revenue_trusted=false): {untrusted}건")
    if untrusted:
        print("        → 어떤 배수로도 전사매출과 안 맞은 것. 화면에서 금액을 감추세요")
        flags.append(f"🟡 매출 단위 미검증 ({untrusted})")

    # ③ 이름이 겹치는 Company — 자동 병합은 위험해서 **사람이 봐야 한다**
    #   (실측 127쌍 중 대부분이 모/자회사·사업부문이라 합치면 안 된다)
    rows = [dict(r) for r in session.run(
        "MATCH (a:Company), (b:Company) "
        "WHERE a.norm_name < b.norm_name AND size(a.norm_name) >= 4 "
        "  AND b.norm_name CONTAINS a.norm_name "
        "  AND NOT (a.corp_code IS NOT NULL AND b.corp_code IS NOT NULL "
        "           AND a.corp_code <> b.corp_code) "
        "RETURN a.name + ' ⟷ ' + b.name AS v LIMIT 200")]
    icon = "🟡" if rows else "✓"
    print(f"  {icon} [F-통계] 이름이 겹치는 Company(사람 판단 대기): {len(rows)}쌍")
    for r in rows[:3]:
        print(f"        - {r['v']}")
    if rows:
        print("        → python -m batch.repair.node_identity --only overlap")
        flags.append(f"🟡 이름 겹침 미판정 ({len(rows)}쌍)")
    return flags


def _matrix_check(session) -> list[str]:
    """실제 그래프가 **노드-엣지 허용 행렬**을 지키고 있나.

    ★적재기(`validators/matrix.py`)가 이미 막는데 왜 또 보나 — **적재 뒤에 라벨이
      바뀌기 때문이다.** `repair/node_identity`가 Person을 Company로 옮기거나
      노드를 병합하면, 통과했던 엣지가 사후에 위반이 될 수 있다. 막는 자리와
      확인하는 자리는 달라야 한다.
    """
    from pipeline.validators.matrix import validate_edge

    rows = [dict(r) for r in session.run(
        "MATCH (a)-[r]->(b) RETURN type(r) AS t, "
        "[l IN labels(a) WHERE l IN ['Company','Person','Organization','Product','Event']][0] AS al, "
        "[l IN labels(b) WHERE l IN ['Company','Person','Organization','Product','Event']][0] AS bl, "
        "count(*) AS n")]
    bad = [r for r in rows if not validate_edge(r["al"], r["t"], r["bl"])[0]]
    icon = "🔴" if bad else "✓"
    print(f"  {icon} [B-엣지] 노드-엣지 허용 행렬 위반: {len(bad)}종 / 조합 {len(rows)}종")
    for r in bad[:5]:
        print(f"        - {r['al']} -{r['t']}-> {r['bl']}  {r['n']}건")
    return [f"🔴 매트릭스 위반 ({len(bad)}종)"] if bad else []


def _seed_merge_check(session) -> list[str]:
    """**아직 안 넣은 시드를 넣으면 노드가 갈리는가** — 넣기 전에 답한다.

    ★적재기는 시드를 `corp_code`로, stub을 `norm_name`으로 MERGE한다
      (`graph_loader._company_ident`). 그래서 같은 회사라도 **stub에 corp_code가
      없으면 시드 적재가 새 노드를 만든다** — 기존 stub에 붙어 있던 관계가
      통째로 고아가 된다. 눈에 안 띈다. 그냥 「연결이 적은 회사」로 보인다.

      이 검사는 그 일이 일어날지 **미리** 말한다. 확장 전에 돌리는 신호등이다.
    """
    import json
    from app.core.config import ETF_LIST_PATH
    from app.core.database import postgres_connection
    from pipeline.normalizer.base import normalize_company_name

    try:
        seeds = [(c["corpCode"], c["companyName"])
                 for c in json.load(open(ETF_LIST_PATH, encoding="utf-8"))["companies"]]
    except Exception as exc:
        print(f"  ⚠ [G-확장] 시드 목록을 못 읽었습니다 ({exc!r}) — 검사 건너뜀")
        return []

    with postgres_connection() as conn:
        try:
            done = {r[0] for r in conn.execute(
                "SELECT DISTINCT corp_code FROM extraction_runs").fetchall()}
        except Exception:
            done = set()

    nodes = [dict(r) for r in session.run(
        "MATCH (c:Company) RETURN c.norm_name AS nn, c.corp_code AS cc, "
        "size([(c)--() | 1]) AS deg")]
    by_code = {n["cc"] for n in nodes if n["cc"]}
    by_norm: dict[str, list[dict]] = {}
    for n in nodes:
        by_norm.setdefault(n["nn"], []).append(n)

    pending = [(cc, nm) for cc, nm in seeds if cc not in done]
    split = []
    for cc, nm in pending:
        if cc in by_code:
            continue                       # corp_code로 붙는다 — 안전
        twin = [x for x in by_norm.get(normalize_company_name(nm), []) if x["cc"] != cc]
        if twin:
            split.append((nm, twin[0]["deg"]))

    icon = "🔴" if split else "✓"
    print(f"  {icon} [G-확장] 시드 추가 시 노드가 갈리는 기업: {len(split)}곳 "
          f"/ 미진행 {len(pending)}곳")
    for nm, deg in split[:5]:
        print(f"        - {nm}: 이름만 같은 stub의 연결 {deg}개가 고아가 됩니다")
    if split:
        print("          → 확장 전에 `python -m batch.repair.node_identity`로 "
              "corp_code를 붙이거나 병합하세요")
    return [f"🔴 시드 추가 시 노드 분열 ({len(split)}곳)"] if split else []


def _cross_db_checks(session) -> list[str]:
    """Neo4j ↔ PostgreSQL ↔ ChromaDB 참조 정합성 (§5-5)."""
    flags = []
    print("\n[크로스-DB 참조 정합성]")

    # Neo4j 엣지의 evidence_id / corp_code 수집
    # 스칼라 evidence_id + 중복 병합 시 보존한 evidence_ids 목록을 모두 참조로 인정한다.
    # (여러 기사가 같은 관계를 보도하면 엣지 하나에 근거가 여러 개 달린다)
    _ev_rows = session.run(
        "MATCH ()-[r]->() WHERE r.evidence_id IS NOT NULL OR r.evidence_ids IS NOT NULL "
        "RETURN r.evidence_id AS ev, r.evidence_ids AS evs")
    edge_ev: set[str] = set()
    for _row in _ev_rows:
        if _row["ev"]:
            edge_ev.add(_row["ev"])
        for _e in _row["evs"] or []:
            if _e:
                edge_ev.add(_e)
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
