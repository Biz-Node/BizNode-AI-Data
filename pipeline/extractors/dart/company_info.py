"""DART 기업개황(company.json) 수집 → Company 노드 속성.

corp_name_eng(name_en), ceo_nm, induty_code, est_dt, corp_cls(시장구분).
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from app.core.config import DART_KEY

COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"

# corp_cls → 시장구분
CORP_CLS_MARKET = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "비상장"}


def fetch_company_info(corp_code: str) -> Optional[dict[str, Any]]:
    """기업개황 응답(dict). 실패 시 None."""
    resp = requests.get(
        COMPANY_URL, params={"crtfc_key": DART_KEY, "corp_code": corp_code}, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return None
    return data


def to_iso_date(yyyymmdd: Optional[str]) -> Optional[str]:
    """'19690113' -> '1969-01-13'. 형식이 다르면 None."""
    if not yyyymmdd:
        return None
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None
