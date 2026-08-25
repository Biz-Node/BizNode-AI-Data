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
from typing import Optional

from app.core.database import neo4j_session
from pipeline.normalizer.ksic import label_of

# 전체 기업 수 — lift 의 분모. 노드가 늘면 조회 때 다시 센다.
_MIN_SHARED = 2        # 최소 이만큼 겹쳐야 카드가 된다
_MIN_LIFT = 10.0       # 보통의 10배 이상 몰려 있어야
_MAX_EVENTS = 3        # 연쇄 위험을 볼 때 기업당 사건 수 (전부 돌리면 못 기다린다)
_CASCADE_MAX_WS = 12   # 이보다 크면 연쇄 위험은 건너뛴다
_RISK_SLOTS = 2        # 위험 카드에 떼어 주는 상위 자리


def _pp(word: str, has: str, no: str) -> str:
    """조사. **받침을 봐야 한다** — 「국민연금공단가」로 나갔었다."""
    if not word:
        return no
    ch = word[-1]
    if "가" <= ch <= "힣":
        return has if (ord(ch) - 0xAC00) % 28 else no
    return no if ch in "aeiouAEIOU0123456789" else has

_WS_Q = """
UNWIND $ks AS k
MATCH (c:Company) WHERE coalesce(c.corp_code, c.norm_name) = k
OPTIONAL MATCH (c)-[r]-(o)
  WHERE NOT coalesce(r.grounding_suspect, false)
RETURN k AS me, c.name AS myname, c.ksic AS ksic,
       type(r) AS t, startNode(r) = c AS out, r.ratio AS ratio,
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
            "event_id": None, "score": None, "stated": None, "path": []}
    card.update(extra)
    return card


def _how_many(shared: int, of: int) -> str:
    """「4곳 중 4곳」보다 **「전부」**가 읽기 쉽다."""
    return f"담은 {of}곳이 전부" if shared == of else f"담은 {of}곳 중 {shared}곳이"


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
                   names_of: dict, of: int, head_tail: str, noun: str) -> list[dict]:
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
        out.append(_card(
            kind,
            f"{_how_many(len(ks), of)} {head_tail}",
            f"{subject}{noun.format(p=_pp(subject, '이', '가'))} "
            f"전국 {b}곳{'뿐입니다' if b <= 30 else '입니다'}",
            ks, [names_of[k] for k in ks], of,
            subject=subject, base=b, base_pct=round(b / total * 100, 2),
            lift=round(lift, 1), lift_label=_lift_label(lift)))
    return out


def workspace_insights(keys: list[str], limit: int = 5) -> list[dict]:
    """담아 둔 기업들에서 **합쳐야 드러나는 것**을 뽑는다."""
    ks = list(dict.fromkeys(k for k in keys if k))
    of = len(ks)
    # ★1곳이면 「겹친다」가 성립하지 않는다. 빈 배열이 정답이다.
    if of < 2:
        return []

    with neo4j_session() as s:
        total = s.run(_TOTAL_Q).single()["n"] or 1
        rows = [dict(r) for r in s.run(_WS_Q, ks=ks)]
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

    for r in rows:
        ksic[r["me"]] = r["ksic"]
        t = r["t"]
        if t == "SUPPLIES_TO":
            (cust if r["out"] else supp)[r["oname"]].add(r["me"])
        elif t == "OWNS_STAKE_IN" and not r["out"]:
            own[r["oname"]].add(r["me"])
            if r["ratio"] is not None:
                ratio_of[r["oname"]].append(float(r["ratio"]))
        elif t == "HAS_EVENT" and r["risk"]:
            ev[(r["oname"], r["okey"])].add(r["me"])
        elif t == "COMPETES_WITH" and r["okey"] in ks:
            compete.add(frozenset((r["me"], r["okey"])))

    cards: list[dict] = []

    # ① 거래 집중
    cards += _concentration("shared_customer", cust, base_c, total, names_of, of,
                            "공급합니다", "에 공급하는 회사는")
    cards += _concentration("shared_supplier", supp, base_s, total, names_of, of,
                            "공급받습니다", "{p} 공급하는 회사는")

    # ② 지분 집중 — 지분율도 함께
    # ★방향을 뒤집어 쓰면 **뜻이 반대가 된다.** 「3곳이 국민연금공단의 주주로
    #   있습니다」로 나갔었는데, 실제로는 국민연금공단이 그 3곳의 주주다.
    for c in _concentration("shared_owner", own, base_o, total, names_of, of,
                            "주주로 두고 있습니다", "{p} 주주로 들어간 회사는"):
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
                        f"{names_of[src]}에서 시작해 {names_of[tgt]}까지 닿습니다",
                        ("기사가 직접 그렇게 말했습니다" if p.stated
                         else "기사에 없습니다 — 공급망을 타고 우리가 계산한 것입니다"),
                        [src, tgt], [names_of[src], names_of[tgt]], of,
                        subject=e["name"], subject_key=e["id"], event_id=e["id"],
                        score=round(p.score, 3), stated=p.stated,
                        path=list(p.path)))

    # ⑤ 내부 경쟁
    if compete:
        ks_ = sorted({k for pair in compete for k in pair})
        cards.append(_card(
            "internal_competition",
            f"담은 기업 중 {len(compete)}쌍이 서로 경쟁합니다",
            " · ".join(" ↔ ".join(names_of[k] for k in sorted(p)) for p in compete),
            ks_, [names_of[k] for k in ks_], of))

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
    order = {"shared_customer": 0, "shared_supplier": 1, "shared_owner": 2,
             "internal_competition": 3, "sector_concentration": 4}
    rank = lambda c: (-c["shared"], -(c["lift"] or c["coverage"] or 0),
                      order.get(c["kind"], 9))

    # ★위험 카드에 **자리를 떼어 준다.**
    #   그냥 한 줄로 세우면 「위험 사건에 2곳이 걸렸다」가 「4곳이 같은 곳에
    #   판다」에 밀려 통째로 잘린다 — 실측으로 위험 카드 5장이 전부 밀려났다.
    #   리스크 분석 도구에서 그건 거꾸로다. 그렇다고 위험을 전부 위에 두면
    #   위험 카드가 화면을 다 먹는다. 그래서 **상위 두 자리만** 떼어 준다.
    risky = sorted((c for c in cards if c["kind"] in ("shared_risk", "cascade_risk")),
                   key=rank)
    rest = sorted((c for c in cards if c["kind"] not in ("shared_risk", "cascade_risk")),
                  key=rank)
    head = risky[:_RISK_SLOTS]
    cards = head + sorted(risky[_RISK_SLOTS:] + rest, key=rank)

    # ⑦ 겹치는 게 없다 — **이것도 정보다.** 빈 화면보다 낫다.
    if not cards:
        return [_card(
            "no_overlap",
            f"담은 {of}곳은 서로 겹치는 것이 없습니다",
            "공통 거래처·주주·사건이 없습니다. 관련 있는 기업을 더 담으면 "
            "관계가 드러납니다.",
            sorted(ks), [names_of.get(k, k) for k in sorted(ks)], of)]
    return cards[:limit]
