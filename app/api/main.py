"""BizNode 데이터 API.

모양을 먼저 확정하고 내용을 나중에 채웠다

  프론트는 응답이 진짜인지 가짜인지 구분하지 않는다. 모양만 같으면 화면을
  끝까지 만들 수 있다 — 그동안 프론트·백엔드·추론이 대기하지 않아도 된다.

      1단계  응답 계약        app/api/schemas.py   ← 코드가 곧 문서
      2단계  스텁 서버        이 파일
      3단계  하나씩 진짜로     app/services/       ← 라우트는 그대로, 속만 간다

  **3단계가 끝났다. 공개 라우트 21개가 전부 실제 DB 를 읽는다**(2026-08-23).
  스텁이 없으므로 `X-Stub` 표시 장치도 지웠다 — 붙일 라우트가 0개인 채로
  모든 요청이 미들웨어를 지나고 있었다.

경계 — 이 API 는 사용자를 모른다

  누가 로그인했는지 알 필요가 없다. 노드 키를 받아 사실을 돌려줄 뿐이다.
  워크스페이스·보관함은 **백엔드 것**이다 — 「사용자가 어느 기업을 담아 뒀나」는
  그래프가 아니라 사용자 데이터다. 백엔드 DB 에 두고 여기엔 **키 목록만** 넘긴다.

  반대로 백엔드는 온톨로지를 모른다. 엣지 12종도, 근거 검증도, 신선도도 몰라도
  된다. **이 두 줄이 깨지는 순간이 신호다** — 백엔드에 Cypher 가 등장하거나
  이 API 가 `user_id` 를 받기 시작하면 경계를 다시 봐야 한다.

실행:
    uvicorn app.api.main:app --reload --port 8100
    → http://localhost:8100/docs      대화형 문서 (백엔드가 볼 것)
    → http://localhost:8100/openapi.json   클라이언트 자동 생성용
"""

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

# 라우트를 만들기 전에 켠다 — 이게 없으면 검색·답변 경계의 trace 로그가 root
# 로거 기본값(WARNING)에 막혀 통째로 사라진다.
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


# ★프로세스당 하나. 안에서 SearchOrchestrator 를 들고 있어(커넥션·캐시) 요청마다
#   만들면 낭비다. 테스트는 app.dependency_overrides 가 아니라 이 모듈 속성을
#   갈아끼운다 — 라우트가 Depends 를 쓰지 않기 때문이다.
_retrieve_service = RetrieveService()
# ★`/ask` 는 이 인스턴스를 **그래프를 통해** 쓴다. 1차 답변 경로는 폐기했다
#   (2026-09-04) — 대조 기준선이던 그쪽이 1.5차 표기·앵커리스 개정 뒤로 이미
#   빨간불이라 「예전과 같은 답인가」를 물을 수 없는 상태였다.
# 그래프는 위 인스턴스를 **그대로** 쓴다 — 두 벌을 만들면 SearchOrchestrator 가
# 둘이 되어 커넥션·캐시가 갈린다.
bind_service(_retrieve_service)



# 프론트가 로컬에서 바로 붙어 볼 수 있게. ★배포 때는 백엔드 도메인만 남긴다
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════
#  검색
# ══════════════════════════════════════════════════════════════════


# ★두 곳을 함께 뒤진다 — 우리 그래프 3,432곳 + DART 명부 118,535곳.
#   「한화오션엔지니어링」을 안 모았다고 **없다고 답하면 안 된다.** 실재한다.
# ★이름을 키로 쓰지 않는다. 명부에 동명 법인이 있다(「신우」 138건).
@app.get("/search", response_model=SearchResponse, tags=["검색"],
         summary="기업 검색")
def search(q: str = Query(description="기업명 또는 별칭. **부분 일치**", examples=["삼성"]),
           limit: int = Query(20, ge=1, le=100),
           include_registry: bool = Query(
               True, description="DART 명부에만 있는 회사도 포함할까")) -> SearchResponse:
    """이름·별칭 **부분 일치**. 「삼성」 → 295건.

    - `in_graph=false` 는 **DART 명부에만 있는 회사** — 실재하지만 우리가 아직
      수집하지 않았다. 화면은 「수집되지 않은 기업」으로 알리고 관계·재무를
      조회하지 않는다.
    - `total` 은 **있는 수**, `hits` 는 실어 보낸 수. 「295건 중 20건」.
    - 정렬: 정확 일치 → 시작 일치 → 포함 → 별칭, 같으면 `degree` 순.
    """
    return SearchResponse(**search_service.search(
        q, limit=limit, include_registry=include_registry))


# ══════════════════════════════════════════════════════════════════
#  기업
# ══════════════════════════════════════════════════════════════════


# ★`depth=2` 는 허브를 지나면 폭발한다. 연결 150 초과가 14곳이라 삼성전자를
#   거쳐 두 칸이면 거의 전부가 딸려온다. `degree` 를 보내니 화면이 접을 수 있다.
# ★상한은 **유형별**이다. 갈래별로 걸면 거래 갈래 8칸을 공급사가 다 먹어
#   경쟁·소송이 한 건도 안 그려진다(삼성전자에서 실제로 그랬다).
@app.get("/companies/{key}/graph", response_model=GraphResponse, tags=["기업"],
         summary="관계 그래프 (다시 그리기)")
def company_graph(key: str, depth: int = Query(1, ge=1, le=2)) -> GraphResponse:
    """이 회사 중심 관계 그래프. **상세 응답에 이미 들어 있다** —
    `depth`·`max_nodes` 를 바꿔 다시 그릴 때만 부른다.

    - `nodes[].kind` 로 범례를 가른다 — 거래·주주·인물·제품·사건·기관.
    - `omitted` 는 유형별로 뺀 수. **그린 것 + `omitted` = `counts.relations`.**
    """
    return GraphResponse(**company_service.company_graph(key, depth=depth))


# ★남의 API 를 안 쓰는 이유 — 연결인지 별도인지, 어느 분기 실적인지를 모른다.
# ★유통주식수를 못 믿는 기업(DART 단위 오류)은 시총·PER 을 안 보낸다.
#   그대로 두면 시가총액 순위가 통째로 뒤집힌다.
@app.get("/companies/{key}/market", response_model=MarketResponse, tags=["기업"],
         summary="주가 · 시총 · PER · PBR · PSR")
def market_of(key: str, days: int = Query(30, ge=1, le=365)) -> MarketResponse:
    """주가·시총·PER·PBR·PSR + `days` 일치 시세.
    **상세에는 최신 한 건만 있고 `series` 가 없다** — 차트는 이걸 부른다.

    - **시총·PER·PBR·PSR 은 저장하지 않고 조회 때 계산한다.** 그래서
      `fin_year`·`fs_div` 가 함께 나간다 — 화면이 「2025년 연결 기준」이라 밝힌다.
    - 시세가 있는 회사는 **417곳**뿐이다. 없으면 `listed=false` 에 나머지가 `null`.
    - 적자면 `per` 이 `null`. 음수 PER 을 만들지 않는다.
    """
    return MarketResponse(**company_service.market_of(key, days=days))


# 「파업 리스크」가 2022~2025년 4개 국면을 한 노드에 들고 있는 식이다.
@app.get("/companies/{key}/events", response_model=list[Event], tags=["기업"],
         summary="사건 전체")
def events_of(key: str) -> list[Event]:
    """사건 전체. 상세는 **10건**까지 주고 「더보기」가 이걸 부른다.

    - `timeline` 이 붙은 사건은 **여러 국면이 한 노드에 모인 것**이다.
      흩어진 기사가 아니라 하나의 줄거리로 보여준다.
    - `role`: `subject` 당사자 · `counterparty` 사건의 한쪽 · `mentioned` **이름만
      나온 것**. `mentioned` 를 당사자로 세면 집계가 부풀려진다.
    - `evidence_ids` 는 **이 기업이 이 사건에 엮인 근거만**이다. 같은 사건에 여러
      기업이 붙어도 서로의 근거가 섞이지 않는다.
    """
    return [Event(**e) for e in company_service.events_of(key)]


# ★뉴스 피드가 아니다. 「이 관계의 근거가 된 기사」라 외부 뉴스 API 로
#   대체할 수 없다.
@app.get("/companies/{key}/news", response_model=list[NewsItem], tags=["기업"],
         summary="근거가 된 기사 전체")
def news_of(key: str, limit: int = Query(20, ge=1, le=1000)) -> list[NewsItem]:
    """근거가 된 기사 목록. 상세는 **10건**까지.

    **본문은 없다**(저작권). 제목·언론사·발행일·링크까지다. 인용이 필요하면
    관계의 근거 원문(`/relations/{edge_id}`)을 쓴다.
    """
    return [NewsItem(**n) for n in company_service.news_of(key, limit=limit)]


@app.get("/companies/{key}/filings", response_model=list[Filing], tags=["기업"],
         summary="DART 공시 전체")
def filings_of(key: str, limit: int = Query(20, ge=1, le=1000)) -> list[Filing]:
    """DART 공시 목록. 상세는 **10건**까지. **시드 64곳에만 있다.**"""
    return [Filing(**f) for f in company_service.filings_of(key, limit=limit)]


@app.get("/companies/{key}/products", response_model=list[ProductItem], tags=["기업"],
         summary="제품 · 기술 전체")
def products_of(key: str, limit: int = Query(100, ge=1, le=500)) -> list[ProductItem]:
    """제품·기술 전체. 상세는 **10건**까지 (삼성전자 152건)."""
    return [ProductItem(**x) for x in company_service.products_of(key, limit=limit)]


@app.get("/companies/{key}/executives", response_model=list[ExecutiveItem], tags=["기업"],
         summary="임원 전체")
def executives_of(key: str, limit: int = Query(100, ge=1, le=500)) -> list[ExecutiveItem]:
    """임원 전체. 상세는 **10명**까지."""
    return [ExecutiveItem(**x) for x in company_service.executives_of(key, limit=limit)]


# 한 배열로 주고 방향 플래그를 달면 화면이 매번 다시 가른다.
@app.get("/companies/{key}/ownership", response_model=OwnershipResponse, tags=["기업"],
         summary="지배구조 전체 (양방향)")
def ownership_of(key: str, limit: int = Query(100, ge=1, le=500)) -> OwnershipResponse:
    """지배구조 전체. 상세는 각 방향 **10건**까지 (삼성전자 자회사 157곳).

    **양방향을 갈라서** 준다 — `owned_by`(누가 나를 소유하나)와
    `owns`(내가 무엇을 소유하나)는 화면에서 다른 뜻이다.
    """
    return OwnershipResponse(**company_service.ownership_of(key, limit=limit))


# ★뉴스는 관계의 시작만 보도하고 **종료는 보도하지 않는다.** 오래됐다고
#   지우면 살아 있는 관계를 잃고, 그대로 두면 끝난 관계를 현재형으로 말한다.
# ★근거 없는 관계 하나가 **없는 파급을 만들어 낸다.** 파급 계산도 같이 막는다.
@app.get("/companies/{key}/relations", response_model=list[Relation], tags=["기업"],
         summary="관계 전체")
def relations_of(key: str) -> list[Relation]:
    """관계 전체. 상세는 **그래프에 그린 것과 같은 목록**을 주고,
    「더보기」가 이걸 부른다 (삼성전자 526건).

    - **근거 검증에서 걸린 관계는 오지 않는다.**
    - `freshness=stale` 도 지우지 않고 보낸다. 「2024-06에 그렇게 보도됨」으로
      표시한다.
    """
    return [Relation(**r) for r in company_service.relations_of(key)]


# ★`key` 에 `/` 가 들어갈 수 있다(「한국S/W공제조합」 등 12곳). 그래서 이
#   경로만 `{key:path}` 로 받고 **하위 경로보다 뒤에** 선언한다 — 순서가
#   바뀌면 `/companies/x/market` 을 이 라우트가 삼킨다.
@app.get("/companies/{key:path}", response_model=CompanyDetail, tags=["기업"],
         responses={404: {"model": ErrorResponse}}, summary="기업 상세 — 페이지 한 방")
def company_detail(key: str) -> CompanyDetail:
    """기업 상세 페이지 **한 방**. 목업 4-3 의 블록 전부가 여기 있다 —
    시장·재무·사업부문·**관계 그래프**·연관 기업·사건·지배구조·제품·인물·뉴스·공시.

    - 목록은 **블록마다 10건**까지(관계 그래프는 60). 전체 수는 `counts`,
      전체 목록은 서브 라우트(`/events`·`/products`·`/relations` …).
    - `blocks` 로 블록마다 채움 정도를 본다. `detail_level` 하나로는
      「재무는 있는데 공시가 없다」를 표현할 수 없다.
    - `detail_level` 로 화면을 가른다 — `full`(64곳) 과 `partial`(416곳) 은
      상세 페이지를 열고, `none`(2,952곳) 는 좌 패널 요약까지다.
    - **재무가 없는 게 정상이다** — 3,432곳 중 재무 477 · 시세 417 · 공시 64.
    - `graph` 와 `related` 는 **같은 관계 집합**이다. 목록에서 한 줄을 누르면
      그래프의 그 선을 강조할 수 있다.
    """
    got = company_service.company_detail(key)
    if got is None:
        raise HTTPException(404, "해당 키의 기업이 없습니다")
    return CompanyDetail(**got)



# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════


# 원문은 세 곳에 흩어져 있고 id 가 같다 —
#   Neo4j 엣지 속성 · ChromaDB 원문 10,510건 · PG 언론사·보도일.
@app.get("/relations/{edge_id}", response_model=RelationDetail, tags=["관계"],
         responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
         summary="관계 상세 — 근거 원문 · 리스크 전파")
def relation_detail(edge_id: str) -> RelationDetail:
    """선을 클릭했을 때. 관계 + **근거 원문** + 이 선을 타고 닿는 위험.

    - `evidence[].text` 가 **원문 그대로**다. 우리가 요약한 문장이 아니라
      기사·공시에 쓰여 있는 문장이라 화면이 그대로 인용한다.
    - `propagation[].stated` 를 **갈라 그린다** — `true` 기사가 직접 말한 것,
      `false` 우리가 공급망으로 계산한 것.
    - 파급은 **공급 관계에서만** 계산한다. 지분·임원은 위험을 실어 나르는
      통로가 아니다.
    - 원문을 못 꺼내면 **`503`** 이다. 빈 배열을 주면 「근거 없는 관계」로 읽힌다.
    """
    try:
        got = relation_service.relation_detail(edge_id)
    except Exception as e:                       # ChromaDB 가 안 뜨면 여기로 온다
        # ★빈 배열을 주면 **「근거가 없는 관계」로 읽힌다.** 근거 없는 관계는 애초에
        #   응답에서 빠지므로, 못 꺼낸 것을 없는 것처럼 보내면 거짓말이 된다.
        raise HTTPException(
            503, f"근거 저장소(ChromaDB {CHROMA_HOST}:{CHROMA_PORT})에서 원문을 "
                 f"못 꺼냈습니다. 관계는 있지만 근거를 확인할 수 없습니다. ({e})")
    if got is None:
        raise HTTPException(404, f"관계 `{edge_id}` 를 찾을 수 없습니다. "
                                 "검증에서 제외된 관계이거나 종료된 관계일 수 있습니다.")
    return RelationDetail(**got)


# ★저장하지 않고 질의 시점에 계산한다. 저장하면 그래프가 바뀔 때 낡는다.
# ★허브를 지나는 경로는 약하게 본다(`40/(40+차수-1)`). 삼성전자를 거치면
#   거의 모든 회사에 닿는데, 그건 관계가 아니라 **삼성전자가 크다는 뜻**이다.
@app.get("/events/{event_id}/impact", response_model=list[Propagation], tags=["관계"],
         responses={404: {"model": ErrorResponse}},
         summary="리스크 파급 — 이 사건이 어디까지 번지나")
def propagate_risk(event_id: str,
                   max_hops: int = Query(3, ge=1, le=4)) -> list[Propagation]:
    """**사건을 클릭했을 때** 이 사건이 어디까지 번지나.

    - `stated` 로 **보도와 계산을 가른다.** 실측(모트라스 파업): 124곳 =
      기사가 직접 말한 것 10곳 + 공급망으로 계산한 것 114곳. 섞어 그리면
      추론을 사실로 팔게 된다.
    - `path` 를 그대로 보여준다 — 어느 관계를 타고 닿았는지.
    - `key` 로 그 기업 상세로 넘어간다.
    """
    got = relation_service.event_impact(event_id, max_hops=max_hops)
    if got is None:
        raise HTTPException(404, f"사건 `{event_id}` 를 찾을 수 없습니다.")
    return [Propagation(**p) for p in got]


# ══════════════════════════════════════════════════════════════════
#  워크스페이스
# ══════════════════════════════════════════════════════════════════


# ★워크스페이스 자체는 백엔드가 저장한다. 이 API 는 키 목록을 받을 뿐
#   누구의 워크스페이스인지 모른다.
@app.post("/workspace/summary", response_model=CompanySummary, tags=["워크스페이스"],
          summary="노드를 클릭했을 때")
def company_summary(body: WorkspaceSummaryRequest) -> CompanySummary:
    """**노드를 클릭했을 때** — 워크스페이스 좌 패널. 기업 상세와 다른 화면이다.

    `workspace_relations` 는 **담아 둔 다른 기업들과 어떻게 이어지는지**라
    기업 키만으로는 답이 안 나온다. 그래서 `POST` 로 목록을 함께 받는다.
    """
    got = company_service.company_summary(body.key, body.workspace_keys)
    if got is None:
        raise HTTPException(404, "해당 키의 기업이 없습니다")
    return CompanySummary(**got)


# ★②가 중요하다. 실측으로 「고영」을 이어 주던 중간 노드가 삼성자산운용
#   (연결 32)이었다. 자산운용사는 양쪽에 지분이 있을 뿐 두 회사가 관계있다는
#   뜻이 아니다. 다리로 쓰면 펀드 하나가 모든 회사를 이어버린다.
@app.post("/workspace/graph", response_model=GraphResponse, tags=["워크스페이스"],
          summary="워크스페이스 캔버스")
def workspace_graph(body: WorkspaceGraphRequest) -> GraphResponse:
    """워크스페이스 캔버스. 세 단계로 만든다.

        ① 담긴 기업끼리 직접 이어진 엣지        언제나 포함
        ② 섬이 된 기업만 한 칸 건너 잇기         **허브는 다리로 안 씀**
        ③ 그래도 못 이으면 섬으로 두고 표시       억지로 잇지 않음

    - `nodes[].role`: `pinned` 담은 기업 · `bridge` 이어주려 딸려온 노드 ·
      `neighbor` 참조 기업(`refs=true` 일 때).
    - `islands` 는 **그려질 선이 하나도 없는 노드.** 없는 관계를 그리는 것보다
      없다고 말하는 게 낫다.
    - `ref_candidates` 는 후보가 몇 곳이었나 — 화면이 「337곳 중 10곳」이라 쓴다.
    """
    return GraphResponse(**workspace_service.workspace_graph(
        body.keys, expand=body.expand, max_nodes=body.max_nodes, refs=body.refs))


# 검색 → 담기 흐름에서 **저장은 백엔드 몫**이다.
#   1 GET /search  2 백엔드가 자기 DB 에 저장  3 POST /suggest  4 POST /graph
@app.post("/workspace/suggest", response_model=WorkspaceSuggestResponse,
          tags=["워크스페이스"], summary="같이 담을 기업 추천")
def workspace_suggest(body: WorkspaceSuggestRequest) -> WorkspaceSuggestResponse:
    """**기업을 담을 때 같이 담을 것**을 추천한다. 「한 곳만 담으면
    관계가 안 보인다」가 이 화면의 첫 벽이다.

    - `reason_text` 를 **그대로 화면에 쓴다.** 「한미반도체를 추천합니다」만으로는
      담을 이유를 모른다 — 「공통 고객 4곳 — 삼성전자·SK하이닉스·…」가 필요하다.
    - 근거 다섯: `shared_customer` · `shared_supplier` · `shared_owner` ·
      `same_event` · `competitor`.
    """
    return WorkspaceSuggestResponse(
        keys=body.keys,
        suggestions=[Suggestion(**x) for x in
                     workspace_service.suggest(body.keys, limit=body.limit)])


@app.post("/workspace/changes", response_model=WorkspaceChangesResponse,
          tags=["워크스페이스"], summary="알림 — 그동안 무엇이 바뀌었나")
def workspace_changes(body: WorkspaceChangesRequest) -> WorkspaceChangesResponse:
    """알림. 기준 시각 이후 **무엇이 바뀌었나**.

        new_risk_event   새 위험 사건
        new_relation     새 관계
        relation_ended   관계 종료

    - **누구에게 보낼지는 모른다.** 구독 설정·발송은 백엔드 것이다.
    - `relation_ended` 는 지금 **언제나 빈 배열**이다 — 비교 기준(`loaded_at`)이
      2026-07-31 도입이라 대상이 없다. 다음 DART 재적재 이후 채워진다.
      **필드는 미리 열어 두었으니 그때 계약이 바뀌지 않는다.**
    """
    return WorkspaceChangesResponse(
        **workspace_service.changes(body.keys, body.since))


# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈
# ══════════════════════════════════════════════════════════════════


# ★`/companies/{key}/news` 와 다르다. 그쪽은 「관계의 근거가 된 기사」이고
#   이쪽은 화면용 피드다. 둘 다 우리가 모은 기사에서 나온다.
# ★외부 뉴스 API 를 안 쓰는 이유 — 화면의 축 1(주제)을 외부가 못 만든다.
#   국내는 뉴스 저작권 때문에 무료 API 도 사실상 없다.
@app.get("/news", response_model=NewsFeedResponse, tags=["뉴스"], summary="뉴스 피드")
def news_feed(
    category: str | None = Query(None, description="공급망 · 지분 · 규제 · 사건"),
    workspace_keys: list[str] | None = Query(None, description="내 워크스페이스로 좁히기"),
    risk_only: bool = Query(False, description="사건 갈래만"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NewsFeedResponse:
    """뉴스/이슈 화면. **세 축을 겹쳐 쓸 수 있다** — 주제 / 범위 / 최신순.

    - `total` 은 필터를 통과한 **전체 수**다. 화면은 「2,387건 중 20건」.
    - `categories` 는 **여러 개가 정상**이다 — 「공정위, 납품단가 담합 제재」는
      공급망이면서 규제다.
    - 본문은 없다. 원문은 언론사 링크로 보낸다.
    - 매일 아침 `batch.build.news_feed` 가 언론사 RSS 로 채운다.
    """
    return NewsFeedResponse(**news_service.news_feed(
        category=category, workspace_keys=workspace_keys,
        risk_only=risk_only, limit=limit, offset=offset))


@app.post("/insights", response_model=list[InsightCard], tags=["홈"],
          summary="인사이트 카드")
def workspace_insights(body: WorkspaceInsightRequest) -> list[InsightCard]:
    """**합쳐야 드러나는 것**만 낸다. 기업 하나를 열어서는 안 보이는 것들이다.

    - `lift` 가 핵심이다 — 「몇 곳이 겹치나」가 아니라 **「보통보다 얼마나
      몰려 있나」**. 흔한 상대(삼성전자 4.37% · 국민연금 0.96%)는 자동으로
      아래로 내려간다.
    - 정렬은 **`shared` 1차, `lift` 2차.**
    - 위험 사건은 lift 가 아니라 **커버리지**로 본다 — 「이 사건에 걸린 회사가
      전체 2곳인데 둘 다 여기 있다」.
    - `cascade_risk` 의 `stated=false` 는 **기사에 없고 우리가 계산한 것**이다.
    - 담은 곳이 **1곳이면 빈 배열**이다. 「겹친다」가 성립하지 않는다.
    - 아무것도 안 겹치면 `no_overlap` 카드 한 장이 나간다 — 빈 화면보다 낫다.
    """
    return [InsightCard(**c) for c in
            insight_service.workspace_insights(body.keys, limit=body.limit)]


# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


# ★추론 담당은 이 HTTP 를 거치지 않고 `app/services/` 를 그대로 import 한다
#   (같은 레포·같은 프로세스). 이 라우트는 **백엔드가 볼 모양**이다.
@app.post("/retrieve", response_model=RetrieveResponse, tags=["챗봇"],
          summary="챗봇이 쓸 사실과 근거")
async def retrieve(body: AskRequest) -> RetrieveResponse:
    """챗봇이 인용할 **사실과 근거만** 돌려준다. **답변 문장은 만들지 않는다** —
    생성은 추론 담당 몫이다. 경계를 섞으면 「누가 지어냈나」를 못 가린다.

    `evidence[].evidence_id` 를 반드시 붙인다. 화면이 답과 근거를 나란히 놓는다.

    - `workspace_keys` 는 **선택이고 필터가 아니다.** 주면 Global 검색 결과의
      **순서**에 반영되고, 안 주면 Global Ranking 그대로다 — 워크스페이스 밖
      기업도 그대로 나온다(최종 설계 §6-2).
    - `evidence[].missing=true` 는 **id 는 있는데 원문을 못 찾은 것**이다. 지우지
      않고 알린다 — 지우면 「근거가 없는 관계」로 읽힌다. **인용하면 안 된다.**
    - `propagation[].stated` 로 **보도와 계산을 가른다.** 섞어 말하면 추론을
      사실로 파는 것이 된다.
    """
    # ★이 라우트는 **어댑터다.** 로직은 RetrieveService 에 있다 — 추론 담당은
    #   이 HTTP 를 거치지 않고 같은 서비스를 직접 import 한다(같은 프로세스).
    #   여기 로직을 넣으면 두 입구가 다르게 동작한다.
    return await _retrieve_service.retrieve_async(body)


@app.post("/ask", response_model=AskResponse, tags=["챗봇"],
          summary="챗봇 답변 생성")
async def ask(body: AskRequest) -> AskResponse:
    """질문 하나 → LLM 이 쓴 답변 + 화이트리스트를 통과한 근거.

    `/retrieve` 가 만든 재료 밖의 것은 인용할 수 없다 — `sources` 에 실리는
    `evidence_id` 는 전부 서버가 재료 안에서 확인한 것이다.

    - `workspace_keys` 는 **선택이다.** 비어 있어도 Global Search 로 답한다
      (최종 설계 §6-1) — 워크스페이스는 검색 경계가 아니라 랭킹 문맥이다.
    - `anchor_source` 로 **무엇을 대상으로 답했는지**를 가른다 —
      `query`(질문이 지정) / `context`(보고 있는 기업) /
      `anchorless`(지정하지 않음 — 정상) / `unresolved`(지정했는데 못 찾음).
      ★`unresolved` 면 LLM 을 부르지 않고 못 찾았다고만 답한다. 워크스페이스
      기업으로 **갈아타지 않는다**(설계서 §14-3).
    - `failed=true` 면 `answer` 는 고정 안내 문구다. `sources` 는 그래도 원본
      근거를 담고 있다 — 답을 못 썼어도 근거는 보여줄 수 있다.
    - `missing=true` 였던 근거는 `sources` 에 오지 않는다.
    """
    # ★실행 담당이 **LangGraph 로 넘어갔다**(Phase 1). 동작은 그대로다 —
    #   재료 조립도 프롬프트도 후처리도 전부 같은 함수를 그대로 부른다.
    #   실측(2026-08-27): 대표 질문 20개 전부 프롬프트·응답이 바이트까지 동일
    #   (`batch/audit/ask_graph_parity.py`).
    #
    # ★그래프 노드는 전부 **sync** 다. 안이 Neo4j·PostgreSQL·OpenAI 왕복이라
    #   이벤트루프에서 그냥 부르면 멈춘다 — `retrieve_async()` 와 같은 이유로
    #   threadpool 로 내린다. `contextvars`(trace id)는 anyio 가 복사해 준다.
    return await run_in_threadpool(run_ask, body)


@app.get("/health", tags=["운영"], summary="상태 확인")
def health() -> dict:
    # ★`stub` 키를 **남긴다.** 값은 언제나 `false` 다(전부 실물이라 고정값 라우트가
    #   없다). 백엔드가 이미 읽고 있을 수 있어 키를 빼지 않는다 — 실물 여부는
    #   이 값이 아니라 **본문이 무엇을 부르는지**로 판단한다.
    return {"status": "ok", "stub": False, "version": app.version}


# ══════════════════════════════════════════════════════════════════
#  미리보기 — 응답이 화면에서 어떻게 보이는지
# ══════════════════════════════════════════════════════════════════
_PREVIEW = Path(__file__).with_name("preview.html")


@app.get("/preview", include_in_schema=False)
def preview() -> HTMLResponse:
    return HTMLResponse(_PREVIEW.read_text(encoding="utf-8"))
