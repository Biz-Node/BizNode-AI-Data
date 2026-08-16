"""스텁이 돌려주는 **고정 응답.**

★값이 전부 실제 데이터에서 뽑은 것이다 (2026-08-15 측정)

  지어낸 값을 쓰면 프론트가 **현실에 없는 모양**을 전제로 화면을 만든다.
  삼성전자 시가총액 1,599.7조·PER 35.39 는 우리 `market_metrics` 뷰가
  실제로 돌려주는 값이고, 재무도 `financials` 원본 그대로다.

★여기서 제일 중요한 건 **정상 응답이 아니라 가장자리**다

  정상 응답만 주면 프론트가 정상 경우만 만들고, 진짜 데이터를 붙이는 날
  화면이 깨진다. 그래서 아래를 일부러 넣어 뒀다:

      NVIDIA        detail_level=relations_only · 재무도 시세도 없음
      「신우」        동명 법인 여러 건
      「없는회사」     검색 0건
      고영           워크스페이스에서 **섬**이 되는 기업
      비상장사        listed=false — 시장 블록이 통째로 null

  실제 분포가 이걸 요구한다: Company 3,432곳 중 재무 477(14%) · 시세 427(12%) ·
  공시 64(1.9%). **대부분은 이름·업종·관계만 있는 게 정상이다.**
"""

from __future__ import annotations

from app.api.schemas import (
    Change, CompanyDetail, CompanySummary, DetailBlocks, DetailCounts,
    Evidence, Event, EventTimelinePhase,
    FinancialYear, Filing, GraphEdge, GraphNode, GraphResponse, InsightCard,
    MarketMetrics, MarketPoint, MarketResponse, NewsFeedItem, NewsFeedResponse,
    NewsItem, OwnershipItem, ProductItem, Propagation, PropagationStep,
    Relation, RelationDetail, RelationEndpoint, RiskEvent, Segment,
    SharedCustomer, Suggestion, TrendingItem,
)

# ── 자주 쓰는 노드 ───────────────────────────────────────────────
SAMSUNG = RelationEndpoint(key="00126380", name="삼성전자")
HYNIX = RelationEndpoint(key="00164779", name="SK하이닉스")
HANMI = RelationEndpoint(key="00121932", name="한미반도체")
NVIDIA = RelationEndpoint(key="엔비디아", name="엔비디아")
KOHYOUNG = RelationEndpoint(key="00145261", name="고영")

# ══════════════════════════════════════════════════════════════════
#  검색 — 세 가지 결과 모양
# ══════════════════════════════════════════════════════════════════

# ★검색 예시는 없앴다. `/search` 가 실제 DB 를 읽으므로 고정 응답이 필요 없고,
#   두면 **현실과 어긋난 예시**가 남는다 — 실제로 「신우 3건」이라는 없는 예시를
#   만들어 뒀다가 고쳤다(그래프 안에 동명 노드는 0건이다).

# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════

REL_SUPPLY = Relation(
    edge_id="ev_a3f21c8e5b90d417", type="SUPPLIES_TO", subtype="TC 본더",
    source=HANMI, target=HYNIX, amount=44200000000,
    freshness="current", last_seen="2026-06-08", valid_from="2026-06-08",
    score=1.0, corroboration=3, source_type="dart",
    refresh_cycle_days=180, days_since=69, days_until_refresh=111,
    exclusive=False,
    other_counterparties=["삼성전자", "엔비디아", "마이크론"],
)

REL_STAKE = Relation(
    edge_id="ev_7d10b4a9f2e35c68", type="OWNS_STAKE_IN", subtype="5%이상주주",
    source=RelationEndpoint(key="00365493", name="국민연금공단"), target=SAMSUNG,
    ratio=7.51, freshness="current", last_seen="2026-03-11",
    score=1.0, corroboration=1, source_type="dart",
)

# ★오래됐지만 지우지 않는다 — 뉴스는 관계의 종료를 보도하지 않는다
REL_STALE = Relation(
    edge_id="ev_2b8e91f4c07a6d35", type="PARTNERS_WITH", subtype="공동 연구",
    source=SAMSUNG, target=NVIDIA, symmetric=True,
    freshness="stale", last_seen="2024-06-14",
    score=0.54, corroboration=1, source_type="news",
)

EVIDENCE_SUPPLY = Evidence(
    evidence_id="ev_a3f21c8e5b90d417",
    text="한미반도체는 SK하이닉스와 단일판매·공급계약을 체결하였다. "
         "계약금액은 44,200,000,000원이며 최근 매출액 대비 7.66%에 해당한다.",
    source_doc="20260608800436", source_type="dart",
)

RELATION_DETAIL = RelationDetail(
    relation=REL_SUPPLY,
    evidence=[EVIDENCE_SUPPLY],
    propagation=[
        Propagation(key="00126380", name="삼성전자", score=0.44, hops=2,
                    path=[PropagationStep(key="00164779", name="SK하이닉스",
                                          edge_type="SUPPLIES_TO"),
                          PropagationStep(key="00126380", name="삼성전자",
                                          edge_type="COMPETES_WITH")]),
    ],
)

# ══════════════════════════════════════════════════════════════════
#  사건
# ══════════════════════════════════════════════════════════════════

EVENT_RAID = Event(
    event_id="evt_news_c915fa8bf141", name="삼성전자 본사 압수수색",
    event_type="규제수사", is_risk=True, role="subject",
    occurred_at="2026-06-11", article_count=2,
    timeline=[EventTimelinePhase(period="2026-06", name="삼성전자 압수수색")],
    evidence_ids=["ev_5c2a8f9013e4b7d6"],
)

# ★4년에 걸친 사건이 한 노드에 모인 예 — 흩어진 기사가 아니라 하나의 줄거리
EVENT_STRIKE = Event(
    event_id="evt_news_4a71e0c9b823", name="파업 리스크",
    event_type="노무", is_risk=True, role="subject",
    occurred_at="2025-02-18", article_count=9,
    timeline=[
        EventTimelinePhase(period="2022-03", name="삼성전자 노조 파업 위기"),
        EventTimelinePhase(period="2024-05", name="전국삼성전자노동조합 파업"),
        EventTimelinePhase(period="2024-09", name="인도 공장 무기한 파업"),
        EventTimelinePhase(period="2025-02", name="노조 리스크"),
    ],
    evidence_ids=["ev_9f31c8d075a2e46b"],
)

# ══════════════════════════════════════════════════════════════════
#  시장 — 상장사와 비상장사
# ══════════════════════════════════════════════════════════════════

MARKET_SAMSUNG = MarketMetrics(
    trade_date="2026-08-14", close_price=274500, change_pct=2.43, volume=21668266,
    listed_shares=5827808935, market_cap=1599733552657500,
    per=35.39, pbr=3.67, psr=4.80, fin_year=2025, fs_div="CFS",
)

MARKET_RESPONSE = MarketResponse(
    key="00126380", listed=True, stock_code="005930",
    unavailable_reason=None, latest=MARKET_SAMSUNG,
    series=[
        MarketPoint(trade_date="2026-08-12", close_price=266000, change_pct=-0.75, volume=14203881),
        MarketPoint(trade_date="2026-08-13", close_price=268000, change_pct=0.75, volume=16559120),
        MarketPoint(trade_date="2026-08-14", close_price=274500, change_pct=2.43, volume=21668266),
    ],
)

# ★비상장 — 3,005곳이 이 모양이다. **오류가 아니다**
MARKET_UNLISTED = MarketResponse(key="엔비디아", listed=False, stock_code=None,
                                 unavailable_reason="unlisted", latest=None, series=[])

# ══════════════════════════════════════════════════════════════════
#  기업 — full 과 relations_only
# ══════════════════════════════════════════════════════════════════

FIN_2025 = FinancialYear(
    bsns_year=2025, fs_div="CFS", revenue=333605938000000,
    operating_profit=43601051000000, net_profit=45206805000000,
    total_assets=566942110000000, total_liabilities=130621773000000,
    total_equity=436320337000000, debt_ratio=29.94,
    roe=10.36, roa=7.97, operating_margin=13.07,
)
FIN_2024 = FinancialYear(
    bsns_year=2024, fs_div="CFS", revenue=300870903000000,
    operating_profit=32725961000000, net_profit=34451351000000,
    total_assets=514531948000000, total_liabilities=112339878000000,
    total_equity=402192070000000, debt_ratio=27.93,
    roe=8.57, roa=6.70, operating_margin=10.88,
)

COMPANY_FULL = CompanyDetail(
    key="00126380", name="삼성전자", detail_level="full", coverage="complete",
    blocks=DetailBlocks(overview="full", financials="full", segments="partial",
                        products="full", related="full", risk="partial",
                        news="full", filings="partial", ownership="full", market="full"),
    counts=DetailCounts(relations=1169, related_companies=98, events=12,
                        risk_events=5, news=412, filings=8),
    collected_at="2026-08-12", corp_code="00126380", stock_code="005930",
    market="KOSPI", entity_kind="기업", ksic="26",
    ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    also_names=["삼성전자 DS부문", "삼성전자 파운드리 사업부", "삼성전자 MX 사업부"],
    overview="반도체·디스플레이·모바일을 만드는 국내 최대 제조사",
    business_overview="당사는 본사를 거점으로 한국과 CE, IM 부문 산하 해외 9개 지역총괄 및 "
                      "DS 부문 산하 해외 5개 지역총괄의 생산·판매법인, Harman 산하 종속기업 등 "
                      "총 226개의 종속기업으로 구성된 글로벌 전자기업입니다. …",
    ceo="전영현, 노태문", established_at="1969-01-13",
    name_en="SAMSUNG ELECTRONICS CO,.LTD", induty="264",
    market_metrics=MARKET_SAMSUNG,
    financials=[FIN_2025, FIN_2024],
    segments=[
        Segment(name="DX 부문", revenue=187967300000000, revenue_ratio=56.30),
        Segment(name="DS 부문", revenue=130128200000000, revenue_ratio=39.00),
        Segment(name="SDC", revenue=29841700000000, revenue_ratio=8.90),
        # ★단위를 못 믿는 부문 — 금액을 **안 보낸다**
        Segment(name="Harman", revenue=None, revenue_ratio=4.20),
    ],
    products=[
        ProductItem(key="hbm3e", name="HBM3E", category="제품", source="dart"),
        ProductItem(key="12인치실리콘웨이퍼", name="12인치 실리콘 웨이퍼", category="소재",
                    source="news"),
    ],
    executives=[],
    owned_by=[OwnershipItem(key="00365493", name="국민연금공단", ratio=7.51, subtype="5%이상주주")],
    owns=[OwnershipItem(key="00164645", name="삼성디스플레이", ratio=84.78, subtype="자회사")],
    related=[REL_STALE],
    events=[EVENT_RAID, EVENT_STRIKE],
    news=[NewsItem(url="https://www.seoulfn.com/news/articleView.html?idxno=630959",
                   title="삼성전자 본사 압수수색", press="서울파이낸스", published_at="2026-06-11")],
    filings=[Filing(rcept_no="20260608800436", doc_type="사업보고서",
                    title="사업보고서 (2025.12)", rcept_dt="2026-03-11",
                    url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260608800436")],
)

# ★해외 기업 — 관계는 많은데 재무도 시세도 공시도 없다.
#   **이게 3,432곳 중 대다수의 모양이다.** 프론트가 이걸 오류로 처리하면 안 된다.
COMPANY_RELATIONS_ONLY = CompanyDetail(
    key="엔비디아", name="엔비디아", detail_level="relations_only", coverage="complete",
    blocks=DetailBlocks(overview="none", financials="none", segments="none",
                        products="partial", related="full", risk="none",
                        news="none", filings="none", ownership="partial", market="none"),
    counts=DetailCounts(relations=59, related_companies=31, events=0,
                        risk_events=0, news=0, filings=0),
    collected_at="2026-08-11", corp_code=None, stock_code=None, market=None,
    entity_kind="기업", ksic="26", ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    also_names=["NVIDIA", "엔비디아 코리아"],
    overview=None, business_overview=None,
    market_metrics=None, financials=[], segments=[], filings=[],
    products=[ProductItem(key="블랙웰", name="블랙웰", category="제품", source="news")],
    related=[REL_STALE],
    events=[], news=[],
)

SHARED = [
    SharedCustomer(key="00252005", name="테크윙", shared_count=3,
                   customers=["삼성전자", "SK하이닉스", "마이크론"]),
    SharedCustomer(key="00351314", name="ISC", shared_count=3,
                   customers=["삼성전자", "SK하이닉스", "마이크론"]),
]

COMPANY_SUMMARY = CompanySummary(
    key="00164779", name="SK하이닉스", detail_level="full", coverage="complete",
    collected_at="2026-08-13", corp_code="00164779", stock_code="000660",
    market="KOSPI", entity_kind="기업", ksic="26",
    ksic_label="전자부품·컴퓨터·영상·음향·통신장비",
    overview="메모리 반도체를 만드는 국내 2위 제조사",
    latest_financial=FinancialYear(bsns_year=2025, fs_div="CFS",
                                   revenue=66192900000000, operating_profit=23467300000000,
                                   net_profit=19797200000000, debt_ratio=42.11),
    ceo="곽노정", established_at="1949-10-15",
    risk_summary="최근 12개월 리스크 사건 3건", risk_event_count=3,
    workspace_relations=[REL_SUPPLY], shared_customers=SHARED,
    recent_news=[NewsItem(url="https://www.etnews.com/20260608000123",
                          title="한미반도체, SK하이닉스에 TC본더 공급", press="전자신문",
                          published_at="2026-06-08")],
)

# ══════════════════════════════════════════════════════════════════
#  그래프 — ★섬이 생기는 예를 일부러 넣는다
# ══════════════════════════════════════════════════════════════════

WORKSPACE_GRAPH = GraphResponse(
    nodes=[
        GraphNode(key="00126380", name="삼성전자", role="pinned", entity_kind="기업", degree=412),
        GraphNode(key="00164779", name="SK하이닉스", role="pinned", entity_kind="기업", degree=288),
        GraphNode(key="00121932", name="한미반도체", role="pinned", entity_kind="기업", degree=63),
        GraphNode(key="00113058", name="심텍", role="pinned", entity_kind="기업", degree=41),
        # ★고영은 아무와도 안 이어진다. 억지로 잇지 않고 **섬으로 표시한다**
        GraphNode(key="00145261", name="고영", role="pinned", entity_kind="기업",
                  degree=18, is_island=True),
    ],
    edges=[
        GraphEdge(edge_id="ev_a3f21c8e5b90d417", type="SUPPLIES_TO", subtype="TC 본더",
                  source="00121932", target="00164779", freshness="current", score=1.0),
        GraphEdge(edge_id="ev_6c4b02e8d15f7a93", type="COMPETES_WITH", subtype="HBM 시장",
                  source="00126380", target="00164779", symmetric=True,
                  freshness="current", score=0.9),
        GraphEdge(edge_id="ev_8e0d3a7b91c26f45", type="SUPPLIES_TO", subtype="반도체 PCB",
                  source="00113058", target="00126380", freshness="stale", score=0.54),
    ],
    islands=["00145261"],
    truncated=False,
)

COMPANY_GRAPH = GraphResponse(
    nodes=[
        GraphNode(key="00164779", name="SK하이닉스", role="pinned", entity_kind="기업", degree=288),
        GraphNode(key="00121932", name="한미반도체", role="neighbor", entity_kind="기업", degree=63),
        GraphNode(key="00126380", name="삼성전자", role="neighbor", entity_kind="기업", degree=412),
    ],
    edges=[
        GraphEdge(edge_id="ev_a3f21c8e5b90d417", type="SUPPLIES_TO", subtype="TC 본더",
                  source="00121932", target="00164779", freshness="current", score=1.0),
        GraphEdge(edge_id="ev_6c4b02e8d15f7a93", type="COMPETES_WITH", subtype="HBM 시장",
                  source="00126380", target="00164779", symmetric=True,
                  freshness="current", score=0.9),
    ],
    islands=[], truncated=False,
)

# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈 · 챗봇
# ══════════════════════════════════════════════════════════════════

NEWS_FEED = NewsFeedResponse(
    total=2,
    items=[
        NewsFeedItem(url="https://www.seoulfn.com/news/articleView.html?idxno=630959",
                     title="삼성전자 본사 압수수색", press="서울파이낸스", published_at="2026-06-11",
                     companies=[SAMSUNG],
                     event=RelationEndpoint(key="evt_news_c915fa8bf141",
                                            name="삼성전자 본사 압수수색", label="Event"),
                     category="규제", is_risk=True),
        NewsFeedItem(url="https://www.etnews.com/20260608000123",
                     title="한미반도체, SK하이닉스에 TC본더 공급", press="전자신문",
                     published_at="2026-06-08", companies=[HANMI, HYNIX],
                     category="공급망", is_risk=False),
    ],
)

RISK_EVENTS = [
    RiskEvent(event_id="evt_news_c915fa8bf141", name="삼성전자 본사 압수수색",
              event_type="규제수사", occurred_at="2026-06-11", affected_count=4,
              affected=[SAMSUNG, HYNIX], article_count=2),
    RiskEvent(event_id="evt_news_4a71e0c9b823", name="파업 리스크", event_type="노무",
              occurred_at="2025-02-18", affected_count=2, affected=[SAMSUNG],
              article_count=9),
]

TRENDING = [
    TrendingItem(key="00126380", name="삼성전자", mention_count=412),
    TrendingItem(key="00164779", name="SK하이닉스", mention_count=288),
    TrendingItem(key="00121932", name="한미반도체", mention_count=63),
]

INSIGHTS = [
    InsightCard(key="00164779", name="SK하이닉스", headline="최근 3개월 리스크 사건 2건",
                why="화재·라인 전환 연기가 같은 분기에 보도됨",
                event_ids=["evt_news_c915fa8bf141"]),
    InsightCard(key="00121932", name="한미반도체", headline="SK하이닉스 의존도가 높습니다",
                why="공급 관계 4건 중 3건이 같은 상대", event_ids=[]),
]

PROPAGATION = [
    Propagation(key="00121932", name="한미반도체", score=0.71, hops=1,
                path=[PropagationStep(key="00121932", name="한미반도체",
                                      edge_type="SUPPLIES_TO")]),
    Propagation(key="00113058", name="심텍", score=0.38, hops=2,
                path=[PropagationStep(key="00126380", name="삼성전자", edge_type="COMPETES_WITH"),
                      PropagationStep(key="00113058", name="심텍", edge_type="SUPPLIES_TO")]),
]

EVENTS_OF = [EVENT_RAID, EVENT_STRIKE]

NEWS_OF = [
    NewsItem(url="https://www.seoulfn.com/news/articleView.html?idxno=630959",
             title="삼성전자 본사 압수수색", press="서울파이낸스", published_at="2026-06-11"),
]

FILINGS_OF = [
    Filing(rcept_no="20260608800436", doc_type="사업보고서", title="사업보고서 (2025.12)",
           rcept_dt="2026-03-11",
           url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260608800436"),
]

RELATIONS_OF = [REL_SUPPLY, REL_STAKE, REL_STALE]


# ══════════════════════════════════════════════════════════════════
#  추천 — 실측값 그대로 (심텍 기준 · 2026-08-16)
# ══════════════════════════════════════════════════════════════════

# ★지어낸 값이 아니다. 아래 Cypher 를 실제로 돌린 결과다.
#     MATCH (m:Company {name:'심텍'})-[:SUPPLIES_TO]->(c)<-[:SUPPLIES_TO]-(peer)
#     RETURN peer, count(DISTINCT c) AS shared ORDER BY shared DESC
SUGGESTIONS = [
    Suggestion(key="00121932", name="한미반도체", reason="shared_customer",
               reason_text="공통 고객 4곳 — SK하이닉스 · 삼성전자 · 엔비디아 · 마이크론",
               overlap=4, via=["SK하이닉스", "삼성전자", "엔비디아", "마이크론"],
               ksic_label="특수 목적용 기계"),
    Suggestion(key="00650599", name="이오테크닉스", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 마이크론",
               overlap=3, via=["SK하이닉스", "삼성전자", "마이크론"],
               ksic_label="특수 목적용 기계"),
    Suggestion(key="두산테스나", name="두산테스나", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 엔비디아",
               overlap=3, via=["SK하이닉스", "삼성전자", "엔비디아"],
               ksic_label="전자부품·컴퓨터·영상·음향·통신장비"),
    Suggestion(key="asml", name="ASML", reason="shared_customer",
               reason_text="공통 고객 3곳 — SK하이닉스 · 삼성전자 · 마이크론",
               overlap=3, via=["SK하이닉스", "삼성전자", "마이크론"],
               in_graph=True, detail_level="relations_only"),
]

# ══════════════════════════════════════════════════════════════════
#  알림 — 그동안 바뀐 것
# ══════════════════════════════════════════════════════════════════

# ★relation_ended 가 여기 없는 게 정상이다. loaded_at 이 2026-07-31 도입이라
#   비교 대상이 없어 아직 판정할 수 없다 — 다음 DART 재적재 이후에 생긴다.
CHANGES = [
    Change(kind="new_risk_event", company_key="00164779", company_name="SK하이닉스",
           title="청주 M15X 화재로 8명 병원 이송", detail="사고재해 · 영향 33곳",
           occurred_at="2026-08-04", ref_id="evt_news_8f21c04ab735"),
    Change(kind="new_relation", company_key="01095722", company_name="심텍",
           title="심텍 → 엔비디아 공급 관계가 새로 확인됨",
           detail="SUPPLIES_TO · 근거 1건", occurred_at="2026-08-02",
           ref_id="ev_3c8a71e0d95b2f46"),
]

# ══════════════════════════════════════════════════════════════════
#  고객 공유 — 직접 연결은 없지만 같은 고객에게 파는 곳
# ══════════════════════════════════════════════════════════════════

