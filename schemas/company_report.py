# 사업보고서에서 추출한 섹션 텍스트를 담는 DTO.

from __future__ import annotations
from dataclasses import dataclass
from schemas.source import Source

@dataclass
class CompanyReport:
    company_id: str   # 회사 식별자(예: 종목코드 005930)
    rcept_no: str     # OpenDART 접수번호(예: 202
    report_year: str  # 사업연도(YYYY)
    report_type: str  # 보고서 종류(예: "사업보고서")
    section: str      # 추출한 섹션명(예: "사업의 내용")
    text: str         # 추출한 섹션 텍스트
    source: Source    # 출처 정보(원본 공시 문서)
