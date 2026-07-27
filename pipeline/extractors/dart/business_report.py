"""사업보고서 → 타겟 섹션(II-2 제품·IX 계열사·II-6 계약) 추출 (경로 C, Sprint 3).

parse_sections가 목차 TITLE별로 분리하고 번호 접두어를 벗겨준다.
타겟 섹션만 정제 텍스트로 뽑는다(전체 파싱 안 함 — 방법서 §7).
"""

from __future__ import annotations

from typing import Optional

from pipeline.extractors.dart.disclosure_list import fetch_filings
from pipeline.extractors.dart.downloader import download_and_extract
from pipeline.extractors.dart.text_cleaner import clean_text
from pipeline.extractors.dart.xml_parser import parse_sections

# 타겟 섹션 (정규화 제목 부분일치, 공백 무시)
TARGET_SECTIONS = {
    "products": "주요제품및서비스",       # II-2
    "affiliates": "계열회사현황(상세)",    # IX 상세
    "contracts": "주요계약및연구개발활동",  # II-6
}


def find_business_report(corp_code: str, bgn_de: str, end_de: str) -> Optional[dict]:
    """최신 사업보고서 공시(반기·분기 제외). 없으면 None."""
    filings = fetch_filings(corp_code, bgn_de, end_de)
    for f in filings:  # list.json은 최신순
        nm = f.get("report_nm", "")
        if "사업보고서" in nm and "반기" not in nm and "분기" not in nm:
            return f
    return None


def get_report_sections(rcept_no: str) -> dict[str, str]:
    """사업보고서 원문 → {products, affiliates, contracts} 정제 텍스트."""
    xml_path = download_and_extract(rcept_no)
    sections = parse_sections(xml_path)

    # 정규화 제목(공백제거) → 원본 키 인덱스
    by_norm = {t.replace(" ", ""): t for t in sections}

    out: dict[str, str] = {}
    for key, want in TARGET_SECTIONS.items():
        match = next((orig for norm, orig in by_norm.items() if want in norm), None)
        if match:
            out[key] = clean_text(sections[match])
    return out
