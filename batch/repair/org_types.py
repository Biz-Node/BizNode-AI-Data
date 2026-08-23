"""Organization 에 **종류**를 붙이고, 노드가 아닌 것을 표시한다.

왜 (2026-08-15)

`org_type` 이 580곳 전부 「기관」 한 값이었다. 그래서 갈리지 않는다:

    "이 회사를 규제한 기관이 어디인가"
      → 공정위 · 검찰 · 노조 · 협회 · 대학병원이 전부 「기관」으로 나온다

규제기관이 제재한 것과 노조가 파업한 것은 **성격이 전혀 다른 리스크**인데,
지금은 `REGULATES` 엣지 하나로 뭉뚱그려져 있다.

★규칙으로는 65%밖에 안 되고 그마저 틀린다(실측):
      「금융감독원」          → `원$` 에 걸려 연구·교육
      「금융정보분석원」        → 같은 이유
      「국제장애인올림픽위원회」   → `위원회$` 에 걸려 규제기관
  기관 이름은 접미사가 종류를 안 말해 준다. 그래서 모델이 판정한다.

분류와 함께 **노드가 아닌 것도 표시한다.** 실측으로 두 종류가 섞여 있었다:

    집합명사      「소비자 집단」 -SUES-> SK하이닉스
                 서로 다른 원고들이 한 노드로 뭉친다 — 추출기가 금지한 것인데 샜다
    관계 뒤집힘    「대만」 -IS_EXECUTIVE_OF-> 차이잉원
                 대만은 회사가 아니고 방향도 반대다

  ★지우지 않고 `node_suspect` 로 표시한다. 관계 자체가 사실인 경우가 섞여 있고
    (「중국」 -REGULATES-> 마이크론 은 맞다), 지우면 되돌릴 수 없다.

실행:
    python -m batch.repair.org_types --dry-run
    python -m batch.repair.org_types
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app.core.database import neo4j_session
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_WORKERS = 8

# ── 종류 10종 ────────────────────────────────────────────────
# 「이 기관이 우리 기업에 무엇을 할 수 있나」로 나눈다. 규제기관은 제재하고,
# 노조는 파업하고, 협회는 표준을 만든다 — 리스크의 성격이 다르다.
ORG_TYPES = [
    "규제기관",    # 공정위·금융위·금감원·국세청·ITC·FTC — 제재·인허가 권한
    "수사사법",    # 검찰·경찰·법원·특검 — 수사·판결
    "정부부처",    # 산업부·기재부·중국 정부·백악관 — 정책·보조금·수출규제
    "지자체",     # 제주도·광주시 — 부지·인허가·지역 협약
    "국가",       # 미국·중국·대만 — 국가 단위로만 언급된 것
    "노동조합",    # 노조·연맹·지부
    "협회단체",    # 협회·연합회·재단·학회
    "연구교육",    # 대학·연구원·병원·학술기관
    "공공기관",    # 한국전력공사·원자력환경공단 — 정부가 세운 사업체
    "기타",
]

_FIND = """
MATCH (o:Organization)
WHERE $full OR o.org_type IS NULL OR o.org_type = '기관'
OPTIONAL MATCH (o)-[r]-(x)
RETURN o.norm_name AS key, o.name AS name,
       collect(DISTINCT type(r) + '→' + coalesce(x.name, ''))[..5] AS rels
ORDER BY size(rels) DESC
"""

_APPLY = """
MATCH (o:Organization {norm_name: $key})
SET o.org_type = $otype, o.classified_at = datetime()
"""
_SUSPECT = """
MATCH (o:Organization {norm_name: $key})
SET o.node_suspect = true, o.node_suspect_why = $why
"""

_SYSTEM = f"""기관 이름에 **종류**를 붙이고, 그것이 **노드가 될 자격이 있는지** 판정하세요.

【종류 — 아래 10종 중 하나】
· 규제기관 : 공정위·금융위·금감원·국세청·관세청·ITC·FTC — **제재·인허가 권한**이 있는 곳
· 수사사법 : 검찰·지검·경찰·법원·특검 — 수사하거나 판결하는 곳
· 정부부처 : 산업부·기재부·국방부·중국 정부·백악관·의회 — 정책을 만드는 곳
· 지자체   : 제주도·광주시·경기도 — 광역·기초 자치단체
· 국가     : 미국·중국·대만 — **국가 이름만** 나온 것
· 노동조합 : 노조·노동조합·연맹·지부
· 협회단체 : 협회·연합회·재단·학회·조합
· 연구교육 : 대학·연구원·연구소·병원·학술기관
· 공공기관 : 한국전력공사·한국원자력환경공단 — 정부가 세운 **사업체**
· 기타     : 위 어디에도 안 맞는 것

★이름의 접미사에 속지 마세요. 실측된 오분류입니다:
      「금융감독원」      원 으로 끝나지만 **규제기관**입니다 (연구원 아님)
      「금융정보분석원」   같습니다 — 규제기관
      「국제장애인올림픽위원회」  위원회지만 **협회단체**입니다 (규제 권한 없음)
  판단 기준: **이 기관이 기업에 무엇을 할 수 있는가.**
  제재·인허가면 규제기관, 수사·판결이면 수사사법, 정책이면 정부부처입니다.

【노드가 될 자격 — is_valid】
**거의 전부 true 입니다.** false 는 두 가지뿐입니다.

  ✗ **여럿을 한 덩이로 부른 것** — 서로 다른 주체가 한 노드로 뭉칩니다
        「소비자 집단」 · 「소액주주연대」 · 「원고들」 · 「업계」 · 「조합원」
  ✗ **어느 주체인지 모를 만큼 막연한 것** — 「정부」 · 「금융 당국」

  ★**「규제 권한이 없다」는 부적격 사유가 아닙니다.** 이걸 혼동해 실재하는
    기관 156곳을 부적격으로 판정한 적이 있습니다(2026-08-15 실측):
        「국제장애인올림픽위원회」  실재하는 국제기구 → valid · 협회단체
        「탄소중립녹색성장위원회」  실재하는 정부위원회 → valid · 정부부처
        「법무법인 율촌」         실재하는 법무법인 → valid · 기타
        「광주지방고용노동청 군산지청」 실재하는 정부기관 → valid · 정부부처
    **이름을 가진 실재 조직이면 종류가 무엇이든 valid 입니다.**

  ✓ 「중국」 같은 국가 이름도 **valid 입니다.** 「중국 정부가 마이크론을
     규제했다」는 실재하는 사실이고, 국가 단위로만 보도되는 일이 있습니다.

why 는 15~40자. is_valid=false 일 때만 이유를 적고, true 면 빈 문자열로 두세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "org_type": {"type": "string", "enum": ORG_TYPES},
        "is_valid": {"type": "boolean"},
        "why": {"type": "string"},
    },
    "required": ["org_type", "is_valid", "why"],
    "additionalProperties": False,
}


def _classify(row: dict) -> tuple[dict, dict]:
    rels = [r for r in (row.get("rels") or []) if r and not r.endswith("→")]
    user = (f"기관: 「{row['name']}」\n"
            f"관계: {' · '.join(rels[:5]) or '(없음)'}")
    return row, ask_json(_SYSTEM, user, schema=_SCHEMA, name="org_class",
                         fallback={"org_type": "기타", "is_valid": True, "why": ""})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full", action="store_true", help="이미 분류한 것도 다시")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND, full=args.full)]
    print(f"분류 대상 Organization {len(rows)}곳 (약 {len(rows) * 0.3:.0f}원)")
    if not rows:
        return 0

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(_classify, rows))

    tally, bad, failed = Counter(), [], 0
    for r, v in results:
        if v.get("failed"):
            failed += 1
            continue
        tally[v["org_type"]] += 1
        if not v["is_valid"]:
            bad.append((r["name"], v["why"]))
    ok = len(results) - failed

    print(f"\n{'종류':12}{'건수':>6}{'비율':>7}")
    print("-" * 28)
    for t in ORG_TYPES:
        if tally[t]:
            print(f"{t:12}{tally[t]:>6}{tally[t] / max(ok, 1) * 100:>6.0f}%")
    print("-" * 28)
    print(f"{'노드 부적격':12}{len(bad):>6}{len(bad) / max(ok, 1) * 100:>6.0f}%")
    if failed:
        print(f"  ⚠ 분류 실패 {failed}곳 — 기록하지 않습니다")

    print("\n노드가 아닌 것으로 본 것 (지우지 않고 표시):")
    for nm, why in bad[:12]:
        print(f"   {nm[:24]:<26}{why[:38]}")

    if args.dry_run:
        print("\n[dry-run] 변경 없음")
        return 0
    with neo4j_session() as s:
        for r, v in results:
            if v.get("failed"):
                continue
            s.run(_APPLY, key=r["key"], otype=v["org_type"])
            if not v["is_valid"]:
                s.run(_SUSPECT, key=r["key"], why=v["why"])
    print(f"\n✅ {ok}곳 분류 · 부적격 {len(bad)}곳에 `node_suspect` 표시")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
