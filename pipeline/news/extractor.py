"""뉴스 개체·관계 추출 — 스키마 유도형.

프롬프트에 **12종 엣지 + 노드-엣지 매트릭스**를 주입해 LLM이 스키마를 벗어나지
못하게 한다. 추출 후에도 `validators/matrix.py`가 적재 전 재검증한다(2단 방어).

필터를 통과한 소수(전체의 ~5%)만 처리하므로 상위 모델을 쓴다 — 추출 품질이
그래프 품질을 좌우하기 때문.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Any, Optional

import openai

from app.core.config import OPENAI_API_KEY
from pipeline.ontology import (
    AMOUNT_RULES,
    EDGE_DEFINITIONS,
    HAS_EVENT_ROLES,
    SUBTYPE_RULES,
)

_EXTRACT_MODEL = "gpt-4o"     # 품질 우선 (필터 통과분만 처리)

# ★허용 매트릭스는 **코드에서 만들어 낸다**(2026-08-12).
#
#   전에는 이 표를 손으로 적어 뒀는데, 적재를 강제하는 건 `validators/matrix.py`의
#   `EDGE_MATRIX`다. 즉 **LLM에게 말하는 규칙과 실제로 강제하는 규칙이 두 벌**이었고,
#   실측해 보니 이미 한 곳이 어긋나 있었다:
#
#       OWNS_STAKE_IN   표 「→ Company」        코드 「→ Company/Organization」
#
#   코드가 맞다 — 재단·연구원 **출연**을 담으려고 넓힌 것이고 실제 엣지도 9건 있다.
#   표만 좁아서 LLM은 그 관계를 아예 안 만들고 있었다. 있는 사실을 못 담은 셈이다.
#
#   손으로 맞추는 대신 한쪽을 지웠다. 이제 매트릭스를 고치면 프롬프트가 따라온다.
def _matrix_text() -> str:
    from pipeline.validators.matrix import EDGE_MATRIX, EDGE_TO_L1_CATEGORY

    # 사건성 엣지 — 「언제 일어났나」를 물을 수 있는 것. 나머지는 이어지는 상태.
    events = {"ACQUIRES", "SUES", "HAS_EVENT", "IMPACTS"}
    rows = ["| 엣지 | 허용 방향 (source → target) | 성격 | 분류 |",
            "|---|---|---|---|"]
    for name, rule in EDGE_MATRIX.items():
        src = "/".join(sorted(rule.sources))
        tgt = "/".join(sorted(rule.targets))
        arrow = "⇄" if rule.symmetric else "→"
        kind = ("사건" if name in events else "상태") + ("(대칭)" if rule.symmetric else "")
        rows.append(f"| {name:<15} | {src} {arrow} {tgt} | {kind} | "
                    f"{EDGE_TO_L1_CATEGORY.get(name, '기타')} |")
    return "\n".join(rows)


_MATRIX_TEXT = _matrix_text()

_SYSTEM = f"""당신은 한국 경제 뉴스에서 기업 지식그래프를 추출하는 도구입니다.

【노드 5종】 Company(기업) · Person(인물) · Organization(정부·규제기관·협회 등 비기업)
· Product(제품·기술·부품·소재) · Event(사건·이슈)

【엣지 12종과 허용 매트릭스】 — 이 표를 **반드시** 지키세요.
{_MATRIX_TEXT}

【엣지 12종이 각각 무엇인가】 — 이 정의로 뽑고, **이 정의로 검증됩니다.**
{EDGE_DEFINITIONS}

【규칙】
1. source=주어, target=목적어로 방향을 정확히. "A가 B를 인수" → A -ACQUIRES-> B.
   (엣지마다 방향 규칙이 다릅니다 — 위 정의를 보세요. 특히 SUPPLIES_TO.)
2. 매트릭스에 없는 조합은 **추출하지 마세요**(예: Product -SUPPLIES_TO-> Person 금지).
3. 기사에 **명시된 관계만**. 추론·배경지식으로 만들어내지 마세요.
4. 12종에 안 맞으면 edge_type="OTHER"로 두고 raw_expression에 원문 표현을 남기세요.
5. IMPACTS는 sign(positive/negative/neutral)을 반드시 판단하세요.
6. COMPETES_WITH·PARTNERS_WITH는 대칭이므로 **한 방향만** 출력하세요.
7. 위 정의에서 ✗로 표시된 것은 **만들지 마세요.** 그대로 검증에서 걸립니다.
   실제로 그렇게 만들어졌다가 지워진 엣지가 있습니다:
     "SK하이닉스 영업이익률 71.5%로 엔비디아(67.7%)를 앞질렀다"
       ✗ SK하이닉스 -COMPETES_WITH-> 엔비디아  ← 실적 비교이지 경쟁이 아닙니다

8. **여럿을 한 덩이로 부른 것은 노드가 아닙니다.** 실명 하나가 아니면 만들지 마세요.
      ✗ 「인도 기업들」 · 「로보틱스 기업들」 · 「지주 등 관계 기업들」
      ✗ 「미국 소비자 14명과 중소 PC조립·유통업체 3곳」 · 「원고들」
      ✗ 「벨벳제1호 유한회사 등 2개사」
    이유: 서로 **다른 회사들**이 한 노드로 뭉칩니다. 다른 기사의 「인도 기업들」과
    같은 노드가 되는데 실제로는 아무 관계가 없습니다.
    → 기사에 **이름이 나온 회사만** 각각 만드세요. 이름이 없으면 관계를 만들지 마세요.

9. **문장·특허 명칭을 이름으로 쓰지 마세요.** 이름은 명사구입니다.
      ✗ 「Cloud 스토리지간 Data를 실시간으로 이전할 수 있는 기술 연구」
      ✗ 「락(위상) 고정 루프(PLL) 회로 및 이를 포함하는 디스플레이 구동기」  ← 특허 제목
      ✓ 「PLL 회로」 · 「디스플레이 구동기」

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

{SUBTYPE_RULES}

{HAS_EVENT_ROLES}

{AMOUNT_RULES}
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
                    # ★빈 문자열이 정상 값이다(2026-08-11). 타입 이름으로 때우는
                    #   것보다 비우는 편이 낫다 — 「모름」과 「평범함」이 갈린다.
                    "subtype": {"type": "string"},
                    # ★HAS_EVENT 전용. 다른 엣지에서는 null.
                    "role": {"type": ["string", "null"],
                             "enum": ["subject", "counterparty", "mentioned", None]},
                    # ★지분율 — OWNS_STAKE_IN·ACQUIRES 전용. 0~100 숫자.
                    #   전에는 「지분 61.6%」처럼 subtype 문자열로 들어와 조회도
                    #   비교도 안 됐다(2026-08-12). 숫자로 받는다.
                    "ratio": {"type": ["number", "null"]},
                    # ★거래·처분 규모 — **원 단위 숫자**.
                    #   근거에 금액이 나오는 비율(실측 2026-08-12):
                    #     ACQUIRES 24% · OWNS_STAKE_IN 21% · SUPPLIES_TO 11% · REGULATES 11%
                    #   전에는 담을 칸이 없어 「420억원 규모 공급계약」이 통째로 버려졌다.
                    "amount": {"type": ["number", "null"]},
                    "sign": {"type": ["string", "null"],
                             "enum": ["positive", "negative", "neutral", None]},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                    "raw_expression": {"type": ["string", "null"]},
                },
                "required": ["source", "source_type", "target", "target_type", "edge_type",
                             "subtype", "role", "ratio", "amount", "sign", "evidence",
                             "confidence", "raw_expression"],
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
    # HAS_EVENT에서 「당사자냐 이름만 나왔냐」. 다른 엣지에서는 None.
    role: Optional[str] = None
    # 지분율 — OWNS_STAKE_IN·ACQUIRES 에서만. 0~100.
    ratio: Optional[float] = None
    # 거래·처분 규모 (원). 계약금액·인수금액·과징금·손해배상액.
    amount: Optional[float] = None


_client: Optional[openai.OpenAI] = None


# 숫자 필드가 의미를 갖는 타입 (`ontology.AMOUNT_RULES`)
_RATIO_TYPES = frozenset({"OWNS_STAKE_IN", "ACQUIRES"})
_AMOUNT_TYPES = frozenset({"ACQUIRES", "SUPPLIES_TO", "REGULATES", "SUES",
                           "OWNS_STAKE_IN"})


def _num(value, allowed: frozenset, edge_type: str,
         lo: float, hi: float) -> Optional[float]:
    """숫자 필드를 받되 **타입과 범위를 벗어나면 버린다**.

    모델은 스키마에 칸이 있으면 아무 데나 채우려 든다 — 실제로 지분율 자리에
    금액(억원)이 들어온 적이 있다. 남의 칸에 든 값은 고칠 방법이 없으니 버린다.
    문자열("약 61%")로 오는 것도 같은 이유로 버린다.
    """
    if edge_type not in allowed or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if lo < num <= hi else None


def _ratio(value, edge_type: str, evidence, subtype) -> Optional[float]:
    """지분율. **근거에 적힌 퍼센트와 대조해 단위를 바로잡는다.**

    ★왜 범위 검사만으로는 안 되나(2026-08-15 실측). 전에는 `0 < x <= 100`만
      봤는데, 모델이 **비율꼴로 주면 그대로 통과**했다:

          근거「지분 67.96%를 취득」  →  ratio = 0.6796   ← 0.68%로 읽힌다
          근거「지분 100%」          →  ratio = 1.0      ← 1%로 읽힌다

      0.68%짜리 진짜 소액 지분과 구분이 안 되므로 **범위로는 못 잡는다.**
      DART 경로는 원본이 퍼센트꼴이라 멀쩡했고, 뉴스 경로만 섞여 있었다.

    ★근거에 「N%」가 있으면 그것이 정답이다 — 이 저장소가 쓰는 「원문을 믿는다」
      원칙 그대로다. 100배 차이면 조용히 고치고, 그 밖의 불일치는 버린다
      (남의 칸에 든 값은 고칠 방법이 없다).
    """
    num = _num(value, _RATIO_TYPES, edge_type, 0, 100)
    if num is None:
        return None
    text = f"{evidence or ''} {subtype or ''}"
    pcts = [float(m) for m in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)]
    if not pcts:
        return num
    if any(abs(p - num) < 0.01 for p in pcts):        # 이미 맞다
        return num
    if any(abs(p - num * 100) < 0.01 for p in pcts):  # 비율꼴로 왔다
        return round(num * 100, 4)
    return num                                        # 근거의 %가 다른 숫자다


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _client


def extract_relations(title: str, body: str, hint_companies: list[str],
                      known_products: str = "") -> list[ExtractedRelation]:
    """기사 → 관계 목록. 실패 시 빈 리스트.

    ★기사를 **청크로 나누지 않는다.** RAG의 청킹은 검색을 위한 것이고 여기는
      추출이라 목적이 다르다. 관계는 문장에 걸쳐 있어서 자르면 깨진다 —
      「계약을 맺었다 … 올해 초 해지됐다」를 나눠 넣으면 앞 조각은 「체결」,
      뒤 조각은 「해지」로 각각 나와 서로 모순되는 엣지가 두 개 생긴다.
      청킹은 **근거 저장**에서 한다(관계마다 그 관계를 말한 문장만 잘라 임베딩).

    ★`body[:6000]` 절단의 실측 근거 (2026-08-08, 본문 확보 5,620건):
          평균 1,571자 · 95분위 3,415자 · 6,000자 초과 **57건(1.0%)**
      게다가 잘리는 57건이 전부 「한경 로보뉴스 수주공시」류로 본문 대부분이
      종목 시세표·공시 원문 전문이고 실제 내용은 앞 몇 줄이다(20,880자가 다 같음).

    ★「6,000자가 추출에 너무 긴 것 아닌가」도 재 봤다. **아니다.**
      본문 길이별 추출 성적 (기사 2,199건 · 엣지 5,719건):

          길이       기사    엣지  기사당  근거의심
          ~500        75     122   1.6     6%
          500~1k     486   1,029   2.1     7%
          1k~2k    1,088   2,858   2.6     7%
          2k~3k      389   1,156   3.0     8%
          3k~4k       99     333   3.4     6%
          4k~6k       52     182   3.5     2%   ← 의심률 최저
          6k+         10      39   3.9    10%   ← 표본 10건, 판단 보류

      길수록 엣지가 더 나오고 **근거 의심률은 안 오른다.** 「lost in the middle」이
      우려되는 자리인데 이 규모(6,000자 ≈ 5,000토큰, 128k의 4%)에선 안 나타난다.

      ※ 헷갈리기 쉬운 점 — 6,000은 **상한**이고 실제 입력은 평균 1,571자다.
        일반적인 RAG 청크 범위(300~1,500자) 안이라 「너무 길다」가 성립하지 않는다.
      ※ 기사당 엣지가 1.6 → 3.9로 느는 것은 「긴 기사에 관계가 많다」와
        「긴 입력에서 과잉 생성한다」 둘 다로 읽힌다. 의심률이 안 오르므로 전자로
        보지만, 6k+ 표본이 쌓이면 다시 볼 것.
    """
    hint = f"\n\n[참고] 이 기사에 등장하는 관심 기업: {', '.join(hint_companies[:8])}" if hint_companies else ""
    try:
        resp = _get_client().chat.completions.create(
            model=_EXTRACT_MODEL,
            temperature=0,
            messages=[
                # ★이미 쓰는 제품명을 붙인다(2026-08-13). 제품은 이름이 곧
                #   식별자라 표기가 갈리면 노드가 갈린다. 사후 병합은 문자로
                #   「양팔형 휴머노이드」와 「AI 기반 휴머노이드」를 못 가르므로
                #   (`product_registry` 주석), **애초에 안 갈리게** 보여준다.
                {"role": "system", "content": _SYSTEM + known_products},
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
        # ★role은 HAS_EVENT에만 의미가 있다. 다른 엣지에 붙어 오면 버린다 —
        #   모델이 스키마의 칸을 보고 아무 데나 채우는 일이 있다.
        role = r.get("role") if r["edge_type"] == "HAS_EVENT" else None
        # ★지분율도 해당 타입에서만 받는다. 범위를 벗어나면 버린다 —
        #   모델이 금액(억원)을 여기 넣는 일이 있다.
        ratio = _ratio(r.get("ratio"), r["edge_type"], r.get("evidence"),
                       r.get("subtype"))
        # ★금액 — 자릿수 실수가 흔하다. 「420억원」을 420으로 주거나 반대로
        #   부풀린다. **100만원 미만·1000조 초과는 버린다.** 뉴스에 나오는 계약·
        #   과징금은 이 사이에 있고, 밖의 값은 단위를 잘못 읽은 것이다.
        amount = _num(r.get("amount"), _AMOUNT_TYPES, r["edge_type"], 1e6, 1e15)
        out.append(ExtractedRelation(
            source=src, source_type=r["source_type"],
            target=tgt, target_type=r["target_type"],
            edge_type=r["edge_type"], subtype=subtype,
            sign=r.get("sign"), evidence=(r.get("evidence") or "").strip(),
            confidence=float(r.get("confidence") or 0.7),
            raw_expression=r.get("raw_expression"),
            role=role, ratio=ratio, amount=amount,
        ))
    return out
