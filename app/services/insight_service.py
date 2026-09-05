"""인사이트 — **합쳐야 드러나는 것.**

왜 워크스페이스 단위인가

  기업 하나를 열어서는 안 보인다. 「담은 4곳 전부가 엔비디아에 공급한다」는
  네 곳을 겹쳐 놓아야 나온다. 그래서 카드는 기업이 아니라 **워크스페이스**에
  붙는다.

「몇 곳이 겹치나」로 세면 안 된다 — **흔한 것이 매번 이긴다**

  삼성전자에 공급하는 회사는 전체 3,432곳 중 150곳(4.37%)이다. 반도체
  워크스페이스면 당연히 겹친다. 반면 엔비디아는 9곳(0.26%)뿐이다.

      엔비디아  4/4 = 100%  ÷ 0.26%  =  381배
      삼성전자  3/4 =  75%  ÷ 4.37%  =   17배

  둘 다 사실이지만 무게가 다르다. `lift` 가 이 차이를 잡는다. 그래프에서
  허브를 다리로 안 쓴 것과 **같은 문제, 같은 해법**이다 — 국민연금공단이
  3곳에 들어와 있어도 원래 33곳에 들어가는 곳이라 위로 안 올라온다.

비율 기준(「절반 이상」)은 쓰면 안 된다

  실측: 무작위 워크스페이스 30개씩 돌려 본 결과

      크기   비율≥50%   lift≥10배
       5       0.2        0.4
      12       0.1        1.1
      20       0.0        2.4      ← 비율은 죽고
      30       0.0        3.8      ← lift 는 산다

  분모가 커지면 비율은 반드시 무너진다. 30곳 중 15곳이 같은 고객을 갖는 일은
  없다. lift 는 반대로 **커질수록 재료가 는다.**

정렬은 lift 가 1차가 아니다

  lift 만으로 줄 세우면 `2/4`(전체 2곳, 858배)가 `4/4`(전체 9곳, 381배)를
  이긴다. 화면에서는 **더 많이 겹친 쪽이 위**여야 자연스럽다.
  그래서 **걸린 곳 수 1차, lift 2차.**

위험 사건은 lift 가 아니라 커버리지로 본다

  「이 사건에 걸린 회사가 세상에 2곳인데 **둘 다 여기 있다**」가 훨씬 직관적이다.

문장은 만들지만 **해석은 하지 않는다**

  `headline`·`why` 는 아래 숫자로 채운 템플릿이다. 「엔비디아 수요가 꺾이면
  위험하다」 같은 해석은 넣지 않는다 — 그건 숫자에서 안 나온다. 필요하면
  추론 계층이 이 재료를 받아 쓴다(`/retrieve` 와 같은 경계).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Optional

from app.core.database import neo4j_session
from pipeline.normalizer.ksic import label_of

# 전체 기업 수 — lift 의 분모. 노드가 늘면 조회 때 다시 센다.
_MIN_SHARED = 2        # 최소 이만큼 겹쳐야 카드가 된다
_MIN_LIFT = 10.0       # 보통의 10배 이상 몰려 있어야
_MAX_EVENTS = 3        # 연쇄 위험을 볼 때 기업당 사건 수 (전부 돌리면 못 기다린다)
_CASCADE_MAX_WS = 12   # 이보다 크면 연쇄 위험은 건너뛴다
_RISK_SLOTS = 2        # 위험 카드에 떼어 주는 상위 자리
_FRESH_SLOTS = 3       # 시점이 있는 카드(밖에서 온 사건·진행 중·만료)에 떼어 주는 자리


def _months_ago(today: str, months: int) -> str:
    """`today` 에서 `months` 개월 전(음수면 뒤). 날짜는 문자열로만 비교한다 —
    엣지의 `occurred_at`·`valid_until` 이 문자열이라 형을 맞춘다."""
    y, m = int(today[:4]), int(today[5:7])
    t = y * 12 + (m - 1) - months
    return f"{t // 12:04d}-{t % 12 + 1:02d}-{today[8:10]}"


def _months_between(a: str, b: str) -> int:
    """`a` 에서 `b` 까지 몇 개월. 만료가 「언제인지」보다 「얼마 남았는지」가 행동을 부른다."""
    try:
        return ((int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7])))
    except (ValueError, IndexError, TypeError):
        return 0


def _round_robin(cards: list[dict], kinds: tuple, slots: int) -> list[dict]:
    """상위 자리를 **종류를 돌아가며** 채운다.

    ★그냥 위에서 자르면 한 종류가 자리를 다 먹는다. 실측(2026-09-04): 로봇
      워크스페이스에서 `inbound_risk` 가 3장 나와 두 자리를 다 가져가고
      `event_ongoing`·`contract_expiring` 은 한 장도 못 올라왔다 — 셋 다
      `shared=1` 이라 순위로는 갈리지 않는다.

    ★같은 종류 두 장보다 **두 종류 한 장씩**이 홈 화면에 낫다. 홈은
      「무엇이 있나」를 보여 주는 자리이지 한 종류를 깊게 파는 자리가 아니다.

    `cards` 는 이미 정렬돼 있어야 한다 — 종류 안에서는 그 순서를 지킨다.
    """
    by_kind: dict[str, list] = defaultdict(list)
    for c in cards:
        by_kind[c["kind"]].append(c)
    out: list[dict] = []
    for i in range(max((len(v) for v in by_kind.values()), default=0)):
        for k in kinds:
            if i < len(by_kind.get(k, ())) and len(out) < slots:
                out.append(by_kind[k][i])
    return out


def _pp(word: str, has: str, no: str) -> str:
    """조사. **받침을 봐야 한다** — 「국민연금공단가」로 나갔었다."""
    if not word:
        return no
    ch = word[-1]
    if "가" <= ch <= "힣":
        return has if (ord(ch) - 0xAC00) % 28 else no
    return no if ch in "aeiouAEIOU0123456789" else has

def _ro(word: str) -> str:
    """「로 / 으로」. ★받침이 있으면 「으로」인데 **ㄹ받침만 예외**로 「로」다 —
    「LG이노텍로」로 나갔었다. `_pp` 는 이/가 처럼 두 갈래라 이 예외를 못 담는다."""
    if not word:
        return "로"
    ch = word[-1]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return "로" if jong in (0, 8) else "으로"       # 0=받침없음 · 8=ㄹ
    return "로" if ch in "aeiouAEIOU0123456789lLnNmMrR" else "으로"


# ★**화면이 감춘 관계를 카드가 되살리면 안 된다.**
#
#   `/relations/{edge_id}` 는 검증에서 걸렸거나 종료된 관계에 404 를 준다
#   (`relation_service.relation_detail` → `company_service._relation` → None).
#   그런데 인사이트 쿼리가 그 조건을 따로 적고 있어서 **어긋났다** — 카드가
#   404 나는 `edge_ids` 를 실어 보냈고(실측 2026-09-05: 53개 중 5개),
#   더 나쁘게는 **2024년에 끝난 거래를 「지금 납품합니다」로** 말했다.
#
#   조건을 함수 하나로 모아 모든 쿼리가 같은 문자열을 쓴다. 갈라 적으면 또 어긋난다.
#     grounding_suspect  근거 검증에서 걸린 것
#     is_current=false   종료된 것 (실측 1,443건 · 관계의 28%)
#     valid_until 경과   유효기간이 지난 것 (56건)
def _live(r: str) -> str:
    return (f"NOT coalesce({r}.grounding_suspect, false) "
            f"AND coalesce({r}.is_current, true) <> false "
            f"AND ({r}.valid_until IS NULL OR {r}.valid_until >= $today)")


_WS_Q = f"""
UNWIND $ks AS k
MATCH (c:Company) WHERE coalesce(c.corp_code, c.norm_name) = k
OPTIONAL MATCH (c)-[r]-(o)
  WHERE {_live('r')}
RETURN k AS me, c.name AS myname, c.ksic AS ksic,
       type(r) AS t, startNode(r) = c AS out, r.ratio AS ratio,
       elementId(r) AS eid, r.subtype AS subtype,
       coalesce(o.name, '?') AS oname,
       coalesce(o.corp_code, o.norm_name, o.event_id) AS okey,
       coalesce(o.is_risk, false) AS risk
"""

_TOTAL_Q = "MATCH (c:Company) RETURN count(c) AS n"
_BASE_CUST = ("MATCH (c:Company)-[r:SUPPLIES_TO]->(o:Company) "
              "WHERE NOT coalesce(r.grounding_suspect,false) "
              "RETURN o.name AS n, count(DISTINCT c) AS k")
_BASE_SUPP = ("MATCH (c:Company)-[r:SUPPLIES_TO]->(o:Company) "
              "WHERE NOT coalesce(r.grounding_suspect,false) "
              "RETURN c.name AS n, count(DISTINCT o) AS k")
_BASE_OWN = ("MATCH (a:Company)-[r:OWNS_STAKE_IN]->(c:Company) WHERE a <> c "
             "AND NOT coalesce(r.grounding_suspect,false) "
             "RETURN a.name AS n, count(DISTINCT c) AS k")
_BASE_EV = ("MATCH (c:Company)-[:HAS_EVENT]->(e:Event) "
            "RETURN e.name AS n, count(DISTINCT c) AS k")

# ── 2026-09-04 신설 넷 — `batch/audit/insight_candidates.py` 로 실측하고 옮긴 것
#
# 왜 넣나 — 기존 8종 중 6종이 **구조**라 어제와 오늘이 같았다. 홈 화면인데
# 매일 같은 카드가 나온다. 아래 넷은 전부 **시점이 있다.**
#
#   inbound_risk       담지 않은 곳의 사건이 담은 곳까지 — 사용자가 직접 못 본다
#   event_ongoing      국면이 진행 중인 사건 — 「지금 어디까지 왔나」
#   bottleneck         여럿이 한 중간 노드를 거친다 — 1홉으로는 안 보인다
#   contract_expiring  만료 임박 — 행동 가능성이 가장 높다
#
# ★`cascade_risk` 와 `inbound_risk` 는 다르다. 저쪽은 담은 A → 담은 B(둘 다 안),
#   이쪽은 **밖 → 안**이다. 밖에서 오는 충격이라 사용자가 워크스페이스만 봐서는
#   알 수 없다.

# 병목으로 인정할 중간 노드의 연결 수 상한.
# ★허브를 다리로 쓰지 않는다. 삼성전자(거래 연결 359)를 거치면 거의 모든 회사에
#   닿는데 그건 관계가 아니라 **삼성전자가 크다는 뜻**이다. 기존 코드가 lift 로
#   흔한 상대를 누른 것과 같은 문제·같은 해법이다(모듈 독스트링).
#   실측(2026-09-03): 삼성전자 359 · SK하이닉스 162 · LG전자 156 · 한미반도체 38.
_HUB_DEGREE = 60
# 「최근」의 기본 창. 이보다 오래된 사건은 홈에 올리지 않는다.
_RECENT_MONTHS = 12
# 만료 임박으로 볼 앞날.
_EXPIRY_MONTHS = 12

_TRADE = "SUPPLIES_TO|DEPENDS_ON|PARTNERS_WITH"


# ① 밖에서 오는 사건 — 담지 않은 기업의 사건이 담은 기업까지 닿는다.
_INBOUND_Q = f"""
MATCH (src:Company)-[:HAS_EVENT {{role:'subject'}}]->(e:Event)-[i:IMPACTS]->(dst:Company)
WHERE coalesce(dst.corp_code, dst.norm_name) IN $ks
  AND NOT coalesce(src.corp_code, src.norm_name) IN $ks
  AND e.is_risk AND i.occurred_at >= $since
RETURN e.event_id AS eid, e.name AS ename, e.event_type AS etype,
       src.name AS srcname, max(i.occurred_at) AS at,
       collect(DISTINCT i.sign) AS signs,
       collect(DISTINCT coalesce(dst.corp_code, dst.norm_name)) AS hit,
       collect(DISTINCT elementId(i)) AS edges
ORDER BY at DESC LIMIT 6
"""

# ② 국면이 진행 중인 사건 — `timeline` 이 있고 최근에 움직였다.
_ONGOING_Q = """
MATCH (c:Company)-[r:HAS_EVENT]->(e:Event)
WHERE coalesce(c.corp_code, c.norm_name) IN $ks
  AND e.timeline IS NOT NULL AND size(e.timeline) >= 2
  AND r.occurred_at >= $since
RETURN e.event_id AS eid, e.name AS ename, e.event_type AS etype,
       e.is_risk AS risk, e.timeline AS tl, max(r.occurred_at) AS at,
       collect(DISTINCT coalesce(c.corp_code, c.norm_name)) AS hit
ORDER BY at DESC LIMIT 4
"""

# ③ 병목 — 담은 여럿이 **같은 중간 노드**를 거쳐 같은 곳에 닿는다.
#    ★중간 노드가 워크스페이스 안이면 병목이 아니다(그건 내부 연결이다).
#    ★중간 노드가 허브면 제외한다(`_HUB_DEGREE`).
_BOTTLENECK_Q = f"""
MATCH (c:Company)-[r1:{_TRADE}]->(mid:Company)-[r2:{_TRADE}]->(dst:Company)
WHERE {_live('r1')} AND {_live('r2')}
  AND coalesce(c.corp_code, c.norm_name) IN $ks
  AND NOT coalesce(mid.corp_code, mid.norm_name) IN $ks
  AND NOT coalesce(dst.corp_code, dst.norm_name) IN $ks
WITH mid, collect(DISTINCT coalesce(c.corp_code, c.norm_name)) AS hit,
     collect(DISTINCT dst.name) AS dsts, collect(DISTINCT r1.subtype) AS r1sub,
     collect(DISTINCT elementId(r1)) + collect(DISTINCT elementId(r2)) AS edges
WHERE size(hit) >= 2
MATCH (mid)-[h:{_TRADE}]-()
WITH mid, hit, dsts, edges, r1sub, count(h) AS deg
WHERE deg <= $hub
RETURN mid.name AS midname, hit, dsts[0..3] AS dsts, deg, edges[0..6] AS edges,
       [x IN r1sub WHERE x IS NOT NULL AND x <> ''][0..3] AS what
ORDER BY size(hit) DESC, deg ASC LIMIT 3
"""

# ④ 만료 임박 — `valid_until` 이 앞으로 다가온다.
_EXPIRY_Q = f"""
MATCH (a:Company)-[r:{_TRADE}]->(b:Company)
WHERE coalesce(a.corp_code, a.norm_name) IN $ks
  AND NOT coalesce(r.grounding_suspect, false)
  AND coalesce(r.is_current, true) <> false
  AND r.valid_until IS NOT NULL
  AND r.valid_until >= $today AND r.valid_until <= $until
RETURN a.name AS aname, coalesce(a.corp_code, a.norm_name) AS akey,
       b.name AS bname, r.valid_until AS until, r.subtype AS subtype,
       elementId(r) AS eid
ORDER BY r.valid_until ASC LIMIT 4
"""

_RISK_EV = """
UNWIND $ks AS k
MATCH (c:Company)-[:HAS_EVENT]->(e:Event)
WHERE coalesce(c.corp_code, c.norm_name) = k AND e.is_risk
RETURN k AS me, e.name AS name, e.event_id AS id,
       coalesce(e.occurred_at, e.last_seen) AS at
ORDER BY at DESC
"""


def _card(kind: str, headline: str, why: str, keys: list[str],
          names: list[str], of: int, **extra) -> dict:
    card = {"kind": kind, "headline": headline, "why": why,
            "keys": keys, "names": names, "shared": len(keys), "of": of,
            "subject": None, "subject_key": None, "base": None,
            "base_pct": None, "lift": None, "lift_label": None, "coverage": None,
            "event_id": None, "score": None, "stated": None, "path": [],
            "edge_ids": []}
    card.update(extra)
    return card


def _how_many(shared: int, of: int) -> str:
    """「4곳 중 4곳」보다 **「전부」**가 읽기 쉽다.

    ★「담은 N곳」이라고 쓰지 않는다(2026-09-04). 이 카드가 워크스페이스에 붙는다는
      것은 화면이 이미 말하고 있어서, 문장마다 되풀이하면 군더더기가 된다.
      **안팎을 갈라야 할 때만** 「워크스페이스」라고 쓴다.
    """
    return f"{of}곳이 전부" if shared == of else f"{of}곳 중 {shared}곳이"


def _lift_label(lift: float) -> str:
    """lift 를 **말로.** 화면은 이 단어를 쓰고, 숫자(`lift`)는 응답에 그대로 남는다.

    ★사용자가 「381배」를 어떻게 읽어야 할지는 스스로 판단해야 한다. 경계값을
      우리가 정해 주는 대신 원 수치를 지워 버리지는 않는다 — 검증은 숫자로 한다.
    """
    if lift >= 100:
        return "매우 이례적"
    if lift >= 30:
        return "이례적"
    return "다소 몰림"


def _concentration(kind: str, bucket: dict, base: dict, total: int,
                   names_of: dict, of: int, head_tail: str, noun: str,
                   edges: Optional[dict] = None,
                   whats: Optional[dict] = None) -> list[dict]:
    """공통 고객·공급사·주주 — **lift 로 흔한 것을 눌러 준다.**

    ★`headline` 은 **대상 이름을 다시 쓰지 않는다.** 화면이 `subject` 를 카드
      제목으로 올리기 때문에, 문장에도 넣으면 「엔비디아 / 엔비디아에 …」가 된다.
    """
    out = []
    for subject, keys in bucket.items():
        if len(keys) < _MIN_SHARED:
            continue
        b = base.get(subject, 0)
        if b <= 0:
            continue
        lift = (len(keys) / of) / (b / total)
        if lift < _MIN_LIFT:
            continue
        ks = sorted(keys)
        # ★**무엇을** 파는지 붙인다. 「공급합니다」만 있으면 사용자가 무슨 거래인지
        #   모른다 — `SUPPLIES_TO.subtype` 이 1,179건 중 983건(83%)에 있는데
        #   쓰지 않고 있었다(실측 2026-09-04). 「반도체 장비」·「협동로봇」·「DDI」.
        w = " · ".join(sorted((whats or {}).get(subject, ()))[:3])
        out.append(_card(
            kind,
            f"{_how_many(len(ks), of)} {head_tail}" + (f" — {w}" if w else ""),
            f"{subject}{noun.format(p=_pp(subject, '이', '가'))} "
            f"전국 {b}곳{'뿐입니다' if b <= 30 else '입니다'}",
            ks, [names_of[k] for k in ks], of,
            subject=subject, base=b, base_pct=round(b / total * 100, 2),
            lift=round(lift, 1), lift_label=_lift_label(lift),
            edge_ids=sorted((edges or {}).get(subject, ()))))
    return out


def workspace_insights(keys: list[str], limit: int = 5,
                       today: Optional[str] = None) -> list[dict]:
    """담아 둔 기업들에서 **합쳐야 드러나는 것**을 뽑는다.

    ★`today` 를 주입할 수 있게 둔다 — 시점이 있는 카드(밖에서 오는 사건 · 진행 중 ·
      만료 임박)가 생겨서, 테스트가 「오늘」을 고정하지 못하면 시간이 지나며 깨진다.
      `search/service/orchestrator.py` 가 `today` 를 받는 것과 같은 관례다.
    """
    today = today or date.today().isoformat()
    ks = list(dict.fromkeys(k for k in keys if k))
    of = len(ks)
    # ★1곳이면 「겹친다」가 성립하지 않는다. 빈 배열이 정답이다.
    if of < 2:
        return []

    with neo4j_session() as s:
        total = s.run(_TOTAL_Q).single()["n"] or 1
        rows = [dict(r) for r in s.run(_WS_Q, ks=ks, today=today)]
        base_c = {r["n"]: r["k"] for r in s.run(_BASE_CUST)}
        base_s = {r["n"]: r["k"] for r in s.run(_BASE_SUPP)}
        base_o = {r["n"]: r["k"] for r in s.run(_BASE_OWN)}
        base_e = {r["n"]: r["k"] for r in s.run(_BASE_EV)}

    names_of = {r["me"]: r["myname"] for r in rows}
    if not names_of:
        return []

    # ★그래프에 없는 키는 **여기서 떨군다.** 검색은 DART 명부 118,535곳까지
    #   보여 주므로(`in_graph=false`) 아직 수집 안 한 회사가 그대로 담겨 올 수 있다.
    #   실측(2026-08-18): 한화오션엔지니어링(01622599)을 담으면 `names_of[k]` 가
    #   KeyError 로 터져 **500** 이 났다. 겹칠 것이 없는 회사지 오류가 아니다.
    ks = [k for k in ks if k in names_of]
    of = len(ks)
    if of < 2:
        return []

    cust: dict[str, set] = defaultdict(set)
    supp: dict[str, set] = defaultdict(set)
    own: dict[str, set] = defaultdict(set)
    ratio_of: dict[str, list] = defaultdict(list)
    ev: dict[tuple, set] = defaultdict(set)
    ksic: dict[str, Optional[str]] = {}
    compete: set = set()
    # ★버킷과 **같은 열쇠**로 엣지 id 를 모은다 — 카드가 근거로 가는 유일한 통로다.
    #   `/relations/{edge_id}` 가 `elementId(r)` 를 받으므로 그대로 실어 보낸다.
    e_cust: dict[str, set] = defaultdict(set)
    e_supp: dict[str, set] = defaultdict(set)
    e_own: dict[str, set] = defaultdict(set)
    e_comp: set = set()
    # 무엇을 파는가 — 카드가 「공급합니다」로 끝나지 않게 한다.
    w_cust: dict[str, set] = defaultdict(set)
    w_supp: dict[str, set] = defaultdict(set)

    for r in rows:
        ksic[r["me"]] = r["ksic"]
        t = r["t"]
        if t == "SUPPLIES_TO":
            (cust if r["out"] else supp)[r["oname"]].add(r["me"])
            (e_cust if r["out"] else e_supp)[r["oname"]].add(r["eid"])
            if r["subtype"]:
                (w_cust if r["out"] else w_supp)[r["oname"]].add(r["subtype"])
        elif t == "OWNS_STAKE_IN" and not r["out"]:
            own[r["oname"]].add(r["me"])
            e_own[r["oname"]].add(r["eid"])
            if r["ratio"] is not None:
                ratio_of[r["oname"]].append(float(r["ratio"]))
        elif t == "HAS_EVENT" and r["risk"]:
            ev[(r["oname"], r["okey"])].add(r["me"])
        elif t == "COMPETES_WITH" and r["okey"] in ks:
            compete.add(frozenset((r["me"], r["okey"])))
            e_comp.add(r["eid"])

    cards: list[dict] = []

    # ① 거래 집중
    cards += _concentration("shared_customer", cust, base_c, total, names_of, of,
                            "납품합니다", "에 공급하는 회사는", e_cust, w_cust)
    cards += _concentration("shared_supplier", supp, base_s, total, names_of, of,
                            "공급받습니다", "{p} 공급하는 회사는", e_supp, w_supp)

    # ② 지분 집중 — 지분율도 함께
    # ★방향을 뒤집어 쓰면 **뜻이 반대가 된다.** 「3곳이 국민연금공단의 주주로
    #   있습니다」로 나갔었는데, 실제로는 국민연금공단이 그 3곳의 주주다.
    for c in _concentration("shared_owner", own, base_o, total, names_of, of,
                            "주주로 두고 있습니다", "{p} 주주로 들어간 회사는", e_own):
        rs = ratio_of.get(c["subject"]) or []
        if rs:
            c["why"] += f" · 지분 {min(rs):.1f}~{max(rs):.1f}%"
        cards.append(c)

    # ③ 공통 위험 사건 — **lift 가 아니라 커버리지로 본다**
    for (name, eid), got in ev.items():
        if len(got) < _MIN_SHARED:
            continue
        b = max(base_e.get(name, len(got)), len(got))
        ks_ = sorted(got)
        cards.append(_card(
            "shared_risk",
            f"{_how_many(len(ks_), of)} 걸려 있습니다",
            (f"이 사건에 걸린 회사가 전국 {b}곳인데 "
             + ("전부 담겨 있습니다" if len(ks_) == b else f"그중 {len(ks_)}곳이 담겨 있습니다")),
            ks_, [names_of[k] for k in ks_], of,
            subject=name, subject_key=eid, event_id=eid,
            base=b, coverage=round(len(ks_) / b * 100, 1)))

    # ④ 연쇄 위험 — 담은 A 의 사건이 담은 B 까지 닿나
    #    ★`propagate_risk` 를 그대로 쓴다. 점수 규칙을 두 번 구현하면 어긋난다.
    if of <= _CASCADE_MAX_WS:
        from app.services.graph_service import propagate_risk

        with neo4j_session() as s:
            evs = [dict(r) for r in s.run(_RISK_EV, ks=ks)]
        per: dict[str, list] = defaultdict(list)
        for r in evs:
            if len(per[r["me"]]) < _MAX_EVENTS:
                per[r["me"]].append(r)
        # ★사건마다 **한 번만** 계산하고 상대별로 나눠 쓴다.
        #   상대마다 다시 부르면 같은 사건을 몇 번씩 돈다 — 실측으로 3.1초였다.
        want = {names_of[k]: k for k in ks}
        seen_pair, done_ev = set(), set()
        for src, items in per.items():
            for e in items:
                if e["id"] in done_ev:
                    continue
                done_ev.add(e["id"])
                for p in propagate_risk(e["name"]):
                    tgt = want.get(p.target)
                    if tgt is None or tgt == src or (e["id"], tgt) in seen_pair:
                        continue
                    seen_pair.add((e["id"], tgt))
                    cards.append(_card(
                        "cascade_risk",
                        (f"{names_of[src]}의 일이 {names_of[tgt]}에도 영향을 줬습니다"
                         if p.stated else
                         f"{names_of[src]}에서 {names_of[tgt]}"
                         f"{_ro(names_of[tgt])} 번질 수 있습니다"),
                        ("기사가 직접 그렇게 보도했습니다" if p.stated
                         else "기사에 없습니다 — 공급망을 타고 우리가 계산한 것입니다"),
                        [src, tgt], [names_of[src], names_of[tgt]], of,
                        subject=e["name"], subject_key=e["id"], event_id=e["id"],
                        score=round(p.score, 3), stated=p.stated,
                        path=list(p.path),
                        # ★경로를 이루는 실제 엣지. 「마이크론을 거쳐 왔다」를
                        #   말로만 하지 않고 그 선을 열 수 있게 한다.
                        edge_ids=list(p.edge_ids)))

    # ④-2 밖에서 오는 사건 · 진행 중 · 병목 · 만료 임박 (2026-09-04 신설)
    #     ★넷 다 **시점이 있다.** 구조 카드만으로는 홈이 어제와 같아진다.
    since = _months_ago(today, _RECENT_MONTHS)
    until = _months_ago(today, -_EXPIRY_MONTHS)
    with neo4j_session() as s:
        for r in s.run(_INBOUND_Q, ks=ks, since=since):
            got = [k for k in r["hit"] if k in names_of]
            if not got:
                continue
            who = (names_of[got[0]] if len(got) == 1
                   else f"{len(got)}곳")
            # ★`sign` 을 쓴다. 「닿습니다」는 그래프를 순회했다는 말이지
            #   **무슨 일이 났는지가 아니다** — 악재인지 호재인지 아는데 안 쓰고 있었다.
            #   실측: negative 535 · positive 403 · neutral 43.
            hurt = "negative" in (r["signs"] or [])
            good = "positive" in (r["signs"] or [])
            verb = ("악재로 작용했습니다" if hurt else
                    "호재로 작용했습니다" if good else "영향을 받았습니다")
            cards.append(_card(
                "inbound_risk",
                f"{who}에 {verb}",
                (f"{str(r['at'])[:10]} · " if r["at"] else "")
                + f"{r['srcname']}에서 시작한 사건입니다 — 워크스페이스 밖에서 왔습니다",
                got, [names_of[k] for k in got], of,
                subject=r["ename"], subject_key=r["eid"], event_id=r["eid"],
                edge_ids=sorted(r["edges"] or [])))

        for r in s.run(_ONGOING_Q, ks=ks, since=since):
            got = [k for k in r["hit"] if k in names_of]
            if not got:
                continue
            # `timeline` 은 "연월|이름|id" 문자열이다. 국면 이름만 뽑아 잇는다.
            phases = [t.split("|")[1] for t in (r["tl"] or []) if "|" in t]
            cards.append(_card(
                "event_ongoing",
                f"{len(r['tl'] or []) + 1}단계까지 진행됐습니다",
                " → ".join((phases + [r["ename"]])[:4])
                + (f" · 최근 {str(r['at'])[:10]}" if r["at"] else ""),
                got, [names_of[k] for k in got], of,
                subject=r["ename"], subject_key=r["eid"], event_id=r["eid"]))

        for r in s.run(_BOTTLENECK_Q, ks=ks, hub=_HUB_DEGREE, today=today):
            got = [k for k in r["hit"] if k in names_of]
            if len(got) < _MIN_SHARED:
                continue
            what = " · ".join(r["what"] or [])
            cards.append(_card(
                "bottleneck",
                f"{_how_many(len(got), of)} {r['midname']}"
                f"{_pp(r['midname'], '을', '를')} 통해 납품합니다"
                + (f" — {what}" if what else ""),
                f"{', '.join(r['dsts'])}{_ro(r['dsts'][-1] if r['dsts'] else '')}"
                f" 가는 거래가 모두 "
                f"{r['midname']}{_pp(r['midname'], '을', '를')} 지납니다 "
                f"· {r['midname']}{_pp(r['midname'], '의', '의')} 거래 상대는 {r['deg']}곳입니다",
                got, [names_of[k] for k in got], of,
                subject=r["midname"], base=r["deg"],
                edge_ids=sorted(r["edges"] or [])))

        for r in s.run(_EXPIRY_Q, ks=ks, today=today, until=until):
            if r["akey"] not in names_of:
                continue
            # ★날짜만 주면 사용자가 오늘과 빼야 한다. **남은 기간**이 행동을 부른다.
            left = _months_between(today, str(r["until"])[:10])
            when = ("이번 달" if left <= 0 else f"{left}개월 뒤")
            cards.append(_card(
                "contract_expiring",
                f"{r['aname']}–{r['bname']} 거래가 {when} 끝납니다",
                f"{r['until']} 만료"
                + (f" · {r['subtype']}" if r["subtype"] else ""),
                [r["akey"]], [names_of[r["akey"]]], of,
                subject=r["bname"], edge_ids=[r["eid"]]))

    # ⑤ 내부 경쟁
    if compete:
        ks_ = sorted({k for pair in compete for k in pair})
        cards.append(_card(
            "internal_competition",
            f"워크스페이스 안에서 {len(compete)}쌍이 서로 경쟁합니다",
            " · ".join(" ↔ ".join(names_of[k] for k in sorted(p)) for p in compete),
            ks_, [names_of[k] for k in ks_], of, edge_ids=sorted(e_comp)))

    # ⑥ 업종 쏠림
    cnt = Counter(v for v in ksic.values() if v)
    if cnt:
        code, k = cnt.most_common(1)[0]
        if k >= _MIN_SHARED and k / of >= 0.6:
            ks_ = sorted(x for x in ks if ksic.get(x) == code)
            cards.append(_card(
                "sector_concentration",
                f"{_how_many(k, of)} 이 업종입니다",
                "업종이 겹치면 같은 규제·같은 수요 변동을 함께 받습니다",
                ks_, [names_of[x] for x in ks_], of, subject=label_of(code)))

    # ★정렬 — **걸린 곳 수 1차, lift 2차.** lift 만 쓰면 2/4(858배)가 4/4(381배)를
    #   이겨서 화면이 이상해진다.
    # ★시점이 있는 카드를 **구조 카드보다 앞에** 둔다(2026-09-04). 걸린 곳 수는
    #   여전히 1차지만, 같은 수면 「오늘 일어난 일」이 「원래 그런 구조」를 이긴다.
    order = {"inbound_risk": 0, "event_ongoing": 1, "contract_expiring": 2,
             "bottleneck": 3,
             "shared_customer": 4, "shared_supplier": 5, "shared_owner": 6,
             "internal_competition": 7, "sector_concentration": 8}
    rank = lambda c: (-c["shared"], -(c["lift"] or c["coverage"] or 0),
                      order.get(c["kind"], 9))

    # ★위험 카드에 **자리를 떼어 준다.**
    #   그냥 한 줄로 세우면 「위험 사건에 2곳이 걸렸다」가 「4곳이 같은 곳에
    #   판다」에 밀려 통째로 잘린다 — 실측으로 위험 카드 5장이 전부 밀려났다.
    #   리스크 분석 도구에서 그건 거꾸로다. 그렇다고 위험을 전부 위에 두면
    #   위험 카드가 화면을 다 먹는다. 그래서 **상위 두 자리만** 떼어 준다.
    # ★★시점 카드에도 **자리를 떼어 준다**(2026-09-04). `order` 를 앞으로 당겨
    #   봤지만 그건 **3차 기준**이라 소용이 없었다 — 1차가 `shared` 라서,
    #   「밖에서 온 사건이 담은 1곳에 닿았다」(shared=1)는 「4곳이 같은 곳에
    #   판다」(shared=4)에 무조건 밀린다. 실측(2026-09-04): 로봇 워크스페이스에서
    #   `event_ongoing`·`contract_expiring` 이 limit 10 안에 **한 장도** 못 들어왔다.
    #   위험 카드에 자리를 떼어 준 것과 같은 문제·같은 해법이다.
    _RISKY = ("shared_risk", "cascade_risk")
    _FRESH = ("inbound_risk", "event_ongoing", "contract_expiring")
    risky = sorted((c for c in cards if c["kind"] in _RISKY), key=rank)
    fresh = sorted((c for c in cards if c["kind"] in _FRESH), key=rank)
    rest = sorted((c for c in cards
                   if c["kind"] not in _RISKY and c["kind"] not in _FRESH), key=rank)
    head_fresh = _round_robin(fresh, _FRESH, _FRESH_SLOTS)
    picked = {id(c) for c in head_fresh}
    head = risky[:_RISK_SLOTS] + head_fresh
    cards = head + sorted(risky[_RISK_SLOTS:]
                          + [c for c in fresh if id(c) not in picked]
                          + rest, key=rank)

    # ⑦ 겹치는 게 없다 — **이것도 정보다.** 빈 화면보다 낫다.
    if not cards:
        return [_card(
            "no_overlap",
            f"이 {of}곳은 서로 겹치는 것이 없습니다",
            "공통 거래처·주주·사건이 없습니다. 관련 있는 기업을 더 담으면 "
            "관계가 드러납니다.",
            sorted(ks), [names_of.get(k, k) for k in sorted(ks)], of)]
    return cards[:limit]
