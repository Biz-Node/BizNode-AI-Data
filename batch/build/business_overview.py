"""사업보고서 「사업의 내용」 **원문**을 `business_overview` 에 담는다.

★왜 따로 만드나 (2026-08-15)

`build/company_detail` 이 이미 같은 절을 파싱한다. 그런데 그건 **LLM 에게
읽혀 요약을 만드는** 것이 목적이라, 꺼낸 원문을 쓰고 버린다. 그 결과
`business_overview` 가 0행으로 남아 있었다.

    company_profiles.text          LLM 이 만든 소개문 → ChromaDB 임베딩 (챗봇이 검색)
    business_overview.overview_text  **사업보고서 원문 그대로** → 상세 화면 · 인용

요약만 있고 원문이 없으면 챗봇이 **「사업보고서에 이렇게 적혀 있습니다」라고
인용을 못 한다.** 요약은 우리가 만든 문장이라 근거가 되지 못한다.

★비용이 0이다 — DART 를 다시 부르지 않고, LLM 도 안 쓴다.
      원문 XML  data/raw_reports/{접수번호}/  에 이미 있다 (64/64)
      파싱      parse_sections 가 목차 TITLE 별로 쪼갠다
      저장      꺼낸 텍스트를 그대로 넣는다

  그래서 `company_detail`(기업당 40원)과 **분리한다.** 요약을 다시 만들 일과
  원문을 담을 일은 주기도 비용도 다르다.

실행:
    python -m batch.build.business_overview --dry-run
    python -m batch.build.business_overview
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from app.core.database import postgres_connection
from pipeline.extractors.dart.company_detail import SECTION_OVERVIEW
from pipeline.extractors.dart.downloader import DEFAULT_DOWNLOAD_DIR
from pipeline.extractors.dart.text_cleaner import clean_text
from pipeline.extractors.dart.xml_parser import parse_sections

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 「II. 사업의 내용」 아래 두 절. 개요는 회사가 무엇을 하는지, 제품은 무엇을 파는지.
SECTION_PRODUCTS = "주요 제품 및 서비스"

_UPSERT = """
INSERT INTO business_overview
    (corp_code, bsns_year, overview_text, products_text, source_doc, updated_at)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (corp_code, bsns_year) DO UPDATE SET
    overview_text = EXCLUDED.overview_text,
    products_text = EXCLUDED.products_text,
    source_doc    = EXCLUDED.source_doc,
    updated_at    = now()
"""


def _pick(secs: dict[str, str], want: str) -> str:
    """목차 제목에 부분일치하는 절을 정제 텍스트로. 공백은 무시한다."""
    w = want.replace(" ", "")
    key = next((t for t in secs if w in t.replace(" ", "")), None)
    return clean_text(secs[key]) if key else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시")
    args = ap.parse_args()

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT d.corp_code, d.rcept_no, d.rcept_dt,
                              coalesce(a.name, d.corp_code) AS name
                       FROM documents d
                       LEFT JOIN company_attributes a ON a.corp_code = d.corp_code
                       WHERE d.doc_type = '사업보고서' ORDER BY 4""")
        docs = cur.fetchall()
        cur.execute("SELECT corp_code, bsns_year FROM business_overview")
        done = {(c.strip(), y) for c, y in cur.fetchall()}

    print(f"사업보고서 {len(docs)}건 · 이미 담은 것 {len(done)}건 · 비용 0원\n")

    rows, no_file, no_section = [], 0, 0
    for code, rcept, dt, name in docs:
        year = int(str(dt)[:4]) - 1 if dt else None      # 보고서는 전년도 실적
        if not args.force and (code.strip(), year) in done:
            continue
        files = glob.glob(os.path.join(DEFAULT_DOWNLOAD_DIR, rcept, "**", "*.xml"),
                          recursive=True)
        if not files:
            no_file += 1
            continue
        try:
            secs = parse_sections(max(files, key=os.path.getsize))
        except Exception as exc:
            print(f"  ! {name[:14]:<16}파싱 실패 {exc!r}"[:90])
            no_file += 1
            continue

        ov = _pick(secs, SECTION_OVERVIEW)
        pd = _pick(secs, SECTION_PRODUCTS)
        if not ov and not pd:
            no_section += 1
            print(f"  · {name[:14]:<16}절 없음 (목차 {len(secs)}개)")
            continue
        rows.append((code, year, ov or None, pd or None, rcept))
        print(f"  {name[:14]:<16}{year}년  개요 {len(ov):>6,}자 · 제품 {len(pd):>6,}자")

    print(f"\n담을 것 {len(rows)}건 · 원문 없음 {no_file} · 절 없음 {no_section}")
    if args.dry_run or not rows:
        if args.dry_run and rows:
            print(f"\n── 개요 예시 ──\n{rows[0][2][:400]}")
            print("\n[dry-run] 담지 않았습니다.")
        return 0

    with postgres_connection() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(_UPSERT, r)
    print(f"\n✅ business_overview {len(rows)}건 적재")
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*), count(DISTINCT corp_code),
                       sum(length(coalesce(overview_text,'')) +
                           length(coalesce(products_text,''))) FROM business_overview""")
        n, c, chars = cur.fetchone()
        print(f"   누적 {n}행 · {c}개사 · 원문 {chars:,}자")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
