"""DART 주식의 총수(stockTotqySttus) → 유통 주식수.

★왜 필요한가 (2026-08-15)

시장 지표를 **우리가 계산하기 위해서**다. 주가만으로는 시가총액도 PER 도 못 낸다.

    시가총액 = 종가 × 유통주식수
    PER     = 시가총액 ÷ 당기순이익
    PBR     = 시가총액 ÷ 자본총계
    PSR     = 시가총액 ÷ 매출액

  뒤 셋의 분모는 이미 `financials` 에 있다. **주식수 하나만 있으면 전부 나온다.**

★왜 남의 API 에서 지표를 받아 오지 않나

  ① `pykrx` 는 시총·PER·PBR 엔드포인트에 **KRX 로그인을 요구**한다(1.2.8 실측).
     주가·거래량·등락률만 인증 없이 된다.
  ② 받아 와도 **기준을 모른다.** PER 이 연결인지 별도인지, 어느 분기 실적인지,
     우선주를 포함했는지가 제공처마다 다르다. 우리 `financials` 는 `fs_div` 로
     연결·별도를 구분해 두었으므로 **직접 계산하는 편이 설명 가능하다.**

★자기주식을 뺀다. 회사가 들고 있는 주식은 시장에 없으므로 시가총액에서 빼는 것이
  맞다. DART 는 발행총수(`istc_totqy`)와 자기주식(`tesstk_co`)을 따로 준다.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from app.core.config import DART_KEY

STOCK_TOTQY_URL = "https://opendart.fss.or.kr/api/stockTotqySttus.json"

# 보고서 코드 — 사업보고서를 먼저 보고 없으면 분기로 내려간다
_REPRT_CODES = ("11011", "11014", "11012", "11013")


def _num(raw: Optional[str]) -> Optional[int]:
    """DART 숫자 문자열 → int. 「-」·공백·쉼표를 흡수한다."""
    if not raw:
        return None
    s = str(raw).replace(",", "").strip()
    if s in ("-", "", "0"):
        return 0 if s == "0" else None
    try:
        return int(float(s))
    except ValueError:
        return None


# ★상장사 발행주식수 상한. 국내 최대가 삼성전자 59억 주라 **200억 주**를 넘으면
#   값이 잘못된 것이다. 실측(2026-08-15): DART 가 LS에코에너지를
#   `30,624,879,000,000`(30조 주)로 준다 — 실제는 약 3천만 주로, **회사가
#   공시에 단위를 잘못 적었다.** 우리 파싱 오류가 아니라 원본 오류다.
#
#   이걸 안 막으면 시가총액이 146만 조가 되어 **시총 순위가 통째로 뒤집힌다.**
#   지우지 않고 `suspect` 로 표시해, 지표 계산에서만 뺀다.
_MAX_SHARES = 20_000_000_000


def fetch_shares(corp_code: str, year: int = 2025) -> Optional[dict[str, Any]]:
    """유통 주식수를 돌려준다. 못 구하면 None.

    반환: {"listed": 유통주식수, "issued": 발행총수, "treasury": 자기주식,
           "bsns_year": 연도, "reprt_code": 보고서코드,
           "suspect": 상한을 넘어 못 믿음, "suspect_why": 사유}

    ★보통주만 센다. 우선주는 시가가 달라 같은 주가로 곱하면 틀린다.
      DART 는 `se`(구분)에 「보통주」·「우선주」·「합계」를 준다.
    """
    for reprt in _REPRT_CODES:
        try:
            data = requests.get(STOCK_TOTQY_URL, params={
                "crtfc_key": DART_KEY, "corp_code": corp_code,
                "bsns_year": str(year), "reprt_code": reprt,
            }, timeout=20).json()
        except Exception:
            continue
        if data.get("status") != "000":
            continue

        rows = data.get("list") or []
        common = [r for r in rows if "보통주" in (r.get("se") or "")]
        if not common:
            continue
        r = common[0]
        issued = _num(r.get("istc_totqy"))        # 발행한 주식의 총수
        treasury = _num(r.get("tesstk_co")) or 0  # 자기주식 수
        # 「유통주식수」를 직접 주기도 한다(distb_stock_co). 있으면 그걸 믿는다.
        listed = _num(r.get("distb_stock_co"))
        if listed is None and issued is not None:
            listed = issued - treasury
        if not listed or listed <= 0:
            continue
        suspect = listed > _MAX_SHARES
        return {"listed": listed, "issued": issued, "treasury": treasury,
                "bsns_year": year, "reprt_code": reprt,
                "suspect": suspect,
                "suspect_why": (f"발행주식수 {listed:,}주 — 상한 {_MAX_SHARES:,} 초과. "
                                f"공시 단위 오류로 보임" if suspect else None)}
    return None
