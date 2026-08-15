"""이미 적재된 엣지의 subtype·role을 **새 규칙으로 다시 판정한다.**

★왜 기사를 다시 안 읽어도 되나

subtype은 「이 관계가 무엇에 관한 것인가」이고, 그 답은 **근거 문장 안에** 있다.
근거는 `evidence` 청크로 6,000건 넘게 전부 남아 있으므로 기사를 다시 받을 필요도,
구글을 다시 칠 필요도 없다. 전량 재추출이 약 5만 원인데 이 방식은 2,300원이다.

    수집 → 규칙필터 → URL해석 → 본문 → 라우터 → 추출     ← 전량 재추출은 여기부터
                                            근거 문장 → subtype  ← 이 모듈은 여기만

★세 가지를 한다

  ① B군 비우기 (무료)   DEVELOPS · IMPACTS · HAS_EVENT 의 subtype
      「무엇을」은 Product·Event 노드가 이미 말한다. 그래서 이 셋은 **비는 게 정답**인데
      과거 프롬프트가 타입 이름(개발·영향·사건)으로 채웠다. LLM 없이 지운다.

  ② A군 재판정 (유료)   나머지 9종
      타입 이름 되풀이·숫자·회사설명이 섞여 있다. **규칙으로는 못 가른다** —
      실측에서 「HBM3E 8단 제품」이 숫자 규칙에, 「자회사」가 회사설명 규칙에 걸렸는데
      둘 다 정상이다. 그래서 규칙은 **후보만 좁히고**, 맞는지는 근거를 보고 LLM이 정한다.
      이미 옳으면 그대로 돌려주므로 오탐이 손해로 이어지지 않는다.

  ③ role 백필 (유료)    HAS_EVENT
      당사자(subject)와 단순 언급(mentioned)이 섞여 있어 「이 기업에 난 일」 집계가
      부풀려져 있다.

실행:
    python -m batch.repair.subtype_backfill --dry-run          # 무엇이 바뀔지만
    python -m batch.repair.subtype_backfill --only clear       # B군 비우기(무료)
    python -m batch.repair.subtype_backfill --company 나우로보틱스 # 한 기업만
    python -m batch.repair.subtype_backfill --limit 100        # 조금만 해 보기
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Optional

import openai

from app.core.config import OPENAI_API_KEY
from app.core.database import neo4j_session
from pipeline.normalizer.relations import canonical_forms
from pipeline.ontology import HAS_EVENT_ROLES, SUBTYPE_RULES
from pipeline.vectorstore.chroma_store import ChromaStore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_MODEL = "gpt-4o"        # 추출과 같은 등급 — 판정 품질이 그래프 품질이다
_BATCH = 20              # 규칙 블록(약 1,000토큰)을 나눠 쓰려고 묶는다

# 「무엇을」을 노드가 이미 말하는 타입 — subtype이 비는 게 정답
_B_GROUP = ("DEVELOPS", "IMPACTS", "HAS_EVENT")

_DEFAULTS = {
    "OWNS_STAKE_IN": "지분보유", "IS_EXECUTIVE_OF": "임원", "SUPPLIES_TO": "공급",
    "PARTNERS_WITH": "협력", "ACQUIRES": "인수", "SUES": "소송",
    "COMPETES_WITH": "경쟁", "REGULATES": "규제", "DEVELOPS": "개발",
    "DEPENDS_ON": "의존", "HAS_EVENT": "사건", "IMPACTS": "영향",
}

# 12종 엣지 이름 전부. subtype이 이 중 하나면 — 자기 타입이든 남의 타입이든 — 정보가 없다.
_TYPE_NAMES = frozenset(_DEFAULTS.values())

# 후보를 좁히는 규칙. **판정이 아니라 선별**이다 — 걸린 것도 LLM이 옳다고 하면 그대로 둔다.
_MONEY = re.compile(r"\d+(\.\d+)?\s*(%|퍼센트|억|조|만원|억원)")
_CORPISH = re.compile(r"(기업|법인|회사|업체)$")


def _needs_llm(edge_type: str, subtype: str) -> bool:
    """LLM에게 물어볼 후보인가. **판정이 아니라 선별**이다.

    ★대표형은 건드리지 않는다(2026-08-11 실측 사고).

    `_CORPISH`가 「회사」로 끝나는 값을 「상대 회사 설명」으로 보는데,
    `OWNS_STAKE_IN`의 **정식 대표형 「자회사」**가 여기 걸렸다. LLM은 규칙에
    걸린 값을 고쳐야 한다고 보고 비웠고, 뉴스 5건이 지워질 뻔했다.

    `canonical_forms`는 「일부러 나눈 구분」을 빈도 정리가 덮지 못하게 막는
    목록이다 — 같은 이유로 여기서도 존중한다.
    """
    if not subtype:
        return False                       # 이미 비었으면 손대지 않는다
    if subtype == _DEFAULTS.get(edge_type):
        return True                        # 타입 이름 되풀이
    # ★**다른 타입**의 이름도 잡는다(2026-08-11 2차).
    #   1차 백필이 「자기 타입의 기본값」만 봐서 `SUPPLIES_TO/협력` 88건을 놓쳤다.
    #   조사해 보니 `repair/retypes`가 **타입만 고치고 subtype은 그대로 둔** 흔적이었다
    #   (재분류 123건 중 102건 · 83%). 재분류 자체는 맞는데 subtype이 옛 타입 이름으로
    #   남아 「공급 관계인데 협력이라고 적힌」 상태가 됐다.
    if subtype in _TYPE_NAMES:
        return True
    if subtype in canonical_forms(edge_type):
        return False                       # 명시 등재된 대표형 — 정상 값이다
    if _MONEY.search(subtype):
        return True                        # 지분율·금액 (ratio 필드가 따로 있다)
    if _CORPISH.search(subtype):
        return True                        # 「변압기 전문기업」류 — 상대 회사 설명
    return False


_SUBTYPE_SYSTEM = f"""이미 만들어진 지식그래프 엣지의 `subtype`을 **다시 판정**하세요.
각 항목에는 엣지 유형·양 끝 노드·현재 subtype·근거 문장이 있습니다.

{SUBTYPE_RULES}

【중요 — 고치는 일이지 지우는 일이 아닙니다】

· 현재 값이 이미 규칙에 맞으면 **그대로 돌려주세요.** 억지로 바꾸지 마세요.

· ★**비우기는 마지막 수단입니다.** 근거에 답이 있으면 반드시 채우세요.
  실측에서 답이 있는데도 비운 사례가 있었습니다:

    현재 "자회사"        근거 "자회사 한양로보틱스를 **흡수합병**하기로 결정했음"
      ✗ ""  (근거에 흡수합병이라고 쓰여 있습니다)
      ✓ "흡수합병"

    현재 "지분 93.37%"   근거 "지분 93.37%를 약 75억원에 **인수하는 계약**을 체결"
      ✗ ""  (숫자만 문제입니다)
      ✓ "지분 인수"

· 숫자가 문제면 **숫자만 빼고 나머지 표현을 살리세요.**
    "지분 99.96%" → "지분 인수"        "420억원 공급계약" → "공급계약"

· 상대 회사 설명이 들어와 있으면, 근거에서 **행위**를 찾아 바꾸세요.
    "로봇 자동화 전문 기업" → 근거가 "인수하며"뿐이면 "" (타입 이름 되풀이 금지)
                          → 근거가 "흡수합병"이면 "흡수합병"

· 근거 문장에 정말 아무 단서가 없을 때만 빈 문자열("")을 쓰세요.
· **근거 문장 안에서만** 정하세요. 기사 전체를 보고 있지 않으므로 추측하지 마세요.
· 입력에 준 번호(n)를 그대로 붙여 **모든 항목**을 돌려주세요."""

_ROLE_SYSTEM = f"""지식그래프의 `HAS_EVENT` 엣지에 **role**을 붙이세요.
각 항목에는 기업·사건 이름·근거 문장이 있습니다.

{HAS_EVENT_ROLES}

【중요】
· **근거 문장 안에서만** 정하세요. 판단이 안 서면 `mentioned`입니다.
· 입력에 준 번호(n)를 그대로 붙여 **모든 항목**을 돌려주세요."""


def _schema(field: str, enum: Optional[list[str]] = None) -> dict:
    value = {"type": "string"} if enum is None else {"type": "string", "enum": enum}
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}, field: value},
                    "required": ["n", field],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY 없음")
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _ask(system: str, body: str, field: str,
         enum: Optional[list[str]] = None) -> dict[int, str]:
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL, temperature=0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": body}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "backfill", "strict": True,
                "schema": _schema(field, enum)}},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"    ! LLM 실패: {exc!r}")
        return {}
    return {int(x["n"]): (x.get(field) or "").strip() for x in data.get("items", [])}


# ── 대상 뽑기 ────────────────────────────────────────────────
_FETCH = """
MATCH (a)-[r]->(b)
WHERE coalesce(r.source_type,'') = 'news' AND r.evidence_id IS NOT NULL
  AND ($company IS NULL OR a.name = $company OR b.name = $company)
RETURN elementId(r) AS id, type(r) AS t, a.name AS a, b.name AS b,
       coalesce(r.subtype,'') AS st, r.evidence_id AS ev,
       coalesce(r.role,'') AS role
"""

_SET_SUBTYPE = """
UNWIND $rows AS row
MATCH ()-[r]->() WHERE elementId(r) = row.id
SET r.subtype = row.v, r.subtype_backfilled = true
"""
_SET_ROLE = """
UNWIND $rows AS row
MATCH ()-[r]->() WHERE elementId(r) = row.id
SET r.role = row.v
"""


def _evidence_map(ids: list[str]) -> dict[str, str]:
    """evidence_id → 근거 문장. Chroma는 중복 id를 거부하므로 유일화해서 넣는다."""
    col = ChromaStore()._client.get_collection("evidence")
    out: dict[str, str] = {}
    uniq = sorted(set(ids))
    for i in range(0, len(uniq), 100):
        try:
            g = col.get(ids=uniq[i:i + 100], include=["documents"])
            out.update(dict(zip(g["ids"], g["documents"])))
        except Exception as exc:
            print(f"    (근거 일부를 못 읽었습니다: {exc!r})")
    # 출처 꼬리표(「제목」 URL)는 판정에 필요 없다 — 첫 문단만 쓴다
    return {k: (v or "").split("\n")[0].strip()[:300] for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="무엇이 바뀔지만 출력")
    ap.add_argument("--company", help="이 기업이 양 끝에 있는 엣지만")
    ap.add_argument("--limit", type=int, help="LLM 대상 상한 (조금만 해 볼 때)")
    ap.add_argument("--only", choices=["clear", "subtype", "role"],
                    help="한 단계만 실행")
    args = ap.parse_args()

    with neo4j_session() as s:
        rows = s.run(_FETCH, company=args.company).data()
    if not rows:
        print("대상이 없습니다.")
        return 0

    b_clear = [r for r in rows if r["t"] in _B_GROUP and r["st"]]
    a_todo = [r for r in rows if r["t"] not in _B_GROUP and _needs_llm(r["t"], r["st"])]
    role_todo = [r for r in rows if r["t"] == "HAS_EVENT" and not r["role"]]
    if args.limit:
        a_todo, role_todo = a_todo[:args.limit], role_todo[:args.limit]

    scope = f"「{args.company}」" if args.company else "전체"
    print("=" * 68)
    print(f"  subtype 백필 — {scope} 뉴스 엣지 {len(rows):,}건")
    print("=" * 68)
    print(f"  ① B군 비우기      {len(b_clear):>5}건  (무료)")
    print(f"  ② A군 재판정      {len(a_todo):>5}건  (LLM)")
    print(f"  ③ role 백필       {len(role_todo):>5}건  (LLM)")
    est = (len(a_todo) + len(role_todo)) * 0.74
    print(f"     예상 비용 약 {est:,.0f}원\n")

    ev = _evidence_map([r["ev"] for r in (a_todo + role_todo)]) if (a_todo or role_todo) else {}

    # ── ① B군 비우기 ────────────────────────────────────────
    if args.only in (None, "clear") and b_clear:
        print(f"[1/3] B군 subtype 비우기 — {len(b_clear)}건")
        by_type: dict[str, int] = {}
        for r in b_clear:
            by_type[r["t"]] = by_type.get(r["t"], 0) + 1
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"      {t:<12}{n:>5}건")
        if not args.dry_run:
            with neo4j_session() as s:
                s.run(_SET_SUBTYPE,
                      rows=[{"id": r["id"], "v": ""} for r in b_clear])
            print("      ✅ 비웠습니다")

    # ── ② A군 재판정 ────────────────────────────────────────
    changed = kept = 0
    if args.only in (None, "subtype") and a_todo:
        print(f"\n[2/3] A군 subtype 재판정 — {len(a_todo)}건")
        updates = []
        for i in range(0, len(a_todo), _BATCH):
            chunk = a_todo[i:i + _BATCH]
            lines = []
            for n, r in enumerate(chunk):
                lines.append(
                    f"{n}. [{r['t']}] {r['a']} → {r['b']}\n"
                    f"   현재 subtype: {r['st']}\n"
                    f"   근거: {ev.get(r['ev'], '(근거 없음)')}")
            got = _ask(_SUBTYPE_SYSTEM, "\n".join(lines), "subtype")
            for n, r in enumerate(chunk):
                if n not in got:
                    continue
                new = got[n]
                if new == r["st"]:
                    kept += 1
                    continue
                changed += 1
                updates.append({"id": r["id"], "v": new})
                if changed <= 25 or args.dry_run:
                    print(f"      {r['t']:<15} 「{r['st'][:22]}」 → "
                          f"「{new[:26] or '(비움)'}」")
            print(f"      … {min(i+_BATCH, len(a_todo))}/{len(a_todo)}")
        if updates and not args.dry_run:
            with neo4j_session() as s:
                s.run(_SET_SUBTYPE, rows=updates)
        print(f"      바뀜 {changed}건 · 그대로 {kept}건")

    # ── ③ role 백필 ────────────────────────────────────────
    if args.only in (None, "role") and role_todo:
        print(f"\n[3/3] HAS_EVENT role 백필 — {len(role_todo)}건")
        updates, dist = [], {}
        for i in range(0, len(role_todo), _BATCH):
            chunk = role_todo[i:i + _BATCH]
            lines = [f"{n}. 기업: {r['a']} / 사건: {r['b']}\n"
                     f"   근거: {ev.get(r['ev'], '(근거 없음)')}"
                     for n, r in enumerate(chunk)]
            got = _ask(_ROLE_SYSTEM, "\n".join(lines), "role",
                       ["subject", "counterparty", "mentioned"])
            for n, r in enumerate(chunk):
                v = got.get(n) or "mentioned"
                dist[v] = dist.get(v, 0) + 1
                updates.append({"id": r["id"], "v": v})
            print(f"      … {min(i+_BATCH, len(role_todo))}/{len(role_todo)}")
        if updates and not args.dry_run:
            with neo4j_session() as s:
                s.run(_SET_ROLE, rows=updates)
        for k, v in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"      {k:<14}{v:>5}건")

    print("\n" + "=" * 68)
    if args.dry_run:
        print("  [dry-run] 실제로 바뀐 것은 없습니다.")
    else:
        print("  ✅ 완료 — 바뀐 엣지에 `subtype_backfilled=true`를 남겼습니다.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
