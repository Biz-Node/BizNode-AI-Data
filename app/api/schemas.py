"""응답 계약 — **백엔드·프론트가 보는 유일한 정의.**

스키마 중에 노출하지 않는 필드는 다음과 같다.
    grounding_suspect        그 관계를 **아예 응답에서 뺀다**
    confidence × 신선도 가중치  `score` 하나로 합쳐서
    신선도 판정               `freshness` 세 값으로
    revenue_trusted=false    금액 빼고 비중만
    ratio_trusted=false      비중 빼고 금액만
    listed_shares.suspect    시가총액·PER·PBR·PSR 을 **안 보낸다**
    corp_code 를 못 믿을 때    corp_code 를 **안 보낸다**

금액, 비율, 날짜 등의 단위는 한 곳에서만 정한다.
키 규칙 — 이름을 키로 쓰지 않는다
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ══════════════════════════════════════════════════════════════════
#  공통 — 모든 응답이 지고 다니는 것
# ══════════════════════════════════════════════════════════════════


class DetailLevel(str, Enum):
    """이 기업을 **얼마나 수집했는가.** `none` 이 정상입니다 — 3,432곳 중
    2,952곳이 여기 해당하고, 외국 기업도 여기 옵니다.

        full      64곳     사업보고서까지 다 돌았다 — 상세 페이지가 꽉 찬다
        partial  416곳     숫자까지 있다 (재무·시세) — 공시·개요·사업부문이 없다
        none   2,952곳     **수집을 위한 작업을 하지 않았다.** 다른 기업을
                           수집하다 관계로 딸려온 노드다

    **`full` 과 `partial` 은 상세 페이지를 엽니다.** `partial` 도 재무 3개년·
    주가·시총·제품·관계·사건이 나와서 볼 만합니다. 없는 블록은 `blocks` 를
    보고 안 그리면 됩니다.

    ★`none` 이라고 **빈 노드가 아닙니다.** 관계·사건·뉴스는 있습니다
      (엔비디아: 관계 59 · 블록 7/11). 좌 패널 요약은 그려야 하고,
      상세 페이지만 「아직 수집하지 않았습니다」로 막으면 됩니다.

    ★「비상장이라서」로 설명하면 안 됩니다 — 국내 상장사도 섞여 있습니다
      (현대차·현대중공업이 별칭 노드로 갈라져 여기 있습니다).
    """

    full = "full"
    partial = "partial"
    none = "none"


# `partial` 은 구글 차단이나 상한에 걸려 중간에 멈춘 것이다. 화면이
# 「이 회사는 자료가 적네」와 「아직 덜 모았네」를 구별해야 한다.
class Coverage(str, Enum):
    """수집이 **끝까지 갔는가.**"""

    complete = "complete"
    partial = "partial"


class Freshness(str, Enum):
    """관계를 **언제 관측했는지.** 모델이 아니라 산수다.

        current   `refresh_cycle_days × 1.5` 이내   가중치 1.0
        stale     그 이상                          0.6
        expired   종료 확인 또는 유효기간 경과        0.3  (응답에서 빠진다)
        unknown   날짜 없음                        0.7

    `stale` 도 지우지 않고 보낸다 — 뉴스는 관계의 시작만 보도하고 종료는
    보도하지 않는다. 「2024-06에 그렇게 보도됨」으로 표시한다.
    """

    current = "current"
    stale = "stale"
    expired = "expired"
    unknown = "unknown"


class EntityKind(str, Enum):
    """노드가 무엇인가.

    ⚠ **해외 여부를 이 값으로 판정하면 안 된다.** `해외` 는 9곳뿐이고
    엔비디아·TSMC·ASML 이 전부 `기업` 으로 들어가 있다.
    """

    기업 = "기업"
    금융기관 = "금융기관"
    펀드조합 = "펀드·조합"
    공공기관 = "공공기관"
    대학연구소 = "대학·연구소"
    사업부문 = "사업부문"
    해외 = "해외"
    불명 = "불명"


class EdgeType(str, Enum):
    """엣지 **12종 고정.** 늘어나지 않는다 — 유한해야 N차 추론이 된다."""

    OWNS_STAKE_IN = "OWNS_STAKE_IN"
    IS_EXECUTIVE_OF = "IS_EXECUTIVE_OF"
    SUPPLIES_TO = "SUPPLIES_TO"
    PARTNERS_WITH = "PARTNERS_WITH"
    ACQUIRES = "ACQUIRES"
    SUES = "SUES"
    COMPETES_WITH = "COMPETES_WITH"
    REGULATES = "REGULATES"
    DEVELOPS = "DEVELOPS"
    DEPENDS_ON = "DEPENDS_ON"
    HAS_EVENT = "HAS_EVENT"
    IMPACTS = "IMPACTS"


class NodeLabel(str, Enum):
    Company = "Company"
    Person = "Person"
    Organization = "Organization"
    Product = "Product"
    Event = "Event"


# ══════════════════════════════════════════════════════════════════
#  검색
# ══════════════════════════════════════════════════════════════════


class SearchHit(BaseModel):
    """검색 결과 한 건. **여러 건이 정상이다.**"""

    key: str = Field(description="이후 모든 조회에 쓰는 키 (corp_code 또는 norm_name)",
                     examples=["00126380"])
    name: str = Field(examples=["삼성전자"])
    label: NodeLabel = NodeLabel.Company
    in_graph: bool = Field(
        True,
        description="**`false` 면 DART 명부에만 있는 회사다** — 실재하지만 우리가 아직 "
                    "수집하지 않았다. 화면은 「수집되지 않은 기업입니다」로 알리고, "
                    "관계·재무를 조회하려 하지 말아야 한다")
    entity_kind: Optional[EntityKind] = None
    market: Optional[str] = Field(None, description="KOSPI · KOSDAQ · KONEX · 비상장 · 펀드",
                                  examples=["KOSPI"])
    stock_code: Optional[str] = Field(None, description="있으면 상장사", examples=["005930"])
    ksic: Optional[str] = Field(None, description="업종 중분류 코드 2자리. **묶어 세기용**",
                                examples=["26"])
    ksic_label: Optional[str] = Field(
        None, description="**화면에 쓸 업종 이름.** 코드는 사용자가 못 읽는다",
        examples=["전자부품·컴퓨터·영상·음향·통신장비"])
    # 명부에만 있는 회사(`in_graph=false`)는 등급이 없다 — `null` 이 정상이다
    detail_level: Optional[DetailLevel] = DetailLevel.none
    matched_on: Literal["name", "alias", "corp_code"] = Field(
        "name", description="무엇으로 걸렸나. `alias` 면 옛 표기로 찾아진 것")
    degree: int = Field(0, description="관계 수. 같은 이름이 여럿일 때 **정렬 기준**")


class SearchResponse(BaseModel):
    """**부분 일치.** 결과가 두 종류 섞여 있다 — `in_graph=true` 는
    수집한 것, `false` 는 DART 명부에만 있는 것(실재하지만 자료가 없다).

    `total` 은 **있는 수**, `hits` 는 실어 보낸 수. 「295건 중 20건」.
    """

    query: str = Field(examples=["삼성"])
    total: int = Field(description="**있는 수** (그래프 + 명부)", examples=[295])
    graph_total: int = Field(0, description="그중 우리가 수집한 것", examples=[46])
    registry_total: int = Field(0, description="그중 명부에만 있는 것", examples=[249])
    hits: list[SearchHit] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════


class RelationEndpoint(BaseModel):
    key: str = Field(examples=["00126380"])
    name: str = Field(examples=["삼성전자"])
    label: NodeLabel = NodeLabel.Company


class Relation(BaseModel):
    """관계 한 건. **근거 검증에서 걸린 관계는 여기 오지 않는다.**

    `edge_id` 가 이 관계의 유일한 id 다. `evidence_id` 로 대신하면 안 된다 —
    한 근거가 여러 관계를 뒷받침해서 **유일하지 않다**(엣지 11,060 : 근거 9,228).
    """

    edge_id: str = Field(
        description="**이 관계 하나를 가리키는 유일한 id.** 화면의 키로 쓰고 "
                    "`/relations/{edge_id}` 로 상세를 연다",
        examples=["5:ba4682b0-985e-45c9-96c5-f013f9141dcb:0"])
    # ★`evidence_id` 를 `edge_id` 로 쓰면 안 된다 — **유일하지 않다.**
    #   실측(2026-08-16): 11,060 엣지가 근거 9,228개를 쓴다. 한 문장이 여러 관계를
    #   뒷받침하기 때문이다 — 「삼성전자, SK하이닉스 등을 고객사로 확보」 한 문장이
    #   공급 관계 둘의 근거가 된다. 한 근거가 15개 엣지에 붙은 경우도 있다.
    evidence_id: Optional[str] = Field(
        None, description="근거 문장의 id. **여러 관계가 같은 근거를 쓸 수 있다**",
        examples=["ev_17acfbf5a4041e59"])
    type: EdgeType = Field(examples=["SUPPLIES_TO"])
    subtype: Optional[str] = Field(None, description="근거에서 읽어낸 「무엇을」",
                                   examples=["반도체 PCB"])
    source: RelationEndpoint
    target: RelationEndpoint
    symmetric: bool = Field(False, description="PARTNERS_WITH · COMPETES_WITH 는 방향이 없다")

    amount: Optional[int] = Field(None, description="**원 단위.** 계약 규모·인수 대금·과징금",
                                  examples=[44200000000])
    ratio: Optional[float] = Field(None, ge=0, le=100,
                                   description="**퍼센트(0~100).** 지분율", examples=[67.96])

    freshness: Freshness = Freshness.current
    last_seen: Optional[str] = Field(None, examples=["2025-12-15"])
    valid_from: Optional[str] = Field(None, examples=["2025-12-15"])
    valid_until: Optional[str] = None
    score: float = Field(0.9, ge=0, le=1,
                         description="**신뢰도 × 신선도 가중치.** 내부 confidence 를 그대로 주지 않는다")
    corroboration: int = Field(1, description="뒷받침 출처 수. 여럿이면 같은 사실을 여러 곳이 말한 것")
    source_type: Literal["dart", "dart_filing", "news"] = "news"

    # ── 신선도의 근거를 숫자로도 준다 ──────────────────────────
    # ★「stale」 이라고만 하면 화면이 「얼마나 오래됐나」를 말할 수 없다.
    refresh_cycle_days: Optional[int] = Field(
        None, description="이 출처의 예상 갱신 주기. DART 365 · 뉴스 180", examples=[180])
    days_since: Optional[int] = Field(None, description="마지막 관측 이후 지난 날", examples=[120])
    days_until_refresh: Optional[int] = Field(
        None, description="재확인까지 남은 날. 음수면 이미 지났다", examples=[60])

    # ── 배타성 — SUPPLIES_TO 전용 ────────────────────────────
    # ★「독점 공급인가」는 리스크 판단에 직결된다. 독점이면 그 관계 하나가
    #   끊길 때 대체가 없다. 같은 공급자의 **다른 고객 수**로 판정한다.
    exclusive: Optional[bool] = Field(
        None, description="`SUPPLIES_TO` 전용. 이 공급자가 이 고객에게만 파는가")
    other_counterparties: list[str] = Field(
        default_factory=list,
        description="독점이 아닐 때 **다른 상대 이름들.** 「삼성전자·엔비디아에도 공급」",
        examples=[["삼성전자", "엔비디아", "마이크론"]])


class Evidence(BaseModel):
    """관계의 **근거 원문.** 챗봇이 인용하는 것이 이것이다."""

    evidence_id: str = Field(examples=["ev_684dc0c435ca1676"])
    text: str = Field(
        description="원문. **우리가 요약하지 않은 것** — 화면이 그대로 인용한다",
        examples=["공급 관계 — 공급자: SFA반도체 / 수요자: 삼성전자(주) "
                  "계약유형: 공급계약 / 체결: 1999-01 "
                  "목적·내용: BOC 등 계약제품에 대한 안정적인 생산 공급"])
    source_doc: str = Field(description="DART 접수번호 또는 기사 URL. **되짚을 수 있는 값**",
                            examples=["20260323000826"])
    # ★`Relation.source_type` 과 **같은 3값**이다. 여기만 2값이면 엣지에 실재하는
    #   `dart_filing` 113건이 근거로 올라올 때 검증에서 튕긴다.
    #   (`dart` 정기공시 · `dart_filing` 개별공시 · `news` 보도)
    source_type: Literal["dart", "dart_filing", "news"] = "dart"
    press: Optional[str] = Field(None, description="기사면 언론사, 공시면 보고서 제목",
                                 examples=["전자신문"])
    published_at: Optional[str] = Field(None, examples=["2026-03-23"])
    # ★못 꺼낸 근거를 **조용히 빼지 않는다.** 빼면 「근거가 없는 관계」로 읽힌다.
    missing: bool = Field(False, description="`true` 면 id 는 있는데 원문을 못 찾았다")


class Propagation(BaseModel):
    """리스크 파급 한 갈래. 질의 시점에 계산한다.

    `stated` 를 **갈라 그려야 한다.** `true` 기사가 직접 말한 것,
    `false` 우리가 공급망으로 계산한 것. 섞으면 추론을 사실로 팔게 된다.
    실측(모트라스 파업): 124곳 = 보도 10 + 계산 114.
    """

    target: str = Field(description="영향받는 기업 이름", examples=["현대차증권"])
    key: Optional[str] = Field(None, description="기업 키. 이름만 있고 노드가 없으면 null",
                               examples=["00164779"])
    score: float = Field(ge=0, le=1, examples=[0.297])
    hops: int = Field(description="1 = 보도된 것 · 2 이상 = 공급망으로 계산한 것",
                      examples=[2])
    stated: bool = Field(description="기사가 직접 말했나. `false` 면 우리 계산이다",
                         examples=[False])
    channel: Optional[str] = Field(
        None, description="무엇을 타고 왔나 — `supply`(공급 차질) · `demand`(매출 상실)",
        examples=["supply"])
    path: list[str] = Field(
        default_factory=list, description="어떤 관계를 타고 닿았는지. **화면이 그대로 보여 준다**",
        examples=[["모트라스 파업", "IMPACTS(negative)", "현대차",
                   "SUPPLIES_TO(공급 차질)", "현대차증권"]])


class EdgePropagation(Propagation):
    """`/relations/{edge_id}` 의 파급 — **어느 사건에서 왔는지**가 붙는다."""

    event_id: str = Field(examples=["evt_news_9eae2c4cb76a"])
    event: str = Field(examples=["모트라스 파업"])


class RelationDetail(BaseModel):
    """선을 클릭했을 때. `evidence[].text` 가 **원문 그대로**다.

    `propagation` 은 **이 선을 타고** 번지는 위험만이다 — 공급 관계에서만
    계산한다.
    """

    relation: Relation
    evidence: list[Evidence] = Field(default_factory=list)
    propagation: list[EdgePropagation] = Field(
        default_factory=list,
        description="이 연결을 타고 닿는 위험. **`stated` 로 보도와 계산을 가른다**")


# ══════════════════════════════════════════════════════════════════
#  기업
# ══════════════════════════════════════════════════════════════════


class FinancialYear(BaseModel):
    """연도별 재무. `fs_div` 를 반드시 함께 본다 —
    **연결(CFS)과 별도(OFS)는 비교하면 안 된다.** 비율은 계산해서 준다.
    """

    bsns_year: int = Field(examples=[2025])
    fs_div: Literal["CFS", "OFS"] = Field("CFS", description="CFS=연결 · OFS=별도")
    revenue: Optional[int] = Field(None, description="**원 단위**", examples=[333605938000000])
    operating_profit: Optional[int] = Field(None, examples=[43601051000000])
    net_profit: Optional[int] = Field(None, examples=[45206805000000])
    total_assets: Optional[int] = Field(None, examples=[566942110000000])
    total_liabilities: Optional[int] = Field(None, examples=[130621773000000])
    total_equity: Optional[int] = Field(None, examples=[436320337000000])
    # ★비율은 **계산해서 준다.** 저장하면 원본과 어긋나고, 화면마다 다르게 계산하면
    #   같은 회사가 화면마다 다른 ROE 를 갖는다.
    debt_ratio: Optional[float] = Field(None, description="부채비율 % = 부채÷자본", examples=[29.94])
    roe: Optional[float] = Field(None, description="% = 순이익÷자본", examples=[10.36])
    roa: Optional[float] = Field(None, description="% = 순이익÷자산", examples=[7.97])
    operating_margin: Optional[float] = Field(None, description="% = 영업이익÷매출", examples=[13.07])


class Segment(BaseModel):
    """사업부문. **못 믿는 값은 아예 보내지 않는다**(`null`) —
    사업보고서 표는 단위가 표 밖 캡션에 있어 자주 1,000배 틀린다.
    """

    name: str = Field(examples=["DS 부문"])
    revenue: Optional[int] = Field(None, description="못 믿으면 `null`", examples=[130128200000000])
    revenue_ratio: Optional[float] = Field(None, description="**퍼센트**. 못 믿으면 `null`",
                                           examples=[39.0])


class MarketPoint(BaseModel):
    """시세 하루치."""

    trade_date: str = Field(examples=["2026-08-14"])
    close_price: int = Field(description="**원**", examples=[274500])
    change_pct: float = Field(description="**퍼센트**", examples=[2.43])
    volume: int = Field(examples=[21668266])


class MarketMetrics(BaseModel):
    """시장 지표. **저장하지 않고 조회할 때 계산한다.**

    `fin_year`·`fs_div` 가 값과 함께 나가므로 화면이 「2025년 연결 기준」이라
    밝힐 수 있다. 적자면 `per` 이 `null` 이다.
    """

    trade_date: str = Field(examples=["2026-08-14"])
    close_price: int = Field(examples=[274500])
    change_pct: float = Field(examples=[2.43])
    volume: int = Field(examples=[21668266])
    listed_shares: int = Field(description="유통주식수 = 발행총수 − 자기주식 · 보통주만",
                               examples=[5827808935])
    market_cap: int = Field(description="**원**", examples=[1599733552657500])
    # ★적자면 PER 을 만들지 않는다. 실측: 심텍 2025 순이익 −1,646억 → PER `null`.
    #   억지로 음수 PER 을 주면 화면이 「저평가」로 오독한다.
    per: Optional[float] = Field(
        None, description="**적자면 `null`.** 음수 PER 을 만들지 않는다", examples=[35.39])
    pbr: Optional[float] = Field(None, examples=[3.67])
    psr: Optional[float] = Field(None, examples=[4.80])
    fin_year: int = Field(description="지표의 분모가 된 회계연도", examples=[2025])
    fs_div: Literal["CFS", "OFS"] = Field("CFS", description="연결/별도 — **기준을 밝힌다**")


# unlisted           비상장이라 **원래 없다**       → 「비상장」
# not_collected      상장인데 **아직 안 받았다**     → 「미수집」
# unreliable_shares  주식수를 못 믿는다             → 「확인 중」
# 화면이 이걸 구분해야 한다. 「비상장」은 영원히 안 채워지지만
# 「미수집」은 배치를 돌리면 채워진다.
class MarketUnavailable(str, Enum):
    """시장 정보가 **왜 없는가.** 셋이 완전히 다른 말이다."""

    unlisted = "unlisted"
    not_collected = "not_collected"
    unreliable_shares = "unreliable_shares"


class MarketResponse(BaseModel):
    """시세가 있는 회사는 417곳뿐이다. 없을 때 **왜 없는지**를 함께 보낸다."""

    key: str = Field(examples=["00126380"])
    listed: bool = Field(True, description="`false` 면 아래가 전부 `null` — **오류가 아니다**")
    stock_code: Optional[str] = Field(None, examples=["005930"])
    unavailable_reason: Optional[MarketUnavailable] = Field(
        None, description="`latest` 가 `null` 일 때만. 「비상장」과 「미수집」은 다르다")
    latest: Optional[MarketMetrics] = None
    series: list[MarketPoint] = Field(default_factory=list, description="요청한 기간의 일별 시세")


class EventTimelinePhase(BaseModel):
    period: str = Field(description="연월", examples=["2026-06"])
    name: str = Field(examples=["삼성전자 압수수색"])


# ★**날짜는 사건 노드가 아니라 관계에 있다.** 같은 사건이라도 기업마다
# 엮인 시점이 다를 수 있어서다. 그래서 `occurred_at` 이 여기 온다.
class Event(BaseModel):
    """사건."""

    event_id: str = Field(examples=["evt_news_c915fa8bf141"])
    name: str = Field(examples=["삼성전자 본사 압수수색"])
    event_type: str = Field(description="12종", examples=["규제수사"])
    is_risk: bool = Field(examples=[True])
    role: Literal["subject", "counterparty", "mentioned"] = Field(
        "subject", description="이 기업이 **당사자인가 언급된 것인가**")
    occurred_at: Optional[str] = Field(None, examples=["2026-06-11"])
    article_count: int = Field(1, description="여러 기사가 같은 사건을 말하면 신뢰도가 올라간다")
    timeline: list[EventTimelinePhase] = Field(
        default_factory=list, description="같은 사건의 여러 국면. **흩어진 기사가 아니라 하나의 줄거리**")
    # 사건 노드가 아니라 **이 기업의 연결에서 나온 근거**다. 한 사건에 여러 기업이
    # 붙어도 서로의 근거가 섞이지 않는다 — 챗봇이 남의 근거로 이 기업을 설명하면
    # 없는 연루를 만들어 낸다.
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="**이 기업이 이 사건에 엮인 근거만.** 같은 사건에 붙은 다른 "
                    "기업의 근거는 들어 있지 않다",
        examples=[["ev_1fdde758922d6de6"]])


class NewsItem(BaseModel):
    """본문은 **저장하지 않는다**(저작권). 제목·출처·링크까지만."""

    url: str = Field(examples=["https://www.seoulfn.com/news/articleView.html?idxno=630959"])
    title: str = Field(examples=["삼성전자 본사 압수수색"])
    press: Optional[str] = Field(None, examples=["서울파이낸스"])
    published_at: Optional[str] = Field(None, examples=["2026-06-11"])


class Filing(BaseModel):
    rcept_no: str = Field(description="DART 접수번호", examples=["20260608800436"])
    doc_type: str = Field(examples=["사업보고서"])
    title: str = Field(examples=["사업보고서 (2025.12)"])
    rcept_dt: str = Field(examples=["2026-03-11"])
    url: str = Field(description="DART 원문 링크",
                     examples=["https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260608800436"])


class ExecutiveItem(BaseModel):
    key: str
    name: str = Field(examples=["전영현"])
    position: Optional[str] = Field(None, examples=["대표이사"])


# 나가는 방향으로 나눠 보여준다.
class OwnershipItem(BaseModel):
    """지배구조는 **양방향**이다. 같은 `OWNS_STAKE_IN` 을 들어오는 방향과"""

    key: str
    name: str = Field(examples=["국민연금공단"])
    label: NodeLabel = NodeLabel.Company
    ratio: Optional[float] = Field(None, description="**퍼센트**", examples=[7.51])
    subtype: Optional[str] = Field(None, examples=["5%이상주주"])


# dart   사업보고서에서 나온 것 — 이미 **매출을 내는 제품**
# news   기사에서 나온 것 — **개발 중일 수 있다**
# 화면이 「제품」과 「개발」로 나눠 보여주는 근거가 이 필드다.
# 실측: dart 397건 · news 1,653건.
class ProductItem(BaseModel):
    """`source` 가 **성격을 가른다.**"""

    key: str
    name: str = Field(examples=["HBM3E"])
    category: Optional[str] = Field(None, description="제품·부품·기술·장비·서비스·소재",
                                    examples=["제품"])
    source: Literal["dart", "news"] = Field(
        "news", description="`dart` = 사업보고서(매출 중) · `news` = 기사(개발 중일 수 있음)")


class CompanyBase(BaseModel):
    """요약과 상세가 **함께 쓰는 머리 부분.**"""

    key: str = Field(examples=["00126380"])
    name: str = Field(examples=["삼성전자"])
    detail_level: DetailLevel = DetailLevel.full
    coverage: Coverage = Coverage.complete
    collected_at: Optional[str] = Field(None, description="이 기업을 마지막으로 수집한 날",
                                        examples=["2026-08-12"])

    corp_code: Optional[str] = Field(None, description="**못 믿으면 안 보낸다**", examples=["00126380"])
    stock_code: Optional[str] = Field(None, examples=["005930"])
    market: Optional[str] = Field(None, examples=["KOSPI"])
    entity_kind: Optional[EntityKind] = None
    ksic: Optional[str] = Field(None, description="업종 코드. **묶어 세기용**", examples=["26"])
    ksic_label: Optional[str] = Field(
        None, description="**화면에 쓸 업종 이름.** 숫자를 그대로 보여주지 않는다",
        examples=["전자부품·컴퓨터·영상·음향·통신장비"])
    also_names: list[str] = Field(default_factory=list,
                                  description="병합된 옛 표기. **옛 링크가 깨지지 않게 하는 것**")


class SharedCustomer(BaseModel):
    """직접 관계는 없지만 **같은 고객에게 파는** 상대."""

    key: str
    name: str = Field(examples=["테크윙"])
    shared_count: int = Field(description="겹치는 고객 수", examples=[3])
    customers: list[str] = Field(default_factory=list,
                                 examples=[["삼성전자", "SK하이닉스", "마이크론"]])


class CompanySummary(CompanyBase):
    """**노드를 클릭했을 때** — 워크스페이스 좌 패널.
    `company_detail` 과 다른 화면이다. 이쪽은 「지금 보는 그래프 안에서의 이 회사」다.
    """

    overview: Optional[str] = Field(None, description="한 줄 소개",
                                    examples=["반도체·디스플레이·모바일을 만드는 국내 최대 제조사"])
    ceo: Optional[str] = Field(None, examples=["전영선, 김영구"])
    established_at: Optional[str] = Field(None, examples=["2015-07-01"])
    # ★요약에도 3개년을 준다. 좌 패널이 추세를 보여주는데 최근 한 해만으로는
    #   「나아지는 중인지」를 말할 수 없다.
    financials: list[FinancialYear] = Field(default_factory=list, description="최근 3개년")
    latest_financial: Optional[FinancialYear] = Field(
        None, description="`financials[0]` 과 같다. 한 줄만 쓸 화면을 위한 편의 필드")
    market_metrics: Optional[MarketMetrics] = Field(
        None, description="상장사만. `CompanyBase.market`(KOSPI/KOSDAQ)과 **다른 필드**다 —"
                          " 그쪽은 시장 이름이고 이쪽은 시세·지표다")
    risk_summary: Optional[str] = Field(None, examples=["최근 12개월 리스크 사건 3건"])
    risk_event_count: int = 0
    workspace_relations: list[Relation] = Field(
        default_factory=list, description="**담아 둔 다른 기업들과의 관계만**")
    # ★직접 관계는 없는데 **같은 고객을 판다** — 관계가 아니라 근접성이다.
    #   「◇ 테크윙·ISC·SFA반도체 — 직접 연결 없음, 고객 공유」가 이것이다.
    #   엣지로 만들면 안 된다. 근거가 없는 관계를 그리는 것이 되기 때문이다.
    shared_customers: list["SharedCustomer"] = Field(
        default_factory=list,
        description="담긴 기업 중 **직접 연결은 없지만 고객이 겹치는** 곳")
    recent_news: list[NewsItem] = Field(default_factory=list)


class BlockFill(str, Enum):
    """블록 하나의 완성도. 화면 머리의 ●◐○ 배지가 이것이다."""

    full = "full"        # ●  다 있다
    partial = "partial"  # ◐  일부만
    none = "none"        # ○  없다


# 「재무는 있는데 공시가 없다」가 흔한데, 통짜 등급으로는 표현이 안 된다.
# 화면이 블록마다 접거나 「미수집」을 적으려면 **블록별로** 알아야 한다.
class DetailBlocks(BaseModel):
    """`detail_level` 하나로는 부족하다."""

    overview: BlockFill = BlockFill.none
    financials: BlockFill = BlockFill.none
    segments: BlockFill = BlockFill.none
    products: BlockFill = BlockFill.none
    related: BlockFill = BlockFill.none
    risk: BlockFill = BlockFill.none
    news: BlockFill = BlockFill.none
    filings: BlockFill = BlockFill.none
    ownership: BlockFill = BlockFill.none
    market: BlockFill = BlockFill.none
    graph: BlockFill = BlockFill.none


class DetailCounts(BaseModel):
    """블록별 **실제 수.** 상세의 목록은 블록마다 10건까지(관계 그래프는
    60)이므로, 화면은 이 수로 「148건 중 10건」이라 쓰고 전체는 서브 라우트로
    가져온다.
    """

    relations: int = Field(0, description="이 기업에 붙은 관계 수 (전부)", examples=[1169])
    related_companies: int = Field(0, description="연관 **기업 수**(중복 없이)",
                                   examples=[443])
    events: int = Field(0, examples=[148])
    risk_events: int = Field(0, examples=[69])
    news: int = Field(0, examples=[583])
    filings: int = Field(0, examples=[3])
    products: int = Field(0, examples=[152])
    executives: int = Field(0, examples=[21])
    owns: int = Field(0, description="이 회사가 소유한 쪽", examples=[157])
    owned_by: int = Field(0, description="이 회사를 소유한 쪽", examples=[9])


# ★**양방향을 한 응답에 갈라 담는다.** 같은 `OWNS_STAKE_IN` 이라도
# 「누가 나를 소유하나」와 「내가 무엇을 소유하나」는 화면에서 다른 뜻이다.
# 한 배열로 주고 방향 플래그를 달면 화면이 매번 다시 가른다.
class OwnershipResponse(BaseModel):
    """`/companies/{key}/ownership` — 지배구조 「더보기」."""

    key: str = Field(examples=["00126380"])
    owns_total: int = Field(0, description="**자르기 전 실제 수**", examples=[157])
    owned_by_total: int = Field(0, examples=[9])
    owns: list[OwnershipItem] = Field(default_factory=list, description="이 회사가 소유한 쪽")
    owned_by: list[OwnershipItem] = Field(default_factory=list, description="이 회사를 소유한 쪽")


class CompanyDetail(CompanyBase):
    """기업 상세 — **「이 회사 자체의 전부」.** 워크스페이스 없이도 열린다."""

    blocks: DetailBlocks = Field(default_factory=DetailBlocks,
                                 description="블록별 완성도 — 화면 머리의 ●◐○")
    counts: DetailCounts = Field(default_factory=DetailCounts, description="블록별 건수")

    overview: Optional[str] = Field(None, description="LLM 요약 한 줄")
    business_overview: Optional[str] = Field(
        None, description="**사업보고서 원문 그대로.** 챗봇이 인용할 수 있는 것")
    ceo: Optional[str] = Field(None, examples=["전영현, 노태문"])
    established_at: Optional[str] = Field(None, examples=["1969-01-13"])
    name_en: Optional[str] = Field(None, examples=["SAMSUNG ELECTRONICS CO,.LTD"])
    induty: Optional[str] = Field(None, description="DART 업종코드 5자리", examples=["264"])

    market_metrics: Optional[MarketMetrics] = Field(
        None, description="상장사만. 비상장이면 `null` 이 정상이다")
    financials: list[FinancialYear] = Field(default_factory=list, description="최근 3개년")
    segments: list[Segment] = Field(default_factory=list)
    products: list[ProductItem] = Field(default_factory=list)
    executives: list[ExecutiveItem] = Field(default_factory=list)
    owned_by: list[OwnershipItem] = Field(default_factory=list, description="**이 회사를 소유한 쪽**")
    owns: list[OwnershipItem] = Field(default_factory=list, description="**이 회사가 소유한 쪽**")
    related: list[Relation] = Field(default_factory=list,
                                    description="공급·협력·경쟁 — 신선도·검증 적용됨")
    events: list[Event] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    # ★상세 페이지의 「기업 관계 그래프」 블록(README 4-3). 페이지가 열릴 때
    #   어차피 필요하니 **함께 보낸다** — `market`·`events`·`news`·`filings` 도
    #   따로 라우트가 있지만 상세가 품고 있는 것과 같은 규칙이다.
    #
    #   `related` 에 나가는 관계는 **반드시 이 그래프에도 있다.** 목록에서 한 줄을
    #   누르면 그래프의 그 선을 강조할 수 있어야 한다(실측: 안 맞춰 뒀을 때
    #   SK하이닉스에서 목록에만 있는 관계가 8건 나왔다).
    #
    #   `depth`·`max_nodes` 를 바꿔 다시 그리려면 `/companies/{key}/graph`.
    graph: Optional["GraphResponse"] = Field(
        None, description="이 회사 중심 관계 그래프 — **`related` 를 전부 포함한다**")


# ══════════════════════════════════════════════════════════════════
#  그래프
# ══════════════════════════════════════════════════════════════════


# 그리면 「내가 담은 건 이거고, 이어주려고 딸려온 건 저거」가 한눈에 보인다.
class GraphNode(BaseModel):
    """`role` 이 **담은 기업과 다리 노드를 가른다.** 프론트가 다리를 흐리게"""

    key: str
    name: str
    label: NodeLabel = NodeLabel.Company
    role: Literal["pinned", "bridge", "neighbor"] = "neighbor"
    # ★범례가 이걸로 갈린다 — 「거래 기업 / 주주·자회사 / 위험 사건 / 제품」.
    #   `label` 만으로는 Company 가 거래처인지 주주인지 구별이 안 된다.
    kind: Literal["trade", "ownership", "event", "product", "person", "org"] = Field(
        "trade", description="무엇으로서 딸려왔나. 범례·색을 가르는 값")
    entity_kind: Optional[EntityKind] = None
    ksic_label: Optional[str] = Field(None, examples=["전자부품·컴퓨터·영상·음향·통신장비"])
    degree: int = Field(0, description="전체 그래프에서의 연결 수. **허브를 흐리게 그릴 근거**")
    is_island: bool = Field(False, description="아무와도 안 이어진 노드. **억지로 잇지 않는다**")
    # ★아티팩트 명세 — 참조 기업을 두 축으로 뽑을 때 쓰는 값
    members: Optional[int] = Field(None, description="담은 기업 **몇 곳과** 이어지나", examples=[4])
    # ★두 축 중 어느 쪽으로 뽑혔든 **둘 다 채운다.**
    #   구조 축으로 뽑혔다고 위험을 비워 두면 화면이 「위험 없음」으로 오독한다 —
    #   **안 잰 것(`null`)과 없는 것(`0`)은 다르다.**
    risk_weight: Optional[int] = Field(
        None, description="이 노드를 통해 들어오는 위험 = 사건 기사 수 × `members`. "
                          "**`0` 은 위험 없음, `null` 은 안 잼**(참조 노드가 아닌 경우)",
        examples=[48])
    risk_count: Optional[int] = Field(None, description="리스크 사건 수", examples=[6])
    can_collect: Optional[bool] = Field(
        None,
        description="**더 받을 데가 있나.** `corp_code` 가 있으면 DART 로 받을 수 있고, "
                    "없으면 받을 데가 없다 — 「담을 수 없음」의 진짜 이유는 해외라서가 "
                    "아니라 이것이다")


class GraphEdge(BaseModel):
    edge_id: str = Field(description="유일한 id — 화면의 키로 쓴다")
    evidence_id: Optional[str] = Field(None, description="근거 id. **공유될 수 있다**")
    type: EdgeType
    subtype: Optional[str] = None
    source: str = Field(description="노드 key")
    target: str = Field(description="노드 key")
    symmetric: bool = False
    freshness: Freshness = Freshness.current
    score: float = Field(0.9, ge=0, le=1)


class GraphResponse(BaseModel):
    """그래프. **허브는 다리로 쓰지 않는다** — 삼성전자를 다리로 허용하면
    거의 모든 쌍이 이어져 의미가 0인 그래프가 된다.

    못 이으면 `islands` 에 담아 **없다고 말한다.** 없는 관계를 그리지 않는다.
    """

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    islands: list[str] = Field(
        default_factory=list,
        description="**화면에 그려질 선이 하나도 없는 노드.** `refs` 를 켜면 참조 기업이 "
                    "이어 주므로 줄어든다. 담은 기업끼리 직접 연결이 없는지는 엣지의 "
                    "양 끝이 모두 담은 기업인 것을 세어 알 수 있다")
    truncated: bool = Field(False, description="`max_nodes` 에 걸려 잘렸나")
    # ★없는 키를 **조용히 버리지 않는다.** 검색이 DART 명부까지 보여 주므로
    #   아직 수집 안 한 회사가 담겨 올 수 있다. 말없이 빼면 화면이 「담았는데
    #   왜 안 보이지」를 설명할 수 없다.
    unknown_keys: list[str] = Field(
        default_factory=list,
        description="**그래프에 없어서 못 그린 키.** 화면은 「아직 수집하지 않아 "
                    "그래프에 없습니다」로 알린다",
        examples=[["01622599"]])
    # ★참조 기업은 **두 축에서 각각 상위 5곳만** 보낸다. 후보가 몇 곳이었는지를
    #   알려 주지 않으면 화면은 그게 전부인 줄 안다 — 실측: 반도체 워크스페이스의
    #   참조 후보가 **337곳**인데 10곳만 나간다.
    ref_candidates: int = Field(
        0, description="참조 기업 **후보 수**. `refs=false` 면 0. "
                       "「후보 337곳 중 10곳」을 화면이 말할 수 있다", examples=[337])
    # ★무엇이 잘렸는지 **말한다.** 조용히 자르면 화면은 그게 전부인 줄 안다.
    #   실측(심텍 1홉): OWNS_STAKE_IN 9 · IS_EXECUTIVE_OF 8 · DEVELOPS 6 …
    omitted: dict[str, int] = Field(
        default_factory=dict,
        description="유형별로 **뺀 개수**. 「임원 8명 중 3명만 표시」를 화면이 말할 수 있다",
        examples=[{"IS_EXECUTIVE_OF": 5, "OWNS_STAKE_IN": 4}])


# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈
# ══════════════════════════════════════════════════════════════════


class RiskEvent(BaseModel):
    """「최근 위험 사건」 — `IMPACTS.sign = negative` 를 **영향받는 기업 수**로 정렬."""

    event_id: str
    name: str = Field(examples=["삼성전자 본사 압수수색"])
    event_type: str = Field(examples=["규제수사"])
    occurred_at: Optional[str] = Field(None, examples=["2026-06-11"])
    affected_count: int = Field(description="영향받는 기업 수 = 정렬 기준", examples=[4])
    affected: list[RelationEndpoint] = Field(default_factory=list)
    article_count: int = 1


class NewsFeedItem(NewsItem):
    """뉴스/이슈 화면의 한 줄 — 기사에 **어느 기업·어떤 사건이 걸렸는지**까지."""

    companies: list[RelationEndpoint] = Field(
        default_factory=list,
        description="이 기사에 언급된 **우리가 아는 기업.** 이름을 못 찾는 키는 뺀다")
    event: Optional[RelationEndpoint] = None
    # 한 기사가 여러 갈래에 걸리는 게 정상이다 — 「공정위, 납품단가 담합 제재」는
    # 공급망이면서 규제다. 하나로 고르면 화면이 절반을 놓친다.
    categories: list[str] = Field(
        default_factory=list, description="공급망 · 지분 · 규제 · 사건 (**여러 개 가능**)",
        examples=[["공급망", "규제"]])
    is_risk: bool = Field(False, description="사건 갈래에 걸렸나")


class NewsFeedResponse(BaseModel):
    """뉴스/이슈 화면. 필터가 **세 축**이고 겹쳐 쓸 수 있다.

        축 1  category        전체 · 공급망 · 지분 · 규제 · 사건
        축 2  workspace_keys  전체 · 내 워크스페이스
        축 3  정렬             최신순

    한 줄에 몰면 「위험만」과 「내 워크스페이스」가 배타적인지 겹칠 수 있는지
    알 수 없어서 갈라 두었다.

    본문은 없다 — 제목·언론사·발행일·링크까지다. 원문은 언론사 링크로 간다.
    """

    total: int = Field(0, description="필터를 통과한 **전체 수**. 화면은 「2,387건 중 20건」")
    items: list[NewsFeedItem] = Field(default_factory=list)


class TrendingItem(BaseModel):
    key: str
    name: str = Field(examples=["삼성전자"])
    label: NodeLabel = NodeLabel.Company
    mention_count: int = Field(description="최근 언급 수", examples=[42])


class WorkspaceInsightRequest(BaseModel):
    """`GET` 이 아니라 `POST` 인 이유 — 인사이트는 **담아 둔 기업 전부를 겹쳐야**
    나온다. 키 목록이 길어지면 쿼리스트링에 안 들어간다."""

    keys: list[str] = Field(
        description="**담아 둔 기업 전부.** 1곳이면 빈 배열이 나간다 — "
                    "「겹친다」가 성립하지 않는다",
        examples=[["01095722", "00164779", "00126380", "00161383"]])
    limit: int = Field(5, ge=1, le=20, description="카드 최대 장수")


class InsightKind(str, Enum):
    """카드 종류. 화면이 아이콘·색을 이걸로 가른다."""

    shared_risk = "shared_risk"                    # 같은 위험 사건에 여럿이 걸림
    cascade_risk = "cascade_risk"                  # A 의 사건이 B 까지 번짐
    shared_customer = "shared_customer"            # 같은 곳에 판다
    shared_supplier = "shared_supplier"            # 같은 곳에서 사온다
    shared_owner = "shared_owner"                  # 같은 주주
    internal_competition = "internal_competition"  # 담은 기업끼리 경쟁
    sector_concentration = "sector_concentration"  # 업종 쏠림
    no_overlap = "no_overlap"                      # 겹치는 게 없다


# ★`why` 를 반드시 함께 보낸다. **왜 그렇게 봤는지 없이 결론만 주면
#   사용자가 검증할 방법이 없다.**
# ★`headline`·`why` 는 아래 숫자로 채운 **템플릿**이다. 해석(「엔비디아 수요가
#   꺾이면 위험하다」)은 넣지 않는다 — 숫자에서 안 나온다. 필요하면 추론 계층이
#   이 재료를 받아 쓴다(`/retrieve` 와 같은 경계).
class InsightCard(BaseModel):
    """**합쳐야 드러나는 것.** 기업 하나를 열어서는 안 보인다.

    `lift` 가 핵심이다 — 「몇 곳이 겹치나」가 아니라 **「보통보다 얼마나
    몰려 있나」**다. 삼성전자에 공급하는 회사는 전체의 4.37%라 반도체
    워크스페이스면 당연히 겹치고, 엔비디아는 0.26%라 겹치면 특이하다.

          엔비디아  4/4 = 100% ÷ 0.26% = 381배
          삼성전자  3/4 =  75% ÷ 4.37% =  17배

    정렬은 **`shared` 1차, `lift` 2차**다. lift 만 쓰면 `2/4`(858배)가
    `4/4`(381배)를 이겨 화면이 이상해진다.
    """

    kind: InsightKind
    # ★`headline` 에는 **대상 이름이 없다.** 화면이 `subject` 를 카드 제목으로
    #   올리기 때문에, 문장에도 넣으면 「엔비디아 / 엔비디아에 …」가 된다.
    headline: str = Field(description="`subject` 아래에 쓰는 한 줄",
                          examples=["담은 4곳이 전부 공급합니다"])
    why: str = Field(
        description="**근거.** 결론만 주면 사용자가 검증할 수 없다",
        examples=["엔비디아에 공급하는 회사는 전국 9곳뿐입니다"])
    keys: list[str] = Field(default_factory=list,
                            description="**걸린 기업들.** 화면이 그래프에서 강조한다",
                            examples=[["01095722", "00164779"]])
    names: list[str] = Field(default_factory=list, examples=[["심텍", "SK하이닉스"]])
    shared: int = Field(0, description="걸린 기업 수", examples=[4])
    of: int = Field(0, description="워크스페이스 크기", examples=[4])

    # ── 무엇을 공유하나 (`no_overlap`·`internal_competition` 은 비어 있다)
    subject: Optional[str] = Field(
        None, description="**카드 제목.** 공유하는 대상 — 회사·주주·사건·업종 이름. "
                          "`internal_competition`·`no_overlap` 은 대상이 없어 `null`",
        examples=["엔비디아"])
    subject_key: Optional[str] = Field(None, description="사건이면 `event_id`")

    # ── 거래·지분 집중일 때
    base: Optional[int] = Field(None, description="전체에서 그런 회사가 몇 곳인가",
                                examples=[9])
    base_pct: Optional[float] = Field(None, examples=[0.26])
    lift: Optional[float] = Field(None, description="**보통의 몇 배로 몰려 있나**",
                                  examples=[381.0])
    # ★화면은 이 단어를 쓰고 `lift` 숫자는 검증용으로 남긴다. 수치를 지우지 않는다.
    lift_label: Optional[str] = Field(
        None, description="`lift` 를 말로 — 매우 이례적(100배+) · 이례적(30~) · 다소 몰림(10~)",
        examples=["매우 이례적"])

    # ── 위험 사건일 때는 lift 가 아니라 커버리지로 본다
    coverage: Optional[float] = Field(
        None, description="이 사건에 걸린 회사 중 **몇 %가 이 워크스페이스인가**",
        examples=[100.0])
    event_id: Optional[str] = Field(None, examples=["evt_news_0e95c793dc87"])

    # ── 연쇄 위험일 때
    score: Optional[float] = Field(None, examples=[0.066])
    stated: Optional[bool] = Field(
        None, description="`false` 면 **기사에 없고 우리가 계산한 것**이다")
    path: list[str] = Field(default_factory=list, description="어느 관계를 타고 닿았나")


# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


class MatchType(str, Enum):
    """검색이 이 결과를 어떤 경로로 찾았는가 — 설계서 §11 "SEMANTIC 결과를 같은
    무게로 말하지 않는다"를 추론 계층이 지킬 수 있도록 노출한다. 내부
    `search.model.enums.SearchMode`(NAME/RELATIONSHIP/SEMANTIC)를 그대로 쓰지
    않고 이분화한다 — 추론 계층에 필요한 건 「그래프에서 정확히 찾았나, 의미
    유사도로 찾았나」뿐이다."""

    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"


class AnchorSource(str, Enum):
    """이 답이 **무엇을 대상으로** 쓰였는가 — 설계서 §14.

    ★`match_type`(**어떻게 찾았나**)과 **별개 축**이다. 관계 키워드는 있는데
      질문이 대상을 지정하지 않은 질의는 `EXACT` 로 나가는데, 그건 거짓말이
      아니라 **빠져 있던 축이 이것**이었다(현황서 §5-3).
    """

    # 질문이 대상을 지정했고 그 대상이 해소됐다.
    QUERY = "query"
    # ★질문이 대상을 지정하지 않았지만 **사용자가 지금 보고 있는 기업**이 있다.
    #
    #   ★`workspace` 와 **뭉개지 않는다.** 「담아 둔 것」과 「보고 있는 것」은
    #     사용자가 한 일이 다르다 — 기업 상세 페이지에서 「이 회사 노조 리스크
    #     어때?」라고 물으면 답은 **그 회사**이지 담아 둔 기업이 아니다. 한 값으로
    #     묶으면 「질문이 대상을 지정하지 않아 워크스페이스 기업들을 대상으로
    #     삼았습니다」라는 문구가 사실이 아닌 채로 나간다 —
    #     `prompt.with_target_note()` 가 「판정이 없는데 형태를 지시하면 그게 곧
    #     거짓말이다」라고 못 박은 그 자리다.
    CONTEXT = "context"
    # ★질문이 대상을 지정하지 않았고 보고 있는 기업도 없다 — **앵커가 없다.**
    #
    #   ★전에는 이 자리가 `workspace` 였다. 앵커가 없으면 워크스페이스 기업을
    #     대상으로 **승격**시켰는데, 그것이 「Workspace Context 가 Query 의 의미를
    #     덮어쓰는」 구조였다(최종 설계 §17-3·§19-4). 워크스페이스는 **랭킹
    #     문맥**이지 질문의 대상이 아니다.
    #
    #   ★**정상 상태다.** 「최근 주요 투자 이벤트가 뭐야?」처럼 대상을 지정하지
    #     않은 질문은 그대로 Global Search 를 타고 답이 나간다(최종 설계 §8).
    #     `unresolved`(지정했는데 못 찾음)와 **뭉개면 안 된다** — 사용자가 할 일이
    #     다르다. 이쪽은 아무것도 할 일이 없고, 저쪽은 다시 물어야 한다.
    ANCHORLESS = "anchorless"
    # ★질문이 대상을 지정했는데 **못 찾았다.** 워크스페이스로 갈아타지 않는다 —
    #   그러면 「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이 된다
    #   (설계서 §14-3·§14-4).
    UNRESOLVED = "unresolved"


class Anchor(BaseModel):
    """재료를 모은 **출발점** 한 곳.

    ★`companies` 와 다르다 — `companies` 는 「재료가 된 기업」이고 여기는 「그
      재료를 모으려고 잡은 기업」이다(설계서 §5, 현황서 §5-7).

    ★`source` 에 `unresolved`·`anchorless` 는 올 수 없다 — 둘 다 **앵커 자체가
      없는** 상태다. 그 값들은 `AskResponse.anchor_source` 의 것이다.
    """

    key: str = Field(
        description="**`corp_code`(8자리 숫자) 또는 `norm_name`.** 식별 우선순위는 "
                    "`corp_code` → `norm_name` 이고, `name` 은 식별에 쓰지 않는다 "
                    "(설계서 §16-1)",
        examples=["00164779"])
    name: str = Field(description="표시·해석용. **식별 기준이 아니다**",
                      examples=["SK하이닉스"])
    source: Literal[AnchorSource.QUERY, AnchorSource.CONTEXT] = Field(
        description="`query` — 질문이 지정한 대상. "
                    "`context` — 사용자가 지금 보고 있는 기업. "
                    "★워크스페이스 기업은 앵커가 되지 않는다 — 랭킹 문맥이다")


# ★**답변을 만들지 않는다.** 사실과 근거만 준다 — 문장 생성은 추론 담당 몫이고,
# 경계를 섞으면 「누가 지어냈나」를 못 가린다.
class RetrieveResponse(BaseModel):
    """추론 계층이 쓰는 재료."""

    question: str = Field(examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    match_type: MatchType = Field(
        description="EXACT — 이름/관계가 그래프에서 정확히 일치해 찾았다. "
                    "SEMANTIC — 프로필 문서와의 의미 유사도로 골랐다(설계서 §11, "
                    "같은 무게로 말하면 안 된다)")
    anchors: list[Anchor] = Field(
        default_factory=list,
        description="**재료를 모은 출발점.** `companies` 와 다르다 — 이쪽이 「어디서부터 "
                    "모았나」이고 `companies` 는 「그래서 무엇이 재료가 됐나」다")
    # ★설명을 사실에 맞춘다(현황서 §5-7). 전에는 「질문에서 찾아낸 기업」이라고
    #   적혀 있었는데, 실제로 담기는 것은 검색 히트에서 추린 **재료가 된 기업**이다.
    #   앵커는 `anchors[]` 로 따로 싣는다.
    companies: list[RelationEndpoint] = Field(
        default_factory=list,
        description="**재료가 된 기업.** 질문에서 찾아낸 기업이 아니다 — 앵커는 `anchors`")
    events: list[Event] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    propagation: list[Propagation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list,
                                     description="**인용에 쓸 원문.** 답변에 근거 id 를 반드시 붙인다")


# ══════════════════════════════════════════════════════════════════
#  답변 (Answer Layer)
# ══════════════════════════════════════════════════════════════════


class Source(BaseModel):
    """LLM 이 인용한 근거 한 건 — 화이트리스트를 통과한 것만 여기 온다."""

    evidence_id: str = Field(examples=["ev_684dc0c435ca1676"])
    edge_id: Optional[str] = Field(None, description="근거가 관계에서 왔을 때만")
    text: str
    source_doc: str
    # ★`Evidence.source_type` 을 그대로 물려받는다(`answer_service` 가 옮겨 담는다).
    #   좁히면 근거에서 답변으로 넘어오는 길목에서만 값이 사라진다.
    source_type: Literal["dart", "dart_filing", "news"] = "news"
    published_at: Optional[str] = Field(None, examples=["2026-03-23"])


class AskResponse(BaseModel):
    """`/ask` 응답. `failed=True` 면 `answer` 는 고정 문구다 — 성공과 구별한다."""

    answer: str
    sources: list[Source] = Field(default_factory=list)
    failed: bool = False
    # ★`match_type` 은 여기 싣지 않는다(설계서 부록 A) — 프론트에 필요한 것은
    #   「무엇을 대상으로 답했나」이고, 그건 `anchor_source` 다. `match_type`
    #   (어떻게 찾았나)은 `/retrieve` 에 남는다.
    #
    # ★`None` 은 **다섯 번째 앵커 상태가 아니라 「아직 판정하지 않았다」**이다.
    #   판정기(`query_understanding`)는 현황서 §6-2 ② 단계에서 붙고, 그때 이
    #   필드가 필수가 된다. 그 전에 억지 기본값을 넣으면 — 예컨대 전부
    #   `query` 로 두면 — 못 찾은 대상을 「질문이 지정한 대상으로 답했다」고
    #   말하는 셈이라 §14-3 이 막으려는 바로 그 조용한 오답이 된다.
    #
    # ★**`workspace` 는 없어졌다**(최종 설계 §17-3). 앵커가 없으면 워크스페이스로
    #   갈아타지 않고 `anchorless` 로 남긴다 — 워크스페이스는 랭킹 문맥이다.
    anchor_source: Optional[AnchorSource] = Field(
        None,
        description="이 답이 **무엇을 대상으로** 쓰였나 — `query`(질문이 지정한 대상) / "
                    "`context`(사용자가 지금 보고 있는 기업) / "
                    "`anchorless`(질문이 대상을 지정하지 않았다 — **정상**) / "
                    "`unresolved`(지정했는데 못 찾음). **`match_type` 과 별개 축이다.** "
                    "판정기가 붙기 전에는 `null` 이다")


# ══════════════════════════════════════════════════════════════════
#  요청 바디 — POST 로 받는 것
# ══════════════════════════════════════════════════════════════════


class WorkspaceSummaryRequest(BaseModel):
    key: str = Field(examples=["00126380"])
    workspace_keys: list[str] = Field(
        description="**담아 둔 기업 전부.** 「이 워크스페이스와의 관계」를 내려면 필요하다",
        examples=[["01095722", "00126380", "00164779", "00161383"]])


class WorkspaceGraphRequest(BaseModel):
    keys: list[str] = Field(examples=[["01095722", "00126380", "00164779", "00161383"]])
    expand: bool = Field(True, description="섬이 된 기업을 한 칸 건너 이어 볼까")
    # ★기본이 꺼짐인 이유 — **담은 기업만 보는 게 시작점**이다.
    #   「밖에서 오는 영향」이 궁금할 때 켠다. 켜면 두 축(구조·위험)에서
    #   각각 상위 5곳을 뽑아 7~11곳이 붙는다.
    refs: bool = Field(False, description="참조 기업을 두 축으로 뽑아 붙일까")
    max_nodes: int = Field(150, ge=1, le=500)


class WorkspaceSuggestRequest(BaseModel):
    keys: list[str] = Field(description="이미 담은 기업", examples=[["01095722"]])
    limit: int = Field(5, ge=1, le=20)


# ★**왜 추천인지를 반드시 함께 준다.** 「한미반도체를 추천합니다」만으로는
# 사용자가 담을 이유를 모른다. 「공통 고객 4곳 — 삼성전자·SK하이닉스·
# 마이크론·엔비디아」까지 줘야 판단이 된다.
class Suggestion(BaseModel):
    """「같이 담을 만한 기업」 한 건."""

    key: str = Field(examples=["00161383"])
    name: str = Field(examples=["한미반도체"])
    reason: Literal["shared_customer", "shared_supplier", "shared_owner",
                    "same_event", "competitor"] = Field(
        "shared_customer", description="왜 추천하나")
    reason_text: str = Field(description="화면에 그대로 쓸 한 줄",
                             examples=["공통 고객 4곳 — 삼성전자 · SK하이닉스 · 마이크론 · 엔비디아"])
    overlap: int = Field(description="겹치는 수 — 정렬 기준", examples=[4])
    via: list[str] = Field(default_factory=list, description="무엇을 통해 겹치나",
                           examples=[["삼성전자", "SK하이닉스", "마이크론", "엔비디아"]])
    in_graph: bool = True
    detail_level: DetailLevel = DetailLevel.none
    ksic_label: Optional[str] = None


# 워크스페이스의 첫 벽이고, 이게 그 벽을 넘겨 준다.
class WorkspaceSuggestResponse(BaseModel):
    """**담은 기업이 1곳일 때 가장 쓸모 있다.** 「한 곳만 담으면 관계가 안 보인다」가"""

    keys: list[str] = Field(description="요청에 담겨 있던 기업")
    suggestions: list[Suggestion] = Field(default_factory=list)


class WorkspaceChangesRequest(BaseModel):
    keys: list[str] = Field(examples=[["01095722", "00164779"]])
    since: str = Field(description="이 시각 이후의 변경만. ISO 8601",
                       examples=["2026-08-09"])


class Change(BaseModel):
    """알림 한 건."""

    kind: Literal["new_risk_event", "new_relation", "relation_ended"] = Field(
        description="마이페이지 알림 설정의 세 항목과 **같은 이름**")
    company_key: str = Field(examples=["00164779"])
    company_name: str = Field(examples=["SK하이닉스"])
    title: str = Field(examples=["청주 M15X 화재로 8명 병원 이송"])
    detail: Optional[str] = Field(None, examples=["사고재해 · 영향 33곳"])
    occurred_at: Optional[str] = Field(None, examples=["2026-08-04"])
    ref_id: Optional[str] = Field(None, description="사건 id 또는 관계 id — 눌러서 갈 곳")


class WorkspaceChangesResponse(BaseModel):
    """알림 셋 — `new_risk_event` · `new_relation` · `relation_ended`.

    `relation_ended` 는 지금 **언제나 빈 배열**이다(비교 기준이 2026-07-31
    도입이라 대상이 없다). 필드는 미리 열어 두었다.
    """

    since: str
    total: int = 0
    changes: list[Change] = Field(default_factory=list)


class AskRequest(BaseModel):
    """★`question` 이 비면 **422 다.** 전에는 검증이 없어 빈 문자열이 그대로
    통과했는데, 그 뒤 `SearchRequest.query`(min_length=1 + 공백 검증)에서 터져
    **500** 으로 나갔다 — 사용자 입력 오류를 서버 오류로 보고하는 셈이었다.
    검증을 경계로 올려 두 계약을 맞춘다.
    """

    question: str = Field(min_length=1,
                          examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    # ★설명이 **폐기된 정책**을 담고 있었다(2026-08-25 정정). 「관계는 양끝이
    #   모두 이 안에 있어야 한다」는 hard filter 는 2026-08-20 에 뒤집혔다 —
    #   워크스페이스 밖의 관련 정보가 통째로 사라졌기 때문이다(설계서 §3).
    #   지금은 **후보를 지우지 않고 순서만** 정한다. 동작은 그때 이미 바뀌었고
    #   설명만 옛 정책에 남아 있었다.
    # ★**비어도 된다**(최종 설계 §6-1·§17-1). 전에는 「비면 답하지 않는다」는
    #   게이트가 있었는데, 그건 워크스페이스를 **검색 경계**로 본 정책이었다.
    #   워크스페이스가 없으면 Global Search 를 하고 Global Ranking 으로 답한다.
    workspace_keys: list[str] = Field(
        default_factory=list,
        description="현재 워크스페이스에 담긴 기업의 **`corp_code` 배열. 선택이다** — "
                    "비면 Global Search + Global Ranking 으로 답한다(최종 설계 §6-1). "
                    "**필터가 아니라 랭킹 문맥이다** — 워크스페이스 밖 기업도 그대로 "
                    "나오고 순서만 달라진다(설계서 §3). `corp_code` 가 없는 기업은 "
                    "`norm_name` 으로 온다(설계서 §16-1)",
        examples=[["00126380", "00164779"]])
    # ★**「담은 것」이 아니라 「보고 있는 것」이다.** 기업 상세·검색 결과 화면에서
    #   묻는 질문은 대상을 문장에 안 쓴다 — 「이 회사 노조 리스크 어때?」의 「이
    #   회사」는 화면이 알고 문장은 모른다. 그 값을 여기로 받는다.
    #
    # ★`workspace_keys` 와 **갈라 받는 이유**는 둘이 답변에서 하는 일이 다르기
    #   때문이다. 담은 것은 「그래서 내 기업에 무슨 뜻인가」로 답을 좁히는
    #   랭킹 문맥이고, 보고 있는 것은 **대상 그 자체**다. 한 필드로 받으면
    #   `anchor_source` 가 둘을 못 가르고, 프롬프트도 못 가른다.
    #
    # ★**랭킹 문맥으로는 쓰지 않는다**(지금은). 검색 계층에 넘기지 않으므로
    #   `_ring_of`·`workspace_priority` 의 뜻이 안 바뀐다 — 앵커 판정에만 쓴다.
    #   넓힐지는 실측 뒤에 정할 일이다.
    context_keys: list[str] = Field(
        default_factory=list,
        description="사용자가 **지금 보고 있는** 기업의 `corp_code` 배열 — 기업 상세 "
                    "페이지·검색 결과에서 물을 때 화면이 넘긴다. `workspace_keys` 와 "
                    "**별개 축이다**: 질문이 대상을 지정하지 않았을 때 **이쪽만** "
                    "대상이 된다(`anchor_source=context`). 워크스페이스는 대상이 "
                    "되지 않는다 — 없으면 `anchorless` 다. "
                    "`corp_code` 가 없는 기업은 `norm_name` 으로 온다(설계서 §16-1)",
        examples=[["00164742"]])

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


# 사용자가 담아 둔 기업이 조용히 사라지는 것만 막으면 된다.
class ErrorResponse(BaseModel):
    """없는 키는 `404` 지만, **검증에서 제외된 노드는 사유를 함께 보낸다.**"""

    detail: str = Field(examples=["검증 결과 제외된 노드입니다"])
    reason: Optional[str] = Field(None, examples=["관계 0 — 검사가 마지막 엣지를 지운 자리"])
    purged_at: Optional[str] = Field(None, examples=["2026-08-15"])


# ★`CompanyDetail.graph` 가 아래에 정의된 `GraphResponse` 를 가리키므로
#   여기서 한 번 풀어 준다. 안 하면 OpenAPI 생성이 미해결 참조로 죽는다.
CompanyDetail.model_rebuild()
