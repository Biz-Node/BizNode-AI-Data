"""사업보고서 「매출 및 수주상황」(II-4)에서 **거래처**를 뽑는다.

★왜 필요한가 (2026-08-01 실측)

거래처 정보의 주 출처가 **공급계약 공시**(단일판매·공급계약 체결)인데, 그건
계약 규모가 매출의 5%를 넘을 때만 공시 의무가 생긴다. 그래서 소액 다수 거래를
하는 기업은 한 건도 없다:

    리노공업   공급계약 공시 0건  →  기업 연결이 기관투자자 5건뿐 (그래프의 섬)

이를 보완할 사업보고서 파서는 세 섹션만 보고 있었다:
    products   주요제품및서비스        (II-2)
    affiliates 계열회사현황(상세)       (IX)
    contracts  주요계약및연구개발활동    (II-6)  ← 기술도입·공동연구가 주로 실린다
그런데 **거래처 명단은 II-4「매출 및 수주상황」에 있다.** 이 섹션을 안 봤다.

62개 사업보고서를 조사한 결과:
    섹션 없음                3건
    섹션 있고 고객사 언급     23건 (37%)   ← 여기서 건진다
    섹션 있으나 고객사 없음   36건         ← 리노공업이 여기. 원 데이터에 없다

이 모듈은 그 23개사 분량을 회수한다. 원문이 이미 받아져 있어 **DART 재호출이
필요 없다**(비용은 LLM 추출분뿐).

★문자열 매칭으로 넣지 않는 이유
섹션에 「삼성전자」가 나온다고 전부 고객사가 아니다 — 「삼성전자 대비 점유율」
같은 맥락일 수 있고, 조사에서 LG전자가 자기 이름을 매칭한 사례도 있었다.
그래서 뉴스와 **같은 검증 체계**(LLM 추출 → 매트릭스 → 근거 검증)를 태운다.
"""

from __future__ import annotations

import json
from typing import Optional

import openai

from app.core.config import OPENAI_API_KEY

_MODEL = "gpt-4o"          # 관계 추출이라 뉴스와 같은 등급을 쓴다

SECTION_NAME = "매출 및 수주상황"

_SYSTEM = """사업보고서의 「매출 및 수주상황」 절에서 **거래 상대 기업**을 뽑으세요.

이 절에는 매출 실적(품목·금액), 판매경로, 수주 현황이 실립니다.
그중 **회사 이름이 등장하는 거래 관계**만 추출하세요.

【추출 대상】
· 주요 매출처·고객사    → direction="we_supply"  (이 회사가 그들에게 판다)
· 주요 매입처·공급업체   → direction="they_supply" (그들이 이 회사에 판다)

【★추출하지 말 것 — 가장 흔한 오류】
· 시장 점유율·경쟁사 비교에 나온 회사   "삼성전자 대비 점유율 3%"
· 산업 설명에 나온 회사               "글로벌 반도체 업체들이…"
· **자기 회사 이름** (지점·해외법인 포함)
· 「국내 반도체 업체」「대리점」처럼 **이름이 아닌 것**
· 금액·비중만 있고 상대가 없는 항목

【판단 기준】
그 회사가 **이 회사와 실제로 사고파는 관계**임이 문장에서 드러나야 합니다.
단순히 같은 문단에 이름이 있는 것만으로는 안 됩니다.

【출력】
· counterparty : 상대 기업명을 **본문에 나온 그대로**
· direction    : we_supply | they_supply
· subtype      : 무엇을 거래하는지 짧게 (예: "IC 테스트 소켓", "반도체 장비")
· evidence     : 근거가 된 **원문 문장을 그대로** 인용 (요약 금지)
아무것도 없으면 빈 배열을 돌려주세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "customers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "counterparty": {"type": "string"},
                    "direction": {"type": "string",
                                  "enum": ["we_supply", "they_supply"]},
                    "subtype": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["counterparty", "direction", "subtype", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["customers"],
    "additionalProperties": False,
}

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_customers(corp_name: str, section_text: str) -> list[dict]:
    """「매출 및 수주상황」 본문 → 거래처 목록. 실패하면 빈 리스트.

    돌려주는 dict는 `build_contract_relation_document`가 받는 형식과 같다
    (edge_type·counterparty·direction·subtype·evidence) — 기존 적재 경로를
    그대로 재사용하기 위함이다.
    """
    if not section_text.strip():
        return []
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL, temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",
                 "content": f"회사: {corp_name}\n\n[매출 및 수주상황]\n"
                            f"{section_text[:12000]}"},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "customers",
                                             "schema": _SCHEMA, "strict": True}},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"    [sales_customers] LLM 실패: {exc!r}")
        return []

    out: list[dict] = []
    for c in data.get("customers", []):
        name = (c.get("counterparty") or "").strip()
        if not name or name == corp_name:
            continue
        out.append({
            "edge_type": "SUPPLIES_TO",
            "counterparty": name,
            "direction": c.get("direction") or "we_supply",
            "subtype": (c.get("subtype") or "").strip() or "매출처",
            "evidence": (c.get("evidence") or "").strip(),
        })
    return out
