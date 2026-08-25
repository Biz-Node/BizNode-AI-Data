"""Product 분류 재정리 — 「장비」를 신설하고 전건을 다시 가른다.

왜 (2026-08-15)

`category` 가 여섯 값 중 「기술」에 **84%(1,641/1,949)** 가 몰려 있었다. 세분화가
안 된 게 아니라 **분류가 틀렸다** — 열어 보니 기술이 아닌 것이 대부분이다:

    HBM · HBM4 · DRAM · DDR5 · 낸드플래시        → 제품(메모리)
    TC 본더 · EUV 장비 · 하이브리드 본더           → **장비** ← 분류 자체가 없었다
    액추에이터 · 감속기 · 카메라 모듈               → 부품
    12인치 실리콘 웨이퍼 · 유리기판                → 소재
    삼성페이                                  → 서비스
    AMR · 휴머노이드 로봇 · 양팔로봇               → 제품(로봇)
    자율주행기술 · 피지컬 AI · ALD                → 기술 (진짜)

담을 칸이 없으면 모델은 가장 안전한 값을 고른다. 「기술」이 사실상 「기타」
노릇을 하고 있었다. 그래서 **분류를 하나 늘리고 정의를 좁힌 뒤 다시 묻는다.**

★반도체 밸류체인에서 장비는 독립 범주다. 장비사(ASML·한미반도체·원익IPS)와
  소재사(SK머티리얼즈)와 부품사는 사업 성격도 리스크도 다른데, 지금은 그 셋이
  「기술」 한 칸에 섞여 있어 **업종별 조회가 불가능하다.**

실행:
    python -m batch.repair.product_category --dry-run
    python -m batch.repair.product_category
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_BATCH = 30

CATEGORIES = ["제품", "장비", "부품", "소재", "서비스", "기술"]

_SYSTEM = """당신은 반도체·로봇 산업의 「제품·기술」 이름을 여섯 갈래로 가릅니다.

각 이름이 **무엇인지** 고르세요. 함께 주는 「관계」가 근거입니다.

【분류】
· 제품   그 자체로 팔리는 완성물. 메모리·칩·로봇·기기·소프트웨어.
         예) HBM4 · DDR5 · 낸드플래시 · 휴머노이드 로봇 · AMR · 아틀라스
· 장비   **다른 것을 만들기 위한 기계.** 생산라인에 놓입니다.
         예) TC 본더 · EUV 노광장비 · 하이브리드 본더 · 레이저 어닐링 장비
         ★「~기」「~장비」「~설비」로 끝나면 대개 여기입니다.
· 부품   완성품 안에 들어가는 조립 단위.
         예) 감속기 · 액추에이터 · 카메라 모듈 · 인터페이스 보드
· 소재   가공되어 제품이 되는 원재료.
         예) 12인치 실리콘 웨이퍼 · 유리기판 · 육불화텅스텐 · 포토레지스트
· 서비스 파는 것이 물건이 아니라 용역·플랫폼.
         예) 삼성페이 · 파운드리 서비스 · 클라우드
· 기술   **만질 수 없는 방법론·공정·규격.** 물건이 아닙니다.
         예) ALD · 자율주행기술 · 피지컬 AI · 3나노 공정 · 5G 표준

【반드시 지킬 것】
1. **「기술」을 기본값으로 쓰지 마세요.** 물건이면 물건입니다.
   만질 수 있으면 제품·장비·부품·소재 중 하나입니다.
2. 장비와 부품을 가르는 기준은 **「무언가를 만드는가, 무언가에 들어가는가」**입니다.
       TC 본더는 칩을 만든다 → 장비
       감속기는 로봇에 들어간다 → 부품
3. 제품과 장비를 가르는 기준은 **최종 수요자**입니다.
       DRAM 은 세트업체가 산다 → 제품
       노광장비는 칩 만드는 회사가 산다 → 장비
4. 정말 모르겠으면 「기술」이 아니라 **관계에 드러난 쓰임**을 보고 고르세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["name", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

# 관계를 함께 준다 — 이름만으로는 「베라」·「소캠」 같은 것을 못 가른다.
_TARGETS = """
MATCH (p:Product)
WHERE $all OR p.category = '기술'
OPTIONAL MATCH (c)-[r]->(p)
WITH p, collect(DISTINCT coalesce(c.name,'') + '-' + type(r) + '→')[..4] AS inn
OPTIONAL MATCH (p)-[r2]->(o)
WITH p, inn, collect(DISTINCT type(r2) + '→' + coalesce(o.name,''))[..3] AS out
OPTIONAL MATCH (p)-[any]-()
RETURN p.norm_name AS key, p.name AS name, p.category AS old, inn, out,
       count(any) AS deg ORDER BY deg DESC
"""

_SET = """
UNWIND $rows AS row
MATCH (p:Product {norm_name: row.key}) SET p.category = row.cat
"""


def _render(rows: list[dict]) -> str:
    out = []
    for r in rows:
        ctx = " ".join(r["inn"] + r["out"]) or "(관계 없음)"
        out.append(f"- {r['name']}\n    관계: {ctx[:150]}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="한 배치만 보고 끝")
    ap.add_argument("--all", action="store_true",
                    help="「기술」뿐 아니라 전건을 다시 가른다")
    ap.add_argument("--limit", type=int, metavar="N")
    args = ap.parse_args()

    with neo4j_session() as s:
        targets = [dict(r) for r in s.run(_TARGETS, all=args.all)]
        before = {r["category"]: r["n"] for r in s.run(
            "MATCH (p:Product) RETURN p.category AS category, count(*) AS n")}
    if args.limit:
        targets = targets[:args.limit]

    print("■ Product 재분류 — 「장비」 신설")
    print(f"   현재 분포: " + " · ".join(f"{k} {v}" for k, v in
                                     sorted(before.items(), key=lambda x: -x[1])))
    print(f"   대상 {len(targets)}곳 · 약 {len(targets) * 0.03:.0f}원")
    if not targets:
        return 0
    if args.dry_run:
        targets = targets[:_BATCH]
        print(f"   [dry-run] 앞 {len(targets)}곳만 호출합니다\n")

    moved: list[tuple[str, str, str]] = []
    with neo4j_session() as s:
        for i in range(0, len(targets), _BATCH):
            chunk = targets[i:i + _BATCH]
            got = ask_json(_SYSTEM, _render(chunk), schema=_SCHEMA,
                           name="product_category", fallback={"items": []})
            by_name = {c["name"]: c for c in chunk}
            rows = []
            for it in got.get("items", []):
                src = by_name.get(it["name"])
                if not src or it["category"] == src["old"]:
                    continue
                rows.append({"key": src["key"], "cat": it["category"]})
                moved.append((it["name"], src["old"], it["category"]))
            if rows and not args.dry_run:
                s.run(_SET, rows=rows)
            if (i // _BATCH) % 8 == 0:
                print(f"   … {min(i + _BATCH, len(targets))}/{len(targets)}")

    print(f"\n   바뀐 것 {len(moved)}곳")
    tally: dict[str, int] = {}
    for _, old, new in moved:
        tally[f"{old} → {new}"] = tally.get(f"{old} → {new}", 0) + 1
    for k, n in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"     {k:<20}{n}")

    print("\n   ── 예시 ──")
    for name, old, new in moved[:16]:
        print(f"     {name[:28]:<30}{old} → {new}")

    if not args.dry_run:
        with neo4j_session() as s:
            after = {r["category"]: r["n"] for r in s.run(
                "MATCH (p:Product) RETURN p.category AS category, count(*) AS n")}
        print("\n   최종 분포: " + " · ".join(f"{k} {v}" for k, v in
                                          sorted(after.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
