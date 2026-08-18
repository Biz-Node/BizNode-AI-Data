"""그래프/DB의 이상을 6개 범주로 체계 스캔한다.

A 노드무결성 · B 엣지무결성 · C 값정합성 · D 크로스-DB · E 구조 · F 통계
"이미 아는 문제"뿐 아니라 무결성·참조·범위·분포 이상까지 능동 탐지한다.

실행: python -m batch.audit.graph
"""

from __future__ import annotations

import re
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
    # ★사업보고서가 거래처를 가린 표기가 **노드로 남은 것**(2026-08-10).
    #   「L사 外」·「G사 등」은 회사 이름이 아니라 **가림 표기**다. 노드로 만들면
    #   아무 데도 안 이어지고(전부 연결 1), 검색에도 뜨고, 「거래처 5곳」 같은
    #   집계를 부풀린다.
    #
    #   ※이건 규칙으로 **되는** 좁은 경우다 — 의미가 아니라 **글자 모양**이라서다.
    #     Company 2,868개를 전수로 돌려 5건 나왔고 5건 다 진짜였다(오탐 0).
    #     반대로 「2자 이하」 같은 규칙은 125건에 LG·SK·고영이 섞여 못 쓴다.
    #     규칙으로 되는 것과 안 되는 것을 가르는 기준은 **표기 규약인가**다.
    ("E", "A-노드", "익명 표기가 노드로 남음(사업보고서가 거래처를 가린 것)",
     "MATCH (c:Company) WHERE c.name =~ '^[A-Za-z가-힣]사(\\\\s*(外|외|등))?$' "
     "RETURN c.name AS v LIMIT 10", 0),
    # ★`title`은 `name`으로 통일했다(2026-08-15). 다른 라벨이 전부 `name`을 쓰는데
    #   Event 만 둘을 같은 값으로 들고 있었다.
    ("E", "A-노드", "Event name/event_id 누락",
     "MATCH (e:Event) WHERE e.event_id IS NULL OR e.name IS NULL RETURN e.event_id AS v LIMIT 10", 0),
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
    # ★문턱을 3배로 올렸다(2026-08-12). 「연매출을 넘으면 이상」이 아니다 —
    #   **장기 계약은 원래 넘는다.** 실측으로 걸린 유일한 건이 정상이었다:
    #       현대로템 → 폴란드 군비청  8.98조 · 매출대비 205% · 2025-08~2033-12
    #       → K2 전차 8년짜리 실행계약. 공시 원문을 정규식으로 읽은 값이라 정확하다.
    #   숫자가 진짜 이상한 것(단위 오류·파싱 실패)은 보통 배수가 훨씬 크다.
    ("W", "C-값", "매출대비 300% 초과(단위 오류 의심)",
     "MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE r.revenue_ratio>3 RETURN a.name+'→'+b.name+' '+toString(round(r.revenue_ratio*100))+'%' AS v LIMIT 10", 0),
    # ★금액 단위 오류(2026-08-12 신설). 뉴스에서 `amount`를 받기 시작했는데
    #   LLM이 「420억원」을 420으로 주는 실수를 한다. 추출기가 1차로 막지만
    #   (`extractor._num`) 통과분도 여기서 다시 본다 — 자릿수가 틀린 금액은
    #   「대형 계약」 집계를 통째로 뒤집는다.
    #   DART 실측 하한이 80억이라 **1억 미만이면 단위를 잘못 읽은 것**이다.
    ("W", "C-값", "금액이 1억 미만(단위 오류 의심 — 「420억」을 420으로 읽은 것)",
     "MATCH (a)-[r]->(b) WHERE r.amount IS NOT NULL AND r.amount < 100000000 "
     "RETURN type(r)+' '+a.name+'→'+b.name+'  '+toString(r.amount) AS v LIMIT 10", 0),
    ("W", "C-값", "금액이 100조 초과(자릿수 부풀림)",
     "MATCH (a)-[r]->(b) WHERE r.amount > 100000000000000 "
     "RETURN type(r)+' '+a.name+'→'+b.name+'  '+toString(r.amount) AS v LIMIT 10", 0),
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
    # ★2026-08-10 전면 수정. 원래는 「subtype에 공급어가 **있어야** 한다」는
    #   허용목록이었는데 두 가지가 틀렸다.
    #
    #   ① 전제가 틀렸다. 엣지 타입이 이미 SUPPLIES_TO인데 subtype에도 「공급」이
    #      들어 있으라는 건 중복 요구다. DART 사업보고서의 주요 매출처는 **파는
    #      물건**으로 적힌다 — 「반도체 장비」·「레이저마커 등」·「석유류제품」.
    #      그래서 97건이 걸렸는데 **90건이 멀쩡한 관계**였다. 맞는 것 90건에
    #      틀린 것 7건이 묻히면 아무도 안 본다.
    #
    #   ② 보는 곳이 틀렸다. `source_type<>'news'`로 뉴스를 뺐는데, 오분류는
    #      **LLM이 하는 뉴스 쪽**에서 난다. 파서가 만드는 DART는 표에서 칸을
    #      읽는 것이라 종류를 헷갈릴 일이 없다. 실측: 금지어로 바꿔 전 출처를
    #      보니 3건이 나왔고 **셋 다 news**였다(EPC 공사 2 · 매각 1).
    #
    #   그래서 금지목록으로 뒤집는다 — 「공급이 **아닌** 말이 들어 있으면 의심」.
    # ★「공사」를 금지어에서 뺐다(2026-08-11 2차).
    #   7건을 근거로 열어 보니 **4건이 멀쩡했다** — 전부 「수주」였다:
    #       "초순수 복합동 설비공사를 **수주**했다고 공시했다"
    #       "'평택2단지 건축 및 통신공사'를 **수주**했다고 공시했다"
    #   온톨로지가 `SUPPLIES_TO`를 「납품·수주·발주·공급계약이 나오면 이것」이라
    #   정의하고, 설비·용역도 「대주는 것」에 포함한다. 건설 수주는 맞는 관계다.
    #   (전파 관점에서도 의미가 있다 — 발주처가 흔들리면 수주사 매출이 준다)
    #
    #   남긴 것은 **소유권이 넘어가는 거래**다. 매각·출자·전환사채는 물건을
    #   대주는 게 아니라 자산·지분이 이동하는 것이라 `SUPPLIES_TO`가 아니다.
    #
    # ★「임대」도 뺐다(2026-08-12). 시설·장비를 **계속 쓰게 해 주는 것**이라
    #   서비스 제공이고, 온톨로지가 서비스를 `SUPPLIES_TO`에 넣는다. 소유권이
    #   안 넘어가므로 매각과 다르다. 다만 **빌려주는 쪽이 source**여야 한다 —
    #   실측으로 걸린 1건이 방향이 반대여서 뒤집었다(HD현대중공업 ↔ 아길라 수빅).
    #   방향 오류는 `audit/relations --scope direction`이 따로 본다.
    ("E", "D-의미", "SUPPLIES_TO인데 소유권 이전 거래(매각·전환사채·출자)",
     "MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE any(w IN "
     "['매각','전환사채','유상증자','신주','대여','차입','합병','청산','출자'] "
     "WHERE coalesce(r.subtype,'') CONTAINS w) "
     "RETURN a.name+'→'+b.name+' ['+r.subtype+'] '+coalesce(r.source_type,'?') "
     "AS v LIMIT 10", 0),
    ("E", "D-의미", "PARTNERS_WITH인데 공급계약(방향 소실 오분류)",
     "MATCH (a)-[r:PARTNERS_WITH]->(b) WHERE any(w IN ['공급계약','납품','OEM','ODM'] "
     "WHERE r.subtype CONTAINS w) RETURN a.name+'↔'+b.name+' ['+r.subtype+']' AS v LIMIT 10", 0),
    # ★`DEVELOPS`·`IMPACTS`·`HAS_EVENT`는 **비는 것이 정답**이라 뺀다(2026-08-11).
    #   「무엇을」을 Product·Event 노드와 `sign`·`event_type`이 이미 말하므로
    #   subtype이 할 말이 없다(설계는 `ontology.SUBTYPE_RULES`).
    #
    #   빼지 않았을 때: 3,691건이 걸렸는데 **3,312건이 설계상 정상**이었다.
    #   매번 뜨는 경고는 진짜를 묻는다 — 이 저장소가 반복해서 데인 실수다.
    ("W", "D-의미", "subtype 미정규화(빈값·구두점만)",
     "MATCH ()-[r]->() WHERE NOT type(r) IN ['DEVELOPS','IMPACTS','HAS_EVENT'] "
     "AND (r.subtype IS NULL OR trim(r.subtype) IN ['','.','·','-']) "
     "RETURN type(r)+' '+coalesce(startNode(r).name,'?')+'→'"
     "+coalesce(endNode(r).name,'?') AS v LIMIT 10", 0),
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
    # ★조건을 좁혔다(2026-08-12). 원래는 「양방향이면 의심」이었는데 21건을 근거로
    #   읽어 보니 **17건이 진짜 맞소송**이었다. 특허 분쟁은 침해소송 ↔ 무효심판이
    #   표준 전술이라 **양방향이 정상**이다:
    #       유진테크 ↔ 코쿠사이   침해소송 ↔ 무효심판
    #       삼성전자 ↔ ZTE      "이에 맞서 ZTE는 …맞대응했다"
    #       HMM ↔ 삼성전자      "피소 후 약 한달 만에 …맞대응"
    #
    #   오독(「A와 B가 함께 C를 제소」를 「A가 B를 제소」로 읽음)은 **한쪽에 뒷받침할
    #   문장이 없어** 근거 검증에서 걸린다. 그래서 「양방향 + 한쪽이 검증 실패」만 본다.
    #   (완벽하진 않다 — 실측 오류 4건 중 2건만 이 신호가 있었다. 다만 정상 17건에
    #    오류 2건이 묻히는 것보다는 낫다.)
    ("W", "D-의미", "양방향 SUES인데 한쪽이 근거 검증 실패(공동원고 오독 의심)",
     "MATCH (a)-[r1:SUES]->(b) WHERE EXISTS { MATCH (b)-[:SUES]->(a) } AND a.name < b.name "
     "  AND EXISTS { MATCH (x)-[r2:SUES]->(y) WHERE ((x=a AND y=b) OR (x=b AND y=a)) "
     "              AND (coalesce(r2.grounding_suspect,false) "
     "                   OR r2.grounding_verdict='unfounded') } "
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
    # ★`wrong_type`은 예외다(2026-08-15). 「관계는 있는데 타입이 틀렸다」로 끝난
    #   것이라 2차 전문 재검증을 돌릴 이유가 없다 — 고칠 대상은 타입이지 근거가 아니다.
    ("E", "H-표시", "통과인데 1차 판정만 남음(stage1 있고 suspect·verdict 없음)",
     "MATCH (a)-[r]->(b) WHERE r.grounding_stage1 IS NOT NULL "
     "AND r.grounding_stage1 <> 'wrong_type' "
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
    # ★예외 목록은 `repair/node_identity._LIST_BY_DESIGN`과 **같아야 한다.**
    #   한쪽만 늘리면 다른 쪽이 오탐을 내거나(여기) 값을 부순다(거기).
    ("E", "I-타입", "배열이 되면 안 되는 속성이 배열(노드)",
     "MATCH (x) UNWIND [k IN keys(x) WHERE valueType(x[k]) STARTS WITH 'LIST' "
     "  AND NOT k IN ['evidence_ids','source_docs','subtypes','sector','etf_list',"
     "'aliases','tags','products','sector_variants',"
     "'timeline','candidate_corp_codes','also_names','merged_keys'] "
     "AND NOT k ENDS WITH '_variants'] AS k "
     "RETURN labels(x)[0] + '.' + k AS v LIMIT 10", 0),
    ("E", "I-타입", "배열이 되면 안 되는 속성이 배열(엣지)",
     "MATCH ()-[x]->() UNWIND [k IN keys(x) WHERE valueType(x[k]) STARTS WITH 'LIST' "
     "  AND NOT k IN ['evidence_ids','source_docs','subtypes'] "
     "  AND NOT k ENDS WITH '_variants'] AS k "
     "RETURN type(x) + '.' + k AS v LIMIT 10", 0),

    ("W", "E-구조", "슈퍼노드(degree>150)",
     "MATCH (n) WITH n, size([(n)-[]-()|1]) AS d WHERE d>150 RETURN labels(n)[0]+':'+coalesce(n.name,'?')+' deg='+toString(d) AS v ORDER BY d DESC LIMIT 10", 0),
    # ★「고아 노드 N건」만 세던 것을 **표시 안 된 것**만 세도록 바꿨다(2026-08-03).
    #   34건을 되짚어 보니 원인이 하나였다 — 관계 검사가 「나란한 언급」으로 판정해
    #   마지막 엣지를 지운 자리. 엣지 삭제는 맞으니 이건 고칠 게 아니라 **치운 것**이다.
    #   `repair.orphan_nodes`가 표시하고 벡터에서 뺀다. 여기서는 **아직 안 치운 것**만
    #   띄운다 — 이미 처리한 34건이 매번 경고로 뜨면 사람이 무뎌진다.
    ("W", "E-구조", "고아 노드(엣지 0, 미처리)",
     "MATCH (n) WHERE NOT (n)-[]-() AND NOT coalesce(n.is_orphan, false) "
     "RETURN labels(n)[0]+':'+coalesce(n.name,'?') AS v LIMIT 10", 5),
    ("W", "E-구조", "표시해 뒀는데 엣지가 다시 붙음(표시 해제 필요)",
     "MATCH (n) WHERE n.is_orphan = true AND (n)-[]-() "
     "RETURN labels(n)[0]+':'+coalesce(n.name,'?') AS v LIMIT 10", 0),
    # ★`first_seen`이 없으면 홈의 「알림」과 인사이트의 「변화」 축이 통째로 막힌다.
    #   2026-08-04까지 보유율이 **0%**였는데 아무도 몰랐다 — 화면을 만들 때가 돼서야
    #   드러났고, 그때는 이미 7,130개가 생성 시각 없이 쌓여 있었다.
    #   적재기가 넣게 고쳤으니, 다시 새면 여기서 걸린다.
    ("E", "E-구조", "생성 시각(first_seen) 없는 엣지",
     "MATCH ()-[r]->() WHERE r.first_seen IS NULL "
     "RETURN type(r) AS v LIMIT 10", 0),
]


def _true_count(session, cypher: str, shown: int) -> int:
    """검사 쿼리의 **진짜** 건수. `LIMIT`을 떼고 다시 센다.

    ★왜 필요한가 (2026-08-10)

    검사 쿼리에는 화면이 넘치지 않게 `LIMIT 10`이 붙어 있는데, 건수를
    `len(rows)`로 세고 있었다. 그래서 **10건 이상은 전부 「10건」으로 보고**됐다.
    「SUPPLIES_TO인데 공급 표지 없음 (10)」을 열어 보니 실제로는 **97건**이었다.

    10건이면 「저녁에 한번 훑어보지」인데 97건이면 「검사 규칙이 틀렸나」다.
    숫자가 틀리면 **판단이 틀린다.**
    """
    base = re.sub(r"\s+LIMIT\s+\d+\s*$", "", cypher.strip(), flags=re.I)
    if base == cypher.strip():
        return shown                       # LIMIT이 없으면 이미 전수다
    try:
        # `CALL () { }` — 5.26부터 변수 스코프절이 없으면 경고가 뜬다
        return session.run(
            f"CALL () {{ {base} }} RETURN count(*) AS n").single()["n"]
    except Exception:
        return shown                       # 세지 못하면 보이는 만큼만


def _run_graph_checks(session) -> list[str]:
    flags = []
    print("\n[이상 스캔]")
    for sev, cat, title, cypher, threshold in CHECKS:
        rows = [r["v"] for r in session.run(cypher)]
        total = _true_count(session, cypher, len(rows))
        over = total > threshold
        icon = ("🔴" if sev == "E" else "🟡") if over else "✓"
        more = f" (표본 {len(rows)}건 표시)" if total > len(rows) else ""
        print(f"  {icon} [{cat}] {title}: {total}건{more}")
        if over:
            flags.append(f"{icon} {cat} {title} ({total})")
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
    # ★생년월이 **둘 다 있고 서로 다르면 다른 사람**이다 — DART가 이미 구분해 준 것이라
    #   합치면 안 된다. 그런데 전에는 이것까지 매번 띄웠다(8건 중 3건).
    #   「봐도 할 게 없는 경고」는 사람을 무디게 해서, 진짜 후보까지 흘려보내게 한다.
    #   합칠 수 있는 후보 — **한쪽이라도 생년월이 없는 쌍**만 남긴다.
    q = ("MATCH (p:Person) WITH p.name AS nm, collect(p) AS ps WHERE size(ps) > 1 "
         "WITH nm, ps, [x IN ps WHERE x.birth_year_month IS NULL] AS nobirth, "
         "     size(apoc.coll.toSet([x IN ps | x.birth_year_month])) AS distinct_b "
         "WHERE size(nobirth) > 0 "
         "RETURN nm, size(ps) AS k, [x IN ps | coalesce(x.person_key,'?')] AS keys "
         "ORDER BY k DESC LIMIT 8")
    rows = list(session.run(q))
    # ★사람이 이미 「합치지 않는다」고 판단한 이름은 뺀다(2026-08-11).
    #   그 판단이 `person_merge.py` 주석에만 있어서 감사가 매번 다시 띄웠다.
    #   판단을 코드 목록으로 옮기고 여기서 읽는다 — 판단이 바뀌면 목록에서 빼면 된다.
    from batch.repair.person_merge import REVIEWED_NOT_MERGED
    skipped = [r for r in rows if r["nm"] in REVIEWED_NOT_MERGED]
    rows = [r for r in rows if r["nm"] not in REVIEWED_NOT_MERGED]
    icon = "🟡" if rows else "✓"
    print(f"  {icon} [F-통계] 합칠 수 있는 동명 Person(한쪽에 생년월 없음): {len(rows)}건"
          + (f"  (판단 완료 {len(skipped)}건 제외)" if skipped else ""))
    for r in rows:
        print(f"        - {r['nm']}: {r['keys']}")
    if rows:
        print("          → 근거를 읽고 같은 사람이면 "
              "`batch/repair/person_merge.py`의 CONFIRMED에, "
              "다른 사람이면 REVIEWED_NOT_MERGED에 추가하세요")
        flags.append(f"🟡 동명 Person 합병 후보 ({len(rows)})")

    flags += _matrix_check(session)
    flags += _seed_identity_check(session)
    flags += _seed_merge_check(session)
    flags += _report_body_check(session)
    flags += _pending_judgement(session)
    return flags


def _report_body_check(session) -> list[str]:
    """등록된 사업보고서에 **본문 절이 실제로 있나** — 이름만 보고 잡으면 빈 문서를 잡는다.

    ★2026-08-03 실측. 「사업보고서」라는 낱말이 든 다른 공시를 최신순으로 집어
      본문 없는 문서를 등록해 두고 있었다:

        케이티     「해외증권거래소등에신고한사업보고서등의국내신고」  절 0개 · 5,634바이트
        제이브이엠  「[첨부정정]사업보고서」                        절 9개(감사보고서뿐)

      둘 다 진짜 사업보고서가 며칠 앞서 따로 있었는데 이것들이 더 최신이라 먼저 잡혔다.
      그러면 개요·사업부문·제품이 통째로 비는데 **화면에는 「데이터 없음」으로만** 보인다.
      파일 크기와 절 개수를 보면 바로 드러난다.
    """
    import glob
    import os
    from app.core.database import postgres_connection
    from pipeline.extractors.dart.downloader import DEFAULT_DOWNLOAD_DIR

    with postgres_connection() as conn:
        docs = conn.execute(
            "SELECT d.rcept_no, d.corp_code, c.name FROM documents d "
            "JOIN company_attributes c USING (corp_code) WHERE d.doc_type='사업보고서'").fetchall()

    thin = []
    for rcept, _code, name in docs:
        files = glob.glob(os.path.join(DEFAULT_DOWNLOAD_DIR, rcept, "**", "*.xml"),
                          recursive=True)
        size = max((os.path.getsize(f) for f in files), default=0)
        # 사업보고서 본문은 최소 수십만 바이트다. 10만 미만이면 본문이 아니다.
        if size < 100_000:
            thin.append((name, rcept, size))

    icon = "🔴" if thin else "✓"
    print(f"  {icon} [F-원문] 본문이 없는 사업보고서: {len(thin)}건 / {len(docs)}")
    for name, rcept, size in thin[:5]:
        print(f"        - {name}: {rcept} · {size:,}바이트 (본문이 아닌 문서를 잡았습니다)")
    if thin:
        print("          → documents에서 지우고 `batch.build.business_reports`를 다시 돌리세요")
    return [f"🔴 본문 없는 사업보고서 ({len(thin)}건)"] if thin else []


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
        # ★사유별로 나눈다(2026-08-03). 「17건 미검증」으로 뭉쳐 세고 있었는데
        #   열어 보니 넷이 섞여 있었고 셋은 **고칠 게 없는 것**이었다:
        #     합계 행 1  — 부문이 아니라 표의 총계 줄. 제외가 정상이다
        #     숫자 없음 8 — 원문에 매출액이 아예 안 적혀 있다(나우로보틱스·파두)
        #     대조 불가 5 — 전사 매출액이 없어 맞춰 볼 기준이 없다
        #     안 맞음   3 — **이것만 진짜 미검증**이다
        #   고칠 수 없는 것을 경고로 띄우면 사람이 경고를 안 본다.
        seg = dict(conn.execute("""
            SELECT CASE
                     WHEN trust_reason LIKE '합계 행%%'          THEN 'total'
                     WHEN trust_reason LIKE '부문 매출액이 전부 비어%%' THEN 'empty'
                     WHEN trust_reason LIKE '전사 매출액이 없어%%'    THEN 'nobase'
                     ELSE 'mismatch' END AS kind, count(*)
            FROM business_segments WHERE revenue_trusted IS false
            GROUP BY 1""").fetchall())
        untrusted = seg.get("mismatch", 0)
        no_source = seg.get("empty", 0) + seg.get("nobase", 0)
    ratio = bad * 100 // max(tot, 1)
    icon = "🟡" if ratio > 15 else "✓"
    print(f"  {icon} [F-표시] 기사 언론사명 결측: {bad}/{tot}건 ({ratio}%)")
    if ratio > 15:
        print("        → python -m batch.repair.press_names")
        flags.append(f"🟡 언론사명 결측 ({bad})")

    # ② 사업부문 매출 단위를 못 믿는 것 — 화면이 금액을 그대로 쓰면 안 된다
    icon = "🟡" if untrusted else "✓"
    print(f"  {icon} [C-값] 매출 단위 미검증 사업부문: {untrusted}건 "
          f"(원문에 금액이 없는 것 {no_source}건 · 합계 줄 {seg.get('total', 0)}건은 별개)")
    if untrusted:
        print("        → 어떤 배수로도 전사매출과 안 맞은 것. 화면에서 금액을 감추세요")
        flags.append(f"🟡 매출 단위 미검증 ({untrusted})")

    # ③ 이름이 겹치는 Company — **조치할 것만** 센다
    #
    #   ★2026-08-03까지 「130쌍 판단 대기」를 그냥 띄우고 있었다. 사람이 130쌍을
    #     볼 리 없으니 아무도 안 봤다. 열어 보니 여섯 종류가 섞여 있었고 그중
    #     다섯은 **정상**이었다(해외법인·펀드·계열사·우연 겹침·사업부문).
    #     `repair.name_overlap`이 분류해 주므로 여기서는 남는 것만 띄운다.
    #     실측: 130쌍 → 조치 4쌍 → 확인·적용 후 0쌍.
    from batch.repair.name_overlap import _PAIRS, _classify
    pairs = [dict(r) for r in session.run(_PAIRS)]
    todo = [(p, why) for p, t, why in ((p, *_classify(p)) for p in pairs)
            if t == "조치"]
    icon = "🟡" if todo else "✓"
    print(f"  {icon} [F-통계] 이름 겹침 중 조치 필요: {len(todo)}쌍 "
          f"(전체 {len(pairs)}쌍, 나머지는 해외법인·펀드·계열사 등 정상)")
    for p, why in todo[:3]:
        print(f"        - {p['an']} ⟷ {p['bn']} : {why}")
    if todo:
        print("        → python -m batch.repair.name_overlap  (분류를 보고 확인한 것만 --apply)")
        flags.append(f"🟡 이름 겹침 조치 필요 ({len(todo)}쌍)")
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


def _seed_identity_check(session) -> list[str]:
    """시드가 **동명 다른 법인**을 가리키고 있지 않나 — 공시가 0건이면 의심한다.

    ★2026-08-03 실측. 시드 목록의 corp_code가 틀려 있었다:

        케이씨텍  01142729(비상장)  ← 잡혀 있던 것
                 01261893(종목 281820)  ← 진짜
        태성     01911907(비상장)  ← 잡혀 있던 것
                 01366000(종목 323280)  ← 진짜

      「태성」이라는 이름의 법인이 DART에 **15개**나 있다. ETF 종목명으로 마스터를
      찾을 때 상장 여부를 안 보면 비상장 동명사를 잡는다. 그러면 DART 공시가
      0건이라 재무·사업보고서가 통째로 비는데, **화면에는 그냥 「데이터 없음」**으로
      보여서 원인을 알 수 없다.

      상장 시드인데 마스터에 종목코드가 없으면 여기서 잡는다.
    """
    import json
    from app.core.config import ETF_LIST_PATH
    from app.core.database import postgres_connection
    try:
        seeds = json.load(open(ETF_LIST_PATH, encoding="utf-8"))["companies"]
    except Exception as exc:
        print(f"  ⚠ [G-시드] 시드 목록을 못 읽었습니다 ({exc!r})")
        return []

    with postgres_connection() as conn:
        master = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT corp_code, corp_name, stock_code FROM corp_code_master").fetchall()}
        by_name: dict[str, list] = {}
        for code, (nm, sc) in master.items():
            by_name.setdefault(nm, []).append((code, sc))

    bad = []
    for co in seeds:
        cc, nm = co.get("corpCode"), co.get("companyName")
        mine = master.get(cc)
        if mine and mine[1]:            # 마스터에 종목코드가 있으면 상장 법인 ✓
            continue
        # 같은 이름으로 **상장된** 법인이 따로 있으면 그쪽이 맞다
        alt = [(c, s) for c, s in by_name.get(nm, []) if s]
        if alt:
            bad.append((nm, cc, alt[0][0], alt[0][1]))

    icon = "🔴" if bad else "✓"
    print(f"  {icon} [G-시드] 시드가 동명 비상장 법인을 가리킴: {len(bad)}곳 / 시드 {len(seeds)}")
    for nm, cur, right, sc in bad[:5]:
        print(f"        - {nm}: {cur}(비상장) → {right}(종목 {sc})가 맞습니다")
    if bad:
        print("          → data/company_list/company_list_etf.json 을 고치고 DART를 다시 받으세요")
    return [f"🔴 시드 corp_code 오지정 ({len(bad)}곳)"] if bad else []


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
        # ★`evidence` 컬렉션만 본다(2026-08-07). 예전엔 `vector_chunks` 전체를
        #   가져와 「엣지가 참조하지 않는 것」을 고아로 셌는데, 거기엔 **기업 카드**
        #   (`company` 컬렉션, `co_00126186` 꼴)도 들어 있다. 그건 통합 검색용이라
        #   애초에 엣지가 참조할 대상이 아니다.
        #
        #   그래서 「고아 청크 2,205건」이 계속 떴는데, 세어 보니 company 카드
        #   수와 **정확히 일치**했다 — 전부 오탐이었다. 정리기(`repair.evidence`)는
        #   evidence만 봐서 옳게 돌고 있었고, 그래서 「정리할 고아가 없습니다」와
        #   「고아 2,205건」이 동시에 뜨는 모순이 생겼다.
        cur.execute("SELECT chunk_id FROM vector_chunks WHERE collection = 'evidence'")
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

    # D2: evidence 청크 ∉ 엣지 참조 (고아 청크)
    orphan_chunks = vchunks - edge_ev
    icon = "🟡" if orphan_chunks else "✓"
    print(f"  {icon} [D-크로스] 고아 evidence 청크(엣지 미참조): {len(orphan_chunks)}건")
    if orphan_chunks:
        print("        → python -m batch.repair.evidence")
        flags.append(f"🟡 고아 청크 ({len(orphan_chunks)})")

    # D4: 기업 카드가 그래프와 맞나 — evidence만 보다가 **아무도 안 보던 쪽**
    #     검색으로 들어왔는데 그래프에 없는 회사면 빈 화면이 열린다(`is_orphan`과 같은 사고).
    with postgres_connection() as pg, pg.cursor() as cur:
        cur.execute("SELECT owner_key FROM vector_chunks WHERE collection = 'company'")
        card_owner = {r[0] for r in cur.fetchall()}
    live = {r["k"] for r in session.run(
        "MATCH (c:Company) WHERE NOT coalesce(c.is_orphan, false) "
        "RETURN coalesce(c.corp_code, c.name) AS k")}
    stale_cards = card_owner - live
    icon = "🟡" if stale_cards else "✓"
    print(f"  {icon} [D-크로스] 그래프에 없는 기업 카드(검색하면 빈 화면): "
          f"{len(stale_cards)}건 / 카드 {len(card_owner)}")
    for v in list(stale_cards)[:5]:
        print(f"        - {v}")
    if stale_cards:
        print("        → python -m batch.repair.orphan_nodes  (벡터에서 뺍니다)")
        flags.append(f"🟡 유효하지 않은 기업 카드 ({len(stale_cards)})")

    # D3: Neo4j corp_code ∉ corp_code_master (유령 노드)
    ghost = node_corp - master
    icon = "🔴" if ghost else "✓"
    print(f"  {icon} [D-크로스] Company corp_code ∉ master: {len(ghost)}건")
    for v in list(ghost)[:5]:
        print(f"        - {v}")
    if ghost:
        flags.append(f"🔴 유령 corp_code ({len(ghost)})")

    return flags


def _subtype_quality(session) -> list[str]:
    """subtype이 **엣지 타입 이름을 되풀이하기만 하는** 비율.

    ★왜 이 지표인가 (2026-08-11)

    subtype은 「엣지 타입이 못 하는 말」을 담으라고 둔 칸인데, 재보니 전체 60%
    (뉴스만 보면 87%)가 타입 이름이었다 — `IMPACTS/영향`·`HAS_EVENT/사건`.
    타입이 이미 한 말을 한 번 더 하면 칸이 없는 것과 정보량이 같다.

    원인은 프롬프트가 **무엇을 담으라는지 안 알려준 것**이었다. 고친 뒤 이 숫자가
    떨어지는지로 효과를 잰다. 새로 적재하는 기업분만 봐도 드러난다.

    ★뉴스와 DART를 나눠 본다. DART의 지분·임원은 규칙으로 계산돼 되풀이가 거의
      없어서, 섞으면 뉴스 쪽 악화가 묻힌다. 기업 구성비만 바뀌어도 전체값이 움직인다.
    """
    # B군 넷은 **비우는 것이 정상**이다 — 노드·다른 필드가 이미 말한다.
    empty_ok = {"HAS_EVENT", "IMPACTS", "DEVELOPS"}
    echo = {"OWNS_STAKE_IN": "지분보유", "IS_EXECUTIVE_OF": "임원",
            "SUPPLIES_TO": "공급", "PARTNERS_WITH": "협력", "ACQUIRES": "인수",
            "SUES": "소송", "COMPETES_WITH": "경쟁", "REGULATES": "규제",
            "DEVELOPS": "개발", "DEPENDS_ON": "의존", "HAS_EVENT": "사건",
            "IMPACTS": "영향"}

    print("\n[subtype 품질 — 타입 이름 되풀이 비율]")
    flags: list[str] = []
    for src in ("news", "dart"):
        tot = same = 0
        worst: list[tuple[str, int, int]] = []
        for et, dflt in echo.items():
            r = session.run(
                "MATCH ()-[r]->() WHERE type(r)=$t AND coalesce(r.source_type,'') "
                "STARTS WITH $s RETURN count(*) AS n, "
                "sum(CASE WHEN r.subtype=$d THEN 1 ELSE 0 END) AS same",
                t=et, s=src, d=dflt).single()
            n, s = r["n"], r["same"] or 0
            if not n:
                continue
            tot += n
            same += s
            if et not in empty_ok and s / n >= 0.5:
                worst.append((et, s, n))
        if not tot:
            continue
        pct = same / tot * 100
        icon = "🟡" if pct >= 50 else "✓"
        print(f"  {icon} [F-품질] {src}: {same:,}/{tot:,} ({pct:.0f}%)")
        for et, s, n in sorted(worst, key=lambda x: -x[1] / x[2]):
            print(f"        - {et} {s}/{n} ({s/n*100:.0f}%)")
        if pct >= 50:
            flags.append(f"🟡 F-품질 subtype이 타입 이름 되풀이 ({src} {pct:.0f}%)")
    print("        ※ HAS_EVENT·IMPACTS·DEVELOPS는 비우는 것이 정상입니다(설계).")
    return flags


def _doc_freshness_check(session) -> list[str]:
    """진행현황 문서가 지금 DB와 맞는지 본다.

    ★왜 검사까지 하나 (2026-08-09)

    문서는 `finalize` 꼬리에서 자동 갱신되지만, `repair.person_merge`처럼
    **따로 돌리는 스크립트**는 문서를 건드리지 않는다. 실제로 문서가 01:29
    숫자에 멈춘 채 그 뒤 정리가 돌아 뉴스 엣지 102개가 지워졌고, 1시간 45분
    동안 문서와 DB가 어긋나 있었다. 사람이 「갱신했나?」를 기억할 수는 없다.

    문서에 이미 노드·엣지 수가 박혀 있으니 **그걸 실측과 대보면** 된다.
    새 상태 파일이 필요 없다.
    """
    from pathlib import Path

    doc = Path("BizNode_추출_진행현황.md")
    if not doc.exists():
        print("  🟡 [E-구조] 진행현황 문서 없음 → python -m batch.ops.status --write-doc")
        return ["🟡 진행현황 문서 없음"]

    text = doc.read_text(encoding="utf-8")
    want = {
        "노드": ("그래프 전체 노드", "MATCH (n) RETURN count(*) AS n"),
        "엣지": ("그래프 전체 엣지", "MATCH ()-[r]->() RETURN count(*) AS n"),
        "뉴스 엣지": ("그래프 뉴스 엣지",
                   "MATCH ()-[r]->() WHERE r.source_type='news' RETURN count(*) AS n"),
    }
    drift = []
    for label, (row_name, cypher) in want.items():
        m = re.search(rf"\|\s*{re.escape(row_name)}\s*\|\s*([\d,]+)\s*\|", text)
        if not m:
            continue
        doc_n = int(m.group(1).replace(",", ""))
        live_n = session.run(cypher).single()["n"]
        if doc_n != live_n:
            drift.append(f"{label} 문서 {doc_n:,} ≠ 실제 {live_n:,} ({live_n-doc_n:+,})")

    stamp = re.search(r"생성 시각:\s*(\S+ \S+)", text)
    icon = "🟡" if drift else "✓"
    print(f"  {icon} [E-구조] 진행현황 문서 정합 "
          f"(생성 {stamp.group(1) if stamp else '?'})")
    for d in drift:
        print(f"        - {d}")
    if drift:
        print("        → python -m batch.ops.status --write-doc")
        return [f"🟡 진행현황 문서가 DB와 어긋남 ({len(drift)}항목)"]
    return []


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
    # ★`finalize` 안에서는 문서 정합 검사를 건너뛴다(2026-08-10).
    #
    #   이 감사는 finalize의 **끝에서 두 번째** 단계이고, 문서 갱신은 바로 그
    #   다음이다. 그래서 finalize가 엣지를 하나라도 고치면 여기서 반드시
    #   「문서가 어긋남」이 뜨는데 — 3초 뒤에 저절로 맞춰진다.
    #
    #   매번 뜨는 경고는 **사람을 무디게 한다.** 진짜로 어긋난 때(따로 돌린
    #   repair 스크립트가 그래프만 바꾼 경우)를 이 소음에 묻히게 하면
    #   검사를 넣은 의미가 없다. 그때는 이 모듈을 직접 돌리면 나온다.
    skip_doc = "--skip-doc-check" in sys.argv

    print("=" * 62)
    print(f"BizNode 데이터 품질 감사  (기준일 {TODAY})")
    print("=" * 62)

    flags: list[str] = []
    with neo4j_session() as session:
        _profile(session)
        flags += _run_graph_checks(session)
        flags += _special_checks(session)
        flags += _cross_db_checks(session)
        flags += _subtype_quality(session)
        if not skip_doc:
            flags += _doc_freshness_check(session)

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
