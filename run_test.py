"""수동 시험 — 만든 것을 손으로 돌려 본다.

★**모드마다 비용이 다르다.** LLM 을 부르는 것은 `ask` **하나뿐**이다.

    python run_test.py                          모드 목록
    python run_test.py anchor "질문"             앵커가 어느 갈래로 가나      무료
    python run_test.py search "질의"             검색 계층만                  무료
    python run_test.py tools 삼성전자             도구 7종을 직접 부른다        무료
    python run_test.py ask "질문"                ★Agent 루프 끝까지           LLM 호출

  앞의 셋이 무료인 것이 중요하다 — 도구가 무엇을 돌려주는지, 앵커가 어디로
  가는지는 **LLM 없이 전부 확인된다.** `ask` 는 「Agent 가 그 도구들을 실제로
  골라 부르는가」를 볼 때만 쓰면 된다.

★`ask` 는 **관측치를 함께 찍는다** — 도구 호출 · 링 분포 · 예산 · 임베딩 ·
  최종 인용. 답변만 보면 「왜 이 재료가 왔나」를 되짚을 수 없다.

★워크스페이스가 없으면 `/ask` 는 검색조차 하지 않는다(설계서 §16-2). 기본값을
  삼성전자·SK하이닉스로 둔다 — `--workspace` 로 바꾼다.
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 기본 워크스페이스 — 대표 질문이 이 둘 중심이라 재료가 확실히 잡힌다.
_WORKSPACE = ["00126380", "00164779"]      # 삼성전자 · SK하이닉스

_LINE = "─" * 72


def _head(title: str) -> None:
    print(f"\n{_LINE}\n  {title}\n{_LINE}")


# ══════════════════════════════════════════════════════════════════
#  anchor — 질문이 어느 갈래로 가나 (무료)
# ══════════════════════════════════════════════════════════════════


def cmd_anchor(args) -> None:
    """★`/ask` 의 척추는 `anchor_source` 세 갈래다. 이 모드가 그것만 본다.

        QUERY       질문이 대상을 지정했고 해소됐다     → Agent 호출됨
        WORKSPACE   질문이 대상을 **지정하지 않았다**    → Agent 호출됨 · 의미검색
        UNRESOLVED  지정했는데 못 찾았다                → ★Agent 를 아예 안 부른다

    ★세 번째가 「TSMC 를 물었는데 삼성전자로 답하는」 오답을 막는 장치다 —
      워크스페이스로 **갈아타지 않는다**(설계서 §14-3).
    """
    from app.api.schemas import AskRequest
    from app.graph.nodes import material
    from app.graph.state import initial_state

    state = initial_state(AskRequest(question=args.question,
                                     workspace_keys=args.workspace))
    state.update(material.search(state))
    state.update(material.resolve_anchor(state))

    decision, result = state["decision"], state["result"]
    _head(f"앵커 판정 — {args.question!r}")
    print(f"  anchor_source : {decision.source.value}")
    print(f"  Agent 호출    : {'안 함 (halt_no_material)' if decision.source.value == 'unresolved' else '함'}")
    if decision.anchors:
        print(f"  앵커          : {', '.join(f'{a.name}({a.key})' for a in decision.anchors)}")
    if decision.named:
        print(f"  못 찾은 이름  : {decision.named!r}")
    print(f"  검색 mode     : {result.mode.value} · 히트 {result.total}건")
    if result.hits:
        print(f"  상위          : {' · '.join(h.name for h in result.hits[:5])}")


# ══════════════════════════════════════════════════════════════════
#  search — 검색 계층만 (무료 · 기존 동작)
# ══════════════════════════════════════════════════════════════════


def cmd_search(args) -> None:
    """`SearchOrchestrator.search()` → GraphSearcher·VectorSearcher 를 부르고
    ResultRanker 가 RRF 로 병합한 결과."""
    from search.dto.search_request import SearchRequest
    from search.service.factory import build_orchestrator

    query, result = build_orchestrator().search(
        SearchRequest(query=args.question, workspace_keys=args.workspace))

    _head(f"검색 — {args.question!r}")
    print(f"  mode {query.mode.value} · edge_types {query.edge_types} "
          f"· direction {query.direction} · 총 {result.total}건\n")
    for hit in result.hits:
        # ★`score` 가 아니라 `source_score` 다 — 점수 하나에 뜻이 셋 섞여 있어
        #   이름으로 갈랐다. 최종 순위는 `rank`, RRF 값은 `rrf_score`.
        rrf = f" rrf={hit.rrf_score:.5f}" if hit.rrf_score is not None else ""
        print(f"  {hit.rank:2}. {hit.name:24} {hit.entity_id:12} "
              f"score={hit.source_score:.4f}{rrf} {hit.sources}")


# ══════════════════════════════════════════════════════════════════
#  tools — 도구 7종을 직접 부른다 (무료)
# ══════════════════════════════════════════════════════════════════


def cmd_tools(args) -> None:
    """★**Agent 없이 도구만 부른다.** LLM 이 무엇을 고를지와 무관하게 「도구가
    무엇을 돌려주는가」를 본다 — 재료가 비면 도구 문제인지 Agent 문제인지
    여기서 갈린다.

    ★도구는 범위를 **인자로 받지 않는다**(4원칙 ①). 그래서 `anchor_scope` 를
      먼저 열어야 한다 — 안 열면 `scope.context()` 가 `None` 이라 거부된다.
    """
    import json

    from app.tools import agent_tools, citation, scope

    key = args.key
    rejected: list[str] = []
    _head(f"도구 7종 — key={key!r}  (LLM 없음)")

    with scope.anchor_scope([key], workspace_keys=args.workspace,
                            anchor_keys=[key], anchor_names=[key],
                            intent=args.query):
        calls = [
            ("get_relations", lambda: agent_tools.get_relations([key])),
            ("get_events", lambda: agent_tools.get_events([key])),
            ("search_news", lambda: agent_tools.search_news(args.query, [key])),
            ("search_dart", lambda: agent_tools.search_dart(args.query, [key])),
            ("get_business_overview",
             lambda: agent_tools.get_business_overview(key)),
            ("get_market", lambda: agent_tools.get_market(key)),
            ("get_filings", lambda: agent_tools.get_filings(key)),
        ]
        for name, call in calls:
            raw = call()
            got = json.loads(raw)
            citable = "인용 가능" if name in citation.CITABLE_TOOLS else "인용 불가"
            if isinstance(got, dict) and "error" in got:
                print(f"\n  ★{name:22} 거부 — {got['error']}")
                rejected.append(name)
                continue
            print(f"\n  {name:22} {len(got):3}건  ({citable})")
            for item in got[:args.limit]:
                print(f"      {_one_line(item)}")

    # ★그래프 도구(Neo4j)와 기업 도구(PostgreSQL)는 **key 를 다르게 해소한다.**
    #   표시명을 넣으면 앞의 넷만 조용히 거부되는데, 그걸 「재료가 없다」로
    #   읽기 쉽다. 실제로 그렇게 헷갈렸다 — 그래서 원인을 짚어 준다.
    graph_tools_rejected = {"get_relations", "get_events",
                            "search_news", "search_dart"} & set(rejected)
    if graph_tools_rejected and len(rejected) < 7:
        print(f"\n  ★그래프 도구 {len(graph_tools_rejected)}종만 거부됐습니다 — "
              f"key 형태 문제입니다.")
        print("    저쪽은 Neo4j 에서 Company 를 찾으므로 **corp_code 또는 norm_name** "
              "이어야 하고,")
        print("    기업 도구(PostgreSQL)는 표시명도 받습니다. "
              "corp_code 로 다시 해 보세요 — 예: 00126380(삼성전자)")


def _one_line(item: dict) -> str:
    """DTO 한 건을 한 줄로.

    ★**DTO 를 판별 필드로 가른다.** 전에는 필드 목록을 차례로 훑어 「있는 것만」
      찍었는데, 근거(`EvidenceHitDTO`)가 `occurred_at` 하나만 걸려 `occurred_at=…`
      만 나왔다 — 정작 봐야 할 원문과 `evidence_id` 가 안 보였다. 판별 필드를
      정해 두면 그런 부분 일치가 안 생긴다.
    """
    # ★판별 순서가 중요하다 — `RelationDTO` 도 `evidence_id` 를 갖는다. 근거를
    #   먼저 보면 관계가 근거처럼 찍힌다(실제로 그렇게 나왔다).
    if "edge_id" in item:                            # RelationDTO
        sub = f"({item['subtype']})" if item.get("subtype") else ""
        return (f"{item.get('source')} --{item.get('edge_type')}{sub}--> "
                f"{item.get('target')}  {item.get('freshness_note') or ''}".rstrip())
    if "event_id" in item:                           # EventDTO
        return (f"{item.get('name')}  [{item.get('event_type')}]  "
                f"{item.get('occurred_at') or '시점 없음'}")
    if "evidence_id" in item:                        # EvidenceHitDTO
        text = (item.get("text") or "").replace("\n", " ")
        edge = f" [{item['edge_type']}]" if item.get("edge_type") else ""
        return (f"{item['evidence_id']}  {item.get('source_type', '?')}{edge}  "
                f"{text[:64]}{'…' if len(text) > 64 else ''}")
    if "rcept_no" in item and "title" in item:       # FilingDTO
        return f"{item.get('rcept_dt')}  {item.get('title')}"
    if "market_cap" in item or "per" in item:        # market — 계산값
        return " · ".join(f"{k}={v}" for k, v in item.items()
                          if k in ("corp_name", "close_price", "market_cap",
                                   "per", "pbr", "psr") and v is not None)
    if "overview_text" in item:                      # business_overview
        text = (item.get("overview_text") or "").replace("\n", " ")
        return (f"{item.get('bsns_year')}년 · {len(item.get('overview_text') or '')}자  "
                f"{text[:60]}…")
    text = str(item)
    return text[:110] + ("…" if len(text) > 110 else "")


# ══════════════════════════════════════════════════════════════════
#  ask — ★Agent 루프 끝까지 (LLM 호출 · 비용)
# ══════════════════════════════════════════════════════════════════


def cmd_ask(args) -> None:
    """질문 하나를 그래프로 끝까지 돌리고 **관측치를 함께** 찍는다.

    ★`run_ask()` 를 안 쓴다 — 저건 `AskResponse` 만 돌려주는데, 수동 시험에서
      보고 싶은 것은 그 안이다(어떤 도구를 불렀나 · 링이 어떻게 갈렸나 · 예산이
      찼나). `run_ask()` 가 하는 일을 여기서 그대로 하되 State 를 통째로 받는다.
    """
    from app.api.schemas import AskRequest
    from app.core import observe
    from app.core.trace import new_trace_id
    from app.graph import budget
    from app.graph.ask_graph import ask_graph
    from app.graph.state import initial_state

    _head(f"/ask — {args.question!r}   ★LLM 을 부른다")

    new_trace_id()
    with observe.observing() as seen:
        state = ask_graph().invoke(
            initial_state(AskRequest(question=args.question,
                                     workspace_keys=args.workspace)))

    decision = state.get("decision")
    source = decision.source.value if decision else "—"
    agent_called = bool(state.get("messages"))

    print(f"\n  [앵커]  {source}"
          f"{' · ' + ', '.join(a.name for a in decision.anchors) if decision and decision.anchors else ''}")
    print(f"  [Agent] {'호출됨' if agent_called else '★미호출 — 재료를 만들지 않는다'}")

    if seen.tools_used:
        used = " · ".join(f"{k}×{v}" for k, v in sorted(seen.tools_used.items()))
        print(f"  [도구]  호출 {seen.tool_calls}회 — {used}")
    else:
        print(f"  [도구]  호출 {seen.tool_calls}회")
    if seen.tool_errors:
        print(f"          ★거부 {dict(seen.tool_errors)} — 범위 밖 key 다")

    print(f"  [재료]  관계 {len(state.get('relations') or [])} · "
          f"사건 {len(state.get('events') or [])} · "
          f"파급 {len(state.get('propagation') or [])} · "
          f"근거 {len(state.get('evidence') or [])}")

    if seen.ring_seen or seen.ring_kept:
        print(f"  [링]    본 것 {dict(sorted(seen.ring_seen.items()))} → "
              f"남은 것 {dict(sorted(seen.ring_kept.items()))} "
              f"(kept {seen.relations_kept} · cut {seen.relations_cut})")
        print(f"          최종 인용 링 {dict(sorted(seen.cited_rings.items()))} · "
              f"링 없는 인용 {seen.cited_without_ring}건")

    # ★예산 — 「루프가 잘렸나」와 「끝난 뒤 플래그가 켜졌나」는 다른 사건이다.
    caps = {"tool_calls_used": budget.MAX_TOOL_CALLS,
            "events_used": budget.MAX_EVENTS,
            "propagations_used": budget.MAX_PROPAGATIONS}
    spent = " · ".join(f"{n.replace('_used', '')} {state.get(n) or 0}/{c}"
                       for n, c in caps.items())
    print(f"  [예산]  {spent}")
    if seen.agent_stopped_by_budget:
        print("          ★Agent 루프가 예산으로 잘렸다 — 재료가 적은 이유다")
    elif state.get("budget_exhausted"):
        print("          플래그는 켜졌으나 루프는 안 잘렸다 "
              "(fetch_propagation 이 루프 뒤에 채웠다)")

    print(f"  [임베딩] {seen.embed_calls}회 · 캐시 적중 {seen.embed_cache_hits} · "
          f"빗나감 {seen.embed_cache_misses}")
    if seen.embed_cache_misses:
        print("          ★빗나간 만큼 직접 계산했다 — 그 값은 실행마다 흔들린다")

    response = state.get("response")
    if response is None:
        print("\n  ★응답이 없다 — 그래프 배선이 깨졌다")
        return

    _head("답변")
    print(f"  failed={response.failed} · anchor_source={response.anchor_source.value}\n")
    for line in response.answer.splitlines():
        print(f"  {line}")

    if response.sources:
        _head(f"근거 {len(response.sources)}건")
        for source_row in response.sources:
            note = " ★원문 없음" if getattr(source_row, "missing", False) else ""
            print(f"  {source_row.evidence_id}  "
                  f"[{getattr(source_row, 'source_type', '?')}]{note}")


# ══════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BizNode 수동 시험 — ★`ask` 만 LLM 을 부른다(비용).")
    parser.add_argument("--workspace", nargs="*", default=_WORKSPACE,
                        help=f"워크스페이스 corp_code (기본 {_WORKSPACE})")
    sub = parser.add_subparsers(dest="mode")

    p = sub.add_parser("anchor", help="앵커가 어느 갈래로 가나 (무료)")
    p.add_argument("question")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("search", help="검색 계층만 (무료)")
    p.add_argument("question")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("tools", help="도구 7종을 직접 부른다 (무료)")
    p.add_argument("key", help="corp_code 또는 norm_name (예: 00126380 · 삼성전자)")
    p.add_argument("--query", default="최근 리스크", help="의미검색 도구에 넘길 질의")
    p.add_argument("--limit", type=int, default=3, help="도구마다 몇 건까지 찍나")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("ask", help="★Agent 루프 끝까지 (LLM 호출 · 비용)")
    p.add_argument("question")
    p.set_defaults(func=cmd_ask)

    args = parser.parse_args()
    if args.mode is None:
        parser.print_help()
        print(__doc__)
        return
    args.func(args)


if __name__ == "__main__":
    main()
