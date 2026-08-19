"""스텁이 돌려주는 **고정 응답.**

★모든 값이 실제 DB 에서 나온 것이다 (2026-08-16 측정)

  지어낸 값을 쓰면 프론트가 **현실에 없는 모양**을 전제로 화면을 만든다.
  그리고 실제로 그런 일이 있었다 — 이전 판에서 `corp_code` 8개를 지어냈고
  (`00121932` 를 한미반도체라고 썼는데 그런 노드가 없다), 그중 하나는
  **다른 회사를 가리키고** 있었다(`00164645` → 실제로는 HMM).
  counts 도 넷 중 셋이 틀렸다(연관기업 98 vs 실제 443 …).

  라우트가 200을 돌려준다는 것만 확인했지 **값이 맞는지는 아무도 안 봤던 것**이다.
  그래서 `batch/audit/api_contract.py` 를 만들어 이 파일의 값을 DB 와 대조한다.

★심텍을 기본 예시로 쓰는 이유

  목업이 심텍으로 그려져 있고, **시세·재무·사업부문·공시·사건이 전부 있는**
  몇 안 되는 기업이라 화면의 모든 블록을 채울 수 있다. 게다가 적자라
  **PER 이 `null` 인 경우**까지 자연스럽게 보여준다.

★여기서 제일 중요한 건 정상 응답이 아니라 **가장자리**다

  정상 응답만 주면 프론트가 정상 경우만 만들고, 진짜 데이터를 붙이는 날
  화면이 깨진다. 그래서 아래를 일부러 넣어 뒀다:

      엔비디아     detail_level=none · 재무도 시세도 없음
      비상장사     listed=false — 시장 블록이 통째로 null
      심텍 PER     적자라 null
      고영        워크스페이스에서 **섬**이 되는 기업

  실제 분포가 이걸 요구한다: Company 3,432곳 중 재무 477(14%) · 시세 417(12%) ·
  공시 64(1.9%). **대부분은 이름·업종·관계만 있는 게 정상이다.**
"""

from __future__ import annotations

from app.api.schemas import (
    Change, CompanyDetail, CompanySummary, DetailBlocks, DetailCounts,
    Evidence, Event, EventTimelinePhase,
    FinancialYear, Filing, GraphEdge, GraphNode, GraphResponse,
    MarketMetrics, MarketPoint, MarketResponse, NewsFeedItem, NewsFeedResponse,
    NewsItem, OwnershipItem, ProductItem, Propagation, EdgePropagation,
    Relation, RelationDetail, RelationEndpoint, RiskEvent, Segment,
    SharedCustomer, Suggestion, TrendingItem,
)

# ── 자주 쓰는 노드 (전부 실재하는 key) ──────────────────────────
SIMMTECH = RelationEndpoint(key="01095722", name="심텍")
HYNIX = RelationEndpoint(key="00164779", name="SK하이닉스")
SAMSUNG = RelationEndpoint(key="00126380", name="삼성전자")
HANMI = RelationEndpoint(key="00161383", name="한미반도체")
NVIDIA = RelationEndpoint(key="엔비디아", name="엔비디아")
KOHYOUNG = RelationEndpoint(key="00579999", name="고영")

# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════

# 심텍 → SK하이닉스. evidence_id·날짜·신뢰도·주기 전부 실제 엣지 값
REL_SUPPLY = Relation(
    edge_id="ev_17acfbf5a4041e59", type="SUPPLIES_TO", subtype="공급",
    source=SIMMTECH, target=HYNIX,
    freshness="current", last_seen="2026-04-06", valid_from="2026-04-06",
    score=0.9, corroboration=1, source_type="news",
    refresh_cycle_days=180, days_since=132, days_until_refresh=48,
    # ★독점이 아니다 — 심텍은 삼성전자·엔비디아에도 판다
    exclusive=False,
    other_counterparties=["삼성전자", "엔비디아"],
)

REL_STAKE = Relation(
    edge_id="ev_ce998e6292d4ee35", type="OWNS_STAKE_IN", subtype="최대주주",
    source=RelationEndpoint(key="00152127", name="심텍홀딩스"), target=SIMMTECH,
    ratio=33.19, freshness="current", last_seen="2026-06-25",
    score=1.0, corroboration=1, source_type="dart",
    refresh_cycle_days=365,
)

# ★오래됐지만 지우지 않는다 — 뉴스는 관계의 종료를 보도하지 않는다
REL_STALE = Relation(
    edge_id="ev_5c21c7674b2c7416", type="SUPPLIES_TO", subtype="소캠2",
    source=SIMMTECH, target=NVIDIA,
    freshness="current", last_seen="2026-07-01",
    score=0.8, corroboration=1, source_type="news",
    refresh_cycle_days=180, days_since=46, days_until_refresh=134,
    exclusive=False, other_counterparties=["SK하이닉스", "삼성전자"],
)

EVIDENCE_SUPPLY = Evidence(
    evidence_id="ev_17acfbf5a4041e59",
    text="현재 삼성전자, SK하이닉스 등 글로벌 '빅5' 메모리 칩 메이커를 비롯해 "
         "세계 유수의 반도체 패키징 전문 기업들을 주요 고객사로 확보하고 있다.",
    source_doc="https://www.pinpointnews.co.kr/news/articleView.html?idxno=286034",
    source_type="news", press="핀포인트뉴스", published_at="2026-04-06",
)

# ★`/relations/{edge_id}` 는 **실제 데이터로 구현됐다.** 이 예시는 계약 문서용으로만
#   남는다 — 실측값(2026-08-18): 담합 혐의 피소 → 마이크론 0.900(보도) ·
#   AMD 0.222(2홉 계산).
RELATION_DETAIL = RelationDetail(
    relation=REL_SUPPLY,
    evidence=[EVIDENCE_SUPPLY],
    propagation=[
        EdgePropagation(
            event_id="evt_news_0e95c793dc87", event="담합 혐의 피소",
            target="삼성전자", key="00126380", score=0.48, hops=1,
            stated=True, channel=None,
            path=["담합 혐의 피소", "IMPACTS(negative)", "삼성전자"]),
    ],
)

RELATIONS_OF = [REL_SUPPLY, REL_STAKE, REL_STALE]

# ══════════════════════════════════════════════════════════════════
#  사건 — 실제 event_id
# ══════════════════════════════════════════════════════════════════

EVENT_QUALITY = Event(
    event_id="evt_news_1664a8f17eed", name="심텍 제품 품질 문제 내부고발",
    event_type="품질", is_risk=True, role="subject",
    occurred_at="2023-01-20", article_count=1,
    evidence_ids=["ev_1b5371d5f1fa7f82"],
)

EVENT_REPACK = Event(
    event_id="evt_news_34d7bfaeea8c", name="포장갈이 의혹",
    event_type="품질", is_risk=True, role="subject",
    occurred_at="2023-03-14", article_count=1,
    evidence_ids=["ev_75343d3ec1516cd8"],
)

# ★timeline 이 붙은 사건 — 4년에 걸친 국면이 한 노드에 모인 예
EVENT_TAYLOR = Event(
    event_id="evt_news_857e2add6b7f", name="테일러 팹",
    event_type="사업확장", is_risk=False, role="subject",
    occurred_at="2026-07-15", article_count=2,
    timeline=[
        EventTimelinePhase(period="2021-11", name="테일러시 신규 공장"),
        EventTimelinePhase(period="2022-04", name="테일러공장"),
        EventTimelinePhase(period="2025-08", name="테일러 공장 가동 준비"),
        EventTimelinePhase(period="2026-07", name="미국 테일러 팹2 착공"),
    ],
)

EVENTS_OF = [EVENT_QUALITY, EVENT_REPACK]

# ══════════════════════════════════════════════════════════════════
#  시장 — ★심텍은 적자라 PER 이 null 이다
# ══════════════════════════════════════════════════════════════════

MARKET_SIMMTECH = MarketMetrics(
    trade_date="2026-08-14", close_price=108700, change_pct=2.74, volume=861169,
    listed_shares=37333952, market_cap=4058200000000,
    per=None,          # ★2025년 순이익 −1,646억 → 나눌 수 없다
    pbr=7.04, psr=2.88, fin_year=2025, fs_div="CFS",
)

MARKET_RESPONSE = MarketResponse(
    key="01095722", listed=True, stock_code="222800",
    unavailable_reason=None, latest=MARKET_SIMMTECH,
    series=[
        MarketPoint(trade_date="2026-08-12", close_price=104600, change_pct=-8.17,
                    volume=1925331),
        MarketPoint(trade_date="2026-08-13", close_price=105800, change_pct=1.15,
                    volume=812004),
        MarketPoint(trade_date="2026-08-14", close_price=108700, change_pct=2.74,
                    volume=861169),
    ],
)

# ★상장 표시가 없는 2,980곳이 이 모양이다. **오류가 아니다**
MARKET_UNLISTED = MarketResponse(key="엔비디아", listed=False, stock_code=None,
                                 unavailable_reason="unlisted", latest=None, series=[])

# ══════════════════════════════════════════════════════════════════
#  재무 — 원 단위 그대로, 비율은 계산해서
# ══════════════════════════════════════════════════════════════════

FIN_2025 = FinancialYear(
    bsns_year=2025, fs_div="CFS", revenue=1410559919451,
    operating_profit=11868602919, net_profit=-164580357473,
    total_assets=1620329743829, total_liabilities=1043963591400,
    total_equity=576366152429,
    debt_ratio=181.13, roe=-28.55, roa=-10.16, operating_margin=0.84,
)
FIN_2024 = FinancialYear(
    bsns_year=2024, fs_div="CFS", revenue=1231421381070,
    operating_profit=-46969220820, net_profit=-31044704373,
    total_assets=1443279296879, total_liabilities=994803895879,
    total_equity=448475401000,
    debt_ratio=221.82, roe=-6.92, roa=-2.15, operating_margin=-3.81,
)
FIN_2023 = FinancialYear(
    bsns_year=2023, fs_div="CFS", revenue=1041895724977,
    operating_profit=-88124119458, net_profit=-115143871777,
    total_assets=1176060549354, total_liabilities=705622729689,
    total_equity=470437819665,
    debt_ratio=149.99, roe=-24.48, roa=-9.79, operating_margin=-8.46,
)

# ══════════════════════════════════════════════════════════════════
#  고객 공유 · 추천 — 실측 Cypher 결과
# ══════════════════════════════════════════════════════════════════

# MATCH (m:Company {name:'심텍'})-[:SUPPLIES_TO]->(c)<-[:SUPPLIES_TO]-(peer)
# RETURN peer, count(DISTINCT c) AS shared ORDER BY shared DESC
SHARED = [
    SharedCustomer(key="00535676", name="테크윙", shared_count=3,
                   customers=["삼성전자", "SK하이닉스", "마이크론"]),
    SharedCustomer(key="00572905", name="ISC", shared_count=3,
                   customers=["삼성전자", "SK하이닉스", "마이크론"]),
]

SUGGESTIONS = [
    Suggestion(key="00161383", name="한미반도체", reason="shared_customer",
               reason_text="공통 고객 4곳 — SK하이닉스 · 삼성전자 · 엔비디아 · 마이크론",
               overlap=4, via=["SK하이닉스", "삼성전자", "엔비디아", "마이크론"],
               ksic_label="특수 목적용 기계"),
    Suggestion(key="00246417", name="이오테크닉스", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 마이크론",
               overlap=3, via=["SK하이닉스", "삼성전자", "마이크론"],
               ksic_label="특수 목적용 기계"),
    Suggestion(key="00563545", name="두산테스나", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 엔비디아",
               overlap=3, via=["SK하이닉스", "삼성전자", "엔비디아"],
               ksic_label="전자부품·컴퓨터·영상·음향·통신장비"),
    Suggestion(key="asml", name="ASML", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 마이크론",
               overlap=3, via=["SK하이닉스", "삼성전자", "마이크론"],
               detail_level="none"),
]

# ══════════════════════════════════════════════════════════════════
#  기업 — full 과 none
# ══════════════════════════════════════════════════════════════════

COMPANY_FULL = CompanyDetail(
    key="01095722", name="심텍", detail_level="full", coverage="complete",
    collected_at="2026-08-13", corp_code="01095722", stock_code="222800",
    market="KOSDAQ", entity_kind="기업", ksic="26",
    ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    also_names=[],
    blocks=DetailBlocks(overview="full", financials="full", segments="full",
                        products="full", related="full", risk="full",
                        news="full", filings="partial", ownership="full",
                        market="full"),
    counts=DetailCounts(relations=44, related_companies=13, events=4,
                        risk_events=2, news=18, filings=1),
    overview="PCB(인쇄회로기판) 제조·판매 전문 기업. 주요 고객은 글로벌 메모리칩 제조사",
    business_overview="당사는 반도체용 PCB 를 주력으로 하는 회사로 Package Substrate 와 "
                      "Module PCB 를 생산하고 있습니다. 국내 6개 공장과 R&D 센터를 "
                      "운영하며 중국·일본에 해외 생산법인을 두고 있습니다. …",
    ceo="전영선, 김영구", established_at="2015-07-01",
    name_en="SIMMTECH Co., Ltd.", induty="2622",
    market_metrics=MARKET_SIMMTECH,
    financials=[FIN_2025, FIN_2024, FIN_2023],
    segments=[
        Segment(name="Package Substrate", revenue=1063840000000, revenue_ratio=75.40),
        Segment(name="Module PCB", revenue=346720000000, revenue_ratio=24.60),
    ],
    products=[
        ProductItem(key="packagesubstrate", name="Package Substrate",
                    category="제품", source="dart"),
        ProductItem(key="modulepcb", name="Module PCB", category="제품", source="dart"),
        ProductItem(key="hbm관련기판", name="HBM 관련 기판", category="부품", source="news"),
        ProductItem(key="소캠2", name="소캠2", category="제품", source="news"),
    ],
    executives=[],
    owned_by=[
        OwnershipItem(key="00152127", name="심텍홀딩스", ratio=33.19, subtype="최대주주"),
        OwnershipItem(key="00260453", name="삼성자산운용", ratio=6.93, subtype="5%이상주주"),
        OwnershipItem(key="00706742", name="국민연금공단", ratio=6.09, subtype="5%이상주주"),
    ],
    owns=[
        OwnershipItem(key="01279926", name="글로벌심텍", ratio=98.55, subtype="자회사"),
        OwnershipItem(key="aitech", name="AI TECH Co., Ltd.", ratio=100.0, subtype="자회사"),
    ],
    related=[REL_SUPPLY, REL_STALE],
    events=[EVENT_QUALITY, EVENT_REPACK],
    news=[NewsItem(url="https://www.pinpointnews.co.kr/news/articleView.html?idxno=286034",
                   title="'반도체 혈관' PCB 명가 심텍, 글로벌 빅5 메모리 제조사 사로잡았다",
                   press="핀포인트뉴스", published_at="2026-04-06")],
    filings=[Filing(rcept_no="20260317000641", doc_type="사업보고서",
                    title="사업보고서 (2025.12)", rcept_dt="2026-03-17",
                    url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260317000641")],
)

# ★해외 기업 — 관계는 많은데 재무도 시세도 공시도 없다.
#   **이게 3,432곳 중 대다수의 모양이다.** 프론트가 이걸 오류로 처리하면 안 된다.
COMPANY_RELATIONS_ONLY = CompanyDetail(
    key="엔비디아", name="엔비디아", detail_level="none", coverage="complete",
    collected_at="2026-08-11", corp_code=None, stock_code=None, market=None,
    entity_kind="기업", ksic="26", ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    also_names=["NVIDIA", "엔비디아 코리아"],
    blocks=DetailBlocks(overview="partial", financials="none", segments="none",
                        products="partial", related="full", risk="partial",
                        news="none", filings="none", ownership="none",
                        market="none"),
    counts=DetailCounts(relations=59, related_companies=28, events=3,
                        risk_events=1, news=0, filings=0),
    overview="미국 GPU 및 AI 칩 제조사",
    business_overview=None,
    market_metrics=None, financials=[], segments=[], filings=[],
    products=[ProductItem(key="블랙웰", name="블랙웰", category="제품", source="news")],
    related=[REL_STALE],
    events=[], news=[],
)

COMPANY_SUMMARY = CompanySummary(
    key="01095722", name="심텍", detail_level="full", coverage="complete",
    collected_at="2026-08-13", corp_code="01095722", stock_code="222800",
    market="KOSDAQ", entity_kind="기업", ksic="26",
    ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    overview="PCB(인쇄회로기판) 제조·판매 전문 기업. 주요 고객은 글로벌 메모리칩 제조사",
    ceo="전영선, 김영구", established_at="2015-07-01",
    financials=[FIN_2025, FIN_2024, FIN_2023],
    latest_financial=FIN_2025,
    market_metrics=MARKET_SIMMTECH,
    risk_summary="사건 4건 중 위험 2건", risk_event_count=2,
    workspace_relations=[REL_SUPPLY],
    shared_customers=SHARED,
    recent_news=[NewsItem(url="https://www.pinpointnews.co.kr/news/articleView.html?idxno=286034",
                          title="'반도체 혈관' PCB 명가 심텍, 글로벌 빅5 메모리 제조사 사로잡았다",
                          press="핀포인트뉴스", published_at="2026-04-06")],
)

# ══════════════════════════════════════════════════════════════════
#  그래프 — ★섬이 생기는 예를 일부러 넣는다
# ══════════════════════════════════════════════════════════════════

WORKSPACE_GRAPH = GraphResponse(
    nodes=[
        GraphNode(key="00126380", name="삼성전자", role="pinned", kind="trade",
                  entity_kind="기업", degree=1169,
                  ksic_label="전자부품·컴퓨터·영상·음향·통신장비", can_collect=True),
        GraphNode(key="00164779", name="SK하이닉스", role="pinned", kind="trade",
                  entity_kind="기업", degree=494, can_collect=True),
        GraphNode(key="00161383", name="한미반도체", role="pinned", kind="trade",
                  entity_kind="기업", degree=164, can_collect=True),
        GraphNode(key="01095722", name="심텍", role="pinned", kind="trade",
                  entity_kind="기업", degree=44, can_collect=True),
        # ★고영은 아무와도 안 이어진다. 억지로 잇지 않고 **섬으로 표시한다**
        GraphNode(key="00579999", name="고영", role="pinned", kind="trade",
                  entity_kind="기업", degree=42, is_island=True, can_collect=True),
        # ★참조로 딸려온 노드 — 담은 기업 4곳과 이어진다. corp_code 가 없어
        #   더 받을 데가 없다(can_collect=False)
        GraphNode(key="마이크론", name="마이크론", role="bridge", kind="trade",
                  entity_kind="기업", degree=78, members=4, risk_weight=4,
                  can_collect=False),
    ],
    edges=[
        GraphEdge(edge_id="ev_17acfbf5a4041e59", type="SUPPLIES_TO", subtype="공급",
                  source="01095722", target="00164779", freshness="current", score=0.9),
        GraphEdge(edge_id="ev_6c4b02e8d15f7a93", type="COMPETES_WITH", subtype="DRAM market",
                  source="00126380", target="00164779", symmetric=True,
                  freshness="current", score=0.9),
        GraphEdge(edge_id="ev_8e0d3a7b91c26f45", type="SUPPLIES_TO", subtype="exclusive",
                  source="00161383", target="00164779", freshness="current", score=1.0),
    ],
    islands=["00579999"],
    truncated=False,
    omitted={"OWNS_STAKE_IN": 9, "IS_EXECUTIVE_OF": 8},
)

COMPANY_GRAPH = GraphResponse(
    nodes=[
        GraphNode(key="01095722", name="심텍", role="pinned", kind="trade",
                  entity_kind="기업", degree=44),
        GraphNode(key="00164779", name="SK하이닉스", role="neighbor", kind="trade",
                  entity_kind="기업", degree=494),
        GraphNode(key="00152127", name="심텍홀딩스", role="neighbor", kind="ownership",
                  entity_kind="기업", degree=7),
        GraphNode(key="evt_news_1664a8f17eed", name="심텍 제품 품질 문제 내부고발",
                  label="Event", role="neighbor", kind="event", degree=1),
        GraphNode(key="packagesubstrate", name="Package Substrate", label="Product",
                  role="neighbor", kind="product", degree=1),
    ],
    edges=[
        GraphEdge(edge_id="ev_17acfbf5a4041e59", type="SUPPLIES_TO", subtype="공급",
                  source="01095722", target="00164779", freshness="current", score=0.9),
        GraphEdge(edge_id="ev_ce998e6292d4ee35", type="OWNS_STAKE_IN", subtype="최대주주",
                  source="00152127", target="01095722", freshness="current", score=1.0),
        GraphEdge(edge_id="ev_1b5371d5f1fa7f82", type="HAS_EVENT",
                  source="01095722", target="evt_news_1664a8f17eed",
                  freshness="stale", score=0.54),
    ],
    islands=[], truncated=False,
    omitted={"IS_EXECUTIVE_OF": 8},
)

# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈 · 알림
# ══════════════════════════════════════════════════════════════════

NEWS_OF = [
    NewsItem(url="https://www.pinpointnews.co.kr/news/articleView.html?idxno=286034",
             title="'반도체 혈관' PCB 명가 심텍, 글로벌 빅5 메모리 제조사 사로잡았다",
             press="핀포인트뉴스", published_at="2026-04-06"),
]

FILINGS_OF = [
    Filing(rcept_no="20260317000641", doc_type="사업보고서", title="사업보고서 (2025.12)",
           rcept_dt="2026-03-17",
           url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260317000641"),
]

NEWS_FEED = NewsFeedResponse(
    total=2,
    items=[
        NewsFeedItem(url="https://www.pinpointnews.co.kr/news/articleView.html?idxno=286034",
                     title="'반도체 혈관' PCB 명가 심텍, 글로벌 빅5 메모리 제조사 사로잡았다",
                     press="핀포인트뉴스", published_at="2026-04-06",
                     companies=[SIMMTECH, HYNIX], category="공급망", is_risk=False),
        NewsFeedItem(url="https://www.newslock.co.kr/news/articleView.html?idxno=140312",
                     title="'일주일 만에 또'…SK하이닉스, 청주 M15X 화재로 8명 병원 이송",
                     press="뉴스락", published_at="2026-08-04",
                     companies=[HYNIX], category="사고", is_risk=True),
    ],
)

RISK_EVENTS = [
    RiskEvent(event_id="evt_news_1664a8f17eed", name="심텍 제품 품질 문제 내부고발",
              event_type="품질", occurred_at="2023-01-20", affected_count=1,
              affected=[SIMMTECH], article_count=1),
    RiskEvent(event_id="evt_news_34d7bfaeea8c", name="포장갈이 의혹", event_type="품질",
              occurred_at="2023-03-14", affected_count=1, affected=[SIMMTECH],
              article_count=1),
]

TRENDING = [
    TrendingItem(key="00126380", name="삼성전자", mention_count=1169),
    TrendingItem(key="00164779", name="SK하이닉스", mention_count=494),
    TrendingItem(key="00161383", name="한미반도체", mention_count=164),
]

# ★`INSIGHTS` 는 지웠다 — `/insights` 가 실제 데이터로 구현됐다.
#   계약 예시는 `schemas.InsightCard` 의 `examples=` 에 있다.

# ★`/events/{id}/impact` 도 실제 데이터로 구현됐다. 아래는 계약 문서용 예시로,
#   **보도된 것 하나와 계산한 것 하나**를 나란히 두어 `stated` 의 뜻을 보인다.
PROPAGATION = [
    Propagation(target="마이크론", key=None, score=0.9, hops=1,
                stated=True, channel=None,
                path=["담합 혐의 피소", "IMPACTS(negative)", "마이크론"]),
    Propagation(target="AMD", key=None, score=0.222, hops=2,
                stated=False, channel="supply",
                path=["2분기 영업손실 7000억", "IMPACTS(negative)", "삼성전자",
                      "SUPPLIES_TO(공급 차질)", "AMD"]),
]

# ★relation_ended 가 여기 없는 게 정상이다. loaded_at 이 2026-07-31 도입이라
#   비교 대상이 없어 아직 판정할 수 없다 — 다음 DART 재적재 이후에 생긴다.
CHANGES = [
    Change(kind="new_risk_event", company_key="00164779", company_name="SK하이닉스",
           title="청주 M15X 화재로 8명 병원 이송", detail="사고재해 · 영향 33곳",
           occurred_at="2026-08-04", ref_id="evt_news_8f21c04ab735"),
    Change(kind="new_relation", company_key="01095722", company_name="심텍",
           title="심텍 → 엔비디아 공급 관계가 새로 확인됨",
           detail="SUPPLIES_TO · 근거 1건", occurred_at="2026-07-01",
           ref_id="ev_5c21c7674b2c7416"),
]
