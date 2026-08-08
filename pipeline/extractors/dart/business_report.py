"""사업보고서 → 타겟 섹션(II-2 제품·IX 계열사·II-6 계약) 추출 (경로 C).

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


# ★「사업보고서」라는 낱말이 든 다른 공시들. 이름만 보고 최신순으로 집으면
#   본문이 없는 문서를 잡는다(2026-08-03 실측):
#
#     케이티     「해외증권거래소등에신고한사업보고서등의국내신고」  절 0개 · 5,634바이트
#     제이브이엠  「[첨부정정]사업보고서」                        절 9개(감사보고서뿐)
#
#   둘 다 진짜 사업보고서가 며칠 앞서 따로 있었는데, **더 최신**이라 이것들이
#   먼저 잡혔다. 그러면 「사업의 개요」·「주요 제품 및 서비스」 절이 없어
#   개요·사업부문이 통째로 비는데, 화면에는 그냥 「데이터 없음」으로 보인다.
_NOT_ANNUAL = ("반기", "분기", "해외증권거래소", "국내신고")

# 정정 공시는 **본문을 다시 올리지 않는 경우가 있다**([첨부정정] 등).
# 원본이 같은 기간에 있으면 원본을 쓰고, 정정본밖에 없으면 그거라도 쓴다.
_AMENDED = ("[첨부정정]", "[첨부추가]")


def find_business_report(corp_code: str, bgn_de: str, end_de: str) -> Optional[dict]:
    """최신 사업보고서 공시(반기·분기·해외신고·첨부정정 제외). 없으면 None."""
    filings = fetch_filings(corp_code, bgn_de, end_de)
    cands = [f for f in filings
             if "사업보고서" in f.get("report_nm", "")
             and not any(w in f.get("report_nm", "") for w in _NOT_ANNUAL)]
    if not cands:
        return None
    plain = [f for f in cands
             if not any(w in f.get("report_nm", "") for w in _AMENDED)]
    return (plain or cands)[0]        # list.json은 최신순


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
