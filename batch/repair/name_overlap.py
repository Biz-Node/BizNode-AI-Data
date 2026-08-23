"""이름이 겹치는 Company 쌍을 **자동 분류**한다. 비용 0.

왜 필요한가 (2026-08-03)

검사기가 「이름 겹침 미판정 130쌍」을 매번 띄우는데, 사람이 130쌍을 볼 수는
없다. 그래서 아무도 안 봤다 — 경고가 있으나 마나였다.

130쌍을 전부 열어 보니 **한 종류가 아니었다.** 여섯 가지가 섞여 있다:

  ① 해외 법인      NAVER ⟷ NAVER VIETNAM · RFHIC ⟷ RFHIC US
  ② 펀드·조합       현대차증권 ⟷ 현대차증권 코스넷 미래성장 벤처투자조합
  ③ 계열사·자회사    HD현대 ⟷ HD현대오일뱅크 · SK하이닉스 ⟷ SK하이닉스시스템IC
  ④ 우연히 겹침      마이크론 ⟷ **하나**마이크론 · 이솔루션 ⟷ **피앤**이솔루션
  ⑤ 사업부문·공장    삼성전자 ⟷ 삼성전자 인도 공장    → `repair.business_units`가 처리
  ⑥ **진짜 문제**   같은 것이 두 노드 · 여러 회사가 한 노드 · 이름에 각주가 섞임

①~⑤는 **정상이거나 다른 도구가 맡는다.** 여기서 걸러 내면 ⑥만 남는다.
실측: 130쌍 → 사람이 볼 것 한 줌.

⑥이 어떻게 생겼나 (실제로 나온 것들)

    SK하이닉스시스템IC ⟷ SK하이닉스시스템아이씨      같은 회사, 표기만 다름
    마이크론테크놀러지 ⟷ 마이크론 테크놀로지          띄어쓰기만 다름
    도쿄일렉트론 ⟷ 도쿄일렉트론(Tokyo Electron)     원어명이 이름에 붙음
    ISC VINA MANUFACTURING 주2)               **각주 기호**가 이름에 섞임
    현대모비스, 현대케피코, 현대트랜시스               **세 회사가 한 노드**
    (주)아이마켓코리아/씨앤테크㈜ (SEMES 납품…)       두 회사 + 설명이 한 노드

★스스로 고치지 않는다 — ⑥은 합치기·쪼개기라 되돌리기 어렵고, 어느 쪽이 맞는
  이름인지는 **근거를 봐야** 안다. `person_merge`와 같은 방식으로, 사람이 근거를
  읽고 확인한 것만 아래 `CONFIRMED_*`에 적어 넣고 `--apply`로 적용한다.

    python -m batch.repair.name_overlap             # 분류만
    python -m batch.repair.name_overlap --all       # 걸러 낸 것까지 전부
    python -m batch.repair.name_overlap --apply     # 확인한 것만 적용
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 사람이 근거를 읽고 확인한 것 (2026-08-03) ────────────────────
#
# ★추측으로 늘리지 말 것. 한 줄 잘못 넣으면 서로 다른 두 회사가 한 노드가 된다.
CONFIRMED_MERGE: list[tuple[str, str, str]] = [
    # (남길 이름, 합칠 이름, 왜 같은 회사인가)
    ("도쿄일렉트론", "도쿄일렉트론(Tokyo Electron Limited)",
     "원어명이 이름에 붙은 것 — 둘 다 반도체 장비를 공급한다"),
    ("램리서치", "램리서치(LAM Research)",
     "원어명이 이름에 붙은 것 — 둘 다 반도체 장비를 공급한다"),
    # ★남기는 쪽을 「사업부」로 잡았다. `part_of_corp_code`가 붙어 있어
    #   위험이 삼성전자까지 올라간다 — 짧은 쪽을 남기면 그 연결이 끊긴다.
    ("삼성전자 시스템LSI사업부", "삼성전자 시스템LSI",
     "같은 사업부의 두 표기 — 모회사에 매달린 쪽을 남긴다"),
]

CONFIRMED_SPLIT: list[tuple[str, list[str], str]] = [
    # (쪼갤 이름, 나눠 담을 이름들, 왜)
    ("현대모비스, 현대케피코, 현대트랜시스",
     ["현대모비스", "현대케피코", "현대트랜시스"],
     "현대오토에버의 공급처 셋을 쉼표로 나열한 것이 한 노드가 됐다"),
]

_PAIRS = """
MATCH (a:Company), (b:Company)
WHERE a.norm_name < b.norm_name AND size(a.norm_name) >= 4
  AND b.norm_name CONTAINS a.norm_name
  AND NOT (a.corp_code IS NOT NULL AND b.corp_code IS NOT NULL
           AND a.corp_code <> b.corp_code)
RETURN a.name AS an, b.name AS bn, a.norm_name AS anm, b.norm_name AS bnm,
       coalesce(a.corp_code,'') AS ac, coalesce(b.corp_code,'') AS bc,
       coalesce(a.part_of_corp_code,'') AS apo,
       coalesce(b.part_of_corp_code,'') AS bpo,
       size([(a)-[]-()|1]) AS ad, size([(b)-[]-()|1]) AS bd
ORDER BY an
"""

# ① 해외 법인 — 별개 법인이다. DART가 `OWNS_STAKE_IN(자회사)`로 이미 준다
_FOREIGN = re.compile(
    r"(usa|u\.?s\.?a?|america|americas|europe|asia|china|japan|india|vietnam|"
    r"taiwan|singapore|korea|brasil|brazil|mexico|indonesia|poland|france|"
    r"beijing|shanghai|wuxi|dalian|arabia|australia|gmbh|s\.?a\.?s|pvt|"
    r"inc|ltd|llc|co\.,?\s*ltd|corporation|company\s*limited|"
    r"미국법인|중국법인|일본법인|북미법인|아메리카|코리아|시안법인|상하이법인|"
    r"슬로바키아법인|판매법인|현지법인|법인)\b", re.I)

# ② 펀드·조합 — 별개 기구
_FUND = re.compile(
    r"(조합|펀드|fund|투자|사모|pef|신탁|l\.?p\.?|파트너스|자산운용|"
    r"공제조합|우리사주조합|재단|학원)", re.I)

# ⑥ 여러 회사가 한 노드 — 이름에 구분자가 있다.
#
# ★구분자로 보이지만 아닌 것 두 가지를 먼저 지운다(실측):
#   「SKBATTERYAMERICA,INC.」 — 쉼표 뒤가 **법인격 표기**다. 회사가 둘이 아니다
#   「IBK-BNW 기술금융 (국내/비상장)」 — 괄호 안 슬래시는 **설명**이다
_NOT_SEP = re.compile(r",\s*(inc|ltd|llc|co|corp|s\.?a|gmbh|pte)\b\.?", re.I)
_PAREN_NOTE = re.compile(r"[(（][^)）]*[)）]")
_MULTI = re.compile(r"[,，/·]|\s및\s")

# 부속 조직 꼬리 — `business_units`와 같은 목록을 본다
_UNIT_TAIL = re.compile(
    r"(사업부|사업본부|사업부문|부문|본부|공장|제조시설|생산기술원|기술원|"
    r"연구소|연구원|사업소|캠퍼스|랩|센터)$")

# ⑥ 각주·주석이 이름에 섞임 — 「주2)」 「*1」 「(주1)」
_FOOTNOTE = re.compile(r"(주\s*\d+\s*\)|\*\s*\d+|\(\s*주\s*\d*\s*\)$)")

# ⑥ 원어명이 괄호로 붙음 — 「도쿄일렉트론(Tokyo Electron Limited)」
_ALIAS_PAREN = re.compile(r"^\(?[A-Za-z][A-Za-z0-9 .,&'-]{3,}\)?$")


def _classify(p: dict) -> tuple[str, str]:
    """(분류, 사유). 분류가 '조치'면 사람이 봐야 한다."""
    an, bn, apo, bpo = p["an"], p["bn"], p["apo"], p["bpo"]
    tail_raw = bn[len(an):].strip() if bn.startswith(an) else ""
    tail = tail_raw.strip(" ·-()（）")

    # ⑥ 같은 부속 조직이 **두 이름**으로 있다. 「사업부문이니까 정상」으로
    #    넘기면 안 되는 자리다 — 실측: 「삼성전자 시스템LSI」와 「삼성전자
    #    시스템LSI사업부」가 각각 다른 회사에 공급하는 별개 노드로 있었다.
    #
    #    ★가르는 기준은 **a가 실존 법인인가**다. 처음엔 꼬리만 보고 걸렀더니
    #      「LG전자 ⟷ LG전자 생산기술원」까지 잡혔다 — 그건 모회사와 그 부속이지
    #      중복이 아니다. a에 corp_code가 있으면 모회사이므로 뺀다.
    if not p["ac"] and bpo and bn.startswith(an):
        rest = bn[len(an):].strip(" ·-")
        if _UNIT_TAIL.fullmatch(rest):
            return "조치", f"같은 조직이 「{an}」과 「{bn}」 두 이름으로 있습니다"

    # ⑤ 사업부문·공장 — `business_units`가 이미 매달았다
    if bpo:
        return "사업부문", f"모회사에 매달림({bpo})"

    # ④ 우연 겹침 — 앞이 아니라 **중간**에 들어 있다
    if not bn.startswith(an):
        return "우연겹침", f"「{an}」이 이름 중간에 들어 있을 뿐"

    # ⑥ 여러 회사가 한 노드 (법인격 표기·괄호 설명은 구분자가 아니다)
    if _MULTI.search(_PAREN_NOTE.sub("", _NOT_SEP.sub("", bn))):
        return "조치", "여러 회사가 한 노드로 들어왔습니다 — 쪼개야 합니다"

    # ⑥ 각주 기호가 이름에 남음
    if _FOOTNOTE.search(bn):
        return "조치", "이름에 각주 기호가 섞였습니다 — 이름을 정리해야 합니다"

    if not tail:
        return "조치", "꼬리가 비었습니다 — 같은 이름의 노드가 둘입니다"

    # ⑥ 꼬리가 통째로 원어명 — 같은 회사의 다른 표기
    if _ALIAS_PAREN.match(tail_raw.strip("()（）")) and tail_raw.startswith(("(", "（")):
        return "조치", f"원어명이 이름에 붙었습니다(「{tail[:24]}」) — 같은 회사일 수 있습니다"

    # ② 펀드·조합
    if _FUND.search(tail):
        return "펀드·조합", f"「{tail[:20]}」"

    # ① 해외 법인
    if _FOREIGN.search(tail):
        return "해외법인", f"「{tail[:20]}」"

    # ③ 그 밖 — 계열사·자회사로 본다. 지분 관계가 있으면 확실하다
    return "계열사", f"「{tail[:20]}」"


_LOOK = ("MATCH (c:Company {name:$name}) "
         "RETURN elementId(c) AS id, size([(c)-[]-()|1]) AS deg")

# `mergeNodes`는 속성도 합친다. 'discard'로 남길 쪽 값을 지킨다(person_merge와 같다).
_MERGE = """
MATCH (keep:Company) WHERE elementId(keep) = $keep_id
MATCH (drop:Company) WHERE elementId(drop) = $drop_id
CALL apoc.refactor.mergeNodes([keep, drop],
     {properties: 'discard', mergeRels: true}) YIELD node
SET node.merged_names = coalesce(node.merged_names, []) + $drop_name
RETURN elementId(node) AS id
"""

# 쪼개기 — 붙어 있던 엣지를 **속성 그대로** 각 회사에 다시 만든다.
_EDGES_OF = """
MATCH (c:Company {name:$name})-[r]-(o)
RETURN elementId(startNode(r)) = elementId(c) AS outgoing,
       type(r) AS t, properties(r) AS props, elementId(o) AS other_id
"""
_ENSURE = """
MERGE (c:Company {norm_name: $norm})
ON CREATE SET c.name = $name, c.is_stub = true,
              c.split_from = $from_name
RETURN elementId(c) AS id
"""
_RECREATE_OUT = """
MATCH (a) WHERE elementId(a) = $a_id
MATCH (b) WHERE elementId(b) = $b_id
CALL apoc.create.relationship(a, $t, $props, b) YIELD rel
RETURN elementId(rel) AS id
"""
_DROP_NODE = "MATCH (c:Company {name:$name}) DETACH DELETE c"


def _apply_confirmed(session, dry_run: bool) -> None:
    print("\n■ 사람이 확인한 것 적용")

    for keep_name, drop_name, why in CONFIRMED_MERGE:
        k = session.run(_LOOK, name=keep_name).single()
        d = session.run(_LOOK, name=drop_name).single()
        if not k or not d:
            gone = keep_name if not k else drop_name
            print(f"   · 「{gone}」 없음 — 이미 합쳐졌습니다")
            continue
        print(f"   ✎ 합치기  「{drop_name}」(연결 {d['deg']}) → "
              f"「{keep_name}」(연결 {k['deg']})")
        print(f"        {why}")
        if not dry_run:
            session.run(_MERGE, keep_id=k["id"], drop_id=d["id"],
                        drop_name=drop_name)

    for combined, parts, why in CONFIRMED_SPLIT:
        c = session.run(_LOOK, name=combined).single()
        if not c:
            print(f"   · 「{combined}」 없음 — 이미 쪼개졌습니다")
            continue
        edges = [dict(r) for r in session.run(_EDGES_OF, name=combined)]
        print(f"   ✎ 쪼개기  「{combined}」 → {' · '.join(parts)}")
        print(f"        {why} (엣지 {len(edges)}개를 {len(parts)}곳에 각각 복제)")
        if dry_run:
            continue
        for pname in parts:
            pid = session.run(_ENSURE, norm=pname, name=pname,
                              from_name=combined).single()["id"]
            for e in edges:
                a, b = (pid, e["other_id"]) if e["outgoing"] else (e["other_id"], pid)
                # ★근거를 그대로 옮긴다. 쪼갠 흔적도 남긴다 — 나중에
                #   「이 엣지는 왜 셋인가」를 되짚을 수 있어야 한다.
                props = dict(e["props"])
                props["split_from"] = combined
                session.run(_RECREATE_OUT, a_id=a, b_id=b, t=e["t"], props=props)
        session.run(_DROP_NODE, name=combined)

    if not dry_run:
        from batch.repair.node_identity import unlist_scalars
        n = unlist_scalars(session)
        if n:
            print(f"\n   ↺ 병합으로 배열이 된 스칼라 속성 {n}건 되돌림")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="걸러 낸 것까지 전부 보기")
    ap.add_argument("--apply", action="store_true",
                    help="CONFIRMED_MERGE·CONFIRMED_SPLIT를 실제로 적용")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        if args.apply:
            _apply_confirmed(session, args.dry_run)
            print("\n(적용 뒤 다시 분류합니다)")
        pairs = [dict(r) for r in session.run(_PAIRS)]

    tagged = [(p, *_classify(p)) for p in pairs]
    tally = Counter(t for _, t, _ in tagged)

    print(f"■ 이름이 겹치는 Company {len(pairs)}쌍 — 자동 분류\n")
    for k, v in tally.most_common():
        mark = "🟡" if k == "조치" else "  "
        note = {"해외법인": "별개 법인 — DART가 자회사 지분으로 이미 표현",
                "펀드·조합": "별개 기구 — 합치면 안 됨",
                "계열사": "계열사·자회사 — 합치면 안 됨",
                "우연겹침": "이름이 우연히 포함됐을 뿐",
                "사업부문": "repair.business_units가 모회사에 매달았음",
                "조치": "**사람이 봐야 합니다**"}.get(k, "")
        print(f"  {mark} {k:8}{v:>5}쌍   {note}")

    todo = [(p, why) for p, t, why in tagged if t == "조치"]
    if todo:
        print(f"\n■ 사람이 봐야 하는 것 {len(todo)}쌍\n")
        for p, why in todo:
            print(f"   {p['an'][:26]:28}({p['ad']:>3}) ⟷ "
                  f"{p['bn'][:34]:36}({p['bd']:>3})")
            print(f"      {why}")
    else:
        print("\n✅ 사람이 봐야 하는 쌍이 없습니다.")

    if args.all:
        print("\n■ 걸러 낸 것 전부")
        for p, t, why in tagged:
            if t == "조치":
                continue
            print(f"   [{t:6}] {p['an'][:22]:24} ⟷ {p['bn'][:32]:34} {why[:30]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
