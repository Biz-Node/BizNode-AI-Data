"""`IS_EXECUTIVE_OF`의 **직위**를 근거 문장에서 되찾는다. 비용 0.

왜 필요한가 (2026-08-01)

「기업 상세 · 인물」 화면에 직위가 **「임원」**으로만 뜨는 건이 69건 있었다.
전부 뉴스에서 뽑은 엣지다(DART 건은 이미 사외이사·감사 등이 붙어 있다).

    최태원  @SK하이닉스   임원      ← 화면에 「임원」
    이재용  @삼성전자     임원
    경은국  @LG이노텍    임원

그런데 **직위는 근거 문장에 그대로 있다.** 한국 기사는 인물을 부를 때
「이름 + 회사 + 직위」로 쓰는 관행이 있기 때문이다:

    "이재용 삼성전자 **부회장**이 내달 손정의 소프트뱅크 회장과 만나…"
    "경은국 LG이노텍 **최고재무책임자(CFO)**는 …"
    "김 **부사장**은 지난 2월 한화세미텍에 무보수로 합류하며…"   ← 성만 쓰기도 한다

추출 단계에서 이걸 안 가져왔을 뿐이라 **LLM을 다시 부를 필요가 없다.**
저장된 근거를 정규식으로 훑으면 된다.

    python -m batch.repair.executive_titles --dry-run
    python -m batch.repair.executive_titles

★못 찾으면 그냥 둔다. 근거에 직위가 없는데 추측해 넣으면 **틀린 직함을 사람에게
  붙이는 것**이라 「임원」보다 나쁘다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from app.core.database import neo4j_session
from pipeline.importer.evidence import fetch_texts

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 직위 미상으로 남은 값들
_VAGUE = ("", "임원", "기타", "OTHER", "other")

# ── 직위 어휘 ────────────────────────────────────────────────
# 순서가 우선순위다. **긴 것을 앞에** 둬야 「부회장」이 「회장」으로 잘리지 않고,
# 「대표이사」가 「대표」나 「이사」로 잘리지 않는다.
_TITLES = [
    "대표이사", "부회장", "회장", "부사장", "사장", "사외이사", "사내이사",
    "기타비상무이사", "전무이사", "상무이사", "전무", "상무", "감사위원", "감사",
    "최고경영자", "최고기술책임자", "최고재무책임자", "최고운영책임자",
    "최고전략책임자", "최고정보책임자",
    "CEO", "CTO", "CFO", "COO", "CSO", "CIO",
    "창업자", "설립자", "의장", "고문", "부문장", "본부장", "센터장",
    "연구소장", "공동대표", "각자대표", "대표", "이사",
]
_TITLE_RE = "|".join(re.escape(t) for t in _TITLES)

# 대표형 — 표기가 갈리면 필터가 쪼개진다
_CANON = {
    "최고경영자": "CEO", "최고기술책임자": "CTO", "최고재무책임자": "CFO",
    "최고운영책임자": "COO", "최고전략책임자": "CSO", "최고정보책임자": "CIO",
    "전무이사": "전무", "상무이사": "상무", "감사위원": "감사",
    "설립자": "창업자", "공동대표": "대표", "각자대표": "대표",
}

_FIND = """
MATCH (p:Person)-[r:IS_EXECUTIVE_OF]->(c:Company)
WHERE coalesce(r.subtype,'') IN $vague
RETURN elementId(r) AS eid, p.name AS person, c.name AS company,
       coalesce(r.subtype,'') AS subtype, coalesce(r.source_type,'') AS src,
       coalesce([r.evidence_id],[]) + coalesce(r.evidence_ids,[]) AS evs
"""

_APPLY = ("MATCH ()-[r]->() WHERE elementId(r) = $eid "
          "SET r.subtype = $title, r.title_source = 'evidence_regex'")


# 「전 ○○」 「前 ○○」 = 과거 직함. 「전무」의 '전'과 헷갈리지 않게 뒤에 공백을 본다.
_FORMER_RE = re.compile(r"(?:^|\s)(?:전|前)\s")


def _norm(s: str) -> str:
    return re.sub(r"[\s()（）㈜(주)]|주식회사", "", s or "").lower()


def find_title(name: str, text: str, company: str = "",
               others: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """근거에서 그 사람의 **그 회사에서의** 직위를 찾는다. (직위, 근거조각) 또는 None.

    두 가지를 다 확인해야 한다.

    ① **그 사람 이름 바로 뒤**에서만 찾는다. 문서 아무 데나 있는 직위를 가져오면
       다른 사람 직함이 붙는다. 실측: 「매디슨 황 엔비디아 … 수석 이사가 경기
       성남시 분당구 두산로보틱스…」라는 근거가 김민표에게 달려 있었다.

    ② **이름과 직위 사이에 다른 회사명이 끼면 버린다.** 한국 기사는 인물을
       「이름 + 소속 + 직위」로 부르는데, 그 소속이 우리가 보는 회사가 아닐 수 있다:

           엣지  박성하 -IS_EXECUTIVE_OF-> SK하이닉스
           근거  "박성하 **SK스퀘어 사장**을 기타비상무이사로 신규선임하는 안건"
           → 「사장」은 SK스퀘어에서의 직함이다. SK하이닉스에서는 기타비상무이사다.
              그대로 붙이면 **사람에게 틀린 직함을 다는 것**이라 「임원」보다 나쁘다.
    """
    if not name or not text:
        return None
    tgt = _norm(company)

    for m in re.finditer(rf"{re.escape(name)}\s*([^.。\n]{{0,20}}?)({_TITLE_RE})", text):
        gap, title = m.group(1), m.group(2)
        g = _norm(gap)
        # ★「전(前) ○○」은 **과거 직함**이다. 실측: 「오창훈 전 토스증권
        #   최고기술책임자(CTO)를 전무로 영입」 — 지금 직함은 전무인데 CTO가 붙었다.
        if _FORMER_RE.search(gap):
            continue
        # 사이에 낀 회사명이 대상 회사가 아니면 그 회사의 직함이다 → 버린다
        intruder = next((o for o in others if len(o) >= 2 and _norm(o) in g
                         and (not tgt or _norm(o) != tgt)), None)
        if intruder and (not tgt or tgt not in g):
            continue
        return _CANON.get(title, title), text[max(0, m.start() - 10):m.end() + 12]

    # ③ 「김 부사장은」 — 성만 쓰는 관행. 그 사람 전체 이름이 본문에 나오고,
    #    **대상 회사명도 같은 근거에 있을 때만** 받는다(다른 회사 직함 방지).
    if len(name) >= 2 and name in text and (not tgt or tgt in _norm(text)):
        m = re.search(rf"(?<![가-힣]){re.escape(name[0])}\s({_TITLE_RE})", text)
        if m:
            return (_CANON.get(m.group(1), m.group(1)),
                    text[max(0, m.start() - 10):m.end() + 12])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND, vague=list(_VAGUE))]
        # 「이름과 직위 사이에 낀 다른 회사」를 알아보려면 회사명 목록이 필요하다
        others = frozenset(
            r["n"] for r in session.run(
                "MATCH (c:Company) WHERE c.name IS NOT NULL RETURN c.name AS n")
            if r["n"] and len(r["n"]) >= 2)
    print(f"직위 미상 {len(rows)}건 "
          f"({dict(Counter(r['src'] for r in rows))}) · 대조용 회사명 {len(others)}개\n")
    if not rows:
        print("채울 것이 없습니다.")
        return 0

    docs = fetch_texts([e for r in rows for e in r["evs"] if e])

    found, missed = [], []
    for r in rows:
        text = "\n".join(docs.get(e, "") for e in r["evs"] if e)
        hit = find_title(r["person"], text, r["company"], others)
        if hit:
            found.append((r, hit[0], hit[1]))
        else:
            missed.append((r, None, text))

    print(f"{'인물':12}{'소속':16}{'찾은 직위':12}  근거 조각")
    print("─" * 92)
    for r, title, snip in found:
        print(f"{r['person'][:10]:12}{r['company'][:14]:16}{title:12}  "
              f"{' '.join(snip.split())[:44]}")
    print(f"\n찾음 {len(found)}건 · 못 찾음 {len(missed)}건 (그대로 「임원」으로 둡니다)")
    if missed:
        print("\n[못 찾은 예 — 근거에 직위가 없다]")
        for r, _, text in missed[:6]:
            print(f"   {r['person'][:10]:12}{r['company'][:14]:16}"
                  f"{' '.join(text.split())[:52]}")
    print("\n찾은 직위 분포:", dict(Counter(t for _, t, _ in found).most_common()))

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0
    with neo4j_session() as session:
        for r, title, _ in found:
            session.run(_APPLY, eid=r["eid"], title=title)
    print(f"\n✅ {len(found)}건에 직위 기록 (title_source='evidence_regex')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
