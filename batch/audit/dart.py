"""DART 엣지 검사 — **필드 무결성**과 **원문 대조**. LLM 없이, 비용 0.

★왜 뉴스와 다른 검사를 쓰는가 (2026-08-01)

DART 엣지 2,748건의 「근거」를 열어 보니 **우리가 만든 템플릿 문장**이었다:

    "국민연금공단은(는) 심텍의 5%이상주주로 지분 6.09%를 보유하고 있다."
    "심텍 개발·생산 — Package Substrate (제품) … 출처: 사업보고서"

API 필드를 문장으로 조립한 것이라, LLM에게 「이 근거가 이 관계를 뒷받침하나」라고
물으면 **자기가 쓴 문장을 자기가 채점**하는 꼴이다. 무조건 통과하고 커버율
숫자만 오른다. DART가 틀리는 방식은 **문장 해석이 아니라 필드 처리**다.

그래서 출처별로 나눠 본다:

    구조화 필드 2,206건   ①필드 무결성 — 값의 범위·구조가 말이 되는가
    본문 파싱    542건   ②원문 대조   — 그 이름이 사업보고서 원문에 실제로 있는가

②는 원문이 `data/raw_reports/`에 있어 **DART 재호출도 LLM도 없다.**
「파싱이 지어냈나」를 잡는 가장 확실한 방법이다.

★실측으로 ①이 잡은 것 — LLM으로는 절대 못 잡는 유형:
    최대주주 76건  API가 그룹 전원을 주는데 전부 「최대주주」로 붙었다
                   (현대글로비스 -최대주주-> 현대모비스, 지분 0.72%)
    자회사 14건    상법상 자회사는 50% **초과**인데 `>=`로 판정해 50/50 합작이 자회사로
    주식종류 4건   보통주·우선주 행이 MERGE로 덮여 지분이 우선주 0.01%만 남았다

    python -m batch.audit.dart                 # 둘 다
    python -m batch.audit.dart --apply         # 발견분에 표시
    python -m batch.audit.dart --only parsed

★여기서도 지우지 않는다. `field_suspect` · `parsed_suspect`로 표시만 한다.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter
from datetime import date

from app.core.database import neo4j_session
from pipeline.extractors.dart.downloader import DEFAULT_DOWNLOAD_DIR
from pipeline.extractors.dart.text_cleaner import clean_text
from pipeline.extractors.dart.xml_parser import parse_sections

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
#  ① 필드 무결성 — 값의 범위·구조
# ══════════════════════════════════════════════════════════════
_DART = "r.source_type IN ['dart', 'dart_filing']"

# (이름, 설명, 쿼리) — 쿼리는 eid·detail 두 칼럼을 돌려준다.
CHECKS: list[tuple[str, str, str]] = [
    ("지분율 범위",
     "ratio가 0~100 밖 — 파싱 자리 밀림이나 단위 혼동",
     f"""MATCH (a)-[r:OWNS_STAKE_IN]->(b) WHERE {_DART}
         AND r.ratio IS NOT NULL AND (toFloat(r.ratio) < 0 OR toFloat(r.ratio) > 100)
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  ratio=' + toString(r.ratio) AS detail"""),

    ("자기 참조",
     "출발과 도착이 같은 노드 — 병합 사고나 이름 정규화 충돌",
     f"""MATCH (a)-[r]->(b) WHERE {_DART} AND elementId(a) = elementId(b)
       RETURN elementId(r) AS eid, type(r) + ' ' + a.name AS detail"""),

    ("최대주주 지분 이상",
     "subtype이 최대주주인데 ratio가 5% 미만 — 라벨과 값이 안 맞는다",
     f"""MATCH (a)-[r:OWNS_STAKE_IN]->(b) WHERE {_DART}
         AND r.subtype = '최대주주' AND r.ratio IS NOT NULL AND toFloat(r.ratio) < 5
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  ratio=' + toString(r.ratio) AS detail"""),

    # ★「5%이상주주인데 개별 지분 5% 미만」은 **정상이다.** 대량보유상황보고는
    #   본인 + 특별관계자의 **합산**이 5%를 넘으면 하는 것이라, 명부에 오른
    #   개별 구성원은 5% 미만일 수 있다. 실측: ISC는 보고자 6명 합계 72.28%인데
    #   그중 NorgesBank는 3.99%다. 이걸 오류로 세면 56건이 헛되이 걸린다.
    #   대신 **합산이 5%에 못 미치는 회사**를 본다 — 그건 파싱 누락 신호다.
    ("보고 합계가 5% 미만",
     "한 회사의 5%이상주주 합계가 5%에 못 미친다 — 명부 일부가 빠졌을 수 있다",
     f"""MATCH (a)-[r:OWNS_STAKE_IN]->(b) WHERE {_DART}
         AND r.subtype = '5%이상주주' AND r.ratio IS NOT NULL
       WITH b, sum(toFloat(r.ratio)) AS total, count(*) AS n, collect(r)[0] AS one
       WHERE total < 5
       RETURN elementId(one) AS eid,
              b.name + '  보고자 ' + toString(n) + '명 합계 '
              + toString(round(total, 2)) + '%' AS detail"""),

    ("자회사 지분 이상",
     "subtype이 자회사인데 ratio가 50% 이하 — 자회사 판정이 지분율과 어긋난다",
     f"""MATCH (a)-[r:OWNS_STAKE_IN]->(b) WHERE {_DART}
         AND r.subtype = '자회사' AND r.ratio IS NOT NULL AND toFloat(r.ratio) <= 50
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  ratio=' + toString(r.ratio) AS detail"""),

    # ★계약 엣지는 제외한다. 공급계약 공시의 `valid_from`은 **계약 시작일**이라
    #   공시일(last_seen)보다 미래인 것이 정상이다(미리 공시하고 나중에 시작).
    #   실측 10건 중 8건이 그 경우였다 — 검사가 틀렸던 것이지 데이터가 아니다.
    #   계약기간이 없는 엣지(관측 시점 = 상태 시점)만 본다.
    ("날짜 역전",
     "valid_from이 last_seen보다 늦다 (계약기간이 있는 엣지는 정상이라 제외)",
     f"""MATCH (a)-[r]->(b) WHERE {_DART}
         AND r.valid_until IS NULL
         AND r.valid_from IS NOT NULL AND r.last_seen IS NOT NULL
         AND toString(r.valid_from) > toString(r.last_seen)
       RETURN elementId(r) AS eid,
              type(r) + ' ' + a.name + '→' + b.name
              + '  from=' + toString(r.valid_from)
              + ' seen=' + toString(r.last_seen) AS detail"""),

    # 위에서 찾은 실제 버그 — 주식 종류별 행이 병합되며 덮어써진 흔적.
    ("우선주만 남은 최대주주",
     "최대주주·특수관계인인데 보유 주식이 우선주뿐 — 보통주 행이 덮여 사라졌다",
     f"""MATCH (a)-[r:OWNS_STAKE_IN]->(b) WHERE {_DART}
         AND r.subtype IN ['최대주주', '특수관계인']
         AND r.share_type IS NOT NULL AND toString(r.share_type) CONTAINS '우선'
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  ' + toString(r.share_type)
              + ' ' + toString(r.ratio) + '%' AS detail"""),

    ("미래 날짜",
     "관측일이 오늘보다 뒤 — 연도 파싱 오류일 가능성",
     f"""MATCH (a)-[r]->(b) WHERE {_DART}
         AND r.last_seen IS NOT NULL AND toString(r.last_seen) > $today
       RETURN elementId(r) AS eid,
              type(r) + ' ' + a.name + '→' + b.name
              + '  seen=' + toString(r.last_seen) AS detail"""),

    ("임원인데 Person이 아님",
     "IS_EXECUTIVE_OF의 출발이 인물이 아니다",
     f"""MATCH (a)-[r:IS_EXECUTIVE_OF]->(b) WHERE {_DART} AND NOT a:Person
       RETURN elementId(r) AS eid,
              labels(a)[0] + ' ' + a.name + ' → ' + b.name AS detail"""),

    ("계약금액 이상",
     "공급계약 금액이 0 이하이거나 100조를 넘는다",
     f"""MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE {_DART}
         AND r.contract_amount IS NOT NULL
         AND (toFloat(r.contract_amount) <= 0
              OR toFloat(r.contract_amount) > 100000000000000)
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  금액=' + toString(r.contract_amount) AS detail"""),

    ("매출비중 범위",
     "revenue_ratio가 0~100 밖",
     f"""MATCH (a)-[r:SUPPLIES_TO]->(b) WHERE {_DART}
         AND r.revenue_ratio IS NOT NULL
         AND (toFloat(r.revenue_ratio) < 0 OR toFloat(r.revenue_ratio) > 100)
       RETURN elementId(r) AS eid,
              a.name + ' → ' + b.name + '  비중=' + toString(r.revenue_ratio) AS detail"""),

    ("근거 없음",
     "DART 엣지인데 evidence_id가 없다 — 화면에서 출처를 못 보여준다",
     f"""MATCH (a)-[r]->(b) WHERE {_DART}
         AND r.evidence_id IS NULL AND r.evidence_ids IS NULL
       RETURN elementId(r) AS eid, type(r) + ' ' + a.name + '→' + b.name AS detail"""),

    ("접수번호 없음",
     "source_doc이 비어 DART 원문으로 되짚을 수 없다",
     f"""MATCH (a)-[r]->(b) WHERE {_DART}
         AND (r.source_doc IS NULL OR toString(r.source_doc) = '')
         AND r.source_docs IS NULL
       RETURN elementId(r) AS eid, type(r) + ' ' + a.name + '→' + b.name AS detail"""),
]

_MARK = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
         "SET r.field_suspect = true, r.field_suspect_why = $why")


def run_fields(*, apply: bool, show: int) -> int:
    today = date.today().isoformat()
    total = 0
    with neo4j_session() as session:
        n_edges = session.run(
            f"MATCH ()-[r]->() WHERE {_DART} RETURN count(*) AS n").single()["n"]
        print(f"DART 엣지 {n_edges:,}건 · 검사 {len(CHECKS)}종 · 비용 0\n")

        for name, desc, query in CHECKS:
            rows = [dict(r) for r in session.run(query, today=today)]
            mark = "✗" if rows else "✓"
            print(f"{mark} {name:20}{len(rows):>5}건   {desc}")
            for r in rows[:show]:
                print(f"      · {r['detail'][:86]}")
            if len(rows) > show:
                print(f"      … 외 {len(rows) - show}건")
            total += len(rows)
            if rows and apply:
                session.run(_MARK, eids=[r["eid"] for r in rows], why=name)

    print(f"\n{'─' * 78}")
    if total:
        print(f"이상 {total}건 발견.")
        if apply:
            print("field_suspect 표시를 남겼습니다. **지우지 않았습니다** — "
                  "원문(data/raw_reports/)과 대조해 사람이 판단하세요.")
        else:
            print("--apply 로 표시를 남길 수 있습니다.")
    else:
        print("이상 없음. DART 엣지의 필드는 모두 정상 범위입니다.")
    return 0


# ════════════════════════════════════════════════════════════
#  ② 원문 대조 — 사업보고서 XML에 그 이름이 있는가
# ════════════════════════════════════════════════════════════

# 본문 파싱에서 온 엣지 유형
_PARSED_TYPES = ["DEVELOPS", "SUPPLIES_TO", "PARTNERS_WITH", "DEPENDS_ON"]

_FIND_PARSED = f"""
MATCH (a)-[r]->(b)
WHERE r.source_type = 'dart' AND type(r) IN {_PARSED_TYPES}
      AND r.source_doc IS NOT NULL
RETURN elementId(r) AS eid, type(r) AS t,
       coalesce(a.name,'') AS a, coalesce(b.name,'') AS b,
       labels(b)[0] AS b_label, toString(r.source_doc) AS doc
"""

_MARK_PARSED = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
         "SET r.parsed_suspect = true, r.parsed_suspect_why = $why")

_MARK_PARSED_OK = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
            "SET r.parsed_checked_at = datetime(), r.parsed_suspect = NULL, "
            "    r.parsed_suspect_why = NULL")


def _load_report(rcept_no: str) -> str:
    """접수번호 → 원문 전체 텍스트. 없으면 빈 문자열."""
    files = glob.glob(os.path.join(DEFAULT_DOWNLOAD_DIR, rcept_no, "**", "*.xml"),
                      recursive=True)
    if not files:
        return ""
    text = []
    # 사업보고서는 파일이 여러 개로 쪼개져 있다. 절 이름을 모르므로 **전부** 읽는다.
    for path in sorted(files, key=os.path.getsize, reverse=True)[:6]:
        try:
            for body in parse_sections(path).values():
                text.append(clean_text(body))
        except Exception:
            continue
    return "\n".join(text)


def _flat(s: str) -> str:
    """대조용 정규화 — 공백·괄호·구두점을 지우고 소문자화."""
    return re.sub(r"[\s()（）\[\]·,.\-_/'\"]+", "", s or "").lower()


def run_parsed(*, apply: bool, show: int) -> int:
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND_PARSED)]
    print(f"본문 파싱 엣지 {len(rows)}건 · 접수번호 "
          f"{len({r['doc'] for r in rows})}종\n")

    cache: dict[str, str] = {}
    missing_doc, ok, bad = [], [], []
    for r in rows:
        doc = r["doc"]
        if doc not in cache:
            cache[doc] = _flat(_load_report(doc))
        text = cache[doc]
        if not text:
            missing_doc.append(r)
            continue
        # 노드 이름이 원문에 있는가. **상대 노드**를 본다 — 주체는 보고서 주인이라
        # 항상 나오지만, 상대(제품·거래처)는 파싱이 지어냈을 수 있다.
        target = _flat(r["b"])
        if len(target) < 2:
            continue
        (ok if target in text else bad).append(r)

    print(f"✓ 원문에서 확인됨   {len(ok):>5}건")
    print(f"✗ 원문에 없음      {len(bad):>5}건   ← 파싱이 지어냈거나 표기가 다르다")
    if missing_doc:
        docs = {r["doc"] for r in missing_doc}
        print(f"· 원문 없음        {len(missing_doc):>5}건   "
              f"(접수번호 {len(docs)}종이 data/raw_reports/에 없음 — 판단 보류)")

    if bad:
        print(f"\n[원문에 없는 상대 노드 — 상위 {show}]")
        for k, v in Counter(f"{r['t']}  {r['b'][:34]}" for r in bad).most_common(show):
            print(f"   {k:52}{v:>4}건")

    print(f"\n유형별 확인율")
    by_type: dict[str, list[int]] = {}
    for r in ok:
        by_type.setdefault(r["t"], [0, 0])[0] += 1
    for r in bad:
        by_type.setdefault(r["t"], [0, 0])[1] += 1
    for t, (o, b) in sorted(by_type.items(), key=lambda x: -sum(x[1])):
        tot = o + b
        print(f"   {t:16}{o:>5}/{tot:<5} {o/max(tot,1)*100:>5.0f}%")

    if apply and bad:
        with neo4j_session() as session:
            session.run(_MARK_PARSED, eids=[r["eid"] for r in bad],
                        why="상대 노드 이름이 사업보고서 원문에 없음")
            if ok:
                session.run(_MARK_PARSED_OK, eids=[r["eid"] for r in ok])
        print(f"\n✅ {len(bad)}건에 parsed_suspect 표시 · {len(ok)}건 확인 기록 "
              f"(**지우지 않았습니다**)")
    elif bad:
        print("\n--apply 로 표시를 남길 수 있습니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="발견분에 field_suspect / parsed_suspect 표시")
    ap.add_argument("--only", choices=["fields", "parsed"],
                    help="하나만 실행 (기본은 둘 다)")
    ap.add_argument("--show", type=int, default=6, help="검사별 예시 개수")
    args = ap.parse_args()

    if args.only in (None, "fields"):
        run_fields(apply=args.apply, show=args.show)
    if args.only in (None, "parsed"):
        run_parsed(apply=args.apply, show=max(args.show, 10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
