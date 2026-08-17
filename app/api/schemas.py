"""응답 계약 — **백엔드·프론트가 보는 유일한 정의.**

★왜 문서가 아니라 코드인가 (2026-08-15)

「어떤 주소에 뭘 보내면 뭐가 오는지」를 따로 문서로 쓰면 **코드와 어긋난다.**
pydantic 으로 쓰면 코드가 곧 문서이고, FastAPI 가 `/docs` 를 자동으로 만든다.
필드를 고치면 문서가 같이 바뀐다 — 어긋날 방법이 없다.

★이 파일이 실제로 하는 일은 「무엇을 감출지」다

조회 함수의 결과에는 **내부 검사 흔적**이 붙어 있다. 그대로 내보내면 화면에
새어 나간다. 아래가 그 경계다.

    grounding_suspect        그 관계를 **아예 응답에서 뺀다**
    confidence × 신선도 가중치  `score` 하나로 합쳐서
    신선도 판정               `freshness` 세 값으로
    revenue_trusted=false    금액 빼고 비중만
    ratio_trusted=false      비중 빼고 금액만
    listed_shares.suspect    시가총액·PER·PBR·PSR 을 **안 보낸다**
    corp_code 를 못 믿을 때    corp_code 를 **안 보낸다**

★단위는 한 곳에서만 정한다

    금액   **원** (int)          — 억·백만 단위로 접지 않는다. 화면이 정한다
    비율   **퍼센트** (float)     — 0~100. 0~1 소수로 주지 않는다
    날짜   **ISO 8601** (str)    — "2026-08-14"

  `ratio` 를 0~1 로 준 적이 있어서 18건이 틀렸다. 계약에 못 박는다.

★키 규칙 — 이름을 키로 쓰지 않는다

  법인 명부 118,535건 중 **11.3%가 동명**이다(「신우」 11곳 · 「에스엠」 11곳).
  그래서 `GET /companies/{이름}` 같은 주소는 만들지 않는다. 이름은 `/search`
  로만 받고, 거기서 고른 `key` 를 다시 보낸다.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════
#  공통 — 모든 응답이 지고 다니는 것
# ══════════════════════════════════════════════════════════════════


class DetailLevel(str, Enum):
    """이 기업에 대해 **얼마나 아는가.**

    ★이게 없으면 프론트가 「엔비디아는 재무가 없다」를 **오류로 처리한다.**
      실제 분포(2026-08-15): 전체 3,432곳 중 재무 477 · 시세 427 · 공시 64.
      **대부분은 이름·업종·관계만 있는 게 정상이다.**
    """

    full = "full"                      # 재무·공시·사업부문까지 (시드 64곳)
    relations_only = "relations_only"   # 관계와 기본 정보만 (해외·비상장 다수)
    none = "none"                      # 이름만 아는 노드


class Coverage(str, Enum):
    """수집이 **끝까지 갔는가.**

    `partial` 은 구글 차단이나 상한에 걸려 중간에 멈춘 것이다. 화면이
    「이 회사는 자료가 적네」와 「아직 덜 모았네」를 구별해야 한다.
    """

    complete = "complete"
    partial = "partial"


class Freshness(str, Enum):
    """관계가 **언제 관측됐는지**의 판정. 모델이 아니라 산수다.

        expired   종료가 확인됨 또는 유효기간 경과      가중치 0.3
        current   `refresh_cycle_days × 1.5` 이내      가중치 1.0
        stale     그 이상                            가중치 0.6
        unknown   날짜 정보 없음                      가중치 0.7

    ★뉴스는 관계의 **시작만 보도하고 종료는 보도하지 않는다.** 그래서 오래된
      관계를 지우면 살아 있는 관계를 잃고, 그대로 두면 끝난 관계를 현재형으로
      말하게 된다. 지우지 않고 **언제 봤는지**를 함께 보낸다.
    """

    current = "current"
    stale = "stale"
    expired = "expired"
    unknown = "unknown"


class EntityKind(str, Enum):
    """노드가 **무엇인가.**

    ⚠ **해외 여부는 이 값으로 판정하면 안 된다.** `해외` 는 9곳뿐이고
      엔비디아·TSMC·ASML 이 전부 `기업` 으로 들어가 있다. 해외를 가려내는
      확실한 필드가 아직 없어서, 화면도 **「해외 기업」이라고 단정해 표시하지
      않는다.**
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
        None, description="★**화면에 쓸 업종 이름.** 코드는 사용자가 못 읽는다",
        examples=["전자부품·컴퓨터·영상·음향·통신장비"])
    detail_level: DetailLevel = DetailLevel.relations_only
    matched_on: Literal["name", "alias", "corp_code"] = Field(
        "name", description="무엇으로 걸렸나. `alias` 면 옛 표기로 찾아진 것")
    degree: int = Field(0, description="관계 수. 같은 이름이 여럿일 때 **정렬 기준**")


class SearchResponse(BaseModel):
    """**부분 일치**로 찾는다. 「삼성」을 치면 삼성으로 시작하는 회사가 전부 나온다.

    ★결과가 두 종류 섞여 있다.

        in_graph = true    우리가 수집했다 — 관계·사건·재무를 조회할 수 있다
        in_graph = false   DART 명부에만 있다 — **실재하지만 자료가 없다**

      「있는데 아직 안 모았다」와 「그런 회사가 없다」는 완전히 다른 말이라
      후자를 전자로 답하면 안 된다.

    ★`total` 은 **가져온 수가 아니라 있는 수**다. 명부 쪽은 한 번에 50건까지만
      실어 보내므로 `total` 과 `len(hits)` 가 다를 수 있다 — 화면은
      「295건 중 20건」이라고 쓰면 된다.
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
    """관계 한 건.

    ★**근거가 없는 관계는 여기 오지 않는다.** 검증에서 걸린 것은 응답에서
      아예 빠진다. 파급 계산도 마찬가지 — 근거 없는 관계 하나가
      **없는 파급을 만들어 내기** 때문이다.
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

    evidence_id: str = Field(examples=["ev_1fb80cd92f197c6a"])
    text: str = Field(description="원문 1~2문장. 우리가 요약하지 않은 것",
                      examples=["한미반도체는 SK하이닉스와 단일판매·공급계약을 체결하였다. "
                                "계약금액은 44,200,000,000원이다."])
    source_doc: str = Field(description="기사 URL 또는 DART 접수번호",
                            examples=["20260608800436"])
    source_type: Literal["dart", "dart_filing", "news"] = "dart"
    press: Optional[str] = Field(None, examples=["전자신문"])
    published_at: Optional[str] = Field(None, examples=["2026-06-08"])


class PropagationStep(BaseModel):
    key: str
    name: str
    edge_type: EdgeType


class Propagation(BaseModel):
    """리스크 파급 한 갈래. **저장하지 않고 질의 시점에 계산한다.**"""

    key: str = Field(examples=["00164779"])
    name: str = Field(examples=["한미반도체"])
    score: float = Field(ge=0, le=1, examples=[0.71])
    hops: int = Field(examples=[2])
    path: list[PropagationStep] = Field(default_factory=list,
                                        description="어떤 관계를 타고 닿았는지")


class RelationDetail(BaseModel):
    """선을 클릭했을 때 — 워크스페이스 좌 패널."""

    relation: Relation
    evidence: list[Evidence] = Field(default_factory=list)
    propagation: list[Propagation] = Field(
        default_factory=list, description="이 연결을 타고 닿는 위험")


# ══════════════════════════════════════════════════════════════════
#  기업
# ══════════════════════════════════════════════════════════════════


class FinancialYear(BaseModel):
    """★`fs_div` 를 반드시 함께 본다. **연결과 별도는 비교하면 안 된다.**"""

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
    """사업부문.

    ★사업보고서 표는 **단위가 표 밖 캡션에 있어** 자주 1,000배 틀린다.
      못 믿는 값은 **아예 보내지 않는다** — 56개사 중 39개사가 어긋나 있었다.
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
    """시장 지표.

    ★**저장하지 않고 조회할 때 계산한다.** 종가·유통주식수·재무에서 나오는
      값이라 저장하면 원본과 어긋난다.

    ★`fin_year`·`fs_div` 가 **값과 함께 나가는 이유** — 「PER 35.4」만 주면
      화면이 근거를 못 밝힌다. 남의 API 를 안 쓰는 이유도 이것이다(연결인지
      별도인지, 어느 분기 실적인지를 모른다).

    ★유통주식수를 못 믿는 기업은 이 블록이 **통째로 `null`** 이다. DART 원본이
      틀리는 경우가 있어(공시 단위 오류) 그대로 두면 시총 순위가 뒤집힌다.
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


class MarketUnavailable(str, Enum):
    """시장 정보가 **왜 없는가.** 셋이 완전히 다른 말이다.

        unlisted           비상장이라 **원래 없다**       → 「비상장」
        not_collected      상장인데 **아직 안 받았다**     → 「미수집」
        unreliable_shares  주식수를 못 믿는다             → 「확인 중」

    화면이 이걸 구분해야 한다. 「비상장」은 영원히 안 채워지지만
    「미수집」은 배치를 돌리면 채워진다.
    """

    unlisted = "unlisted"
    not_collected = "not_collected"
    unreliable_shares = "unreliable_shares"


class MarketResponse(BaseModel):
    """★상장사 427곳에만 있다. 없을 때 **왜 없는지**를 함께 보낸다."""

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


class Event(BaseModel):
    """사건.

    ★**날짜는 사건 노드가 아니라 관계에 있다.** 같은 사건이라도 기업마다
      엮인 시점이 다를 수 있어서다. 그래서 `occurred_at` 이 여기 온다.
    """

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
    evidence_ids: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    """★본문은 **저장하지 않는다**(저작권). 제목·출처·링크까지만."""

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


class OwnershipItem(BaseModel):
    """★지배구조는 **양방향**이다. 같은 `OWNS_STAKE_IN` 을 들어오는 방향과
    나가는 방향으로 나눠 보여준다."""

    key: str
    name: str = Field(examples=["국민연금공단"])
    label: NodeLabel = NodeLabel.Company
    ratio: Optional[float] = Field(None, description="**퍼센트**", examples=[7.51])
    subtype: Optional[str] = Field(None, examples=["5%이상주주"])


class ProductItem(BaseModel):
    """★`source` 가 **성격을 가른다.**

        dart   사업보고서에서 나온 것 — 이미 **매출을 내는 제품**
        news   기사에서 나온 것 — **개발 중일 수 있다**

    화면이 「제품」과 「개발」로 나눠 보여주는 근거가 이 필드다.
    실측: dart 397건 · news 1,653건.
    """

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
        None, description="★**화면에 쓸 업종 이름.** 숫자를 그대로 보여주지 않는다",
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
    """워크스페이스 좌 패널 — **「지금 보는 그래프 안에서의 이 회사」.**

    ★`company_detail` 과 다른 화면이다. 이쪽은 **담아 둔 다른 기업들과 어떻게
      이어지는지**를 말한다. 그래서 요청에 `workspace_keys` 가 필요하다 —
      기업 키만으로는 답이 안 나온다.
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
        None, description="★상장사만. `CompanyBase.market`(KOSPI/KOSDAQ)과 **다른 필드**다 —"
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


class DetailBlocks(BaseModel):
    """★`detail_level` 하나로는 부족하다.

    「재무는 있는데 공시가 없다」가 흔한데, 통짜 등급으로는 표현이 안 된다.
    화면이 블록마다 접거나 「미수집」을 적으려면 **블록별로** 알아야 한다.
    """

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


class DetailCounts(BaseModel):
    """화면 머리의 숫자 줄. **한 번에 세어 보낸다** — 화면이 배열 길이를 세면
    상한에 걸린 목록에서 틀린 수가 나온다."""

    relations: int = Field(0, examples=[46])
    related_companies: int = Field(0, examples=[18])
    events: int = Field(0, examples=[4])
    risk_events: int = Field(0, examples=[2])
    news: int = Field(0, examples=[18])
    filings: int = Field(0, examples=[3])


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
        None, description="★상장사만. 비상장이면 `null` 이 정상이다")
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


# ══════════════════════════════════════════════════════════════════
#  그래프
# ══════════════════════════════════════════════════════════════════


class GraphNode(BaseModel):
    """★`role` 이 **담은 기업과 다리 노드를 가른다.** 프론트가 다리를 흐리게
    그리면 「내가 담은 건 이거고, 이어주려고 딸려온 건 저거」가 한눈에 보인다."""

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
    """워크스페이스 캔버스 · 기업 상세의 관계 그래프.

    ★**허브를 다리로 쓰지 않는다.** 삼성전자(연결 150+)를 다리로 허용하면
      거의 모든 쌍이 이어져 **의미가 0인 그래프**가 된다. 자산운용사도 마찬가지 —
      양쪽에 지분이 있을 뿐 두 회사가 관계있다는 뜻이 아니다.

    ★그래도 못 이으면 **섬으로 두고 표시한다.** 없는 관계를 그리는 것보다
      없다고 말하는 게 낫다.
    """

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    islands: list[str] = Field(
        default_factory=list,
        description="**화면에 그려질 선이 하나도 없는 노드.** `refs` 를 켜면 참조 기업이 "
                    "이어 주므로 줄어든다. 담은 기업끼리 직접 연결이 없는지는 엣지의 "
                    "양 끝이 모두 담은 기업인 것을 세어 알 수 있다")
    truncated: bool = Field(False, description="`max_nodes` 에 걸려 잘렸나")
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

    companies: list[RelationEndpoint] = Field(default_factory=list)
    event: Optional[RelationEndpoint] = None
    category: Optional[str] = Field(None, description="공급망 · 지분 · 규제 · 사건",
                                    examples=["규제"])
    is_risk: bool = False


class NewsFeedResponse(BaseModel):
    """★필터가 **세 축**이다. 한 줄에 몰면 「위험만」과 「내 워크스페이스」가
    서로 배타적인지 겹쳐 쓸 수 있는지 알 수 없다."""

    total: int = 0
    items: list[NewsFeedItem] = Field(default_factory=list)


class TrendingItem(BaseModel):
    key: str
    name: str = Field(examples=["삼성전자"])
    label: NodeLabel = NodeLabel.Company
    mention_count: int = Field(description="최근 언급 수", examples=[42])


class InsightCard(BaseModel):
    """홈의 「이 회사에 이런 일이 있었다」 카드.

    ★`why` 를 반드시 함께 보낸다. **왜 그렇게 봤는지 없이 결론만 주면
      사용자가 검증할 방법이 없다.**
    """

    key: str
    name: str = Field(examples=["SK하이닉스"])
    headline: str = Field(examples=["최근 3개월 리스크 사건 2건"])
    why: str = Field(description="근거 한 줄", examples=["화재·라인 전환 연기가 같은 분기에 보도됨"])
    event_ids: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


class RetrieveResponse(BaseModel):
    """추론 계층이 쓰는 재료.

    ★**답변을 만들지 않는다.** 사실과 근거만 준다 — 문장 생성은 추론 담당 몫이고,
      경계를 섞으면 「누가 지어냈나」를 못 가린다.
    """

    question: str = Field(examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    companies: list[RelationEndpoint] = Field(default_factory=list, description="질문에서 찾아낸 기업")
    events: list[Event] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    propagation: list[Propagation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list,
                                     description="**인용에 쓸 원문.** 답변에 근거 id 를 반드시 붙인다")


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


class Suggestion(BaseModel):
    """「같이 담을 만한 기업」 한 건.

    ★**왜 추천인지를 반드시 함께 준다.** 「한미반도체를 추천합니다」만으로는
      사용자가 담을 이유를 모른다. 「공통 고객 4곳 — 삼성전자·SK하이닉스·
      마이크론·엔비디아」까지 줘야 판단이 된다.
    """

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
    detail_level: DetailLevel = DetailLevel.relations_only
    ksic_label: Optional[str] = None


class WorkspaceSuggestResponse(BaseModel):
    """★**담은 기업이 1곳일 때 가장 쓸모 있다.** 「한 곳만 담으면 관계가 안 보인다」가
    워크스페이스의 첫 벽이고, 이게 그 벽을 넘겨 준다."""

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
    """**백엔드가 주기적으로 물어보는 자리.** 마이페이지 알림 셋이 여기서 나온다.

        새 위험 사건    new_risk_event
        새 관계        new_relation
        관계 종료      relation_ended

    ★알림을 **누구에게 보낼지는 우리가 모른다.** 키 목록을 받아 「그동안 뭐가
      바뀌었나」만 답한다. 사용자·구독 설정은 백엔드 것이다.

    ★`relation_ended` 는 지금 **판정할 수 없다.** `loaded_at` 이 2026-07-31에
      도입돼 비교 대상이 없어서, 다음 DART 재적재 전까지는 언제나 빈 배열이다.
      필드는 **미리 열어 둔다** — 나중에 채워질 때 계약이 안 바뀌게.
    """

    since: str
    total: int = 0
    changes: list[Change] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    workspace_keys: list[str] = Field(default_factory=list,
                                      description="있으면 그 범위로 좁혀 찾는다")


class ErrorResponse(BaseModel):
    """★없는 키는 `404` 지만, **검증에서 제외된 노드는 사유를 함께 보낸다.**
    사용자가 담아 둔 기업이 조용히 사라지는 것만 막으면 된다."""

    detail: str = Field(examples=["검증 결과 제외된 노드입니다"])
    reason: Optional[str] = Field(None, examples=["관계 0 — 검사가 마지막 엣지를 지운 자리"])
    purged_at: Optional[str] = Field(None, examples=["2026-08-15"])
