"""L3 subtype 분류 체계 수립 — LLM 기반 의미 군집 (1회성 부트스트랩).

**왜 문자 유사도가 아니라 LLM인가**

레지스트리(`subtype_registry.py`)의 대조-병합 방식은 **깨끗한 기준 목록이 이미 있을 때**
새 표현을 붙이는 증분 도구다. 그런데 지금은 그 기준 목록 자체가 정리돼 있지 않다.

문자 유사도로는 넘을 수 없는 벽이 있다(실측 2026-07-28):
    지분취득 = 지분 인수      동의어인데 글자가 다르다      → 못 붙임
    출자 = 지분투자          동의어                       → 못 붙임
    사장 ≠ 부사장            글자는 포함인데 다른 직위      → 잘못 붙음
    ODM공급 ≠ OEM공급        한 글자 차이, 다른 사업모델    → 잘못 붙음
    「로비」가 PARTNERS_WITH   엣지 타입 자체가 틀림          → 알 수 없음

규칙을 조여 오병합을 막으면 정작 합쳐야 할 동의어도 못 합친다(31→2건).
LLM은 이 셋을 다 안다.

**흐름**
  1. 엣지 타입별 현존 subtype + 빈도를 모은다
  2. LLM에 제시 → 의미 군집 + 대표 이름 + **오분류(다른 엣지 타입) 표시**
  3. `--dry-run`으로 사람이 검토
  4. 적용 — Neo4j 엣지 subtype 갱신 + 레지스트리 반영

이후 새로 들어오는 표현은 레지스트리가 이 체계에 붙인다.

실행:
  python -m batch.build.subtype_taxonomy --dry-run
  python -m batch.build.subtype_taxonomy
  python -m batch.build.subtype_taxonomy --edge PARTNERS_WITH   # 하나만
"""

from __future__ import annotations

import argparse
import sys


from app.core.database import neo4j_session, postgres_connection
from pipeline.llm import ask_json
from pipeline.normalizer.subtype_registry import seed_from_graph

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_MODEL = "gpt-4o"     # 의미 판단이라 품질 우선. 엣지 타입당 1회뿐이라 비용은 작다

# 엣지 12종의 뜻 — LLM이 "이 subtype이 이 엣지에 맞는가"를 판단할 근거
_EDGE_MEANING = {
    "OWNS_STAKE_IN": "A가 B의 지분·주식을 보유한다 (소유·지배)",
    "IS_EXECUTIVE_OF": "인물이 기업의 임원으로 재직한다 (직위)",
    "SUPPLIES_TO": "A가 B에게 제품·부품·소재를 공급한다 (물건이 오간다)",
    "PARTNERS_WITH": "A와 B가 협력한다 (물건이 아니라 기술·권리·사업을 함께한다)",
    "ACQUIRES": "A가 B를 인수·합병한다 (경영권·소유권 이전)",
    "SUES": "A가 B를 상대로 법적 분쟁을 제기한다 (원고 → 피고)",
    "COMPETES_WITH": "A와 B가 같은 시장·제품에서 경쟁한다",
    "REGULATES": "규제·판정 기관이 기업을 조사·제재·인허가한다",
    "DEVELOPS": "기업이 제품·기술을 개발·생산한다",
    "DEPENDS_ON": "기업이 특정 기술·부품에 의존한다",
    "HAS_EVENT": "기업이 어떤 사건의 당사자다",
    "IMPACTS": "사건이 기업에 영향을 미친다",
}

_EDGE_TYPES = tuple(_EDGE_MEANING)     # 12종 — 이 밖의 엣지는 존재하지 않는다

_SYSTEM = f"""당신은 기업 지식그래프의 관계 분류 체계를 정리하는 전문가입니다.

하나의 관계 유형(엣지)에 붙은 세부 분류(subtype) 목록을 받습니다.
같은 뜻인데 표현만 다른 것들을 **묶고**, 각 묶음의 **대표 이름**을 정하세요.

【★이 그래프의 엣지는 아래 12종이 전부입니다 — 새로 만들지 마세요】
{chr(10).join(f'  · {k}: {v}' for k, v in _EDGE_MEANING.items())}

【★subtype의 용도는 엣지마다 다릅니다】
  · DEVELOPS   → subtype은 **대상의 종류**입니다 (제품·기술·부품·소재·서비스).
                 이건 정상이며 오분류가 아닙니다.
  · IS_EXECUTIVE_OF → subtype은 **직위**입니다. 상법상 구분(사내이사·사외이사·
                 기타비상무이사)은 **법적 지위가 달라** 절대 합치지 마세요.
  · SUPPLIES_TO → 「OEM공급」·「ODM공급」·「공급계약」은 **사업모델이 달라** 구분합니다.
  · 나머지 → 관계의 세부 유형.

【병합 기준】
1. 동의어는 묶으세요 — 「지분취득」과 「지분 인수」, 「출자」와 「지분투자」
2. 표기 변형도 묶으세요 — 「전무이사」→「전무」, 「acquisition process」→「주식취득」
3. **다른 개념은 묶지 마세요** — 사장≠부사장, OEM≠ODM, 기술이전≠기술제휴
4. **상위어로 뭉개지 마세요** — 「사내이사」를 「이사」로 바꾸면 정보가 사라집니다
5. 대표 이름은 **가장 널리 쓰이고 짧은 한국어 표현**으로.
   공백을 새로 넣지 마세요(「5%이상주주」를 「5% 이상 주주」로 바꾸지 말 것).

【오분류(misfits)】
이 엣지 유형에 **정말로** 해당하지 않는 것만 넣으세요.
  · `correct_edge`는 반드시 **위 12종 중 하나**여야 합니다.
  · 12종 어디에도 안 맞으면 `correct_edge`를 "DROP"으로 하세요
    (숫자만 있는 값, 의미 없는 문자열 등).
  · 애매하면 misfit으로 넣지 마세요.

【주의】
· 빈도가 높은 표현을 대표로 삼는 편이 좋습니다(도메인에서 통용된다는 신호).
· **확신이 없으면 그대로 두세요.** 잘못 묶는 것이 안 묶는 것보다 나쁩니다.
· 병합할 게 없으면 clusters를 비워도 됩니다."""

_EDGE_TYPES_FOR_SCHEMA = tuple(_EDGE_MEANING)

_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["canonical", "members", "reason"],
                "additionalProperties": False,
            },
        },
        "misfits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subtype": {"type": "string"},
                    # ★12종 + DROP으로 **강제**한다. 프롬프트만으로는 LLM이
                    #   HAS_ROLE·FINANCIAL_TRANSACTION 같은 없는 엣지를 만들어냈다.
                    "correct_edge": {"type": "string",
                                     "enum": list(_EDGE_TYPES_FOR_SCHEMA) + ["DROP"]},
                    "reason": {"type": "string"},
                },
                "required": ["subtype", "correct_edge", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["clusters", "misfits"],
    "additionalProperties": False,
}


def analyze(edge_type: str, subtypes: list[tuple[str, int]]) -> dict:
    """엣지 하나의 subtype 목록 → 군집 + 오분류. 실패 시 빈 결과."""
    listing = "\n".join(f"- {s} ({n}건)" for s, n in subtypes)
    user = (f"엣지 유형: {edge_type}\n"
            f"의미: {_EDGE_MEANING.get(edge_type, '')}\n\n"
            f"현재 subtype 목록:\n{listing}")
    v = ask_json(_SYSTEM, user, schema=_SCHEMA, name="taxonomy",
                 model=_MODEL, fallback={"clusters": [], "misfits": []})
    if v.get("failed"):
        print(f"  ✗ {edge_type} 분석 실패: {v.get('reason','')}")
    return v


_UPDATE = """
MATCH ()-[r]->() WHERE type(r) = $edge_type AND r.subtype = $old
SET r.subtype = $new
RETURN count(r) AS n
"""
_MARK_MISFIT = """
MATCH ()-[r]->() WHERE type(r) = $edge_type AND r.subtype = $sub
SET r.suspect_edge_type = $correct
RETURN count(r) AS n
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--edge", help="특정 엣지 타입만")
    args = ap.parse_args()

    with postgres_connection() as conn, neo4j_session() as session:
        seed_from_graph(conn, session)
        rows = [dict(r) for r in session.run(
            "MATCH ()-[r]->() WHERE r.subtype IS NOT NULL AND r.subtype <> '' "
            "RETURN type(r) AS edge_type, r.subtype AS subtype, count(*) AS n"
        )]

    by_edge: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        by_edge.setdefault(row["edge_type"], []).append((row["subtype"], row["n"]))

    targets = {args.edge: by_edge[args.edge]} if args.edge else by_edge
    total_merged = total_misfit = 0

    with neo4j_session() as session:
        for edge_type, subs in sorted(targets.items(), key=lambda kv: -len(kv[1])):
            if len(subs) < 2:
                continue
            subs.sort(key=lambda kv: -kv[1])
            print(f"\n{'='*72}\n{edge_type} — 현재 {len(subs)}종")
            result = analyze(edge_type, subs)

            for cl in result.get("clusters", []):
                members = [m for m in cl["members"] if m != cl["canonical"]]
                if not members:
                    continue
                print(f"  ▸ {cl['canonical']}  ← {members}")
                print(f"      ({cl.get('reason','')[:70]})")
                total_merged += len(members)
                if not args.dry_run:
                    for old in members:
                        session.run(_UPDATE, edge_type=edge_type,
                                    old=old, new=cl["canonical"])

            for mf in result.get("misfits", []):
                print(f"  ⚠ 오분류: 「{mf['subtype']}」 → {mf['correct_edge']} 여야 함")
                print(f"      ({mf.get('reason','')[:70]})")
                total_misfit += 1
                if not args.dry_run:
                    session.run(_MARK_MISFIT, edge_type=edge_type,
                                sub=mf["subtype"], correct=mf["correct_edge"])

    verb = "예정" if args.dry_run else "완료"
    print(f"\n{'='*72}")
    print(f"{'[dry-run] ' if args.dry_run else '✅ '}"
          f"병합 {total_merged}건 · 오분류 표시 {total_misfit}건 {verb}")
    if not args.dry_run and total_misfit:
        print("   오분류는 `suspect_edge_type` 속성으로 표시했습니다 "
              "(삭제하지 않음 — 검토 후 결정)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
