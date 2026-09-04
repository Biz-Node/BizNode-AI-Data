"""Agent 루프 평가셋 — 케이스 정의.

`tests/search/eval/` 과 **섞지 않는다.** 저쪽은 검색 계층의 회귀 기준선이고
(`SearchOrchestrator.search()` 까지), 여기는 그 위에서 **Agent 가 도구를 골라
재료를 모으는 루프**를 잰다. 재는 대상도 판정 기준도 다르다.

    tests/search/eval/    검색이 설계대로 갈리는가        LLM 을 안 부른다
    tests/agent/eval/     Agent 가 무엇을 얼마나 쓰는가   ★LLM 을 실제로 부른다

★**무엇을 판정하고 무엇을 판정하지 않는가.**

  판정한다    서버가 결정론으로 정하는 것 — `anchor_source` · Agent 호출 여부 ·
              예산 상한 준수 · 범위 준수 · 재료 하한
  판정 안 한다 **어떤 도구를 골랐는가**. 그건 LLM 이 정하고, 같은 질문에 다른
              조합이 나올 수 있다. 케이스마다 「이 도구를 반드시 불러야 한다」로
              박으면 모델을 바꿀 때마다 평가셋이 빨간불이 된다

  대신 **집합 수준**에서 강제한다 — 20 케이스 전체에서 도구 7종이 **한 번씩은
  실제로 불렸는가**(`test_every_tool_is_exercised`). 「5개 도구가 전부 호출되게
  구성한다」는 요구는 케이스 하나가 아니라 **평가셋 전체**의 성질이다.

★**anchor_source 네 갈래가 이 평가셋의 척추다.**

    QUERY       질문이 대상을 지정했고 해소됐다        → Agent 호출됨
    UNRESOLVED  지정했는데 못 찾았다                   → ★**Agent 미호출**
    CONTEXT     **보고 있는 기업**이 있다              → Agent 호출됨
    ANCHORLESS  질문이 대상을 **지정하지 않았다**       → Agent 호출됨 · 의미검색

  `ANCHORLESS` 가 「기업이 명시되지 않은 산업·주제·이벤트 탐색형」이다. 사용자가
  말한 `NOT_SPECIFIED` 가 이 상태다.

★**「출발점 없음」 갈래가 사라졌다**(최종 설계 §17-1). 워크스페이스도 화면 문맥도
  없으면 전에는 **검색조차 안 했는데**, 이제 Global Search 로 답한다 — 그 케이스는
  `ANCHORLESS` 로 흡수됐다. 앵커가 없다는 것과 답할 수 없다는 것은 다르다.

★**`CONTEXT` 는 2026-08-29 에 늘었다**(be2d985). 기업 상세 화면에서 「이 회사
  노조 리스크 어때?」를 물으면 「이 회사」는 **화면이 알고 문장은 모른다.** 그전에는
  워크스페이스가 있으면 담아 둔 기업으로 답하고 비면 검색조차 안 했다 — 둘 다
  「물은 것과 다른 대상으로 답하기」의 같은 종류였다.

★**`workspace_keys` 가 빈 케이스가 이 평가셋에 처음 들어온다.** 그러면
  `_ring_of` 는 양끝이 둘 다 밖이라 **전부 R3** 을 낸다. 링 분포를 한 표로 합치면
  R3 증가를 랭킹 변화로 잘못 읽으므로, 보고서가 `workspace_keys` 유무로 갈라
  찍는다(`report.py` §3-1).

★**질문은 전부 실측으로 골랐다.** 후보를 `search`+`resolve_anchor` 에 실제로
  통과시켜 어느 갈래로 떨어지는지 보고 확정했다. 추측으로 적으면 케이스가
  기대와 다른 경로를 재게 된다. 실측에서 걸러낸 것들:

    「요즘 공급망 리스크 큰 이슈가 뭐야?」  → 「요즘」이 실존 법인(01719318)으로
                                            잡혀 QUERY · 0건. 동음이의 결함
                                            (현황서 §4-5)과 같은 부류라 뺐다
    「인텔 파운드리 사업 어떻게 됐어?」     → 「파운드리서울」(01354528)에 붙었다
    「존재하지않는기업 관련 뉴스」          → UNRESOLVED 가 **아니라** ANCHORLESS
    「TSMC 최근 실적」                     → TSMC 가 그래프에 있어 QUERY

★**기업명·순위를 못 박지 않는다.** 관계 점수와 임베딩 유사도는 데이터가 늘면
  순서가 바뀐다. `tests/search/eval/cases.py` 의 `kind="structural"` 과 같은
  규약이고, 여기는 `kind="fixed"` 를 **앵커에만** 쓴다 — 앵커는 서버가 정하는
  결정론적 값이라 데이터가 늘어도 안 흔들린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.api.schemas import AnchorSource

# 워크스페이스 — 대표 질문이 삼성전자·SK하이닉스 중심이라 둘을 담는다.
# ★**랭킹 문맥이다.** 앵커로 승격되지 않는다(최종 설계 §17-3) — 앵커 없는 질문은
#   이 값이 있든 없든 `ANCHORLESS` 로 간다.
# (`batch/audit/ask_graph_parity._WORKSPACE` 와 같은 값.)
WORKSPACE = ("00126380", "00164779")


@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    question: str
    verifies: str                        # 이 케이스가 무엇을 검증하는가
    coverage: tuple[str, ...]            # 커버하는 분기

    # ── 서버가 결정론으로 정하는 값 — **여기만 못 박는다** ──────────
    # ★`Optional` 이 남아 있지만 **지금은 어느 케이스도 `None` 이 아니다.**
    #   전에는 출발점 게이트가 `resolve_anchor` 앞에서 끊어 State 에 `decision`
    #   이 안 생기는 케이스가 하나 있었다. 게이트가 없어져(최종 설계 §17-1)
    #   모든 요청이 앵커 판정을 지난다.
    expected_anchor_source: Optional[AnchorSource]
    # ★`UNRESOLVED` 면 `halt_no_material` 로 빠져 Agent 를 아예 안 부른다.
    expects_agent: bool

    workspace_keys: tuple[str, ...] = WORKSPACE
    # ★**「담은 것」이 아니라 「보고 있는 것」**이다(2026-08-29). 기업 상세 화면이
    #   넘기는 값이라, 워크스페이스가 비어도 대상이 있다. 기본이 빈 튜플인 이유는
    #   기존 20 케이스가 전부 화면 문맥 없이 묻는 질문이기 때문이다.
    context_keys: tuple[str, ...] = ()

    # ── 도구 — **기대일 뿐 강제가 아니다** ─────────────────────────
    # 이 질문이 끌어오도록 설계된 도구들. 판정은 「이 중 최소 하나」다.
    # 전부를 요구하면 LLM 의 선택 하나에 케이스가 죽는다.
    expects_tools: tuple[str, ...] = ()
    # ★절대 불리면 안 되는 도구. 계약 위반을 잡는 자리다.
    must_not_call: tuple[str, ...] = ()

    # ── 재료 하한 — **상한은 예산이 본다** ────────────────────────
    min_evidence: int = 0
    min_relations: int = 0
    min_events: int = 0
    # 답변이 실제로 나오는가(`failed=False`). 재료가 보장된 질문에만 켠다.
    expects_answer: bool = True

    known_issue: Optional[str] = None

    def is_semantic_probe(self) -> bool:
        """기업을 지정하지 않은 탐색형 질문인가 — 의미검색이 본령인 케이스."""
        return self.expected_anchor_source is AnchorSource.ANCHORLESS


# 평가셋이 반드시 덮어야 하는 분기. 케이스를 지우다 분기가 통째로 비면
# `test_coverage_is_complete` 가 잡는다.
REQUIRED_COVERAGE = frozenset({
    "anchor:QUERY", "anchor:ANCHORLESS", "anchor:UNRESOLVED", "anchor:CONTEXT",
    # ★**게이트가 아니라 문맥 조합이다**(최종 설계 §17-1). 전에는 이 셋이
    #   `_has_starting_point` 의 통과/거절 분기였는데, 거절이 사라지면서 「어떤
    #   문맥으로 물었나」의 축으로 뜻이 바뀌었다. 셋 다 **답이 나가야 한다.**
    "ctx:워크스페이스 없음", "ctx:문맥 없음",
    "agent:호출됨", "agent:미호출",
    "tool:get_relations", "tool:get_events", "tool:search_news",
    "tool:search_dart", "tool:get_business_overview", "tool:get_market",
    "tool:get_filings",
    "scale:단일 도구", "scale:multi-tool",
    "company:단일", "company:복수",
    "topic:산업·주제 탐색", "topic:이벤트 탐색",
    "citation:인용 가능(뉴스)", "citation:인용 불가(사업개요)",
    "citation:근거 id 없음(계산값)",
    "event:노무", "event:실적", "event:정보유출", "event:자본거래", "event:품질",
})


CASES: tuple[AgentEvalCase, ...] = (

    # ══════════════════════════════════════════════════════════════
    #  A. anchor:QUERY — 기업을 지정한 질문
    # ══════════════════════════════════════════════════════════════

    AgentEvalCase(
        id="query-relations-supplies",
        question="삼성전자에 납품하는 기업은?",
        verifies="기업을 지정한 관계 질의가 QUERY 앵커로 가고, Agent 가 관계 도구를 "
                 "골라 재료를 채우는가. 실측: SUPPLIES_TO 1,179건 · 삼성전자 차수 1,169",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_relations",
                  "scale:단일 도구", "company:단일"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_relations",),
        min_relations=1,
        min_evidence=1,
    ),

    AgentEvalCase(
        id="query-events-labor",
        question="SK하이닉스 노조 관련 리스크 알려줘",
        verifies="사건 질의가 사건 도구를 끌어오는가. 실측: SK하이닉스 사건 69건에 "
                 "노무 포함 · 노무는 28사 94건",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_events",
                  "company:단일", "topic:이벤트 탐색", "event:노무"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_events", "search_news"),
        min_evidence=1,
    ),

    AgentEvalCase(
        id="query-overview-context-only",
        question="HD현대는 무슨 사업을 하는 회사야?",
        verifies="★사업의 내용은 **참고 맥락이고 인용할 수 없다**(`citation.py` "
                 "CONTEXT_ONLY). 도구가 불려 재료로 들어와도 근거 화이트리스트에 "
                 "오르지 않는가. 실측: HD현대 사업개요 16,623자 — 64사 중 가장 길다",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_business_overview",
                  "company:단일", "citation:인용 불가(사업개요)"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_business_overview",),
    ),

    AgentEvalCase(
        id="query-market-no-evidence-id",
        question="삼성전자 시가총액이랑 PER 알려줘",
        verifies="★시세는 **계산값이라 근거 id 가 없다**(`get_market` 에 evidence_id "
                 "없음). 근거 없이도 답이 나가되, 그 값이 근거인 척하지 않는가. "
                 "실측: market_data 53,045행 · 64사 × 125거래일",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_market",
                  "company:단일", "citation:근거 id 없음(계산값)"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_market",),
    ),

    AgentEvalCase(
        id="query-filings-list",
        question="HD현대가 낸 공시 목록을 보여줘",
        verifies="공시 **목록**(제목까지, 본문 없음)을 끌어오는가. 실측: HD현대 "
                 "documents 82건 — 파두 68 · 현대로템 55 다음으로 많다",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_filings",
                  "company:단일"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_filings",),
    ),

    AgentEvalCase(
        id="query-dart-evidence",
        question="현대로템 사업보고서 내용에서 주요 제품을 찾아줘",
        verifies="★공시 근거는 `search_dart` 가 집는다 — `search_news` 와 **같은 "
                 "컬렉션**을 `source_type` 으로만 가른다. 실측: 현대로템 dart 32 + "
                 "dart_filing 27 (dart_filing 은 전 기업 통틀어 113건뿐)",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:search_dart",
                  "company:단일"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("search_dart", "get_business_overview"),
    ),

    AgentEvalCase(
        id="query-news-citable",
        question="파두 실적 논란 어떻게 됐어?",
        verifies="★`search_news` 만 **인용 가능**하다(작업 B). 뉴스 근거가 재료로 "
                 "들어오면 화이트리스트에 오를 수 있는가. 실측: 파두 news 97건 · "
                 "사건 26건에 실적 포함 · 실적은 30사 60건",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:search_news",
                  "company:단일", "citation:인용 가능(뉴스)", "event:실적"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("search_news", "get_events"),
        min_evidence=1,
    ),

    # ── 복수 기업 ────────────────────────────────────────────────

    AgentEvalCase(
        id="query-multi-company-suit",
        question="삼성전자와 SK하이닉스 둘 다 관련된 소송 있어?",
        verifies="기업 둘을 담은 질문에서 앵커는 **하나**로 좁혀지되(최고점 1개, "
                 "`query_understanding._primary`) 재료는 워크스페이스 양쪽에서 "
                 "온다. 실측: SUES 339건 · 분쟁소송 36사 88건",
        coverage=("anchor:QUERY", "agent:호출됨", "company:복수",
                  "scale:multi-tool"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_relations", "get_events", "search_news"),
        min_evidence=1,
    ),

    AgentEvalCase(
        id="query-multi-company-relation",
        question="한미반도체와 SK하이닉스 관계 알려줘",
        verifies="두 기업 **사이의** 관계를 묻는 질문. 실측: 한미반도체 차수 164 · "
                 "근거 200건(news 169 · dart 28)",
        coverage=("anchor:QUERY", "agent:호출됨", "company:복수",
                  "tool:get_relations"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_relations",),
    ),

    AgentEvalCase(
        id="query-multitool-invest-and-price",
        question="레인보우로보틱스 최근 투자 상황이랑 주가도 같이 알려줘",
        verifies="★한 질문이 **성격이 다른 두 도구**를 요구한다 — 관계(그래프)와 "
                 "시세(계산값). Agent 가 한 바퀴에 둘을 고르는가. 실측: "
                 "레인보우로보틱스 차수 181 · 사건 21건 · 근거 246건",
        coverage=("anchor:QUERY", "agent:호출됨", "scale:multi-tool",
                  "company:단일"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_relations", "get_market", "get_events"),
    ),

    # ── 사건 유형의 꼬리 ─────────────────────────────────────────

    AgentEvalCase(
        id="query-event-info-leak",
        question="현대오토에버 정보유출 사건",
        verifies="사건 유형의 **꼬리**를 덮는다 — 정보유출은 14사 19건뿐이다. "
                 "드문 유형도 재료가 잡히는가. 실측: 현대오토에버 사건 13건에 "
                 "정보유출 포함",
        coverage=("anchor:QUERY", "agent:호출됨", "tool:get_events",
                  "topic:이벤트 탐색", "event:정보유출"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_events", "search_news"),
        min_evidence=1,
    ),

    AgentEvalCase(
        id="query-event-capital-smallcap",
        question="심텍 최근 자본거래 알려줘",
        verifies="★**중소형 기업 · 얇은 재료**. 기존 20질문이 삼성전자·SK하이닉스에 "
                 "쏠려 못 보던 자리다. 실측: 심텍 근거 42건(news 23 · dart 18) — "
                 "삼성전자 1,247건의 3%. 자본거래는 47사 72건",
        coverage=("anchor:QUERY", "agent:호출됨", "topic:이벤트 탐색",
                  "event:자본거래", "company:단일"),
        expected_anchor_source=AnchorSource.QUERY,
        expects_agent=True,
        expects_tools=("get_events", "search_news", "search_dart"),
    ),

    # ══════════════════════════════════════════════════════════════
    #  B. anchor:ANCHORLESS — ★기업을 지정하지 않은 탐색형
    #     (사용자가 말한 "Anchor NOT_SPECIFIED" 가 이것이다)
    # ══════════════════════════════════════════════════════════════

    AgentEvalCase(
        id="ws-semantic-strike",
        question="반도체 업계 파업 위험이 있나?",
        verifies="★**기업을 지정하지 않았는데 Agent 가 불린다.** 앵커는 **없고** "
                 "(최종 설계 §17-3 — 워크스페이스로 승격하지 않는다) 재료는 검색 "
                 "히트가 댄다. `UNRESOLVED` 와 갈리는 지점이다 — 저쪽은 Agent 를 "
                 "아예 안 부른다. 실측: SEMANTIC 10건",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "topic:산업·주제 탐색",
                  "tool:search_news"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("search_news", "get_events"),
    ),

    AgentEvalCase(
        id="ws-semantic-capital-trend",
        question="최근 자본거래 동향 알려줘",
        verifies="기업도 관계 키워드도 없는 **주제 탐색**. 의미검색이 재료를 열고 "
                 "워크스페이스는 **순서에만** 관여한다. 실측: SEMANTIC 10건 · "
                 "자본거래 47사 72건",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "topic:산업·주제 탐색",
                  "event:자본거래"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("search_news", "get_events", "search_dart"),
    ),

    AgentEvalCase(
        id="ws-semantic-collusion",
        question="메모리 가격 담합 관련 소식",
        verifies="제품·행위만 있는 질문. 「메모리」가 기업으로 오인되지 않고 "
                 "ANCHORLESS 로 가는가. 실측: SEMANTIC 10건",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "topic:산업·주제 탐색"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("search_news", "get_relations"),
    ),

    AgentEvalCase(
        id="ws-semantic-quality",
        question="품질 문제로 논란된 사례 있어?",
        verifies="사건 유형 중 **가장 얇은 축**(품질 7사 18건)을 기업 지정 없이 "
                 "찾는다. 재료가 얇을 때 Agent 가 도구를 더 부르는지 보는 자리이기도 "
                 "하다. 실측: SEMANTIC 10건",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "topic:이벤트 탐색",
                  "event:품질"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("search_news", "get_events"),
    ),

    AgentEvalCase(
        id="ws-market-across-workspace",
        question="우리 워크스페이스 기업들 주가 어때?",
        verifies="★기업 미지정 + **계산값 도구**. 질문이 워크스페이스 전체를 "
                 "가리키므로 `get_market` 을 기업마다 불러야 한다 — 도구 호출 횟수가 "
                 "늘어나는 자리다(예산 관측). ★앵커는 여전히 **없다** — 재료 범위는 "
                 "검색 히트가 정한다. 실측: 워크스페이스 2사 × 125거래일",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "tool:get_market",
                  "company:복수", "scale:multi-tool"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("get_market",),
    ),

    AgentEvalCase(
        id="ws-relationship-regulator",
        question="최근 규제당국 조사 동향",
        verifies="★기업 미지정인데 검색은 **SEMANTIC 이 아니라 RELATIONSHIP** 으로 "
                 "간다 — 관계 키워드가 잡혔기 때문이다. ANCHORLESS 가 의미검색과 "
                 "1:1이 아님을 드러내는 대조군. 실측: RELATIONSHIP 10건 · "
                 "REGULATES 398건 · 규제수사 26사 50건",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "topic:산업·주제 탐색",
                  "tool:get_relations"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        expects_tools=("get_relations", "get_events"),
    ),

    # ══════════════════════════════════════════════════════════════
    #  C. anchor:UNRESOLVED — ★Agent 를 부르지 않는다
    # ══════════════════════════════════════════════════════════════

    AgentEvalCase(
        id="unresolved-unknown-company",
        question="무한상사 실적 알려줘",
        verifies="★**지정했는데 못 찾으면 워크스페이스로 갈아타지 않는다** — "
                 "그러면 「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이 "
                 "된다(설계서 §14-3). `halt_no_material` 로 빠져 **Agent 를 아예 "
                 "안 부르므로** 도구 호출도 0 이어야 한다",
        coverage=("anchor:UNRESOLVED", "agent:미호출"),
        expected_anchor_source=AnchorSource.UNRESOLVED,
        expects_agent=False,
        must_not_call=("get_relations", "get_events", "search_news", "search_dart",
                       "get_business_overview", "get_market", "get_filings"),
        # ★`failed=False` 다 — 재료가 없다고 알리는 것은 실패가 아니다
        #   (`halt_no_material` 독스트링).
        expects_answer=True,
    ),

    AgentEvalCase(
        id="unresolved-gibberish",
        question="storminmvpsdjfk 이 뭐야",
        verifies="의미 없는 문자열도 **이름으로 지정된 것**으로 읽혀 UNRESOLVED 로 "
                 "간다. 의미검색이 10건을 냈어도 그것을 이름 해소로 둔갑시키지 "
                 "않는가 — 재료가 있다고 Agent 를 부르면 안 된다",
        coverage=("anchor:UNRESOLVED", "agent:미호출"),
        expected_anchor_source=AnchorSource.UNRESOLVED,
        expects_agent=False,
        must_not_call=("get_relations", "get_events", "search_news", "search_dart",
                       "get_business_overview", "get_market", "get_filings"),
        expects_answer=True,
    ),

    # ══════════════════════════════════════════════════════════════
    #  D. anchor:CONTEXT — ★보고 있는 기업 (2026-08-29 신설)
    # ══════════════════════════════════════════════════════════════
    #
    # ★기업은 **현대자동차(00164742)**다. 워크스페이스(삼성전자·SK하이닉스)
    #   **밖**이라 「담지 않은 기업을 대상으로 답한다」가 실제로 성립하고,
    #   재료도 있다(실측 2026-08-29: 관계 10 · 사건 8 · 뉴스 10 · 공시 5).
    #
    # ★질문에 기업명을 **안 쓴다.** 쓰면 `resolved_entities` 가 잡혀 `QUERY` 로
    #   가버려 이 갈래를 못 덮는다 — 「이 회사」가 화면에서만 온다는 것이
    #   이 분기의 전부다.

    AgentEvalCase(
        id="ctx-detail-page-no-workspace",
        question="이 회사에 노무 관련 리스크가 있었나?",
        verifies="★**워크스페이스가 비어도 답한다.** 화면이 대상을 알고 있으므로 "
                 "`context` 앵커가 선다 — 담아야만 물어볼 수 있는 것이 아니다. "
                 "★`workspace_keys` 가 비어 `_ring_of` 가 **전부 R3** 을 내는 "
                 "케이스라(보고서 §3-1 이 갈라 찍는 이유) `ctx-beats-workspace` 와 "
                 "대조군을 이룬다. 실측: anchor_source=context · SEMANTIC 10건",
        coverage=("anchor:CONTEXT", "agent:호출됨", "ctx:워크스페이스 없음",
                  "company:단일", "event:노무"),
        expected_anchor_source=AnchorSource.CONTEXT,
        expects_agent=True,
        workspace_keys=(),
        context_keys=("00164742",),
        expects_tools=("get_events", "search_news"),
    ),

    AgentEvalCase(
        id="ctx-beats-workspace",
        question="이 회사 최근에 무슨 일이 있었어?",
        verifies="★**담아 둔 기업이 있어도 보고 있는 기업이 먼저다.** 워크스페이스로 "
                 "가면 「현대자동차 페이지를 보며 물었는데 삼성전자로 답하는」 것이 "
                 "되고, 그건 §14-3 이 막으려는 오답과 같은 종류다. "
                 "★앞 케이스와 **워크스페이스만 다르다** — 링이 R3 일색이 아니라 "
                 "정상 분포로 나와야 하므로, 두 케이스가 §3-1 의 두 줄이 된다",
        coverage=("anchor:CONTEXT", "agent:호출됨", "topic:이벤트 탐색"),
        expected_anchor_source=AnchorSource.CONTEXT,
        expects_agent=True,
        context_keys=("00164742",),          # workspace_keys 는 기본값 그대로
        expects_tools=("get_events", "get_relations"),
    ),

    # ══════════════════════════════════════════════════════════════
    #  E. 문맥이 하나도 없다 — ★그래도 답한다 (최종 설계 §17-1)
    # ══════════════════════════════════════════════════════════════

    AgentEvalCase(
        id="global-no-context-at-all",
        question="최근 반도체 업계 주요 이슈가 뭐야?",
        verifies="★**이 케이스의 기대가 통째로 뒤집혔다**(최종 설계 §6-1·§17-1). "
                 "전에는 `gate-no-starting-point` 였고 「검색조차 하지 않는다」를 "
                 "지켰다 — 담은 것도 보고 있는 것도 없으면 거절이었다. 지금은 "
                 "Home 화면에서 아무것도 안 담고 던지는 **정상 질문**이고, Global "
                 "Search 로 재료를 모아 답한다(사용자 시나리오 1). "
                 "★`workspace_keys` 도 `context_keys` 도 비어 있어 워크스페이스 "
                 "랭킹이 **전혀 개입하지 않는** 유일한 케이스다 — Global Ranking "
                 "단독 경로의 기준선이 된다",
        coverage=("anchor:ANCHORLESS", "agent:호출됨", "ctx:문맥 없음",
                  "ctx:워크스페이스 없음", "topic:산업·주제 탐색"),
        expected_anchor_source=AnchorSource.ANCHORLESS,
        expects_agent=True,
        workspace_keys=(),
        context_keys=(),
        expects_tools=("search_news", "get_events"),
        expects_answer=True,
    ),
)
