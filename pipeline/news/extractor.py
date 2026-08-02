"""뉴스 개체·관계 추출 (방법서 §12-2) — 스키마 유도형.

프롬프트에 **12종 엣지 + 노드-엣지 매트릭스**를 주입해 LLM이 스키마를 벗어나지
못하게 한다. 추출 후에도 `validators/matrix.py`가 적재 전 재검증한다(2단 방어).

필터를 통과한 소수(전체의 ~5%)만 처리하므로 상위 모델을 쓴다 — 추출 품질이
그래프 품질을 좌우하기 때문.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import openai

from app.core.config import OPENAI_API_KEY

_EXTRACT_MODEL = "gpt-4o"     # 품질 우선 (필터 통과분만 처리)

# 방법서 2-2 허용 매트릭스를 프롬프트로 (validators/matrix.py와 동일 규칙)
_MATRIX_TEXT = """
| 엣지 | 허용 방향 (source → target) | 성격 |
|---|---|---|
| OWNS_STAKE_IN   | Company/Person/Organization → Company | 상태 |
| IS_EXECUTIVE_OF | Person → Company/Organization | 상태 |
| SUPPLIES_TO     | Company → Company | 상태 |
| PARTNERS_WITH   | Company/Organization ⇄ Company/Organization | 상태(대칭) |
| ACQUIRES        | Company → Company | 사건 |
| SUES            | Company/Person/Organization → 동일 | 사건 |
| COMPETES_WITH   | Company/Product ⇄ Company/Product | 상태(대칭) |
| REGULATES       | Organization → Company/Product | 상태 |
| DEVELOPS        | Company → Product | 상태 |
| DEPENDS_ON      | Company/Product → Product | 상태 |
| HAS_EVENT       | Company/Product → Event | 사건 |
| IMPACTS         | Event → Company/Product | 사건 |
""".strip()

_SYSTEM = f"""당신은 한국 경제 뉴스에서 기업 지식그래프를 추출하는 도구입니다.

【노드 5종】 Company(기업) · Person(인물) · Organization(정부·규제기관·협회 등 비기업)
· Product(제품·기술·부품·소재) · Event(사건·이슈)

【엣지 12종과 허용 매트릭스】 — 이 표를 **반드시** 지키세요.
{_MATRIX_TEXT}

【규칙】
1. source=주어, target=목적어로 방향을 정확히. "A가 B를 인수" → A -ACQUIRES-> B.

★단, `SUPPLIES_TO`는 **문장 주어가 아니라 실제 공급자**를 source로 하세요.
   한국어 「A는 B와 공급계약을 체결했다」는 **누가 공급하는지 말해주지 않습니다.**
   실제로 이렇게 틀린 사례가 있었습니다:
     원문: "SK하이닉스는 테스와 반도체 제조 장비 공급 계약을 체결했다"
     ✗ SK하이닉스 → 테스   (문장 주어를 공급자로 오인)
     ✓ 테스 → SK하이닉스   (테스가 **장비업체**다)

   판단 기준 — **누가 만들어 주는가**:
     · 장비·부품·소재·후공정(OSAT) 업체  →  칩메이커·완성품업체에 **공급**
     · 칩메이커(메모리·파운드리)          →  세트업체·빅테크에 **공급**
     · 「발주」「수주」가 나오면: 발주한 쪽이 **수요자**, 수주한 쪽이 **공급자**
     · 「납품」이 나오면: 납품하는 쪽이 공급자
   기사에서 역할을 알 수 없으면 SUPPLIES_TO를 만들지 마세요.
2. 매트릭스에 없는 조합은 **추출하지 마세요**(예: Product -SUPPLIES_TO-> Person 금지).
3. 기사에 **명시된 관계만**. 추론·배경지식으로 만들어내지 마세요.
4. 12종에 안 맞으면 edge_type="OTHER"로 두고 raw_expression에 원문 표현을 남기세요.
5. IMPACTS는 sign(positive/negative/neutral)을 반드시 판단하세요.
6. COMPETES_WITH·PARTNERS_WITH는 대칭이므로 한 방향만 출력.
   ★COMPETES_WITH는 **같은 제품·시장에서 실제로 다투는 경우만** 씁니다.
     한 기사에 같이 등장했다는 이유로 묶지 마세요. 업종이 다르면(예: 반도체사 ↔ 은행)
     경쟁이 아닙니다. 무엇을 두고 경쟁하는지 evidence에 드러나야 합니다.
7. **소송과 규제를 구분하세요.**
   · SUES  = 한쪽이 다른 쪽을 **제소**한 것 (원고 → 피고)
   · REGULATES = 규제·판정 기관이 조사·제재·판정하는 것
     법원·위원회(ITC 등)·공정위·금감원은 **소송 당사자가 아니라 판정 주체**입니다.
8. **DEVELOPS를 함부로 만들지 마세요 — 오추출 1위입니다.**
   실측(2026-08-01): 뉴스 DEVELOPS 885건 중 **419건(47%)**이 근거 검증에서 걸렸습니다.
   다른 엣지는 SUPPLIES_TO 9%·IMPACTS 4%인데 DEVELOPS만 47%입니다.

   `DEVELOPS` = 그 기업이 그 제품·기술을 **직접 만들거나 개발한다**.
     ✓ "SK하이닉스가 HBM4를 개발했다" · "한미반도체가 TC 본더를 생산한다"
     ✓ "심텍은 HBM용 패키지 기판을 양산한다"

   ★★**같은 문장으로 SUPPLIES_TO를 만들었다면, 그 문장으로 DEVELOPS를 만들지 마세요.**
     공급 계약 기사가 오추출의 대부분입니다. 실제로 걸린 것들:

       "한화세미텍이 SK하이닉스에 TC 본더를 공급하기로 계약했다"
         ✓ 한화세미텍 -SUPPLIES_TO-> SK하이닉스
         ✗ 한화세미텍 -DEVELOPS-> TC 본더      ← 파는 것과 만드는 것은 다릅니다
         ✗ SK하이닉스 -DEVELOPS-> TC 본더      ← **사는 쪽**입니다. 더 나쁩니다
       "한미반도체가 그리핀 공급 계약을 수주했다"
         ✗ 한미반도체 -DEVELOPS-> 그리핀       ← 수주 얘기지 개발 얘기가 아닙니다
       "삼성전자의 AI 메모리 공급계약"
         ✗ 삼성전자 -DEVELOPS-> AI 메모리

     제조사가 자기 제품을 판다는 건 **사실일 수 있지만 그 문장에 없습니다.**
     배경지식으로 메우지 마세요. 다른 문장이 "만든다"고 말하면 그때 만드세요.

   ✗ 그 밖의 흔한 오추출:
       "마이크론이 한국 경쟁사를 추격하겠다"     → 마이크론 -DEVELOPS-> 고사양 DRAM  ✗ 추측
       "Z 폴드5·Z 플립5는 러시아에서 안 팔았다"  → 삼성전자 -DEVELOPS-> Z 플립5      ✗ 판매 얘기
       "SK하이닉스가 TC 본더를 발주했다"        → 사는 쪽입니다
       "HBM4 시장에서 삼성전자가 경쟁한다"       → 경쟁입니다

   판단 기준: **「만든다·개발한다·양산한다·생산한다」가 그 문장에 있는가.**
   없으면 만들지 마세요.

9. **PARTNERS_WITH도 함부로 만들지 마세요** (실측 오추출 26건).
   두 기업이 **서로** 합작·공동개발·기술제휴하는 것입니다.
     ✗ 「A와 B의 계약」이 각자 제3자와 맺은 것이면 협력이 아닙니다
     ✗ 같은 기사에 나란히 언급된 것만으로는 협력이 아닙니다
     ✗ 거래 관계(납품·수주)는 SUPPLIES_TO입니다
     ✗ 기업이 **정부·규제기관과 협의체에 참여**하는 것은 협력이 아닙니다
         "정부가 삼성전자·SK하이닉스 등이 참여하는 실무협의체를 꾸린다"
           → 삼성전자 -PARTNERS_WITH-> 기후에너지환경부  ✗ (실측 오추출)

10. evidence는 그 관계가 드러난 **기사 원문 문장**을 그대로 인용하세요(요약 금지).
11. confidence는 문장이 관계를 얼마나 명확히 말하는지(0.5~1.0).
12. 기업명은 기사에 나온 그대로. 약칭이면 약칭 그대로.

【Event — 리스크 추론의 핵심】 ★중요
사건은 **기업 리스크를 묻는 질의**에 답하기 위한 것입니다
("이 기업에 어떤 악재가 있었나", "이 공장 화재가 어디까지 번지나").
기사에 사건이 있으면 **적극적으로 추출**하세요.

 · 추출 대상: 화재·폭발·가동중단, 파업·노동쟁의, 과징금·제재·압수수색·기소,
   리콜·품질결함, 경영권 분쟁·주주 갈등, 횡령·배임, 신용등급 변동·적자전환,
   공급망 차질·수출규제, 대형 수주·투자 결정, 신기술 양산 개시.

 · 두 엣지의 역할을 구분하세요:
     `Company -HAS_EVENT-> Event` = 그 기업이 사건의 **당사자**
     `Event -IMPACTS-> Company`   = 그 사건이 기업에 미치는 **영향**(sign 필수)
   당사자가 영향도 받으면 **둘 다** 출력하세요(당사자 여부와 영향은 다른 정보입니다).
   ★기사에 **다른 기업으로 번지는 영향**이 언급되면 반드시 IMPACTS로 넣으세요
   (예: 엔비디아 생산 지연 → 삼성전자·SK하이닉스 IMPACTS negative).
   시드 기업이 아니어도 됩니다 — 기사에 나온 기업이면 모두 대상입니다.

 · ★**이미 일어난 일**만 사건입니다. 이게 가장 중요합니다.
   전망·계획·필요성·분석은 사건이 아닙니다. 실제로 이런 것들이 잘못 들어왔습니다:
     ✗ 「설비 증설 필요성」      — 필요하다는 의견이지 일어난 일이 아님
     ✗ 「메모리 반도체 생산 확대」 — 추세이지 특정 시점의 사건이 아님
     ✗ 「D램 추가수익 확보 포석」  — 해석·전망
     ✗ 「AI 메모리 전쟁 본격화…SK하이닉스 독주, 삼성전자 추격」 — 기사 제목
   판별법: **언제 일어났는지 날짜를 댈 수 있는가?** 못 대면 사건이 아닙니다.
     ✓ 「청주 공장 화재」(6/12) 「담합 혐의 피소」 「HBM4 양산 일정 연기」
       「단체교섭 요구」 「용인 클러스터 착공」

 · 이름 규칙:
   a. 사건을 지칭하는 **짧은 명사구**로 짓되, **반드시 기사 본문에서 뽑으세요.**
      형태 예시(그대로 쓰지 말고 형태만 참고): 「◯◯ 공장 화재」 「◯◯ 양산 개시」
      — ◯◯ 자리에는 **기사에 나온** 지역·제품명을 넣으세요.
      ★아래를 지키세요:
        · 이름에 쓰는 고유명사(회사·지역·제품)는 **기사에 실제로 나온 것만**.
        · 기사에 없는 지역·설비 이름을 붙이지 마세요.
        · 기사 제목을 그대로 넣지 마세요. "Event" 같은 타입명도 금지입니다.
      (실제로 프롬프트의 예시 이름을 그대로 복사해 기사와 무관한 사건이 만들어진
       적이 있습니다. 예시는 형태 참고용이지 값이 아닙니다.)
   b. **시황은 사건이 아닙니다** — 상한가·급등·주가 상승·목표주가 상향은 추출 금지.
   c. 「출시」·「계약 체결」처럼 목적어 없는 맨동사도 금지(무엇인지 특정 불가).
   d. 사건을 이름 붙일 수 없으면 Event 관계를 만들지 마세요.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "source_type": {"type": "string",
                                    "enum": ["Company", "Person", "Organization", "Product", "Event"]},
                    "target": {"type": "string"},
                    "target_type": {"type": "string",
                                    "enum": ["Company", "Person", "Organization", "Product", "Event"]},
                    "edge_type": {"type": "string",
                                  "enum": ["OWNS_STAKE_IN", "IS_EXECUTIVE_OF", "SUPPLIES_TO",
                                           "PARTNERS_WITH", "ACQUIRES", "SUES", "COMPETES_WITH",
                                           "REGULATES", "DEVELOPS", "DEPENDS_ON", "HAS_EVENT",
                                           "IMPACTS", "OTHER"]},
                    "subtype": {"type": "string"},
                    "sign": {"type": ["string", "null"],
                             "enum": ["positive", "negative", "neutral", None]},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                    "raw_expression": {"type": ["string", "null"]},
                },
                "required": ["source", "source_type", "target", "target_type", "edge_type",
                             "subtype", "sign", "evidence", "confidence", "raw_expression"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relations"],
    "additionalProperties": False,
}


@dataclass
class ExtractedRelation:
    source: str
    source_type: str
    target: str
    target_type: str
    edge_type: str
    subtype: str
    sign: Optional[str]
    evidence: str
    confidence: float
    raw_expression: Optional[str]


_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_relations(title: str, body: str, hint_companies: list[str]) -> list[ExtractedRelation]:
    """기사 → 관계 목록. 실패 시 빈 리스트."""
    hint = f"\n\n[참고] 이 기사에 등장하는 관심 기업: {', '.join(hint_companies[:8])}" if hint_companies else ""
    try:
        resp = _get_client().chat.completions.create(
            model=_EXTRACT_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"제목: {title}\n\n본문:\n{body[:6000]}{hint}"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "relations", "schema": _SCHEMA, "strict": True},
            },
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"    [extractor] LLM 실패: {exc!r}")
        return []

    out: list[ExtractedRelation] = []
    for r in data.get("relations", []):
        src, tgt = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if not src or not tgt or src == tgt:
            continue
        # LLM이 빈 subtype 대신 "." 등 무의미 값을 넣는 경우 정리
        subtype = (r.get("subtype") or "").strip().strip(".·-")
        out.append(ExtractedRelation(
            source=src, source_type=r["source_type"],
            target=tgt, target_type=r["target_type"],
            edge_type=r["edge_type"], subtype=subtype,
            sign=r.get("sign"), evidence=(r.get("evidence") or "").strip(),
            confidence=float(r.get("confidence") or 0.7),
            raw_expression=r.get("raw_expression"),
        ))
    return out
