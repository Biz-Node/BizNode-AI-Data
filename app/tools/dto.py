"""도구 반환 계약 — **DB row 를 그대로 돌려주지 않는다.**

도구는 LLM 이 읽는다. LLM 은 열 이름과 숫자만 보고 뜻을 지어낸다. 그래서
**오해할 수 있는 값에는 표기를 붙여서** 돌려준다. 아래 표기들은 전부
실측에서 나온 것이다 — 하나도 장식이 아니다.

    `61.6`                → 61.6% 인가 0.616 인가        (0~1 구간에 진짜 값이 산다)
    `PARTNERS_WITH` 화살표 → 방향에 뜻이 있는가            (없다. symmetric 이다)
    `DEVELOPS` + 뉴스      → 단정해도 되는가              (안 된다. 오추출률 46%)
    `per: null`           → 정보가 없는 건가             (아니다. 적자거나 재무 미수집)
    시총·PER              → 근거 id 를 붙일 수 있는가      (없다. 저장값이 아니라 계산값)

★**계약이 먼저고 구현이 뒤다.** 값을 채우는 규칙을 필드 설명과 아래 상수에
  못 박아 두고, 구현(`app/tools/{graph,company,search}_tools.py`)이 그것을
  따른다 — 구현이 말을 바꿀 수 없게.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════
#  표기 문구 — **구현이 고쳐 쓸 수 없게 상수로 못 박는다**
# ══════════════════════════════════════════════════════════════════

# 「어디서 온 사실인가」. 같은 관계라도 공시면 확정, 보도면 미확정이다.
SOURCE_NOTE = {
    "dart": "DART 정기공시 — 확정 사실",
    "dart_filing": "DART 개별공시 — 확정 사실",
    "news": "언론 보도 — 확정되지 않은 주장",
}

# ★방향이 없는 관계. 화살표를 「A 가 B 에게」로 읽으면 없는 뜻을 만든다.
SYMMETRIC_EDGE_TYPES = frozenset({"PARTNERS_WITH", "COMPETES_WITH"})
DIRECTION_NOTE = {
    "symmetric": "방향이 없는 관계 — 화살표의 앞뒤에 뜻이 없다. "
                 "「A 가 B 에게」로 읽지 말 것",
    "directed": "방향이 있는 관계 — 앞이 주체, 뒤가 대상",
}

# ★실측 근거가 있는 경고. 뉴스에서 뽑은 `DEVELOPS` 는 절반 가까이 틀린다.
#   실측(2026-08-27): 근거검증을 거친 뉴스 DEVELOPS 672건 중 310건 탈락 = 46.1%.
#   (`grounding_verdict` 없으면 `grounding_stage1` 로 본 값. 이전 측정 47%)
CAUTION_NEWS_DEVELOPS = "뉴스 추출 DEVELOPS 는 오추출률 47% — 단정 불가"

# ★`role` 은 「이 기업에 난 일인가」를 가른다. 실측(2026-08-27) HAS_EVENT 1,087건:
#   subject 953 · counterparty 69 · mentioned 65.
ROLE_NOTE = {
    "subject": "이 기업에 난 일",
    "counterparty": "상대방으로 엮인 일 — 이 기업에 난 일이 아니다",
    "mentioned": "기사에 함께 언급됐을 뿐 — 이 기업에 난 일이 아니다",
}

# ★`per` 가 `null` 인 이유가 둘이다. 뭉뚱그리면 LLM 이 「정보 없음」으로 읽는다.
#   실측(2026-08-27) `market_metrics` 에서 `per IS NULL` 인 16,840행:
#       재무는 있는데 순이익 ≤ 0   16,340행 (132곳)  → 적자
#       재무 자체가 없음(LEFT JOIN)   500행 (4곳)   → 미수집
PER_NOTE_LOSS = "적자로 산출 불가"
PER_NOTE_NO_FINANCIALS = "재무 미수집으로 산출 불가"

# ★**어떻게 손에 들어온 사실인가.** 직접 조회한 것과 Agent 가 탐색으로 발견한
#   것을 구별하지 못하면, 「우리가 찾아간 것」과 「우리가 물어본 것」이 같은
#   무게로 읽힌다. 지금은 `direct` 하나뿐이고 `explored` 는 2차에서 쓴다 —
#   **값을 미리 만들어 두는 것이 아니라 자리를 미리 비워 두는 것**이다.
PROVENANCE_DIRECT = "direct"
PROVENANCE_EXPLORED = "explored"


# ★파급이 **보도된 것인가 계산된 것인가.** 섞어 말하면 추론을 사실로 파는 것이
#   된다(설계서 §12 4등급). 실측(모트라스 파업): 124곳 = 보도 10 + 계산 114.
STATED_NOTE = {
    True: "기사가 직접 말한 파급 — 보도된 사실",
    False: "공급망으로 계산한 파급 — 보도된 사실이 아니다",
}

# ★신선도 가중치. `pipeline/freshness.py` 의 `Freshness.confidence_factor` 와
#   **같은 값이어야 한다.** 여기 적어 두는 것은 계약을 읽는 사람을 위해서지,
#   계산을 두 벌 두려는 것이 아니다 — 계산은 `freshness.effective_confidence()`
#   하나만 쓴다.
FRESHNESS_WEIGHT = {"current": 1.0, "stale": 0.6, "expired": 0.3, "unknown": 0.7}


# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════


class RelationDTO(BaseModel):
    """관계 한 건. **엣지 속성을 그대로 펼치지 않는다.**"""

    # ── 식별 — ★표기가 아니라 **되짚을 좌표**다 ────────────────
    # 이것들이 없으면 근거를 관계에 되짚을 수 없어 `Source.edge_id` 가 비고,
    # 워크스페이스 소속 표기(설계서 §12)도 붙일 수 없다. 둘 다 이미 나가 있는
    # 계약이라 잃으면 응답이 후퇴한다.
    edge_id: str = Field(
        description="엣지 자체의 유일한 id(Neo4j elementId). ★`evidence_id` 를 "
                    "쓰면 안 된다 — 한 근거가 여러 관계를 뒷받침해서 유일하지 "
                    "않다(엣지 11,060건에 근거 9,228개)",
        examples=["4:abc:123"])
    source: str = Field(description="출발 기업 이름", examples=["심텍"])
    target: str = Field(description="도착 기업 이름", examples=["SK하이닉스"])
    source_key: str = Field(
        description="출발 기업의 키. **표시용 이름이 아니라 식별자다**(설계서 §16-1)",
        examples=["00152127"])
    target_key: str = Field(description="도착 기업의 키", examples=["00164779"])
    edge_type: str = Field(examples=["SUPPLIES_TO"])
    subtype: Optional[str] = Field(None, description="근거에서 읽어낸 「무엇을」",
                                   examples=["반도체 PCB"])
    evidence_id: Optional[str] = Field(
        None,
        description="근거 원문의 id. **엣지의 스칼라 `evidence_id` 다** — "
                    "`evidence_ids` 배열은 여러 근거의 합집합이라 이 관계 하나의 "
                    "출처가 아니다",
        examples=["ev_17acfbf5a4041e59"])

    # ── 출처 ──────────────────────────────────────────────────
    source_type: Literal["dart", "dart_filing", "news"] = Field(
        "news", description="Neo4j 엣지의 값 그대로. 실측 분포 "
                            "news 8,384 · dart 2,563 · dart_filing 113")
    source_note: str = Field(
        description="★`source_type` 을 **문자열로도** 남긴다. `\"dart\"` 라는 "
                    "토큰만으로는 「확정 사실」이라는 뜻이 전달되지 않는다. "
                    "`SOURCE_NOTE[source_type]` 을 그대로 쓴다",
        examples=["언론 보도 — 확정되지 않은 주장"])

    # ── 방향 ──────────────────────────────────────────────────
    direction: Literal["directed", "symmetric"] = Field(
        "directed",
        description="`edge_type` 이 `SYMMETRIC_EDGE_TYPES` 에 있으면 `symmetric`")
    direction_note: str = Field(
        description="★`symmetric` 표기는 **필수**다. `PARTNERS_WITH`·"
                    "`COMPETES_WITH` 는 화살표에 뜻이 없는데, 방향을 그대로 주면 "
                    "LLM 이 「A 가 B 에게 제휴를 걸었다」를 지어낸다. "
                    "`DIRECTION_NOTE[direction]` 을 그대로 쓴다",
        examples=["방향이 없는 관계 — 화살표의 앞뒤에 뜻이 없다. "
                  "「A 가 B 에게」로 읽지 말 것"])

    # ── 신뢰도 ────────────────────────────────────────────────
    freshness: Literal["current", "stale", "expired", "unknown"] = Field(
        "unknown", description="`pipeline.freshness.assess()` 의 판정")
    effective_confidence: float = Field(
        ge=0, le=1,
        description="★**내부 `confidence` 를 그대로 주지 않는다.** "
                    "`confidence × 신선도 가중치` 다 — "
                    "`pipeline.freshness.effective_confidence()` 로 계산한다. "
                    "가중치는 `FRESHNESS_WEIGHT` (expired 0.3 · current 1.0 · "
                    "stale 0.6 · unknown 0.7)",
        examples=[0.54])

    caution: Optional[str] = Field(
        None,
        description="★단정을 막는 경고. `edge_type == \"DEVELOPS\"` 이고 "
                    "`source_type == \"news\"` 면 `CAUTION_NEWS_DEVELOPS` 를 담는다. "
                    "해당 없으면 `None`",
        examples=[CAUTION_NEWS_DEVELOPS])

    # ── 지분율 ────────────────────────────────────────────────
    # ★값만으로는 단위를 알 수 없다. 실측(2026-08-27) `ratio` 보유 엣지 1,666건 중
    #   **0~1 구간이 126건**인데 전부 진짜 소액지분이다(0.72% 같은 값). 백분율을
    #   소수로 오인해 100 을 곱하면 0.72% 가 72% 가 된다.
    ratio: Optional[float] = Field(
        None, ge=0, le=100, description="**퍼센트(0~100).** 지분율", examples=[0.72])
    ratio_unit: Optional[Literal["percent"]] = Field(
        None, description="`ratio` 가 있을 때만. 언제나 `percent` 다")
    ratio_text: Optional[str] = Field(
        None,
        description="★단위를 **문자열에 박아** 준다. `61.6` 은 61.6% 지 0.616 이 "
                    "아닌데, 0~1 구간에 진짜 소액지분 126건이 실재해서 값만으로는 "
                    "구별이 안 된다",
        examples=["0.72%"])

    # ── 출처 경로 ──────────────────────────────────────────────
    provenance: Literal["direct", "explored"] = Field(
        "direct",
        description="★**어떻게 손에 들어왔나.** `direct` 는 서버가 정한 재료 "
                    "범위에서 직접 조회한 것, `explored` 는 2차 Agent 가 탐색으로 "
                    "발견한 것이다. 지금은 `direct` 뿐이다 — 탐색 경로가 아직 "
                    "없으므로 `explored` 를 만드는 코드도 없다",
        examples=["direct"])


# ══════════════════════════════════════════════════════════════════
#  사건
# ══════════════════════════════════════════════════════════════════


class EventPhaseDTO(BaseModel):
    """`timeline` 한 국면. ★**배열의 원소로 남긴다** — 문자열로 펴지 않는다."""

    period: str = Field(description="연월", examples=["2026-06"])
    name: str = Field(examples=["삼성전자 압수수색"])


class EventCompanyDTO(BaseModel):
    """이 사건이 **누구에게 난 일인가.** 앵커 없는 질문에서만 실린다."""

    key: str = Field(examples=["00126380"])
    name: str = Field(examples=["삼성전자"])


class EventDTO(BaseModel):
    """사건 한 건."""

    event_id: str = Field(examples=["evt_news_c915fa8bf141"])
    name: str = Field(examples=["삼성전자 본사 압수수색"])
    event_type: str = Field(description="12종", examples=["규제수사"])
    is_risk: bool = Field(False)

    # ★**Event 노드에 날짜가 없다.** 실측(2026-08-27): Event 1,058건 전부
    #   `occurred_at` 이 비어 있다. 날짜는 엣지에 있고 거긴 100% 차 있다 —
    #   `HAS_EVENT` 1,087건 · `IMPACTS` 1,083건 전부. 노드에서 읽으면 언제나
    #   `None` 이라 「날짜를 모르는 사건」이 된다.
    occurred_at: Optional[str] = Field(
        None,
        description="★**`HAS_EVENT` 또는 `IMPACTS` 엣지에서 가져온다.** "
                    "Event 노드에는 날짜가 없다(실측 1,058건 전부 비어 있음). "
                    "같은 사건이라도 기업마다 엮인 시점이 다를 수 있어 엣지에 있다",
        examples=["2026-06-11"])

    # ★**이 기업이 이 사건에 엮인 근거만.** 같은 사건에 붙은 다른 기업의 근거는
    #   들어 있지 않다 — 노드의 `evidence_ids` 는 모든 기업의 합집합이라, 그걸
    #   쓰면 「SK하이닉스 노조」 질의에 현대오토에버 기사가 섞인다(2026-08-23).
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="이 기업의 `HAS_EVENT` 엣지에서 나온 근거 id",
        examples=[["ev_1fdde758922d6de6"]])

    role: Literal["subject", "counterparty", "mentioned"] = Field(
        "subject", description="`HAS_EVENT` 엣지의 값")
    role_note: str = Field(
        description="★「이 기업에 난 일」은 **`subject` 만**이다. `role` 토큰만 "
                    "주면 LLM 이 `mentioned` 를 연루로 읽는다. "
                    "`ROLE_NOTE[role]` 을 그대로 쓴다",
        examples=["이 기업에 난 일"])

    sign: Optional[Literal["negative", "positive", "neutral"]] = Field(
        None,
        description="`IMPACTS` 엣지의 값. 실측(2026-08-27) 1,083건: "
                    "negative 585 · positive 452 · neutral 46. "
                    "`IMPACTS` 가 없는 사건이면 `None`")

    # ★**앵커 없는 질문에서만 찬다**(2026-09-02). 대상을 지정하지 않은 질문은
    #   사건마다 기업이 다르다 — 안 실으면 LLM 이 「누구에게 난 일인지 모르는
    #   사건」을 인용하게 되고, 그건 곧 엉뚱한 기업에 사건을 붙이는 일이 된다.
    #   앵커가 있는 질문에서는 서버가 정한 재료 기업이 하나뿐이라 `None` 이다.
    #   ★`app/api/schemas.Event.company` 와 **같은 값**이다 — 두 입구가 같은
    #     사건에 다른 기업을 붙이면 그 차이는 대조에서 안 보인다.
    company: Optional[EventCompanyDTO] = Field(
        None,
        description="★**앵커 없는 질문에서만 찬다.** 이 사건이 누구에게 난 일인가. "
                    "대상을 지정한 질문에서는 서버가 정한 기업이 하나뿐이라 `None`")

    # ── 국면 ──────────────────────────────────────────────────
    # ★**배열을 문자열로 펴지 마라.** 편 적이 있어서 `size()` 가 국면 수가 아니라
    #   글자 수를 셌다(`batch/repair/node_identity.py` 의 `timeline` 보호 목록이
    #   그 사고의 흔적이다 — 28건이 망가졌다).
    timeline: list[EventPhaseDTO] = Field(
        default_factory=list,
        description="같은 사건의 여러 국면. **흩어진 기사가 아니라 하나의 줄거리**. "
                    "실측(2026-08-27) 193건이 갖고 있고 최대 13국면이다")
    timeline_summary: Optional[str] = Field(
        None,
        description="★국면을 **한 줄로 압축한** 문자열. 13국면짜리를 그대로 "
                    "프롬프트에 실으면 사건 하나가 재료를 다 먹는다. 요약은 여기 "
                    "담고 `timeline` 배열은 배열대로 둔다 — 둘은 서로를 대체하지 "
                    "않는다. 국면이 없으면 `None`",
        examples=["2026-04 파업 예고 → 2026-06 압수수색 → 2026-08 합의 (3국면)"])

    # ── 출처 경로 ──────────────────────────────────────────────
    provenance: Literal["direct", "explored"] = Field(
        "direct",
        description="★**어떻게 손에 들어왔나.** `direct` 는 서버가 정한 재료 "
                    "범위에서 직접 조회한 것, `explored` 는 2차 Agent 가 탐색으로 "
                    "발견한 것이다. 지금은 `direct` 뿐이다 — 탐색 경로가 아직 "
                    "없으므로 `explored` 를 만드는 코드도 없다",
        examples=["direct"])


class PropagationDTO(BaseModel):
    """리스크 파급 한 갈래. **질의 시점에 계산한 값이다.**

    ★`stated` 를 **갈라 그려야 한다.** `true` 는 기사가 직접 말한 것, `false` 는
      우리가 공급망으로 계산한 것이다. 섞으면 추론을 사실로 파는 것이 된다 —
      실측(모트라스 파업): 124곳 = 보도 10 + 계산 114.

    ★`RelationDTO` 와 달리 **`evidence_id` 가 없다.** 계산값에 근거 id 를
      발급하면 `MarketDTO` 에서 막은 것과 같은 실수가 된다. 되짚을 좌표는
      `event_id`(어느 사건에서 퍼졌나)와 `hops`(몇 다리 건넜나)다.
    """

    event_id: str = Field(
        description="이 파급을 낳은 사건. **계산의 출발점 좌표다**",
        examples=["evt_news_c915fa8bf141"])
    target: str = Field(description="영향받는 기업 이름", examples=["현대차증권"])
    key: Optional[str] = Field(
        None, description="기업 키. **이름만 있고 노드가 없으면 `null`** — "
                          "그때는 워크스페이스 소속을 판정하지 않는다",
        examples=["00164779"])
    score: float = Field(ge=0, le=1, examples=[0.297])

    hops: int = Field(
        description="1 = 보도된 것 · 2 이상 = 공급망으로 계산한 것", examples=[2])
    stated: bool = Field(
        description="기사가 **직접 말했나.** `false` 면 우리가 계산한 것이다")
    stated_note: str = Field(
        description="★`stated` 를 **문자열로도** 남긴다. `false` 라는 토큰만으로는 "
                    "「우리가 계산한 추론」이라는 뜻이 전달되지 않는다. "
                    "`STATED_NOTE[stated]` 를 그대로 쓴다",
        examples=["공급망으로 계산한 파급 — 보도된 사실이 아니다"])
    path: list[str] = Field(
        default_factory=list,
        description="파급이 지나온 경로. `hops` 와 길이가 맞아야 한다")


# ══════════════════════════════════════════════════════════════════
#  근거 검색 히트
# ══════════════════════════════════════════════════════════════════


class EvidenceHitDTO(BaseModel):
    """의미검색이 짚은 근거 한 건.

    ★**citation 필드를 여기서 만들지 않는다.** `source_doc`(기사 URL·접수번호)
      과 언론사·보도일은 `relation_service.evidence_for_ids()` 가 조립하는
      값이고, 그 경로는 **마감 단계(`evidence_validation`)가 한 번에** 탄다
      (계약 2번). 도구가 따로 만들면 같은 사실을 두 곳에서 짓는 것이 되고,
      두 벌은 반드시 갈린다.

      그래서 이 DTO 는 **Agent 가 읽고 고르는 데 필요한 것**만 담는다.
      인용에 필요한 나머지는 `evidence_id` 로 나중에 이어 붙인다.
    """

    evidence_id: str = Field(examples=["ev_684dc0c435ca1676"])
    text: str = Field(description="근거 원문. ★우리가 요약한 것이 아니다")
    source_type: Literal["dart", "dart_filing", "news"] = Field(examples=["news"])
    source_note: str = Field(description="`SOURCE_NOTE` 의 문구 — 확정 사실인가 주장인가")
    edge_type: Optional[str] = Field(
        None, description="이 근거가 뒷받침하는 관계의 종류", examples=["SUPPLIES_TO"])
    subtype: Optional[str] = Field(None, examples=["공급계약"])
    occurred_at: Optional[str] = Field(
        None,
        description="사건 시점 `YYYY-MM-DD`. ★`null` 이 실재한다 — 적재 때 "
                    "시점을 못 뽑은 근거의 `occurred_at` 은 `0` 이다",
        examples=["2026-06-08"])
    rcept_no: Optional[str] = Field(
        None,
        description="DART 접수번호. ★뉴스 근거에는 **없다**(빈 문자열이 아니라 `null`)",
        examples=["20260608800436"])


# ══════════════════════════════════════════════════════════════════
#  공시 목록
# ══════════════════════════════════════════════════════════════════


class FilingDTO(BaseModel):
    """공시 한 건 — `documents` 표. ★**제목까지다. 본문이 아니다.**

    ★`evidence_id` 를 두지 않는다. 이건 「무엇이 공시됐나」의 목록이지 근거가
      아니다. 인용할 문장이 필요하면 `search_dart` 가 근거 청크를 준다.
    """

    rcept_no: str = Field(examples=["20260310002820"])
    doc_type: Optional[str] = Field(None, examples=["사업보고서"])
    title: Optional[str] = Field(None, examples=["사업보고서 (2025.12)"])
    rcept_dt: Optional[str] = Field(None, description="접수일 `YYYY-MM-DD`",
                                    examples=["2026-03-10"])
    url: Optional[str] = Field(None, description="DART 원문 링크 — 되짚을 수 있는 값")


# ══════════════════════════════════════════════════════════════════
#  사업의 내용
# ══════════════════════════════════════════════════════════════════


class BusinessOverviewDTO(BaseModel):
    """사업보고서 「사업의 내용」 **원문.**

    ★`company_profiles`(우리가 쓴 요약)와 다르다. 저건 근거가 못 된다 —
      우리 문장을 근거로 삼는 셈이라서다. 이것이 **챗봇이 인용할 수 있는
      유일한 재무계 텍스트**다. 실측(2026-08-27): 64행 · 기업 64곳 · 전부 2025년.
    """

    corp_code: str = Field(examples=["00126380"])
    bsns_year: int = Field(examples=[2025])
    overview_text: Optional[str] = Field(
        None, description="「사업의 개요」 원문. **우리가 요약하지 않은 것**")
    products_text: Optional[str] = Field(
        None,
        description="「주요 제품 및 서비스」 원문. ★없는 행이 실재한다"
                    "(64행 중 1행) — 빈 문자열로 바꾸지 말 것")
    source_doc: Optional[str] = Field(
        None,
        description="★DART 접수번호. **되짚을 수 있는 값**이라, 나중에 이 텍스트를 "
                    "`Evidence` 로 승격할 때 필요하다. 지금은 승격하지 않는다",
        examples=["20260310002820"])


# ══════════════════════════════════════════════════════════════════
#  시장
# ══════════════════════════════════════════════════════════════════


class MarketDTO(BaseModel):
    """시세와 지표. ★**`evidence_id` 를 두지 않는다.**

    시총·PER·PBR·PSR 은 **저장값이 아니라 `market_metrics` 뷰의 계산값**이다
    (`종가 × 상장주식수`를 순이익·자본·매출로 나눈 것). 계산값에 근거 id 를
    발급하면 원본이 갱신될 때 id 가 가리키는 값과 실제 값이 어긋난다 —
    `ratio_change` 를 제거했던 것과 같은 실수다(1,306건 중 15건 불일치).

    대신 **계산 좌표**를 담는다. 뷰가 `fin_year`·`fs_div` 를 값과 함께
    내보내는 이유가 이것이다 — 「어느 해 · 어느 재무제표로 나눈 값인가」를
    밝혀야 되짚을 수 있다.
    """

    # ── 계산 좌표 — 근거 id 대신 이것으로 되짚는다 ──────────────
    corp_code: str = Field(examples=["00126380"])
    trade_date: str = Field(description="시세 기준일 — 분자(시총)의 좌표",
                            examples=["2026-08-26"])
    fin_year: Optional[int] = Field(
        None, description="분모로 쓴 재무의 사업연도. ★`null` 이면 재무를 못 "
                          "붙인 것이다(실측 500행·4곳)", examples=[2025])
    fs_div: Optional[Literal["CFS", "OFS"]] = Field(
        None, description="분모로 쓴 재무제표 종류. 연결(CFS)·별도(OFS)")

    # ── 계산값 ────────────────────────────────────────────────
    close_price: Optional[int] = Field(None, examples=[71800])
    market_cap: Optional[int] = Field(None, description="종가 × 상장주식수", examples=[4286_0000_0000])
    per: Optional[float] = Field(None, examples=[13.42])
    pbr: Optional[float] = Field(None, examples=[1.18])
    psr: Optional[float] = Field(None, examples=[1.62])

    per_note: Optional[str] = Field(
        None,
        description="★`per` 가 `null` 일 때 **왜 없는지**를 담는다. 그냥 `null` 이면 "
                    "LLM 이 「정보 없음」으로 읽는다. 이유가 둘이라 뭉뚱그리면 안 "
                    "된다 — 재무는 있는데 순이익 ≤ 0 이면 `PER_NOTE_LOSS`, "
                    "`fin_year` 가 `null` 이면 `PER_NOTE_NO_FINANCIALS`. "
                    "`per` 가 있으면 `None`",
        examples=[PER_NOTE_LOSS])
