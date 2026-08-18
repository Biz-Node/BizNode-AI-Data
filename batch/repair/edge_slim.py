"""엣지 속성 정리 — 단위를 맞추고, subtype 을 다듬고, 파생값을 뺀다.

★왜 (2026-08-15)

엣지 속성이 102가지인데 그중 상당수가 **다른 속성에서 계산되는 값**이거나
**단위가 어긋난 값**이었다. 저장된 파생값은 원본과 어긋날 수 있고(실제로
`ratio_change` 15건이 안 맞았다), 어긋난 단위는 조회를 통째로 틀리게 한다.

    1  ratio 단위      「지분 67.96%」가 0.6796 으로 저장돼 0.68%로 읽힘
    2  subtype 다듬기   숫자·서술문이 들어간 것
    3  파생값 제거      ratio_change · investment_* · is_new_executive · position

★1번이 왜 위험한가: **「100% 자회사」와 「1% 지분」이 구분되지 않는다.**
  범위 검사(0~100)로는 못 잡는다 — 0.68%짜리 진짜 소액 지분이 실재하기 때문이다.
  그래서 **근거에 적힌 퍼센트와 대조**해야만 가려낼 수 있다.
  DART 경로는 원본(staged_edges)이 퍼센트꼴이라 멀쩡했고 뉴스 경로만 섞였다.

★3번 파생값 — 실측으로 확인한 것만 뺀다:
      investment_increased / decreased   1,181/1,181  ratio_change 부호와 일치
      ratio_change                       1,291/1,306  ratio - previous_ratio
      is_new_executive                   tenure_months < 12 로 완전히 갈림
                                         (True 0~11 · False 22~70)
      position                           subtype 과 67% 동일하고 더 좁다
                                         (491/722 vs 719/722) · 원문이 지저분하다
  ★`tenure_months`는 **빼지 않는다** — 계산과 6/167만 일치했다. 남은 임기가
    아니라 **재직 기간**이라 독립 정보다.

실행:
    python -m batch.repair.edge_slim --dry-run
    python -m batch.repair.edge_slim
    python -m batch.repair.edge_slim --step 1
"""

from __future__ import annotations

import argparse
import re
import sys

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_PCT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")

# 계산으로 대체되는 속성 → 어떻게 계산하는지(주석이자 문서)
DERIVED = {
    "ratio_change":         "ratio - previous_ratio",
    "investment_increased": "ratio_change > 0",
    "investment_decreased": "ratio_change < 0",
    "previous_share_count": "share_count - share_count_change",
    "is_new_executive":     "tenure_months < 12",
    "position":             "subtype 과 중복 · subtype 이 더 넓고 정규화됨",
}

# ★**숫자가 이름의 일부인 분류명**은 건드리면 안 된다.
#   「5%이상주주」는 DART 대량보유 공시의 정식 구분이고, 여기 든 「5%」는
#   그 주주의 지분율이 아니라 **기준선**이다. 규칙이 이걸 「이상주주」로
#   만들어 버렸다(dry-run 에서 162건 잡힘).
PROTECTED = {"5%이상주주", "10%이상주주", "1%이상주주"}

# subtype 에서 지울 잡음 — 숫자와 군더더기
_STRIP = [
    (re.compile(r"[0-9]+(?:\.[0-9]+)?\s*%\s*"), ""),          # 「67.96% 지분」→「지분」
    (re.compile(r"[0-9,]+\s*(억|조|만)?\s*원\s*(규모)?\s*"), ""),  # 「420억원 규모 공급」
    (re.compile(r"\s+"), " "),
]


# ─────────────────────────── 1 · ratio 단위 ───────────────────────────
_RATIO_FIND = """
MATCH ()-[r]->() WHERE r.ratio IS NOT NULL AND r.ratio <= 1 AND r.ratio > 0
OPTIONAL MATCH ()-[r2]->() WHERE elementId(r2) = elementId(r)
RETURN elementId(r) AS id, type(r) AS t, r.ratio AS ratio,
       r.subtype AS subtype, r.source_type AS src
"""


def step1_ratio(dry: bool) -> None:
    """근거·subtype 에 남은 퍼센트와 대조해 100배 어긋난 것을 바로잡는다."""
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_RATIO_FIND)]
        print(f"  0~1 구간 ratio {len(rows)}건 — 진짜 소액 지분과 단위 오류가 섞여 있다")

        fixable, unsure = [], 0
        for r in rows:
            st = r["subtype"] or ""
            # ★분류명에 든 %는 **기준선이지 지분율이 아니다.** 「5%이상주주」의
            #   5%를 ratio 와 대조하면 0.05 를 5.0 으로 잘못 고친다.
            if st in PROTECTED:
                unsure += 1
                continue
            pcts = [float(m) for m in _PCT.findall(st)]
            if not pcts:
                unsure += 1
                continue
            if any(abs(p - r["ratio"] * 100) < 0.01 for p in pcts):
                fixable.append((r["id"], r["t"], r["ratio"],
                                round(r["ratio"] * 100, 4), st))
            elif not any(abs(p - r["ratio"]) < 0.01 for p in pcts):
                unsure += 1

        print(f"    subtype 에 원문 %가 남아 **확실히 고칠 수 있는 것** {len(fixable)}건")
        print(f"    대조할 근거가 없어 그대로 두는 것 {unsure}건")
        for _, t, old, new, st in fixable[:8]:
            print(f"      {t:<16}{old} → {new}   subtype「{st[:20]}」")
        if dry or not fixable:
            return
        for eid, _, _, new, _ in fixable:
            s.run("MATCH ()-[r]->() WHERE elementId(r) = $id "
                  "SET r.ratio = $v, r.ratio_unit_fixed = true", id=eid, v=round(new, 4))
        print(f"  → {len(fixable)}건 교정 (`ratio_unit_fixed` 로 표시)")

        left = s.run("""MATCH ()-[r]->() WHERE r.ratio IS NOT NULL AND r.ratio <= 1
            AND r.ratio > 0 RETURN count(*) AS n""").single()["n"]
        print(f"  남은 0~1 구간 {left}건 — 근거 문장으로 재확인이 필요합니다")


# ─────────────────────────── 2 · subtype ───────────────────────────
def step2_subtype(dry: bool) -> None:
    """subtype 에서 숫자를 빼고 서술문을 줄인다. 타입 이름만 남으면 비운다."""
    ECHO = {"SUPPLIES_TO": {"공급", "납품", "거래", "공급계약"},
            "PARTNERS_WITH": {"협력", "제휴", "파트너십"},
            "SUES": {"소송", "제소"}, "REGULATES": {"규제", "감독"},
            "COMPETES_WITH": {"경쟁"}, "ACQUIRES": {"인수", "매수"},
            "OWNS_STAKE_IN": {"지분"},          # 「출자」·「자회사」는 정당한 값
            "DEPENDS_ON": {"의존"}, "IS_EXECUTIVE_OF": {"임원"},
            "DEVELOPS": {"개발", "제품", "기술"},
            "HAS_EVENT": {"사건"}, "IMPACTS": {"영향"}}

    with neo4j_session() as s:
        rows = [dict(r) for r in s.run("""MATCH ()-[r]->()
            WHERE r.subtype IS NOT NULL AND r.subtype <> ''
            RETURN elementId(r) AS id, type(r) AS t, r.subtype AS st""")]
        changes = []
        for r in rows:
            if r["st"] in PROTECTED:
                continue
            new = r["st"]
            for pat, rep in _STRIP:
                new = pat.sub(rep, new)
            new = new.strip(" ·-,")
            # 타입 이름만 남았으면 비운다 — 「모름」과 「평범함」을 가르기 위해
            if new in ECHO.get(r["t"], set()):
                new = ""
            if new != r["st"]:
                changes.append((r["id"], r["t"], r["st"], new))

        tally: dict[str, int] = {}
        for _, t, _, _ in changes:
            tally[t] = tally.get(t, 0) + 1
        print(f"  다듬을 subtype {len(changes)}건")
        for t, n in sorted(tally.items(), key=lambda x: -x[1]):
            print(f"    {t:<18}{n}")
        for _, t, old, new in changes[:10]:
            print(f"      {t:<16}「{old[:26]:<28}」 → 「{new[:22]}」")
        if dry or not changes:
            return
        for eid, _, _, new in changes:
            s.run("MATCH ()-[r]->() WHERE elementId(r) = $id SET r.subtype = $v",
                  id=eid, v=new)
        print(f"  → {len(changes)}건 정리")


# ─────────────────────────── 3 · 파생값 제거 ───────────────────────────
def step3_derived(dry: bool) -> None:
    """다른 속성에서 계산되는 것을 뺀다. **저장하면 어긋난다.**"""
    with neo4j_session() as s:
        alive = []
        for p, how in DERIVED.items():
            n = s.run(f"MATCH ()-[r]->() WHERE r.`{p}` IS NOT NULL "
                      f"RETURN count(*) AS n").single()["n"]
            if n:
                alive.append((p, n, how))
        for p, n, how in alive:
            print(f"    {p:<24}{n:>5}건   ← {how}")
        if dry or not alive:
            return
        for p, _, _ in alive:
            s.run(f"MATCH ()-[r]->() WHERE r.`{p}` IS NOT NULL REMOVE r.`{p}`")
        print(f"  → {len(alive)}종 삭제")


STEPS = [("ratio 단위 교정", step1_ratio),
         ("subtype 다듬기", step2_subtype),
         ("파생값 제거", step3_derived)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--step", type=int, metavar="N", help="한 단계만 (1~3)")
    args = ap.parse_args()

    if args.dry_run:
        print("[dry-run] 아무것도 바꾸지 않습니다\n")
    for i, (name, fn) in enumerate(STEPS, 1):
        if args.step and i != args.step:
            continue
        print("=" * 62)
        print(f"[{i}/{len(STEPS)}] {name}")
        print("=" * 62)
        fn(args.dry_run)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
