"""공장·사업부를 **모회사에 매단다**. 비용 0.

왜 필요한가 (2026-08-03)

「이름 겹침 130쌍」을 열어 보다가 리스크 추론의 구멍을 찾았다:

    삼성전자 인도 공장      -HAS_EVENT-> 「인도 공장 무기한 파업」
    삼성전자 오스틴 반도체공장 -HAS_EVENT-> 「노동자 부상 소송」
    삼성전자 DS 사업부      -HAS_EVENT-> 「LPDDR5X 성능 문제」
    삼성전자 시스템LSI사업부  -SUPPLIES_TO-> 애플

사건이 **공장 노드**에 붙어 있다. 삼성전자 노드에는 안 붙어 있다. 그래서
`propagate_risk('인도 공장 무기한 파업')`을 돌리면 삼성전자의 고객사 1,148개
연결로 **하나도 안 퍼진다.** 화면에서는 삼성전자를 담아도 그 파업이 안 보인다.

법적으로 「삼성전자 인도 공장」은 별개 회사가 아니다. **삼성전자 그 자체**다.

왜 합치지 않고 매다나

합치면 「어느 공장인지」가 사라진다. 「삼성전자 파업」과 「인도 공장 파업」은
사용자에게 다른 정보다. 그리고 엣지 타입은 **12개로 고정**이라 `PART_OF`를
새로 만들 수도 없다.

그래서 노드 속성으로 매단다 — `part_of_corp_code`. 전파는 조회 때 이 속성을
따라 모회사까지 같이 올린다(`graph_service._PROPAGATE`). 전파를 저장하지 않는
원칙 그대로다.

판정 기준 — 이름이 「실존 법인 + 부속 꼬리」인가

  · 앞부분이 corp_code를 가진 회사 이름과 정확히 일치하고
  · 남은 꼬리가 부속 조직을 뜻하는 말로 끝난다

  해외법인(USA·시안법인)은 **넣지 않는다.** 그건 별개 법인이고 DART가 이미
  `OWNS_STAKE_IN(자회사)`로 준다. 여기 섞으면 지분 관계가 소속으로 둔갑한다.

    python -m batch.repair.business_units --dry-run
    python -m batch.repair.business_units
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 부속 조직을 뜻하는 꼬리. **법인을 뜻하는 말은 절대 넣지 않는다** —
# 「법인」·「USA」·「Inc」를 넣으면 자회사가 사업부문이 돼 버린다.
_UNIT_SUFFIX = (
    "사업부", "사업본부", "사업부문", "부문", "본부",
    "공장", "제조시설", "생산기술원", "기술원", "연구소", "연구원",
    "사업소", "캠퍼스", "랩", "센터",
)

_ALL = """
MATCH (c:Company)
RETURN elementId(c) AS eid, c.name AS name, coalesce(c.corp_code,'') AS cc,
       coalesce(c.part_of_corp_code,'') AS po,
       size([(c)-[]-()|1]) AS deg,
       size([(c)-[:HAS_EVENT]-()|1]) AS ev
"""

_ATTACH = """
MATCH (c:Company) WHERE elementId(c) = $eid
SET c.entity_kind        = '사업부문',
    c.part_of_corp_code  = $cc,
    c.part_of_name       = $pname,
    c.part_of_unit       = $unit
"""


def find_units(session) -> list[dict]:
    """「실존 법인 + 부속 꼬리」인 Company를 찾는다."""
    rows = [dict(r) for r in session.run(_ALL)]
    # corp_code가 있는 = DART가 아는 실존 법인. 긴 이름부터 맞춰야
    # 「HD현대」보다 「HD현대중공업」이 먼저 잡힌다.
    parents = sorted(((r["name"], r["cc"]) for r in rows if r["cc"] and r["name"]),
                     key=lambda x: -len(x[0]))
    out = []
    for r in rows:
        if r["cc"] or not r["name"]:      # 자기가 법인이면 부속이 아니다
            continue
        for pname, pcc in parents:
            if len(pname) < 3 or not r["name"].startswith(pname):
                continue
            if r["name"] == pname:
                continue
            unit = r["name"][len(pname):].strip(" ·-()")
            if not unit or not unit.endswith(_UNIT_SUFFIX):
                continue
            out.append({**r, "pname": pname, "pcc": pcc, "unit": unit})
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        units = find_units(session)
        if not units:
            print("모회사에 매달 사업부문 노드가 없습니다.")
            return 0

        fresh = [u for u in units if not u["po"]]
        ev = sum(u["ev"] for u in units)
        print(f"■ 「실존 법인 + 부속 꼬리」인 Company {len(units)}곳 "
              f"(새로 매달 것 {len(fresh)}곳)")
        print(f"   이 노드들이 들고 있는 사건 {ev}건이 지금 모회사 위험으로 안 올라갑니다\n")
        for u in sorted(units, key=lambda x: -x["deg"]):
            mark = " " if u["po"] else "✎"
            print(f"   {mark} {u['name'][:32]:34}← {u['pname'][:14]:16}"
                  f"연결{u['deg']:>3} 사건{u['ev']}  「{u['unit'][:14]}」")

        if not args.dry_run:
            for u in fresh:
                session.run(_ATTACH, eid=u["eid"], cc=u["pcc"],
                            pname=u["pname"], unit=u["unit"])

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0
    print(f"\n✅ {len(fresh)}곳에 `part_of_corp_code` 기록")
    print("   노드는 그대로 둡니다 — 「어느 공장인지」를 잃지 않으려고 합치지 않았습니다.")
    print("   전파는 조회할 때 이 속성을 따라 모회사까지 같이 올립니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
