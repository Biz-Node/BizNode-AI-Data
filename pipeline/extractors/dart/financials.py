"""DART 단일회사 주요계정(fnlttSinglAcnt) → financials 테이블 + 노드 스냅샷.

한 번 호출로 당기/전기/전전기 3년치를 얻는다. 연결(CFS) 우선, 없으면 별도(OFS).
재무는 RDB 전용(§6-2) — Neo4j 노드엔 최신 매출 스냅샷만.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from app.core.config import DART_KEY

FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

# 계정명 → financials 컬럼 (sj_div, 매칭 키워드)
_ACCOUNT_MAP = {
    "revenue": ("IS", ("매출액", "영업수익")),
    "operating_profit": ("IS", ("영업이익",)),
    "net_profit": ("IS", ("당기순이익",)),
    "total_assets": ("BS", ("자산총계",)),
    "total_liabilities": ("BS", ("부채총계",)),
    "total_equity": ("BS", ("자본총계",)),
}


def _parse_amount(raw: Optional[str]) -> Optional[int]:
    if not raw or raw.strip() in ("-", ""):
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None


def fetch_financials(corp_code: str, years: tuple[int, ...] = (2025, 2024)) -> list[dict[str, Any]]:
    """연도 후보를 순서대로 시도해 첫 성공 응답의 3년치를 financials 행으로 반환.
    각 행: {corp_code, bsns_year, reprt_code, revenue, operating_profit, net_profit,
            total_assets, total_equity}
    """
    for year in years:
        resp = requests.get(FNLTT_URL, params={
            "crtfc_key": DART_KEY, "corp_code": corp_code,
            "bsns_year": str(year), "reprt_code": "11011",
        }, timeout=30)
        data = resp.json()
        if data.get("status") != "000" or not data.get("list"):
            continue
        return _parse_rows(corp_code, year, data["list"])
    return []


def _parse_rows(corp_code: str, base_year: int, rows: list[dict]) -> list[dict[str, Any]]:
    # 연결(CFS) 우선, 없으면 별도(OFS)
    fs_div = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"

    # {연도offset: {컬럼: 금액}}  offset 0=당기, 1=전기, 2=전전기
    result: dict[int, dict[str, Optional[int]]] = {0: {}, 1: {}, 2: {}}
    for r in rows:
        if r.get("fs_div") != fs_div:
            continue
        sj, nm = r.get("sj_div"), r.get("account_nm", "")
        for col, (want_sj, keywords) in _ACCOUNT_MAP.items():
            if sj == want_sj and any(k in nm for k in keywords):
                result[0].setdefault(col, _parse_amount(r.get("thstrm_amount")))
                result[1].setdefault(col, _parse_amount(r.get("frmtrm_amount")))
                result[2].setdefault(col, _parse_amount(r.get("bfefrmtrm_amount")))
                break

    out = []
    for offset, cols in result.items():
        if not any(v is not None for v in cols.values()):
            continue
        out.append({
            "corp_code": corp_code,
            "bsns_year": base_year - offset,
            "reprt_code": "11011",
            "fs_div": fs_div,
            "revenue": cols.get("revenue"),
            "operating_profit": cols.get("operating_profit"),
            "net_profit": cols.get("net_profit"),
            "total_assets": cols.get("total_assets"),
            "total_liabilities": cols.get("total_liabilities"),
            "total_equity": cols.get("total_equity"),
        })
    return out
