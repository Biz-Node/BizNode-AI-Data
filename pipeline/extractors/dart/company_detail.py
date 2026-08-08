"""사업보고서에서 **기업 개요**와 **사업부문**을 뽑는다.

★왜 필요한가 (2026-08-01)

서비스의 「기업 상세」 화면이 요구하는 8개 항목 중 2개가 비어 있었다:

    재무·연관기업·리스크·뉴스·공시   ✅ 이미 있음
    기업 개요 · 주요 사업부문        ❌ company_profiles·business_segments 0행
    주가                            ⏸ 보류 (외부 소스 필요)

기존 사업보고서 파서(`business_report.py`)는 제품·계약·계열사 세 절만 봤다.
개요와 사업부문은 다른 절에 있다:

    「사업의 개요」          60/62건 (97%)  → company_profiles
    「주요 제품 및 서비스」   59/62건 (95%)  → business_segments

★「부문별 보고」를 쓰면 안 된다 — 62건 중 **3건(5%)**뿐이다. 연결재무제표 주석의
  세그먼트 정보라 대부분의 중소형사가 작성하지 않는다. 「주요 제품 및 서비스」가
  95% 커버되고 품목·매출·비중이 함께 나온다.

원문은 이미 받아둔 것을 쓴다(`data/raw_reports/`). DART 재호출이 없다.
"""

from __future__ import annotations

import json
from typing import Optional

import openai

from app.core.config import OPENAI_API_KEY

_MODEL = "gpt-4o-mini"      # 요약·표 정리라 상위 모델이 필요 없다

SECTION_OVERVIEW = "사업의 개요"
SECTION_SEGMENT = "주요 제품 및 서비스"

_SYSTEM = """사업보고서에서 **기업 개요**와 **주요 사업부문**을 정리하세요.

【overview — 기업 개요】
이 회사가 **무엇을 하는 회사인지** 3~5문장으로. 투자자·분석가가 읽는다고 가정하세요.
· 주력 사업과 제품, 시장에서의 위치, 주요 고객군을 담으세요.
· 보고서 문장을 그대로 베끼지 말고 **압축해서 다시 쓰세요.**
· 홍보 문구("업계 최고", "혁신적인")는 빼고 사실만 쓰세요.
· 연혁·설립일·조직도는 넣지 마세요(별도 필드에 있습니다).

【segments — 주요 사업부문】
품목별 매출 표에서 부문을 추출하세요.
· name    : 부문·품목명을 본문 그대로 (예: "IC TEST SOCKET류")
· revenue : 최근 사업연도 매출액을 **원 단위 정수**로.
            표가 천원 단위면 ×1000 하세요. 알 수 없으면 null.
· ratio   : 전체 매출 대비 비중(%). 표에 있으면 그대로, 없으면 계산. 모르면 null.
· 합계·소계 행은 **제외**하세요.
· 수출/내수 구분만 다른 같은 품목은 **하나로 합치세요**.

★★ 표에 **연도가 여러 개**면 반드시 첫 번째(가장 최근) 연도만 쓰세요.
   실측 오류 — 넥스틴 표는 열이 「2025 / 2024 / 2023 / 2022」 네 개인데,
   2024년 값을 2025년으로 읽어 비중 합계가 **201%**가 됐습니다:

       AEGIS-II  2021.06 | 18,238 (39.1%) | 74,605(74%) | 54,354(72.0%) | 66,549(65.8%)
                          └ 2025 ←써야 함   └ 2024 ✗     └ 2023 ✗      └ 2022 ✗

   최근 연도 칸이 「-」이면 그 품목의 revenue·ratio는 **null**입니다.
   다른 연도 숫자를 끌어오지 마세요.

★★ **부문을 빠뜨리지 마세요.** 자회사·합병법인이 별도 행으로 있으면 그것도 부문입니다.
   실측 오류 — 덕산네오룩스에서 「현대중공업터보기계 41.2%」를 빼먹어 비중이 59%였습니다:

       덕산네오룩스(주)   디스플레이 소재   202,284   58.8%
       현대중공업터보기계  펌프·압축기 외    142,026   41.2%   ← 이것도 부문
       계                            344,310  100.0%   ← 이건 제외

   **뽑은 부문들의 ratio 합이 100%에 가까운지 스스로 확인**하세요.
   많이 모자라면 빠뜨린 행이 있다는 뜻입니다.

【주의】
· 표를 잘못 읽느니 null이 낫습니다. 숫자에 확신이 없으면 null로 두세요.
· 부문이 하나뿐이면 하나만 반환하세요. 억지로 나누지 마세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "revenue": {"type": ["integer", "null"]},
                    "ratio": {"type": ["number", "null"]},
                },
                "required": ["name", "revenue", "ratio"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "segments"],
    "additionalProperties": False,
}

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_detail(corp_name: str, overview_text: str,
                   segment_text: str) -> Optional[dict]:
    """두 절의 본문 → {overview, segments}. 실패하면 None."""
    if not overview_text.strip() and not segment_text.strip():
        return None
    user = (f"회사: {corp_name}\n\n"
            f"[사업의 개요]\n{overview_text[:6000]}\n\n"
            f"[주요 제품 및 서비스]\n{segment_text[:6000]}")
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL, temperature=0,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "company_detail",
                                             "schema": _SCHEMA, "strict": True}})
        data = json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"    [company_detail] LLM 실패: {exc!r}")
        return None

    segs = []
    for s in data.get("segments", []):
        name = (s.get("name") or "").strip()
        # 합계 행이 프롬프트를 뚫고 들어오는 경우가 있어 코드로도 막는다
        if not name or name.replace(" ", "") in ("합계", "총계", "소계", "계"):
            continue
        segs.append({"name": name, "revenue": s.get("revenue"),
                     "ratio": s.get("ratio")})
    return {"overview": (data.get("overview") or "").strip(), "segments": segs}
