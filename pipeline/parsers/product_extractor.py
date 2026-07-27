"""사업보고서 II-2(주요 제품 및 서비스) → 제품 목록 LLM 추출 (OpenAI).

표·서술이 지저분해("반도체 제조용 장비 外") 규칙 파싱이 취약 → LLM으로 정제 추출.
자사 제품이라 익명화 없음(방법서 §7). 구조화 출력(JSON)으로 안정성 확보.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from app.core.config import OPENAI_API_KEY

_MODEL = "gpt-4o-mini"  # 추출 태스크에 충분·저렴

_SYSTEM = (
    "당신은 한국 기업 사업보고서에서 제품·기술을 추출하는 도구입니다. "
    "입력은 '주요 제품 및 서비스' 섹션 원문입니다. 이 기업이 직접 생산·개발하는 "
    "구체적인 제품/서비스/기술의 이름만 추출하세요.\n"
    "규칙:\n"
    "1. 일반적 설명·수치·매출액이 아닌 '제품명/기술명'만.\n"
    "2. '外', '등', '기타' 같은 접미어는 제거하고 핵심 명칭만 남기세요.\n"
    "3. 너무 포괄적인 것(예: '제품', '상품')은 제외.\n"
    "4. category는 제품/기술/부품/소재/서비스 중 하나.\n"
    "5. 최대 8개. 없으면 빈 배열."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["제품", "기술", "부품", "소재", "서비스"]},
                },
                "required": ["name", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["products"],
    "additionalProperties": False,
}

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_products(section_text: str, company_name: str) -> list[dict[str, Any]]:
    """II-2 섹션 텍스트 → [{name, category}]. 실패 시 빈 리스트."""
    if not section_text or len(section_text) < 30:
        return []
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"기업: {company_name}\n\n{section_text[:4000]}"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "products", "schema": _SCHEMA, "strict": True},
            },
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("products", [])
    except Exception as exc:
        print(f"  [product_extractor] LLM 실패({company_name}): {exc!r}")
        return []
