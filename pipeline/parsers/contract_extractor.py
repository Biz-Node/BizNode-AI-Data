"""사업보고서 II-6(주요계약·연구개발) → PARTNERS_WITH / SUPPLIES_TO / DEPENDS_ON 추출.

경영상 주요계약을 12종 엣지에 매핑(스키마 유도형, 방법서 §12-2):
 - 협력·라이선스·크로스라이선스·공동개발·MOU·JV·기술이전 → PARTNERS_WITH (상대=회사)
 - 제품·부품 **공급/납품/OEM/ODM** 계약 → SUPPLIES_TO (상대=회사, **방향 필수**)
 - 특정 기술/제품에 대한 의존·도입 → DEPENDS_ON (대상=기술/제품)
 - 임차·부동산·상표권·단순매매·양수도(별도 경로) → 제외

★SUPPLIES_TO에 방향을 요구하는 이유(실측 2026-07-28):
   초기에는 공급계약도 PARTNERS_WITH(대칭)로 뭉뚱그렸는데, 대칭 엣지는 방향을
   버리므로 "하나마이크론이 삼성전자에 공급"과 "삼성전자가 하나마이크론에 공급"이
   구분되지 않았다(9건 확인). 공급망은 방향이 곧 의미라 되살릴 수 없는 손실이다.
   방향은 계약 문구에만 있고 계약유형 문자열엔 없어 사후 보정이 불가능하므로,
   추출 시점에 supplier/customer를 명시하게 한다.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from app.core.config import OPENAI_API_KEY
from pipeline.normalizer.generic_names import is_generic_name

_MODEL = "gpt-4o-mini"

_SYSTEM = (
    "당신은 한국 기업 사업보고서의 '경영상 주요계약'을 지식그래프 관계로 변환하는 "
    "도구입니다. 각 계약을 아래 세 관계 중 하나로 분류하거나 제외하세요.\n"
    "• SUPPLIES_TO: **물리적인 제품·부품·소재가 한쪽에서 다른 쪽으로 넘어가는** "
    "공급/납품/수주 계약, OEM·ODM·위탁생산 계약. counterparty=상대 '회사명'.\n"
    "  ✗ SUPPLIES_TO가 아닌 것: 합작법인(JV) 설립, 판매수수료·대리점·총판 계약, "
    "기술 라이선스, 공동개발 — 물건이 오가지 않으므로 PARTNERS_WITH입니다.\n"
    "  ★direction을 반드시 판단하세요 — 공급망은 방향이 곧 의미입니다.\n"
    "    'we_supply'  = 이 기업이 상대에게 공급/납품/생산해 준다\n"
    "    'they_supply'= 상대가 이 기업에게 공급/납품한다\n"
    "  판단 힌트: 위탁생산·OEM·ODM에서 **만들어 주는 쪽이 공급자**입니다. "
    "이 기업이 반도체 후공정(OSAT)·수탁생산 업체라면 대개 'we_supply'입니다.\n"
    "• PARTNERS_WITH: 협력·특허 라이선스·크로스 라이선스·공동개발·MOU·JV·기술이전·"
    "기술제휴. 물건이 오가지 않고 **기술·권리를 나누는** 계약. counterparty=상대 '회사명'.\n"
    "• DEPENDS_ON: 특정 '기술/제품'에 대한 도입·의존(라이선스 대상이 회사가 아니라 "
    "기술·표준). counterparty=그 기술/제품 명칭.\n"
    "• EXCLUDE: **위 셋 중 어디에도 해당하지 않는 계약.** 억지로 분류하지 말고 "
    "여기에 넣으세요.\n"
    "  해당: 유상증자·신주발행·교환사채·주식매도·지분출자·자기주식 교환(→ 자본 거래), "
    "영업양수도·지분양수도·SPA·SHA·합병(→ ACQUIRES 경로에서 별도 처리), "
    "부동산·사무실 임차, 근저당권 설정, 신탁계약, 자금 대여, 상표권 사용료, "
    "인적용역 제공, 도급계약, 단순 매매.\n"
    "  ★이것들은 사업보고서 II-6에 자주 나오지만 **기업 간 협력도 공급도 아닙니다.** "
    "PARTNERS_WITH에 넣지 마세요 — 실제로 「유상증자」·「근저당권 설정」·「로비」가 "
    "협력 관계로 잘못 들어온 사례가 있었습니다.\n"
    "규칙:\n"
    "1. counterparty는 실명(회사명 또는 기술명)만. '비공개'·설명형은 제외.\n"
    "2. subtype은 계약유형을 짧게(예: 공급계약, OEM, 특허라이선스, 공동개발, MOU).\n"
    "3. DEPENDS_ON은 '기술·표준을 도입해 의존'하는 경우만. 사업(영업) 인수는 DEPENDS_ON이 아님.\n"
    "4. direction은 SUPPLIES_TO일 때만 의미가 있습니다. 나머지는 'na'로 두세요.\n"
    "5. 같은 상대와 유형이 중복되면 하나만.\n"
    "6. 최대 10개. 없으면 빈 배열.\n"
    "\n"
    "★아래 3개는 **근거 추적(왜 이 관계가 있는가)** 에 쓰이므로 반드시 채우세요.\n"
    "7. purpose: 보고서의 '목적 및 내용' 항목을 **원문 표현 그대로** 옮기세요.\n"
    "   (예: '상호 특허 라이선스ㆍ부제소를 통한 사업자유도 확보')\n"
    "   해당 항목이 없으면 계약을 설명하는 원문 구절을 그대로 쓰고, 정말 없으면 빈 문자열.\n"
    "8. signed_at: '체결시기'를 YYYY-MM-DD로. 연·월만 있으면 YYYY-MM, 없으면 null.\n"
    "9. evidence: 이 계약이 기재된 **보고서 원문 구절을 그대로 인용**하세요(요약·재작성 금지).\n"
    "   계약 상대방·계약유형·목적이 함께 드러나는 범위로 잡으세요."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "edge_type": {"type": "string",
                                  "enum": ["SUPPLIES_TO", "PARTNERS_WITH", "DEPENDS_ON",
                                           "EXCLUDE"]},
                    "counterparty": {"type": "string"},
                    "subtype": {"type": "string"},
                    "direction": {"type": "string",
                                  "enum": ["we_supply", "they_supply", "na"]},
                    "purpose": {"type": "string"},
                    "signed_at": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                },
                "required": ["edge_type", "counterparty", "subtype", "direction",
                             "purpose", "signed_at", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relations"],
    "additionalProperties": False,
}

# LLM이 프롬프트 지시를 놓칠 때를 대비한 코드 레벨 가드(소유권 이전·부동산 제외)
# '전세'·'부동산'은 실측에서 새로 발견 — "부동산전세계약"이 임차/임대 어느 쪽에도
# 걸리지 않아 공급계약으로 새어 들어왔다(LG이노텍→LG디스플레이).
_EXCLUDE_SUBTYPE_MARKERS = (
    "양수", "양도", "인수", "매매", "신주인수", "주주간", "SHA", "SPA", "합병",
    "임차", "임대", "전세", "부동산", "상표권", "대여",
)

# SUPPLIES_TO로 잘못 온 것을 PARTNERS_WITH로 되돌리는 표지.
# ★이 재배정은 안전하다 — 대상이 **대칭 엣지**라 방향 정보를 잃을 게 없다.
#   (반대 방향, PARTNERS_WITH → SUPPLIES_TO는 방향을 알 수 없어 하지 않는다.)
_NOT_SUPPLY_MARKERS = (
    "합작", "JV", "joint venture", "조인트벤처",     # 합작법인 = 협력
    "수수료", "판매지원", "판매 지원", "대리점", "총판",  # 물건이 아니라 용역·수수료
    "라이선스", "license", "공동개발", "공동연구",       # 기술 협력
)

# ★SUPPLIES_TO **양성 조건** — 계약유형에 '물건이 오간다'는 표지가 있어야 인정한다.
#
# 처음엔 제외 목록(블랙리스트)만 뒀는데 두더지잡기가 됐다. II-6 「주요계약」은
# 자유 서술이라 공사·매각·전환사채매입·부동산전세가 끝없이 새어 들어왔고, 표지를
# 하나 막으면 다른 표현이 나타났다. **무엇이 아닌지**를 세는 대신 **무엇이어야
# 하는지**를 요구하면 한 번에 닫힌다.
#
# 실측(시드 64개 II-6): 이 조건으로 정상 11건 전부 통과, 오분류 3건 전부 차단.
_SUPPLY_MARKERS = (
    "공급", "납품", "수주", "물품", "거래기본", "양산",
    "OEM", "ODM", "위탁생산", "외주가공", "임가공",
)

# ★PARTNERS_WITH **양성 조건** — '기술·권리·사업을 함께한다'는 표지를 요구한다.
#
# SUPPLIES_TO에서 통했던 방식을 그대로 적용한다. 프롬프트에 "제외하라"고 써도
# LLM은 통이 셋뿐이면 애매한 것을 어딘가에 넣는다. 실측(2026-07-28)에서
# PARTNERS_WITH 23종 중 17종이 협력이 아니었다 —
#   「유상증자」「신주발행」「주식매도」「지분출자」  → 자본 거래
#   「근저당권 설정」「신탁계약」「인적용역 제공」    → 협력 아님
#   「로비」                                      → 관계가 아님
# 무엇이 아닌지를 세는 대신 **무엇이어야 하는지**를 요구하면 한 번에 닫힌다.
_PARTNERSHIP_MARKERS = (
    "협력", "제휴", "협약", "파트너", "동맹",
    "라이선스", "license", "실시권",
    "공동", "합작", "jv", "조인트",
    "기술이전", "기술제공", "기술도입", "기술공여", "mou", "양해각서",
)
_client: openai.OpenAI | None = None


# 실명이 아닌 일반명사·설명형 counterparty (LLM이 종종 뱉음) — 노드로 만들지 않는다
_GENERIC_COUNTERPARTY = {
    "계열회사", "계열사", "관계회사", "자회사", "고객사", "거래처", "협력사", "공급사",
    "해당사항없음", "기술사용허락", "기술도입", "라이선스", "미공개", "비공개",
}


def _is_generic(counterparty: str) -> bool:
    """일반명사·설명형이면 True. 공용 가드 + 이 파서 전용 목록."""
    compact = counterparty.replace(" ", "")
    if compact in _GENERIC_COUNTERPARTY:
        return True
    return is_generic_name(counterparty)


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
            # LLM이 명시적으로 "해당 없음"으로 분류한 것
            if r["edge_type"] == "EXCLUDE":
                continue

            # 협력 관계는 '함께한다'는 표지가 있어야 인정한다
            if r["edge_type"] == "PARTNERS_WITH":
                blob = f"{r.get('subtype', '')} {r.get('purpose', '')}".lower()
                if not any(m in blob for m in _PARTNERSHIP_MARKERS):
                    print(f"  [contract_extractor] 협력 표지 없어 제외: "
                          f"{company_name}-{cp} [{r.get('subtype')}]")
                    continue

            if r["edge_type"] == "SUPPLIES_TO":
                subtype_l = (r.get("subtype") or "").lower()
                # ① 공급이 아닌 것(합작·수수료·라이선스)은 협력으로 되돌린다
                if any(m.lower() in f"{subtype_l} {cp.lower()}" for m in _NOT_SUPPLY_MARKERS):
                    r = {**r, "edge_type": "PARTNERS_WITH", "direction": "na"}
                # ② 물건이 오간다는 표지가 없으면 공급으로 인정하지 않는다
                #    (공사·매각·전환사채매입 등이 여기서 걸린다)
                elif not any(m.lower() in subtype_l for m in _SUPPLY_MARKERS):
                    print(f"  [contract_extractor] 공급 표지 없어 제외: "
                          f"{company_name}-{cp} [{r.get('subtype')}]")
                    continue
                # ③ 방향을 못 정한 공급관계는 버린다 — 틀린 방향의 공급 엣지는
                #    없는 엣지보다 나쁘다(공급망 추론이 통째로 뒤집힌다).
                elif r.get("direction") not in ("we_supply", "they_supply"):
                    print(f"  [contract_extractor] 방향 불명으로 제외: {company_name}-{cp}")
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
