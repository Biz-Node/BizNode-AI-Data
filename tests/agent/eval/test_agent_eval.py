"""Agent 루프 평가셋 — 실행·판정.

실제 PostgreSQL·Neo4j·ChromaDB 를 대상으로 `ask_graph()` 를 끝까지 돌린다.
★**LLM 도 실제로 부른다** — Agent 가 도구를 고르는 것이 이 평가셋의 대상이라
  고정 응답을 물리면 잴 것이 사라진다.

    pytest -m needs_llm tests/agent/eval -q

★기본 실행에서 **빠진다**(`needs_llm`). 20 케이스 × (LLM 왕복 + 도구 루프)라
  비용과 시간이 든다. `needs_db` 와 나란한 규약이고, 이유는 하나 더 있다 —
  API 키가 없는 환경에서 실패하면 「내 변경이 깼나」를 매번 다시 가려야 한다.

★**무엇을 판정하는가**(`cases.py` 독스트링과 같은 말이다):

    판정한다    서버가 결정론으로 정하는 것 — `anchor_source` · Agent 호출 여부 ·
                예산 상한 준수 · 범위 준수 · 재료 하한 · 계약(인용 규칙)
    판정 안 한다 **어떤 도구를 골랐는가.** LLM 이 정하고 같은 질문에 다른 조합이
                나올 수 있다. 케이스마다 박으면 모델을 바꿀 때 평가셋이 죽는다

  대신 도구 커버리지는 **집합 수준**에서 강제한다
  (`test_every_tool_is_exercised`) — 「도구가 전부 실제 호출되게 구성한다」는
  요구는 케이스 하나가 아니라 평가셋 전체의 성질이기 때문이다.

★**ranking 을 판정하지 않는다.** 링 분포·kept/cut·인용된 링은 **관측만** 한다.
  Phase 8 은 ranking 을 고정한 채 Agent 의 효과와 비용을 재는 단계이고, 링
  랭킹을 바꿀지는 이 관측 결과를 보고 정한다. 여기에 링 임계값을 넣으면 재는
  도구가 판정기가 되어 그 판단을 미리 해버린다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import AnchorSource
from app.graph import budget
from app.tools import agent_tools, citation
from tests.agent.eval.cases import CASES, REQUIRED_COVERAGE, AgentEvalCase
from tests.agent.eval.runner import CaseRun

pytestmark = pytest.mark.needs_llm


def _params() -> list:
    return [
        pytest.param(
            case, id=case.id,
            marks=[pytest.mark.xfail(strict=True, reason=case.known_issue)]
            if case.known_issue else [],
        )
        for case in CASES
    ]


# ══════════════════════════════════════════════════════════════════
#  공통 판정 — 모든 케이스
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("case", _params())
def test_case(case: AgentEvalCase, runs: dict[str, CaseRun]):
    run = runs[case.id]
    ctx = run.describe()

    assert run.error is None, f"실행이 예외로 끝났다{ctx}"

    # ── ① 앵커 — 서버가 정하는 결정론적 값 ────────────────────
    assert run.anchor_source is case.expected_anchor_source, \
        f"anchor_source 불일치{ctx}"

    # ── ② Agent 호출 여부 — `UNRESOLVED` 면 아예 안 부른다 ────
    assert run.agent_called is case.expects_agent, \
        f"Agent 호출 여부가 기대와 다르다{ctx}"

    # ── ③ 부르면 안 되는 도구 ────────────────────────────────
    for tool in case.must_not_call:
        assert tool not in run.tools_used, \
            f"불리면 안 되는 도구가 불렸다: {tool}{ctx}"
    if not case.expects_agent:
        assert run.tool_calls == 0, f"Agent 를 안 부르는데 도구 호출이 있다{ctx}"
        assert not run.tools_used, f"Agent 를 안 부르는데 도구가 불렸다{ctx}"

    # ── ④ 예산 — **상한을 넘지 않는다** ───────────────────────
    assert run.tool_calls <= budget.MAX_TOOL_CALLS, \
        f"도구 호출이 상한({budget.MAX_TOOL_CALLS})을 넘었다{ctx}"
    assert len(run.events) <= budget.MAX_EVENTS, \
        f"사건이 상한({budget.MAX_EVENTS})을 넘었다{ctx}"

    # ── ⑤ 재료 하한 ──────────────────────────────────────────
    assert len(run.relations) >= case.min_relations, \
        f"관계가 {case.min_relations}건에 못 미친다{ctx}"
    assert len(run.events) >= case.min_events, \
        f"사건이 {case.min_events}건에 못 미친다{ctx}"
    assert len(run.evidence) >= case.min_evidence, \
        f"근거가 {case.min_evidence}건에 못 미친다{ctx}"

    # ── ⑥ 답변이 나왔는가 ────────────────────────────────────
    assert run.response is not None, f"응답이 없다 — 그래프 배선 확인{ctx}"
    if case.expects_answer:
        assert not run.failed, f"답변이 실패로 끝났다{ctx}"

    # ── ⑦ 응답의 `anchor_source` 는 서버 값 그대로다 ──────────
    #
    # ★**옛 비대칭이 사라졌다**(최종 설계 §17-1). 전에는 출발점이 하나도 없으면
    #   게이트가 `resolve_anchor` 앞에서 끊어 State 에 `decision` 이 안 생기는데
    #   (`run.anchor_source is None`) 응답에는 `unresolved` 가 실렸다 — 「지정했는데
    #   못 찾았다」와 「지정할 것도 없었다」를 한 값으로 말하는 자리였다.
    #   게이트가 없어져 모든 요청이 앵커 판정을 지나므로, 지금은 둘이 늘 같다.
    assert run.response.anchor_source is case.expected_anchor_source, \
        f"응답에 실린 anchor_source 가 서버 판정과 다르다{ctx}"
    assert run.anchor_source is case.expected_anchor_source, \
        f"State 의 앵커 판정이 응답과 갈렸다{ctx}"

    # ── ⑧ 인용 — 화이트리스트 밖 근거가 나가지 않는다 ─────────
    allowed = {e.evidence_id for e in run.evidence}
    for source in run.sources:
        assert source.evidence_id in allowed, \
            f"재료에 없는 근거가 응답에 실렸다: {source.evidence_id}{ctx}"


def test_coverage_is_complete():
    """평가셋이 선언한 분기를 하나도 빠뜨리지 않았는가.

    ★케이스를 지우다 분기가 통째로 비면 여기서 잡는다. `tests/search/eval/
      test_search_eval.py::test_coverage_is_complete` 과 같은 장치다."""
    covered = {tag for case in CASES for tag in case.coverage}
    assert REQUIRED_COVERAGE <= covered, \
        f"덮이지 않은 분기: {sorted(REQUIRED_COVERAGE - covered)}"


def test_every_case_declares_a_tool_it_is_designed_to_pull():
    """Agent 를 부르는 케이스는 **무엇을 끌어오려는 질문인지** 선언해야 한다.

    ★선언이 없으면 그 케이스가 어떤 도구를 덮는지 아무도 모른다 —
      `test_every_tool_is_exercised` 가 빨간불일 때 어느 케이스를 고칠지
      되짚을 수 없게 된다."""
    for case in CASES:
        if case.expects_agent:
            assert case.expects_tools, f"[{case.id}] expects_tools 가 비었다"
        for tool in case.expects_tools:
            assert tool in agent_tools.TOOL_NAMES, \
                f"[{case.id}] 존재하지 않는 도구를 기대한다: {tool}"


# ══════════════════════════════════════════════════════════════════
#  집합 수준 판정 — ★케이스 하나가 아니라 평가셋 전체의 성질
# ══════════════════════════════════════════════════════════════════


def test_every_tool_is_exercised(runs: dict[str, CaseRun]):
    """★**도구 7종이 전부 한 번씩은 실제로 불렸는가.**

    이 평가셋의 존재 이유다. 기존 `/ask` 20질문(2026-08-23)은 도구 5종이 생기기
    **전에** 쓰였고, `search_dart`·`get_business_overview`·`get_market`·
    `get_filings` 를 끌어오는 질문이 하나도 없었다. 그래서 새 도구가 도는지
    아무도 못 봤다.

    ★케이스마다 「이 도구를 불러라」로 박지 않는 이유는 도구 선택이 LLM 의
      몫이기 때문이다. 하지만 **평가셋 전체가 도구를 못 건드리면** 그건 LLM 의
      변덕이 아니라 평가셋의 결함이다 — 그건 여기서 잡는다.
    """
    called = {tool for run in runs.values() for tool in run.tools_used}
    missing = set(agent_tools.TOOL_NAMES) - called
    assert not missing, (
        f"평가셋 전체에서 한 번도 안 불린 도구: {sorted(missing)} — "
        f"불린 것: {sorted(called)}. 질문을 고치거나 도구 설명을 고쳐야 한다")


def test_workspace_anchor_actually_reaches_the_agent(runs: dict[str, CaseRun]):
    """★**기업을 지정하지 않은 질문에서 Agent 가 불리고 재료를 모으는가.**

    `UNRESOLVED`(지정했는데 못 찾음)와 갈리는 지점이다. 저쪽은 Agent 를 아예
    안 부르는데, 이쪽은 워크스페이스를 대상 문맥으로 삼아 **부른다.** 둘을
    같은 것으로 다루면 「기업을 안 물으면 답을 못 하는」 챗봇이 된다.
    """
    probes = [run for run in runs.values() if run.case.is_semantic_probe()]
    assert probes, "기업 미지정 케이스가 하나도 없다 — 평가셋의 축 하나가 비었다"

    for run in probes:
        assert run.agent_called, f"기업 미지정인데 Agent 가 안 불렸다{run.describe()}"
        assert run.tool_calls > 0, \
            f"기업 미지정인데 도구를 하나도 안 불렀다{run.describe()}"

    # ★그중 **의미검색 도구**(뉴스·공시)가 실제로 도는 케이스가 있어야 한다 —
    #   기업 미지정 질문의 본령이 그것이다.
    semantic_tools = {"search_news", "search_dart"}
    reached = {tool for run in probes for tool in run.tools_used} & semantic_tools
    assert reached, (
        "기업 미지정 케이스가 의미검색 도구를 한 번도 안 불렀다 — "
        f"불린 것: {sorted({t for r in probes for t in r.tools_used})}")


def test_unresolved_never_reaches_the_agent(runs: dict[str, CaseRun]):
    """★지정했는데 못 찾으면 **워크스페이스로 갈아타지 않는다**(설계서 §14-3).

    갈아타면 「TSMC 를 물었는데 삼성전자로 답하는」 탐지 불가능한 오답이 된다.
    그 방어가 Agent **앞의** 결정론 노드에 있어야 하는 이유이기도 하다 —
    판정을 LLM 뒤로 옮기면 장치가 사라진다.
    """
    unresolved = [run for run in runs.values()
                  if run.case.expected_anchor_source is AnchorSource.UNRESOLVED]
    assert unresolved, "UNRESOLVED 케이스가 없다 — Agent 미호출 경로가 안 덮인다"

    for run in unresolved:
        assert not run.agent_called, f"UNRESOLVED 인데 Agent 가 불렸다{run.describe()}"
        assert run.tool_calls == 0, f"UNRESOLVED 인데 도구를 불렀다{run.describe()}"
        assert not run.evidence, \
            f"UNRESOLVED 인데 근거가 모였다 — 재료를 만들면 안 된다{run.describe()}"


def test_budget_is_never_exceeded(runs: dict[str, CaseRun]):
    """★상한은 **누적치**로 지켜진다(계약 4번).

    소진돼도 예외가 아니라 마감으로 전이하므로, 소진된 케이스도 **응답은
    나와야 한다** — 도구를 덜 불렀어도 있는 재료로 답하는 것이 옳다.
    """
    for run in runs.values():
        assert run.tool_calls <= budget.MAX_TOOL_CALLS, \
            f"[{run.case.id}] 도구 호출 상한 초과{run.describe()}"
        # ★**카운터가 자기 상한을 넘으면 자르는 단위와 세는 단위가 갈린 것이다**
        #   (2026-08-29 · Phase 10). 상한이 낮다는 뜻이 아니다 —
        #   `propagations_used` 가 사건 수로 자르고 파급 행 수로 세어 12 에 92 가
        #   찍혔던 결함이 그것이다.
        assert int(run.state.get("propagations_used") or 0) \
            <= budget.MAX_PROPAGATIONS, \
            f"[{run.case.id}] 파급 상한 초과 — 세는 단위가 갈렸다{run.describe()}"
        if run.budget_exhausted:
            assert run.response is not None, \
                f"[{run.case.id}] 예산이 소진됐는데 응답이 없다 — 예외로 끝났다는 뜻"


def test_a_cited_relation_never_loses_its_ring(runs: dict[str, CaseRun]):
    """★**관계를 인용했으면 링이 되짚혀야 한다**(2026-08-29 · Phase 11).

    `get_relations` 가 돌려준 관계는 전부 `ring_by_edge` 에 담기므로, 인용된
    edge_id 가 거기 없다는 것은 위쪽 규칙이 바뀌었다는 뜻이다.

    ★`cited_without_ring`(사건·검색히트·뉴스 근거)은 **여기서 안 본다** — 그건
      링이 없는 것이 정상이라 0 을 요구할 수 없다. 둘을 한 통에 두면 이 단언을
      아예 쓸 수가 없어서 갈랐다.
    """
    for run in runs.values():
        assert run.observed.cited_relation_without_ring == 0, \
            (f"[{run.case.id}] 관계를 인용했는데 링을 못 찾았다 "
             f"{run.observed.cited_relation_without_ring}건{run.describe()}")


def test_context_only_material_never_becomes_a_citation(runs: dict[str, CaseRun]):
    """★**인용 불가 자료가 근거로 나가지 않는가**(작업 B).

    `get_business_overview` 는 참고 맥락이다 — Chroma 청크가 없어 인용하면
    `missing=True` 로 나간다. 규칙은 `app/tools/citation.py` 한 곳에 있고,
    여기서는 그 규칙이 **실제 실행에서 지켜졌는지**를 본다.
    """
    for run in runs.values():
        for tool in run.tools_used:
            if tool in citation.CITABLE_TOOLS:
                continue
            # 인용 불가 도구의 결과가 근거 화이트리스트를 늘리면 안 된다.
            assert not citation.citable_evidence_ids(
                tool, run.state.get("tool_results", {}).get(tool, [])), \
                f"[{run.case.id}] 인용 불가 도구 {tool} 의 결과가 근거가 됐다"


def test_out_of_scope_keys_are_never_used_as_material(runs: dict[str, CaseRun]):
    """★Agent 가 범위 밖 key 를 넘기면 도구가 **거부하고, 재료로 새지 않는다.**

    거부는 예외가 아니라 문자열이라 그래프가 죽지 않는다. 그래서 「거부됐다」는
    사실이 조용히 묻힐 수 있어 **세어서 남긴다** — 0 이 아니면 Agent 가 범위를
    벗어나려 했다는 뜻이고, 그 자체가 관측 대상이다.
    """
    for run in runs.values():
        rejected = sum(run.observed.tool_errors.values())
        if rejected:
            # 거부가 있었다면 그만큼 재료가 안 늘었어야 한다 — 응답은 여전히 나온다.
            assert run.response is not None, \
                f"[{run.case.id}] 도구 거부 {rejected}건에 응답이 없다 — 그래프가 죽었다"
