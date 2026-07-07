# 다트 접수번호 -> OpenDART 보고서 본문 -> 사업 내용 섹션 추출 -> 정제된 텍스트로 반환하는 파이프라인.
from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol

from pipeline.extractors.dart import downloader as dart_downloader
from pipeline.extractors.dart import text_cleaner as dart_text_cleaner
from pipeline.extractors.dart import xml_parser as dart_xml_parser
from schemas.company_report import CompanyReport
from schemas.source import Source

BUSINESS_SECTION_NAME = "사업의 내용"

class ReportDownloader(Protocol):
    """접수번호로 본문 XML을 확보해 로컬 경로를 반환하는 컴포넌트의 인터페이스."""

    def __call__(self, rcept_no: str) -> Path: ...


class SectionParser(Protocol):
    """XML 파일을 `{섹션명: 원문}`으로 구조화하는 컴포넌트의 인터페이스."""

    def __call__(self, xml_path: Path) -> dict[str, str]: ...


class TextCleaner(Protocol):
    """섹션 원문을 정제된 텍스트로 바꾸는 컴포넌트의 인터페이스."""

    def __call__(self, raw_text: str) -> str: ...


def extract_company_report(
    company_id: str,    # 회사 식별자
    rcept_no: str,      # OpenDART 접수번호
    report_year: str,   # 사업연도
    report_type: str,   # 보고서 종류
    *,                  # 키워드 전용 인자
    download: ReportDownloader = dart_downloader.download_and_extract,  # XML 확보 컴포넌트.
    parse_sections: SectionParser = dart_xml_parser.parse_sections,     # XML -> 섹션 구조화 컴포넌트.
    clean_text: TextCleaner = dart_text_cleaner.clean_text,             # 섹션 원문 -> 정제 텍스트 컴포넌트.
    section_name: str = BUSINESS_SECTION_NAME,                          # 추출할 섹션명
) -> Optional[CompanyReport]:
    
    xml_path = download(rcept_no)
    sections = parse_sections(xml_path)

    raw_section_text = sections.get(section_name)
    if raw_section_text is None:
        return None

    cleaned_text = clean_text(raw_section_text)
    if not cleaned_text:
        return None

    return CompanyReport(
        company_id=company_id,
        rcept_no=rcept_no,
        report_year=report_year,
        report_type=report_type,
        section=section_name,
        text=cleaned_text,
        source=Source(dart_rcept_no=rcept_no, xml_path=str(xml_path)),
    )
