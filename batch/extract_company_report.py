# [Phase 2] 사업보고서 XML -> "사업의 내용" 섹션 추출 CLI

from __future__ import annotations
import argparse
import json
import os
import sys
from typing import Optional
from pipeline.extractors.dart.report_extractor import extract_company_report
from schemas.company_report import CompanyReport

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_REPORT_TYPE = "사업보고서"

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "company_reports")

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", required=True, help="회사 식별자(예: 종목코드 005930)")
    parser.add_argument("--rcept-no", required=True, help="OpenDART 접수번호(예: 20250318000763)")
    parser.add_argument(
        "--report-year",
        help="사업연도. 생략하면 접수번호(rcept-no) 앞 4자리(접수연도)를 사용한다.",
    )
    parser.add_argument(
        "--report-type",
        default=DEFAULT_REPORT_TYPE,
        help=f"보고서 종류(기본값: {DEFAULT_REPORT_TYPE})",
    )
    return parser.parse_args(argv)

# 사업보고서 DTO를 JSON으로 직렬화하여 파일로 저장함. 
def save_report_json(report: CompanyReport) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    file_path = os.path.join(
        OUTPUT_DIR, f"{report.company_id}_{report.report_year}_business_report.json"
    )

    payload = {
        "company_id": report.company_id,
        "report_year": int(report.report_year),
        "report_type": report.report_type,
        "section": report.section,
        "text_length": len(report.text),
        "text": report.text,
        "source": {
            "dart_rcept_no": report.source.dart_rcept_no,
            "xml_path": report.source.xml_path,
        },
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return file_path


def main() -> None:
    args = parse_args()
    report_year = args.report_year or args.rcept_no[:4]

    report = extract_company_report(
        company_id=args.company_id,
        rcept_no=args.rcept_no,
        report_year=report_year,
        report_type=args.report_type,
    )

    if report is None:
        print(f"{args.company_id} {args.rcept_no}: '사업의 내용' 섹션을 찾지 못했습니다.")
        return

    file_path = save_report_json(report)

    print("Saved:")
    print(file_path)
    print()
    print(f"Report Year : {report.report_year}")
    print(f"Section : {report.section}")
    print(f"Text Length : {len(report.text)} chars")


if __name__ == "__main__":
    main()
