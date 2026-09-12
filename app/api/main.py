from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse


from fastapi.concurrency import run_in_threadpool

from app.core.config import CHROMA_HOST, CHROMA_PORT, LOG_LEVEL
from app.core.trace import configure_logging
from app.services.retrieve_service import RetrieveService
from app.graph.ask_graph import run_ask
from app.graph.nodes.material import bind_service
from app.services import (
    company_service,
    insight_service,
    news_service,
    relation_service,
    search_service,
    workspace_service,
)
from app.api.schemas import (
    AskRequest, AskResponse, CompanyDetail, CompanySummary, ErrorResponse, Event, ExecutiveItem,
    Filing, GraphResponse, InsightCard, MarketResponse, NewsFeedResponse, NewsItem,
    OwnershipResponse, ProductItem, Propagation, Relation, RelationDetail,
    RetrieveResponse, SearchResponse, Suggestion,
    WorkspaceChangesRequest, WorkspaceChangesResponse, WorkspaceGraphRequest,
    WorkspaceInsightRequest, WorkspaceSuggestRequest, WorkspaceSuggestResponse,
    WorkspaceSummaryRequest,
)

# `logging.basicConfig()`를 호출하여 로그 레벨을 설정, 로거를 구성한다.
configure_logging(LOG_LEVEL)

app = FastAPI(
    title="BizNode 데이터 API",
    version="0.2.0",
    description=(
        "기업 관계 그래프 조회 API. **공개 라우트 21개가 전부 실제 DB 를 읽습니다** (2026-08-23).\n\n"
        "화면이 어떻게 보이는지는 **`/preview`** 에서 실제 응답으로 볼 수 있습니다.\n\n"
        "### 화면을 만들 때 반드시 다뤄야 하는 것\n"
        "- `in_graph = false` — **실재하지만 우리가 안 모은 회사**입니다. "
        "「없는 회사」가 아니라 「자료가 없는 회사」로 표시합니다\n"
        "- `detail_level = none` — 재무가 **없는 게 정상**입니다 "
        "(3,432곳 중 재무 477 · 시세 417 · 공시 64)\n"
        "- `listed = false` — 상장 표시가 없는 2,980곳은 시장 블록이 통째로 `null` 입니다\n"
        "- `counts` vs 목록 길이 — 상세는 블록마다 **10건**까지입니다. "
        "「148건 중 10건」이라 쓰고 전체는 서브 라우트로 가져옵니다\n"
        "- `islands` — 아무와도 안 이어진 기업을 **억지로 잇지 않습니다**\n"
        "- `propagation[].stated` — `true` 는 기사가 말한 것, `false` 는 우리 계산입니다. "
        "**갈라 그려야 합니다**\n\n"
        "### 단위\n"
        "금액 **원** · 비율 **퍼센트(0~100)** · 날짜 **ISO 8601**"
    ),
    openapi_tags=[
        {"name": "검색", "description": "부분 일치로 기업 찾기"},
        {"name": "기업", "description": "상세 한 방 + 블록별 「더보기」 라우트"},
        {"name": "관계", "description": "관계 상세 · 근거 원문 · 리스크 파급"},
        {"name": "워크스페이스", "description": "그래프 · 노드 클릭 · 추천 · 알림"},
        {"name": "뉴스", "description": "뉴스/이슈 화면 — 주제·워크스페이스·최신순"},
        {"name": "홈", "description": "인사이트 카드"},
        {"name": "운영", "description": "배포 점검용"},
        {"name": "챗봇", "description": "추론 계층이 쓰는 재료"},
    ],
)


# 프로세스에서 하나만 생성하고 /retrieve와 LangGraph가 공유한다. 
_retrieve_service = RetrieveService()

# LangGraph가 쓰는 재료를 /retrieve가 만든다. /retrieve는 HTTP 어댑터일 뿐이다.
bind_service(_retrieve_service)

# 로컬 프론트엔드 개발을 위한 CORS 설정.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"], allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════════════
#  검색 - 그래프 + DART 명부
# ══════════════════════════════════════════════════════════════════

@app.get("/search", response_model=SearchResponse, tags=["검색"],
         summary="기업 검색")
def search(q: str = Query(description="기업명 또는 별칭. **부분 일치**", examples=["삼성"]),
           limit: int = Query(20, ge=1, le=100),
           include_registry: bool = Query(
               True, description="DART 명부에만 있는 회사도 포함할까")) -> SearchResponse:
    """ 기업명 또는 별칭으로 부분 일치 검색 """
    return SearchResponse(**search_service.search(
        q, limit=limit, include_registry=include_registry))


# ══════════════════════════════════════════════════════════════════
#  기업
# ══════════════════════════════════════════════════════════════════

@app.get("/companies/{key}/graph", response_model=GraphResponse, tags=["기업"],
         summary="관계 그래프 (다시 그리기)")
def company_graph(key: str, depth: int = Query(1, ge=1, le=2)) -> GraphResponse:
    """ 기업 중심의 관계 그래프를 조회 """
    return GraphResponse(**company_service.company_graph(key, depth=depth))

@app.get("/companies/{key}/market", response_model=MarketResponse, tags=["기업"],
         summary="주가 · 시총 · PER · PBR · PSR")
def market_of(key: str, days: int = Query(30, ge=1, le=365)) -> MarketResponse:
    """ 기업의 주가, 시가총액, PER, PBR, PSR 등의 시장 데이터를 조회"""
    return MarketResponse(**company_service.market_of(key, days=days))

@app.get("/companies/{key}/events", response_model=list[Event], tags=["기업"],
         summary="사건 전체")
def events_of(key: str) -> list[Event]:
    """ 기업 관련 사건 전체 조회. 상세는 10건까지 조회. """
    return [Event(**e) for e in company_service.events_of(key)]


@app.get("/companies/{key}/news", response_model=list[NewsItem], tags=["기업"],
         summary="근거가 된 기사 전체")
def news_of(key: str, limit: int = Query(20, ge=1, le=1000)) -> list[NewsItem]:
    """ 기업 관계의 근거가 된 기사 목록을 조회 """
    return [NewsItem(**n) for n in company_service.news_of(key, limit=limit)]


@app.get("/companies/{key}/filings", response_model=list[Filing], tags=["기업"],
         summary="DART 공시 전체")
def filings_of(key: str, limit: int = Query(20, ge=1, le=1000)) -> list[Filing]:
    return [Filing(**f) for f in company_service.filings_of(key, limit=limit)]


@app.get("/companies/{key}/products", response_model=list[ProductItem], tags=["기업"],
         summary="제품 · 기술 전체")
def products_of(key: str, limit: int = Query(100, ge=1, le=500)) -> list[ProductItem]:
    return [ProductItem(**x) for x in company_service.products_of(key, limit=limit)]


@app.get("/companies/{key}/executives", response_model=list[ExecutiveItem], tags=["기업"],
         summary="임원 전체")
def executives_of(key: str, limit: int = Query(100, ge=1, le=500)) -> list[ExecutiveItem]:
    return [ExecutiveItem(**x) for x in company_service.executives_of(key, limit=limit)]


# 한 배열로 주고 방향 플래그를 달면 화면이 매번 다시 가른다.
@app.get("/companies/{key}/ownership", response_model=OwnershipResponse, tags=["기업"],
         summary="지배구조 전체 (양방향)")
def ownership_of(key: str, limit: int = Query(100, ge=1, le=500)) -> OwnershipResponse:
    """ 기업의 양방향 지배구조를 조회."""
    return OwnershipResponse(**company_service.ownership_of(key, limit=limit))


@app.get("/companies/{key}/relations", response_model=list[Relation], tags=["기업"],
         summary="관계 전체")
def relations_of(key: str) -> list[Relation]:
    """ 기업의 검증된 관계 목록을 조회 """
    return [Relation(**r) for r in company_service.relations_of(key)]


# 'key'에 '/'가 들어갈 수 있으므로 path converter를 사용. 
@app.get("/companies/{key:path}", response_model=CompanyDetail, tags=["기업"],
         responses={404: {"model": ErrorResponse}}, summary="기업 상세 — 페이지 한 방")
def company_detail(key: str) -> CompanyDetail:
    got = company_service.company_detail(key)
    if got is None:
        raise HTTPException(404, "해당 키의 기업이 없습니다")
    return CompanyDetail(**got)



# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════

@app.get("/relations/{edge_id}", response_model=RelationDetail, tags=["관계"],
         responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
         summary="관계 상세 — 근거 원문 · 리스크 전파")
def relation_detail(edge_id: str) -> RelationDetail:

    try:
        got = relation_service.relation_detail(edge_id)
    except Exception as e:  
        raise HTTPException(
            503, f"근거 저장소(ChromaDB {CHROMA_HOST}:{CHROMA_PORT})에서 원문을 "
                 f"못 꺼냈습니다. 관계는 있지만 근거를 확인할 수 없습니다. ({e})")
    if got is None:
        raise HTTPException(404, f"관계 `{edge_id}` 를 찾을 수 없습니다. "
                                 "검증에서 제외된 관계이거나 종료된 관계일 수 있습니다.")
    return RelationDetail(**got)

# ══════════════════════════════════════════════════════════════════
#  리스크 파급 - 사건을 기준으로 공급망을 따라 리스크 파급 범위를 계산.
# ══════════════════════════════════════════════════════════════════

@app.get("/events/{event_id}/impact", response_model=list[Propagation], tags=["관계"],
         responses={404: {"model": ErrorResponse}},
         summary="리스크 파급 — 이 사건이 어디까지 번지나")
def propagate_risk(event_id: str,
                   max_hops: int = Query(3, ge=1, le=4)) -> list[Propagation]:
    got = relation_service.event_impact(event_id, max_hops=max_hops)
    if got is None:
        raise HTTPException(404, f"사건 `{event_id}` 를 찾을 수 없습니다.")
    return [Propagation(**p) for p in got]


# ══════════════════════════════════════════════════════════════════
#  워크스페이스
# ══════════════════════════════════════════════════════════════════

@app.post("/workspace/summary", response_model=CompanySummary, tags=["워크스페이스"],
          summary="노드를 클릭했을 때")
def company_summary(body: WorkspaceSummaryRequest) -> CompanySummary:
    got = company_service.company_summary(body.key, body.workspace_keys)
    if got is None:
        raise HTTPException(404, "해당 키의 기업이 없습니다")
    return CompanySummary(**got)


@app.post("/workspace/graph", response_model=GraphResponse, tags=["워크스페이스"],
          summary="워크스페이스 캔버스")
def workspace_graph(body: WorkspaceGraphRequest) -> GraphResponse:

    return GraphResponse(**workspace_service.workspace_graph(
        body.keys, expand=body.expand, max_nodes=body.max_nodes, refs=body.refs))


@app.post("/workspace/suggest", response_model=WorkspaceSuggestResponse,
          tags=["워크스페이스"], summary="같이 담을 기업 추천")
def workspace_suggest(body: WorkspaceSuggestRequest) -> WorkspaceSuggestResponse:

    return WorkspaceSuggestResponse(
        keys=body.keys,
        suggestions=[Suggestion(**x) for x in
                     workspace_service.suggest(body.keys, limit=body.limit)])


@app.post("/workspace/changes", response_model=WorkspaceChangesResponse,
          tags=["워크스페이스"], summary="알림 — 그동안 무엇이 바뀌었나")
def workspace_changes(body: WorkspaceChangesRequest) -> WorkspaceChangesResponse:
    """ 기준 시각 이후 워크스페이스와 관련된 변경 사항을 조회. """
    return WorkspaceChangesResponse(
        **workspace_service.changes(body.keys, body.since))


# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈
# ══════════════════════════════════════════════════════════════════

# 뉴스/이슈 화면. 세 축을 겹쳐 쓸 수 있다 — 주제 / 범위 / 최신순.
@app.get("/news", response_model=NewsFeedResponse, tags=["뉴스"], summary="뉴스 피드")
def news_feed(
    category: str | None = Query(None, description="공급망 · 지분 · 규제 · 사건"),
    workspace_keys: list[str] | None = Query(None, description="내 워크스페이스로 좁히기"),
    risk_only: bool = Query(False, description="사건 갈래만"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NewsFeedResponse:

    return NewsFeedResponse(**news_service.news_feed(
        category=category, workspace_keys=workspace_keys,
        risk_only=risk_only, limit=limit, offset=offset))


@app.post("/insights", response_model=list[InsightCard], tags=["홈"],
          summary="인사이트 카드")
def workspace_insights(body: WorkspaceInsightRequest) -> list[InsightCard]:
    """ 홈 화면에 띄울 인사이트 카드. 워크스페이스를 주면 그 안에서, 안 주면 Global Ranking 그대로. """
    return [InsightCard(**c) for c in
            insight_service.workspace_insights(body.keys, limit=body.limit)]


# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════

@app.post("/retrieve", response_model=RetrieveResponse, tags=["챗봇"],
          summary="챗봇이 쓸 사실과 근거")
async def retrieve(body: AskRequest) -> RetrieveResponse:
    return await _retrieve_service.retrieve_async(body)


@app.post("/ask", response_model=AskResponse, tags=["챗봇"],
          summary="챗봇 답변 생성")
async def ask(body: AskRequest) -> AskResponse:
    return await run_in_threadpool(run_ask, body)


@app.get("/health", tags=["운영"], summary="상태 확인")
def health() -> dict:
    return {"status": "ok", "stub": False, "version": app.version}


# ══════════════════════════════════════════════════════════════════
#  미리보기 — 응답이 화면에서 어떻게 보이는지
# ══════════════════════════════════════════════════════════════════
_PREVIEW = Path(__file__).with_name("preview.html")


@app.get("/preview", include_in_schema=False)
def preview() -> HTMLResponse:
    return HTMLResponse(_PREVIEW.read_text(encoding="utf-8"))
