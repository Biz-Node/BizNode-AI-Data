"""DART 단일회사 주요계정(fnlttSinglAcnt) → financials 테이블 + 노드 스냅샷.

한 번 호출로 당기/전기/전전기 3년치를 얻는다. 연결(CFS) 우선, 없으면 별도(OFS).
재무는 RDB 전용 — Neo4j 노드엔 최신 매출 스냅샷만.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from app.core.config import DART_KEY

FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

FNLTT_ALL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

# 계정명 → financials 컬럼 (sj_div 후보, 매칭 키워드)
#
# ★`sj_div`가 API마다 다름. 요약 재무제표는 손익계산서를 `IS`로 주는데,
# 전체 재무제표는 `CIS`(포괄손익계산서)로 준다. `IS`만 보면 전체 재무제표에서 아무것도 못 건진다.
_ACCOUNT_MAP = {
    "revenue": (("IS", "CIS"), ("매출액", "영업수익")),
    "operating_profit": (("IS", "CIS"), ("영업이익",)),
    "net_profit": (("IS", "CIS"), ("당기순이익",)),
    "total_assets": (("BS",), ("자산총계",)),
    "total_liabilities": (("BS",), ("부채총계",)),
    "total_equity": (("BS",), ("자본총계",)),
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
        rows = _parse_rows(corp_code, year, data["list"])

        # ★요약 재무제표에 **매출액이 빠져 있는 회사가 있다**(실측: 유일로보틱스).
        #   DART 요약본은 계정을 5~6개로 줄이는데, 그 과정에서 매출이 빠지기도 한다.
        #   그때만 전체 재무제표를 한 번 더 불러 매출을 채운다 — 무료이고,
        #   매출이 없으면 사업부문 단위 검증도 못 하므로 그냥 넘기면 안 된다.
        if rows and all(r.get("revenue") is None for r in rows):
            full = _fetch_full(corp_code, year)
            if full:
                by_year = {r["bsns_year"]: r for r in full}
                for r in rows:
                    src = by_year.get(r["bsns_year"])
                    if src and src.get("revenue") is not None:
                        r["revenue"] = src["revenue"]
        return rows
    return []


def _fetch_full(corp_code: str, year: int) -> list[dict[str, Any]]:
    """전체 재무제표(계정 181개). 요약본이 매출을 빠뜨렸을 때만 부른다."""
    for fs_div in ("CFS", "OFS"):        # 연결 우선
        try:
            data = requests.get(FNLTT_ALL_URL, params={
                "crtfc_key": DART_KEY, "corp_code": corp_code,
                "bsns_year": str(year), "reprt_code": "11011", "fs_div": fs_div,
            }, timeout=30).json()
        except Exception:
            continue
        if data.get("status") == "000" and data.get("list"):
            got = _parse_rows(corp_code, year, data["list"], fs_hint=fs_div)
            if any(r.get("revenue") is not None for r in got):
                return got
    return []


def _parse_rows(corp_code: str, base_year: int, rows: list[dict],
                fs_hint: Optional[str] = None) -> list[dict[str, Any]]:
    # 연결(CFS) 우선, 없으면 별도(OFS)
    #
    # ★전체 재무제표(`fnlttSinglAcntAll`)는 **`fs_div`를 안 준다**(전부 None) —
    #   요청할 때 이미 fs_div를 지정하니 응답에 안 넣는 것이다. 그걸 모르고
    #   `r.get("fs_div") != fs_div`로 거르면 **모든 행이 탈락한다**(실측:
    #   유일로보틱스 CIS 19행이 전부 버려졌다). 힌트를 받아 그때는 거르지 않는다.
    has_div = any(r.get("fs_div") for r in rows)
    fs_div = (fs_hint if not has_div
              else ("CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"))

    # {연도offset: {컬럼: 금액}}  offset 0=당기, 1=전기, 2=전전기
    result: dict[int, dict[str, Optional[int]]] = {0: {}, 1: {}, 2: {}}
    for r in rows:
        if has_div and r.get("fs_div") != fs_div:
            continue
        sj, nm = r.get("sj_div"), r.get("account_nm", "")
        for col, (want_sj, keywords) in _ACCOUNT_MAP.items():
            if sj in want_sj and any(k in nm for k in keywords):
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
