"""사업보고서 II-6(주요계약·연구개발) → PARTNERS_WITH / DEPENDS_ON LLM 추출.

경영상 주요계약을 12종 엣지에 매핑(스키마 유도형, 방법서 §12-2):
 - 협력·라이선스·크로스라이선스·공동개발·MOU·JV·기술이전 → PARTNERS_WITH (상대=회사)
 - 특정 기술/제품에 대한 의존·도입 → DEPENDS_ON (대상=기술/제품)
 - 임차·부동산·상표권·단순매매·양수도(별도 경로) → 제외
"""

from __future__ import annotations

import json
from typing import Any

import openai

from app.core.config import OPENAI_API_KEY

_MODEL = "gpt-4o-mini"

_SYSTEM = (
    "당신은 한국 기업 사업보고서의 '경영상 주요계약'을 지식그래프 관계로 변환하는 "
    "도구입니다. 각 계약을 아래 두 관계 중 하나로 분류하거나 제외하세요.\n"
    "• PARTNERS_WITH: 협력·특허 라이선스·크로스 라이선스·공동개발·MOU·JV·기술이전·"
    "기술제휴. counterparty=상대 '회사명'.\n"
    "• DEPENDS_ON: 특정 '기술/제품'에 대한 도입·의존(라이선스 대상이 회사가 아니라 "
    "기술·표준). counterparty=그 기술/제품 명칭.\n"
    "★반드시 제외(추출 금지): 부동산·사무실 임차, 상표권 사용료, 단순 매매, 자금 대여, "
    "그리고 소유권이 이전되는 거래 일체 — 영업양수도, 지분양수도, 주식매매(SPA), "
    "신주인수, 주주간계약(SHA), 합병. 이들은 ACQUIRES 경로에서 별도 처리하므로 "
    "여기서 절대 추출하지 마세요.\n"
    "규칙:\n"
    "1. counterparty는 실명(회사명 또는 기술명)만. '비공개'·설명형은 제외.\n"
    "2. subtype은 계약유형을 짧게(예: 특허라이선스, 크로스라이선스, 공동개발, MOU, 기술이전).\n"
    "3. DEPENDS_ON은 '기술·표준을 도입해 의존'하는 경우만. 사업(영업) 인수는 DEPENDS_ON이 아님.\n"
    "4. 같은 상대와 유형이 중복되면 하나만.\n"
    "5. 최대 10개. 없으면 빈 배열."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "edge_type": {"type": "string", "enum": ["PARTNERS_WITH", "DEPENDS_ON"]},
                    "counterparty": {"type": "string"},
                    "subtype": {"type": "string"},
                },
                "required": ["edge_type", "counterparty", "subtype"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relations"],
    "additionalProperties": False,
}

# LLM이 프롬프트 지시를 놓칠 때를 대비한 코드 레벨 가드(소유권 이전 거래 제외)
_EXCLUDE_SUBTYPE_MARKERS = (
    "양수", "양도", "인수", "매매", "신주인수", "주주간", "SHA", "SPA", "합병",
    "임차", "임대", "상표권", "대여",
)
_client: openai.OpenAI | None = None


# 실명이 아닌 일반명사·설명형 counterparty (LLM이 종종 뱉음) — 노드로 만들지 않는다
_GENERIC_COUNTERPARTY = {
    "계열회사", "계열사", "관계회사", "자회사", "고객사", "거래처", "협력사", "공급사",
    "해당사항없음", "기술사용허락", "기술도입", "라이선스", "미공개", "비공개",
}


def _is_generic(counterparty: str) -> bool:
    """일반명사·설명형이면 True (공백 제거 후 비교)."""
    compact = counterparty.replace(" ", "")
    return compact in _GENERIC_COUNTERPARTY or len(compact) < 2


def _is_excluded(rel: dict[str, Any]) -> bool:
    """소유권 이전·임차 등 PARTNERS/DEPENDS 대상이 아닌 계약 걸러내기."""
    blob = f"{rel.get('subtype', '')} {rel.get('counterparty', '')}"
    if any(m in blob for m in _EXCLUDE_SUBTYPE_MARKERS):
        return True
    return _is_generic(rel.get("counterparty") or "")


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_contract_relations(section_text: str, company_name: str) -> list[dict[str, Any]]:
    """II-6 텍스트 → [{edge_type, counterparty, subtype}]. 실패 시 빈 리스트."""
    if not section_text or len(section_text) < 40:
        return []
    if "해당사항" in section_text[:120] and "없" in section_text[:120]:
        return []  # "경영상 주요계약: 해당사항 없음"
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"기업: {company_name}\n\n{section_text[:5000]}"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "relations", "schema": _SCHEMA, "strict": True},
            },
        )
        relations = json.loads(resp.choices[0].message.content).get("relations", [])
        # 코드 가드 + (edge_type, counterparty) 중복 제거
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for r in relations:
            cp = (r.get("counterparty") or "").strip()
            if not cp or _is_excluded(r):
                continue
            key = (r["edge_type"], cp)
            if key in seen:
                continue
            seen.add(key)
            out.append({**r, "counterparty": cp})
        return out
    except Exception as exc:
        print(f"  [contract_extractor] LLM 실패({company_name}): {exc!r}")
        return []
