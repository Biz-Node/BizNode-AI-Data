"""BizNode 데이터 API — **스텁 서버.**

★지금은 DB 를 붙이지 않고 **고정된 답만** 돌려준다

  프론트는 응답이 진짜인지 가짜인지 **구분하지 않는다.** 모양만 같으면 화면을
  끝까지 만들 수 있다. 그래서 모양을 먼저 확정하고 내용을 나중에 채운다 —
  그동안 프론트·백엔드·추론이 대기하지 않아도 된다.

      1단계  응답 계약        app/api/schemas.py   ← 코드가 곧 문서
      2단계  스텁 서버        이 파일             ← 여기서 팀이 합류
      3단계  하나씩 진짜로     app/services/       ← 라우트는 그대로 두고 속만 간다

  `X-Stub: true` 헤더가 붙어 나간다. **진짜로 바뀌면 헤더가 사라진다** —
  프론트가 「이거 아직 가짜인가」를 눈으로 확인할 수 있다.

★경계 — 이 API 는 사용자를 모른다

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

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import examples as ex
from app.services import search_service
from app.api.schemas import (
    AskRequest, CompanyDetail, CompanySummary, ErrorResponse, Event,
    Filing, GraphResponse, InsightCard, MarketResponse, NewsFeedResponse,
    NewsItem, Propagation, Relation, RelationDetail, RetrieveResponse,
    RiskEvent, SearchResponse, TrendingItem, WorkspaceChangesRequest,
    WorkspaceChangesResponse, WorkspaceGraphRequest, WorkspaceSuggestRequest,
    WorkspaceSuggestResponse, WorkspaceSummaryRequest,
)

app = FastAPI(
    title="BizNode 데이터 API",
    version="0.1.0-stub",
    description=(
        "기업 관계 그래프 조회 API.\n\n"
        "`/search` 는 **실제 DB 를 읽습니다.** 나머지는 아직 스텁이라 고정된 답을 "
        "돌려주고, 응답에 `X-Stub: true` 헤더가 붙습니다 — **헤더가 없으면 진짜**입니다.\n\n"
        "스텁 값도 전부 **실제 데이터에서 뽑은 것**입니다 — 삼성전자 시가총액 1,599.7조·"
        "PER 35.39 는 실제 조회 결과입니다.\n\n"
        "### 화면을 만들 때 반드시 다뤄야 하는 것\n"
        "- 검색 **0건**과 **여러 건** — 「삼성」은 295건입니다(그래프 46 · 명부 249)\n"
        "- `in_graph = false` — **실재하지만 우리가 안 모은 회사**입니다. "
        "「없는 회사」가 아니라 「자료가 없는 회사」로 표시해야 합니다\n"
        "- `detail_level = relations_only` — 재무가 **없는 게 정상**인 기업이 대다수입니다\n"
        "- `listed = false` — 비상장 3,005곳은 시장 블록이 통째로 `null` 입니다\n"
        "- 워크스페이스의 **섬** — 아무와도 안 이어진 기업을 억지로 잇지 않습니다\n\n"
        "### 단위\n"
        "금액은 **원**, 비율은 **퍼센트(0~100)**, 날짜는 **ISO 8601** 입니다."
    ),
    openapi_tags=[
        {"name": "검색", "description": "부분 일치로 찾기. **★실제 데이터**"},
        {"name": "기업", "description": "기업 상세 · 시장 · 사건 · 뉴스 · 공시"},
        {"name": "관계", "description": "관계 목록 · 근거 원문 · 리스크 파급"},
        {"name": "워크스페이스", "description": "담아 둔 기업 문맥의 요약과 그래프"},
        {"name": "뉴스", "description": "뉴스 피드 · 최근 위험 사건"},
        {"name": "홈", "description": "트렌드 · 인사이트 카드"},
        {"name": "챗봇", "description": "추론 계층이 쓰는 재료"},
    ],
)

# 프론트가 로컬에서 바로 붙어 볼 수 있게. ★배포 때는 백엔드 도메인만 남긴다
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


# ★실제 DB 를 읽는 라우트. 하나씩 진짜로 바뀔 때마다 여기 추가한다.
_REAL: set[str] = {"/search"}


@app.middleware("http")
async def _mark_stub(request, call_next):
    """**아직 가짜인 라우트에만 표시.**

    프론트가 「이 화면은 아직 고정값인가」를 눈으로 확인할 수 있다.
    3단계에서 하나씩 갈아끼울 때마다 `_REAL` 에 넣으면 헤더가 사라진다.
    """
    resp: Response = await call_next(request)
    if request.url.path not in _REAL:
        resp.headers["X-Stub"] = "true"
    return resp


# ══════════════════════════════════════════════════════════════════
#  검색
# ══════════════════════════════════════════════════════════════════


@app.get("/search", response_model=SearchResponse, tags=["검색"],
         summary="이름·별칭 부분일치로 기업 찾기 ★실제 데이터")
def search(q: str = Query(description="기업명 또는 별칭. **부분 일치**", examples=["삼성"]),
           limit: int = Query(20, ge=1, le=100),
           include_registry: bool = Query(
               True, description="DART 명부에만 있는 회사도 포함할까")) -> SearchResponse:
    """★**이 라우트는 스텁이 아니라 실제 DB 를 읽습니다.**

    ### 두 곳을 함께 뒤집니다

        우리 그래프    3,432곳    관계·사건·재무가 있다      in_graph = true
        DART 명부    118,535곳   이름과 번호만 있다         in_graph = false

    사용자가 「한화오션엔지니어링」을 찾는데 우리가 아직 안 모았다면
    **없다고 답하면 안 됩니다.** 실재하는 회사입니다. `in_graph=false` 로
    돌려주니 화면은 **「수집되지 않은 기업입니다」**라고 알리고, 관계·재무
    조회는 걸지 말아야 합니다.

    ### 부분 일치입니다

    「삼성」 → 삼성전자·삼성에스디에스·삼성중공업… 실측 **295건**
    (그래프 46 · 명부 249). 사용자는 회사 이름 전체를 정확히 외우고 있지
    않습니다.

    ### `total` 은 가져온 수가 아니라 **있는 수**입니다

    명부 쪽은 한 번에 50건까지만 실어 보냅니다. `total`(295)과
    `len(hits)`(20)가 다를 수 있으니 화면은 **「295건 중 20건」**이라고 쓰면
    됩니다.

    ### 순위

        1  이름이 정확히 같다
        2  이름이 그 말로 시작한다        「삼성전자」 ← 「삼성」
        3  이름 안에 들어 있다
        4  옛 표기(별칭)로 걸렸다
        같은 등급이면 → 그래프 노드 먼저 → 관계 수(degree) 많은 순

    ### 이름을 키로 쓰지 않습니다

    여기서 고른 `key` 를 이후 모든 조회에 씁니다.
    `GET /companies/{이름}` 같은 주소는 **만들지 않습니다** — 명부에는
    같은 이름의 다른 법인이 있습니다(「신우」 138건).
    """
    return SearchResponse(**search_service.search(
        q, limit=limit, include_registry=include_registry))


# ══════════════════════════════════════════════════════════════════
#  기업
# ══════════════════════════════════════════════════════════════════


@app.get("/companies/{key}", response_model=CompanyDetail, tags=["기업"],
         responses={404: {"model": ErrorResponse}}, summary="기업 상세 — 이 회사 자체의 전부")
def company_detail(key: str) -> CompanyDetail:
    """워크스페이스 좌 패널이 **「지금 보는 그래프 안에서의 이 회사」**라면,
    이 화면은 **「이 회사 자체의 전부」**입니다. 워크스페이스 없이도 열립니다.

    ★`detail_level` 을 반드시 보세요. 실제 분포가 이렇습니다:

        재무 있음   477곳 (14%)
        시세 있음   427곳 (12%)
        공시·개요    64곳 (1.9%)
        나머지 2,900여 곳은 이름·업종·관계만

    **재무가 없는 게 오류가 아니라 정상**입니다. 스텁에서 `엔비디아` 로
    조회하면 그 모양을 볼 수 있습니다.

    ★지배구조는 **양방향**입니다 — `owned_by`(이 회사를 소유한 쪽)와
    `owns`(이 회사가 소유한 쪽)로 나눠 보냅니다.
    """
    if key in ("엔비디아", "nvidia", "NVIDIA"):
        return ex.COMPANY_RELATIONS_ONLY
    if key == "없는키":
        raise HTTPException(404, "해당 키의 기업이 없습니다")
    return ex.COMPANY_FULL


@app.get("/companies/{key}/graph", response_model=GraphResponse, tags=["기업"],
         summary="이 기업 중심 그래프")
def company_graph(key: str, depth: int = Query(1, ge=1, le=2)) -> GraphResponse:
    """`depth=2` 는 **허브를 지나면 폭발합니다.** 연결 150 초과 노드가 14곳이라
    삼성전자를 거쳐 두 칸을 가면 거의 모든 회사가 딸려옵니다.
    `degree` 를 함께 보내니 화면이 흐리게 그리거나 접을 수 있습니다."""
    return ex.COMPANY_GRAPH


@app.get("/companies/{key}/market", response_model=MarketResponse, tags=["기업"],
         summary="주가·등락률·시총·PER·PBR·PSR")
def market_of(key: str, days: int = Query(30, ge=1, le=365)) -> MarketResponse:
    """**시가총액·PER·PBR·PSR 은 저장돼 있지 않습니다.** 조회할 때 계산합니다 —
    종가 × 유통주식수, 그리고 우리 재무로 나눕니다. 저장하면 원본이 갱신될 때
    어긋나기 때문입니다.

    그래서 `fin_year`·`fs_div` 가 값과 함께 나갑니다. 「PER 35.4」만 주면
    화면이 **근거를 못 밝힙니다** — 남의 API 를 안 쓰는 이유이기도 합니다
    (연결인지 별도인지, 어느 분기 실적인지를 모릅니다).

    ★**상장사 427곳에만 있습니다.** 비상장이면 `listed=false` 에 나머지가
    전부 `null` 입니다. 스텁에서 `엔비디아` 로 확인하세요.

    ★유통주식수를 못 믿는 기업(DART 공시 단위 오류)도 `null` 로 나갑니다.
    그대로 두면 시가총액 순위가 통째로 뒤집힙니다.
    """
    if key in ("엔비디아", "nvidia", "NVIDIA"):
        return ex.MARKET_UNLISTED
    return ex.MARKET_RESPONSE


@app.get("/companies/{key}/events", response_model=list[Event], tags=["기업"],
         summary="사건 목록 (시계열 포함)")
def events_of(key: str) -> list[Event]:
    """★`timeline` 이 붙은 사건은 **여러 국면이 한 노드에 모인 것**입니다.
    「파업 리스크」가 2022~2025년 4개 국면을 들고 있는 식입니다 —
    흩어진 기사가 아니라 **하나의 줄거리**로 보여주세요.

    ★`role` 을 보세요. `mentioned` 는 **당사자가 아니라 이름만 나온 것**입니다.
    이걸 당사자로 세면 「이 기업에 난 일」 집계가 부풀려집니다."""
    return ex.EVENTS_OF


@app.get("/companies/{key}/news", response_model=list[NewsItem], tags=["기업"],
         summary="관련 기사")
def news_of(key: str, limit: int = Query(20, ge=1, le=100)) -> list[NewsItem]:
    """★**본문은 저장하지 않습니다**(저작권). 제목·언론사·발행일·링크까지입니다.
    인용이 필요하면 관계의 근거 문장(`/relations/{id}`)을 쓰세요."""
    return ex.NEWS_OF


@app.get("/companies/{key}/filings", response_model=list[Filing], tags=["기업"],
         summary="DART 공시 목록")
def filings_of(key: str, limit: int = Query(20, ge=1, le=100)) -> list[Filing]:
    """★시드 64곳에만 있습니다."""
    return ex.FILINGS_OF


@app.get("/companies/{key}/relations", response_model=list[Relation], tags=["관계"],
         summary="관계 목록 (신선도·검증 적용)")
def relations_of(key: str) -> list[Relation]:
    """**근거 검증에서 걸린 관계는 여기 오지 않습니다.** 파급 계산도
    마찬가지입니다 — 근거 없는 관계 하나가 **없는 파급을 만들어 냅니다.**

    `freshness` 가 `stale` 이어도 지우지 않고 보냅니다. 뉴스는 관계의 시작만
    보도하고 **종료는 보도하지 않기** 때문에, 오래됐다고 지우면 살아 있는
    관계를 잃습니다. 대신 **언제 봤는지**를 함께 보내니
    「2024-06에 그렇게 보도됨」으로 표시하세요."""
    return ex.RELATIONS_OF


# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════


@app.get("/relations/{edge_id}", response_model=RelationDetail, tags=["관계"],
         responses={404: {"model": ErrorResponse}}, summary="관계 + 근거 원문 + 파급")
def relation_detail(edge_id: str) -> RelationDetail:
    """선을 클릭했을 때 뜨는 것입니다.

    ★`evidence[].text` 가 **원문 그대로**입니다. 우리가 요약한 문장이 아니라
    기사·공시에 실제로 쓰여 있는 문장이라, 화면이 그대로 인용할 수 있습니다."""
    return ex.RELATION_DETAIL


@app.get("/events/{event_id}/impact", response_model=list[Propagation], tags=["관계"],
         summary="리스크 파급 경로와 점수")
def propagate_risk(event_id: str,
                   max_hops: int = Query(3, ge=1, le=4)) -> list[Propagation]:
    """**저장하지 않고 질의 시점에 계산합니다.** 그래프가 바뀌면 점수도 바뀌어야
    하는데, 저장해 두면 낡은 값이 남습니다.

    ★허브를 지나는 경로는 **약하게 봅니다**(`40/(40+차수-1)`). 삼성전자를
    거치면 거의 모든 회사에 닿는데, 그건 관계가 있다는 뜻이 아니라
    **삼성전자가 크다는 뜻**이기 때문입니다."""
    return ex.PROPAGATION


# ══════════════════════════════════════════════════════════════════
#  워크스페이스
# ══════════════════════════════════════════════════════════════════


@app.post("/workspace/summary", response_model=CompanySummary, tags=["워크스페이스"],
          summary="기업 요약 — 담아 둔 기업 목록을 함께 받습니다")
def company_summary(body: WorkspaceSummaryRequest) -> CompanySummary:
    """**「기업 상세」와 다른 화면입니다.**

    `workspace_relations` 는 **담아 둔 다른 기업들과 어떻게 이어지는지**라서,
    기업 키만으로는 답이 안 나옵니다. 그래서 `GET` 이 아니라 `POST` 로
    목록을 함께 받습니다.

    ★워크스페이스 자체는 **백엔드가 저장합니다.** 이 API 는 키 목록을
    받을 뿐 누구의 워크스페이스인지 모릅니다."""
    return ex.COMPANY_SUMMARY


@app.post("/workspace/graph", response_model=GraphResponse, tags=["워크스페이스"],
          summary="담긴 기업들의 그래프")
def workspace_graph(body: WorkspaceGraphRequest) -> GraphResponse:
    """세 단계로 만듭니다.

        ① 담긴 기업끼리 직접 이어진 엣지        언제나 포함
        ② 섬이 된 기업만 골라 한 칸 건너 잇기     **허브는 다리로 안 씀**
        ③ 그래도 못 이으면 섬으로 두되 표시       억지로 잇지 않음

    ★②가 중요합니다. 실측으로 「고영」을 나머지와 이어 주는 중간 노드가
    **삼성자산운용**(연결 32)이었는데, 자산운용사가 양쪽에 지분이 있을 뿐
    두 회사가 관계있다는 뜻이 아닙니다. 이런 걸 다리로 쓰면 펀드 하나가
    모든 회사를 이어버립니다.

    ★③이 더 중요합니다. **없는 관계를 그리는 것보다 없다고 말하는 게 낫습니다.**
    `islands` 에 담아 보내니 화면이 「이 회사는 담긴 다른 회사들과 직접 연결이
    없습니다」라고 알려 주세요.

    ★`nodes[].role` 로 **담은 기업(`pinned`)과 이어주려고 딸려온 노드(`bridge`)**
    를 가릅니다. 다리를 흐리게 그리면 한눈에 구별됩니다."""
    return ex.WORKSPACE_GRAPH


@app.post("/workspace/suggest", response_model=WorkspaceSuggestResponse,
          tags=["워크스페이스"], summary="같이 담을 만한 기업")
def workspace_suggest(body: WorkspaceSuggestRequest) -> WorkspaceSuggestResponse:
    """**워크스페이스의 첫 벽을 넘겨 주는 자리입니다.**

    「한 곳만 담으면 관계가 안 보인다」가 이 화면의 시작 문제라, 기업을 담는
    순간 **다음에 뭘 담을지** 알려 줘야 합니다.

    ### 왜 추천인지를 반드시 함께 줍니다

        한미반도체   공통 고객 4곳 — 삼성전자 · SK하이닉스 · 마이크론 · 엔비디아
        이오테크닉스  공통 고객 3곳 — 삼성전자 · SK하이닉스 · 마이크론

    「한미반도체를 추천합니다」만으로는 담을 이유를 모릅니다. `reason_text` 를
    그대로 화면에 쓰면 됩니다.

    ### 추천 근거 다섯

        shared_customer   같은 고객에게 판다      ← 가장 쓸모 있음
        shared_supplier   같은 곳에서 사온다
        shared_owner      같은 주주가 들어와 있다
        same_event        같은 사건에 걸려 있다
        competitor        경쟁 관계다

    ★검색으로 기업을 담는 흐름에서 **2번은 백엔드 몫**입니다.

        1  GET  /search           키를 고른다
        2  백엔드가 자기 DB 에 저장  ← 우리는 누구의 워크스페이스인지 모릅니다
        3  POST /workspace/suggest  다음에 담을 것
        4  POST /workspace/graph    다시 그린다
    """
    return WorkspaceSuggestResponse(keys=body.keys, suggestions=ex.SUGGESTIONS[:body.limit])


@app.post("/workspace/changes", response_model=WorkspaceChangesResponse,
          tags=["워크스페이스"], summary="그동안 무엇이 바뀌었나 — 알림용")
def workspace_changes(body: WorkspaceChangesRequest) -> WorkspaceChangesResponse:
    """마이페이지 알림 셋이 여기서 나옵니다.

        새 위험 사건    new_risk_event
        새 관계        new_relation
        관계 종료      relation_ended

    ★**알림을 누구에게 보낼지는 우리가 모릅니다.** 키 목록과 기준 시각을 받아
    「그동안 뭐가 바뀌었나」만 답합니다. 구독 설정·발송은 백엔드 것입니다.

    ★`relation_ended` 는 지금 **언제나 빈 배열**입니다. 「이번 재적재에서 빠진
    관계 = 종료됨」으로 판정하는데, `loaded_at` 이 2026-07-31에 도입돼
    비교 대상이 없습니다. 다음 DART 재적재 이후에 채워집니다 —
    **필드는 미리 열어 두었으니 그때 계약이 바뀌지 않습니다.**
    """
    return WorkspaceChangesResponse(since=body.since, total=len(ex.CHANGES),
                                    changes=ex.CHANGES)


# ══════════════════════════════════════════════════════════════════
#  뉴스 · 홈
# ══════════════════════════════════════════════════════════════════


@app.get("/news", response_model=NewsFeedResponse, tags=["뉴스"], summary="뉴스 피드")
def news_feed(
    category: str | None = Query(None, description="공급망 · 지분 · 규제 · 사건"),
    workspace_keys: list[str] | None = Query(None, description="내 워크스페이스로 좁히기"),
    sort: str = Query("recent", description="recent · impact"),
    risk_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
) -> NewsFeedResponse:
    """★필터가 **세 축**입니다 — 주제 / 범위 / 정렬. 한 줄에 몰면
    「위험만」과 「내 워크스페이스」가 서로 배타적인지 겹쳐 쓸 수 있는지
    알 수 없습니다."""
    return ex.NEWS_FEED


@app.get("/risk-events", response_model=list[RiskEvent], tags=["뉴스"],
         summary="최근 위험 사건")
def risk_events(limit: int = Query(10, ge=1, le=50)) -> list[RiskEvent]:
    """`IMPACTS.sign = negative` 인 사건을 **영향받는 기업 수**로 정렬합니다."""
    return ex.RISK_EVENTS


@app.get("/trending", response_model=list[TrendingItem], tags=["홈"],
         summary="최근 많이 언급된 기업")
def trending(limit: int = Query(10, ge=1, le=50)) -> list[TrendingItem]:
    return ex.TRENDING


@app.get("/insights", response_model=list[InsightCard], tags=["홈"],
         summary="인사이트 카드")
def recent_insights(
    workspace_keys: list[str] | None = Query(
        None, description="**담아 둔 기업들.** 없으면 일반 카드만 나갑니다"),
    limit: int = Query(5, ge=1, le=20),
) -> list[InsightCard]:
    """★**워크스페이스 문맥이 있어야 쓸모가 생깁니다.**

    목업의 인사이트 셋이 전부 「합쳐야 드러나는 것」입니다.

        ⚠ 담은 5곳 **전부**가 같은 위험에 노출돼 있습니다
        ▣ 거래가 **세 곳에 몰려** 있습니다 — 삼성전자 5/5 · SK하이닉스 4/5
        ▦ 지분까지 **한 곳이 다** 들어와 있습니다

    각 기업을 하나씩 열어서는 안 보이고, 합쳐야 드러납니다 — 그래서
    워크스페이스가 필요한 것이고, 이 라우트가 `workspace_keys` 를 받는
    이유입니다. 키가 없으면 홈의 일반 카드만 나갑니다.

    ★`why` 를 반드시 함께 보여 주세요. **왜 그렇게 봤는지 없이 결론만 주면
    사용자가 검증할 방법이 없습니다.**"""
    return ex.INSIGHTS[:limit]


# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


@app.post("/retrieve", response_model=RetrieveResponse, tags=["챗봇"],
          summary="질문에 필요한 사실과 근거")
def retrieve(body: AskRequest) -> RetrieveResponse:
    """**답변을 만들지 않습니다.** 사실과 근거만 돌려줍니다 — 문장 생성은
    추론 담당 몫입니다. 경계를 섞으면 「누가 지어냈나」를 못 가립니다.

    ★추론 담당은 이 HTTP 를 거치지 않고 `app/services/` 를 그대로 import
    합니다(같은 레포·같은 프로세스). 이 라우트는 **백엔드가 볼 모양**을
    보여 주기 위한 것입니다.

    ★답변에는 `evidence[].evidence_id` 를 **반드시 붙입니다.** 그래야
    화면이 답과 근거를 나란히 놓을 수 있습니다."""
    return RetrieveResponse(
        question=body.question,
        companies=[ex.HYNIX],
        events=ex.EVENTS_OF,
        relations=[ex.REL_SUPPLY],
        propagation=ex.PROPAGATION,
        evidence=[ex.EVIDENCE_SUPPLY],
    )


@app.get("/health", tags=["홈"], summary="살아 있나")
def health() -> dict:
    return {"status": "ok", "stub": True, "version": app.version}
