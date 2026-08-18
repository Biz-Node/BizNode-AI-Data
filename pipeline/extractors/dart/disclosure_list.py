"""공시검색(list.json)으로 특정 유형 공시 목록 수집.

list.json은 응답에 pblntf_ty(유형코드)를 싣지 않으므로 report_nm(보고서명)
부분일치로 필터한다. 시계열 반복·정정 공시 포함.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from app.core.config import DART_KEY

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_PAGE_COUNT = 100
_MAX_PAGES = 40
_REQUEST_DELAY = 0.25


def fetch_filings(corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, Any]]:
    """기간 내 전체 공시 목록을 페이지네이션으로 수집."""
    filings: list[dict[str, Any]] = []
    page = 1
    while page <= _MAX_PAGES:
        resp = requests.get(LIST_URL, params={
            "crtfc_key": DART_KEY, "corp_code": corp_code,
            "bgn_de": bgn_de, "end_de": end_de,
            "page_no": page, "page_count": _PAGE_COUNT,
        }, timeout=30)
        time.sleep(_REQUEST_DELAY)
        data = resp.json()
        status = data.get("status")
        if status == "013":       # 결과 없음
            break
        if status != "000":
            break
        filings.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
    return filings


def filter_by_keyword(filings: list[dict[str, Any]], keyword: str) -> Iterator[dict[str, Any]]:
    """report_nm에 keyword를 포함하는 공시만."""
    for f in filings:
        if keyword in f.get("report_nm", ""):
            yield f


def latest_supply_contracts(corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, Any]]:
    """공급계약 공시만 필터링해 반환. 정정 공시는 원 접수번호 기준 최신 1건만 유지.
    (report_nm의 '[기재정정]' 등을 제거한 핵심 제목 + 최근접수일 기준)
    """
    contracts = list(filter_by_keyword(fetch_filings(corp_code, bgn_de, end_de), "공급계약"))
    # 해지 공시는 SUPPLIES_TO 생성 대상 아님(관계 종료) — 별도 처리 위해 표시만
    return [c for c in contracts if "해지" not in c.get("report_nm", "")]
