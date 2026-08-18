"""주요사항보고서(DS005) 구조화 API 수집 — 합병·주식취득·소송.

원문 파싱이 아니라 구조화 JSON. corp_code + bgn_de/end_de(사건 기반).
반도체·로봇 시드 실측 시 합병 9·주식양수 5·소송 1건, 부도/회생 0로 양이 적다.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from app.core.config import DART_KEY

_BASE = "https://opendart.fss.or.kr/api"

# 유형 → 엔드포인트 slug
SLUGS = {
    "merger": "cmpMgDecsn",                   # 회사합병결정
    "stock_acquisition": "otcprStkInvscrInhDecsn",  # 타법인 주식 양수결정
    "lawsuit": "lwstLg",                      # 소송 등의 제기
}


def fetch_major_report(corp_code: str, kind: str, bgn_de: str, end_de: str) -> list[dict[str, Any]]:
    """유형별 주요사항보고 목록. 없으면 빈 리스트."""
    slug = SLUGS[kind]
    resp = requests.get(f"{_BASE}/{slug}.json", params={
        "crtfc_key": DART_KEY, "corp_code": corp_code,
        "bgn_de": bgn_de, "end_de": end_de,
    }, timeout=20)
    time.sleep(0.15)
    data = resp.json()
    if data.get("status") != "000":
        return data.get("list", []) if data.get("status") == "013" else []
    return data.get("list", [])
