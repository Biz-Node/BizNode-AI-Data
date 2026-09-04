"""관측 — **재기만 한다. 아무 동작도 바꾸지 않는다.**

Phase 8 의 완료 기준은 「Agent 가 답을 더 잘 쓰나」가 아니라 **「Agent 가 무엇을
얼마나 썼나」**다. 그걸 재려면 한 질문이 그래프를 지나는 동안 흩어져 일어나는
일들을 한 자리에 모아야 한다:

    도구        몇 번 불렀나 · 어느 도구를 · 거부당한 것은 몇 건인가
    임베딩      몇 번 계산했나 · 캐시가 몇 건 맞고 몇 건 빗나갔나
    링(ring)    도구가 본 관계의 링 분포 · 상한에 남은 것과 잘린 것
    인용        **최종 답변이 인용한 관계**의 링 분포
    주장        질문 의도와 **연결이 없다고 판정된** 주장 — 개수와 **본문**

★**이 모듈은 정책이 아니다.** 값을 읽어 무엇을 자르거나 순서를 바꾸지 않는다.
  Phase 8 은 ranking 을 **고정한 채로** Agent 의 효과와 비용을 재는 단계이고,
  링 랭킹을 바꿀지는 이 관측 결과를 보고 나서 정한다. 그래서 여기에 임계값이
  없다 — 있으면 재는 도구가 아니라 판정기가 된다.

★**버킷이 안 열려 있으면 전부 no-op 이다.** 운영 `/ask` 는 버킷을 열지 않는다.
  `agent_tools._COLLECTED` 와 같은 규약이다 — 관측을 켜는 것은 **부르는 쪽**의
  일이고, 재는 코드가 스스로 켜지 않는다. 그래서 운영 경로에 비용이 없다.

★**`log.info` 는 버킷과 무관하게 남긴다.** 버킷은 평가셋이 구조화된 값을 읽는
  통로이고, 로그는 운영에서 같은 사실을 되짚는 통로다. 둘 중 하나만 두면
  「평가에서는 보이는데 운영에서는 안 보이는」 값이 생긴다.

사용:

    with observe.observing() as seen:
        response = run_ask(request)
    seen.tool_calls, seen.tools_used, seen.cited_rings   # ...
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence

from app.core.trace import trace_logger

log = trace_logger(__name__)


@dataclass
class Observation:
    """한 질문이 그래프를 지나며 남긴 관측치. **여기 있는 값은 전부 사후다** —
    어느 노드도 이 값을 읽고 행동을 바꾸지 않는다."""

    # ── 도구 ──────────────────────────────────────────────────
    # Agent 가 **요청한** 호출 수(누적). 예산이 세는 것과 같은 값이다.
    tool_calls: int = 0
    # 실제로 결과를 낸 도구별 호출 수. `{도구이름: 횟수}`
    tools_used: Counter = field(default_factory=Counter)
    # 도구별 결과 건수 합. 「불렀는데 0건」과 「안 불렀다」를 가른다.
    tool_items: Counter = field(default_factory=Counter)
    # 범위 밖 key 등으로 **거부된** 호출. ★재료를 늘리지 않은 호출이다.
    tool_errors: Counter = field(default_factory=Counter)
    # ★한 턴이 상한을 넘겨 **실행하지 않은** 호출. `tool_errors` 와 갈라 센다 —
    #   저건 범위 밖 key 처럼 **도구가** 거부한 것이고, 이건 **예산이** 막은
    #   것이다. 한 통에 담으면 「Agent 가 범위를 자꾸 벗어난다」와 「상한이
    #   낮다」가 같은 숫자로 보인다. 답이 갈리는 두 사실이다.
    tool_calls_denied_by_budget: int = 0

    # ── 임베딩 ────────────────────────────────────────────────
    # `embed_with_cache` 진입 횟수. 텍스트 수가 아니라 **호출 수**다.
    embed_calls: int = 0
    embed_texts: int = 0
    embed_cache_hits: int = 0
    # ★빗나감은 「실제로 계산했다」는 뜻이다 — 그 실행은 값이 흔들릴 수 있다
    #   (현황서 §8-13). `EMBED_CACHE_STRICT=1` 이면 여기 오기 전에 멈춘다.
    embed_cache_misses: int = 0

    # ── 예산 ──────────────────────────────────────────────────
    # ★**Agent 루프가 예산 때문에 잘렸는가.** State 의 `budget_exhausted` 와
    #   같은 말이 아니다 — 저건 `fetch_propagation` 이 루프 **뒤에** 파급 예산을
    #   채워도 켜진다. 둘을 섞으면 「상한을 올려야 하나」의 답이 갈린다:
    #   루프가 잘렸으면 도구 예산 얘기고, 뒤에서 찬 것이면 파급 예산 얘기다.
    agent_stopped_by_budget: bool = False

    # ── 링(ring) ──────────────────────────────────────────────
    # 도구가 **본** 관계의 링 분포. 자르기 전이다.
    ring_seen: Counter = field(default_factory=Counter)
    # 상한 안에 **남은** 것의 링 분포.
    ring_kept: Counter = field(default_factory=Counter)
    relations_kept: int = 0
    relations_cut: int = 0
    # edge_id → ring. ★인용된 관계의 링을 되짚으려고 둔다. 겸해서 **중복 계수를
    #   막는 열쇠**다 — 여기 이미 있으면 그 관계는 다시 안 센다.
    ring_by_edge: dict[str, int] = field(default_factory=dict)
    # kept 로 센 edge_id. ★`ring_by_edge` 와 따로 둔다 — 「봤다」와 「남았다」는
    #   다른 사건이고, 한 관계가 본 뒤에 잘릴 수도 있다.
    _kept_edges: set[str] = field(default_factory=set)

    # ── 인용 ──────────────────────────────────────────────────
    # **최종 답변이 인용한** 관계의 링 분포. 도구가 본 분포와 다를 수 있고,
    # 그 차이가 「링 순서가 답변까지 살아갔나」다.
    cited_rings: Counter = field(default_factory=Counter)
    # 인용됐지만 **관계가 아닌** 근거 — 사건·검색히트·뉴스. **링이 없는 것이 정상이다.**
    cited_without_ring: int = 0
    # ★**관계인데 링을 못 찾은 인용. 0 이 아니면 결함 신호다.**
    #
    #   위와 갈라 두는 이유 — 한 숫자에 섞여 있으면 `cited_rings {}` 를 읽을 수가
    #   없다. 「인용이 전부 사건·뉴스 근거였다」(정상)와 「관계를 인용했는데 되짚기가
    #   끊겼다」(결함)가 같은 값으로 보이기 때문이다.
    #
    #   `graph_tools` 의 `suspect_dropped` 와 **같은 관례**다 — 위쪽 규칙대로면
    #   여기 올 수 없는 것이 오면, 조용히 넘기지 않고 세어서 남긴다.
    cited_relation_without_ring: int = 0

    # ── LLM 비용 ──────────────────────────────────────────────
    # ★**모델별로 가른다.** 단가가 모델마다 다르므로 합계 하나로는 비용을 못
    #   낸다. 게다가 Agent 와 답변이 이제 **다른 모델을 쓸 수 있다**
    #   (`config.AGENT_MODEL`·`ANSWER_MODEL`) — 갈라 두지 않으면 「Agent 만
    #   올렸을 때 얼마가 더 나가나」를 잴 수가 없다.
    #
    # ★키는 **응답이 말한 모델명**이다(`gpt-4o-mini-2024-07-18`). 설정 상수를
    #   쓰면 「무엇을 부르려 했나」가 남는데, 알아야 하는 것은 **무엇이 실제로
    #   불렸나**다 — 별칭(`gpt-4o-mini`)은 언제든 다른 스냅샷을 가리킨다.
    llm_calls: Counter = field(default_factory=Counter)
    llm_input_tokens: Counter = field(default_factory=Counter)
    llm_output_tokens: Counter = field(default_factory=Counter)
    # ★추론 토큰은 **출력에 포함돼 청구된다.** 따로 세지 않으면 gpt-5 계열로
    #   바꿨을 때 늘어난 비용이 어디서 왔는지 못 짚는다.
    llm_reasoning_tokens: Counter = field(default_factory=Counter)
    # ★사용량이 안 실려 온 호출. **0 토큰과 갈라 센다** — 섞으면 「공짜로
    #   돌았다」로 읽힌다.
    llm_calls_without_usage: int = 0

    # ── 주장(claim) — ★관측만 한다 ────────────────────────────
    # `check_claims` 가 이미 계산해 **로그로만** 남기던 값이다. 「uncited 비율」을
    # 모델 교체 전후로 비교하려면, 그리고 `STRIP_UNLINKED_CLAIMS` 를 켤지
    # 정하려면 **구조화된 값**이 있어야 한다 — 로그는 사람이 긁어야 한다.
    #
    # ★`claims_checked` 를 따로 둔다 — 「주장이 0건」과 「그 노드를 안 지났다」가
    #   같은 값으로 보이면 비율의 **분모를 만들 수 없다.**
    #
    # ★**세는 주인은 `record_claims` 하나다**(2026-08-29 · 머지에서 정했다).
    #   두 브랜치가 각자 기록기를 두어 `claims_total`·`claims_unlinked` 를 **둘 다**
    #   늘리고 있었다. `check_claims` 가 둘을 연달아 부르므로 그대로 두면 값이
    #   **두 배**가 된다 — 같은 이름을 두 주인이 쓰면 합칠 때 조용히 어긋난다.
    #   개수는 `summarize()` 한 곳에서만 읽고, `record_claim_links` 는 **본문만**
    #   담는다(그건 `summarize()` 가 못 주는 값이다).
    claims_checked: bool = False
    claims_total: int = 0
    claims_uncited: int = 0
    claims_no_text: int = 0
    # `intent_linked is False` — 질문이 지목한 사건 종류에서 온 근거가 아니다.
    claims_unlinked: int = 0
    # ★`intent_linked is None` — **판정 불가**다. 「연결 없음」과 섞으면 관계
    #   질의(「삼성전자에 납품하는 기업」)가 통째로 차단된 것처럼 보인다
    #   (`claim_check.summarize` 가 갈라 세는 것과 같은 이유).
    claims_link_unknown: int = 0
    # ★**사람이 읽어야 하는 값이다.** 오탐률은 숫자로 안 나온다 — 「이 주장이
    #   정말 질문과 무관했나」는 문장을 봐야 정해진다. 그래서 개수가 아니라
    #   본문과 근거 id 를 담는다. `(주장, 든 근거 id들)`.
    unlinked_claims: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def summary(self) -> dict:
        """보고서·로그가 읽는 납작한 dict. **정렬을 여기서 못 박는다** —
        Counter 를 그대로 내보내면 순서가 실행마다 달라져 문서 diff 가 커진다."""
        return {
            "tool_calls": self.tool_calls,
            "tools_used": dict(sorted(self.tools_used.items())),
            "tool_items": dict(sorted(self.tool_items.items())),
            "tool_errors": dict(sorted(self.tool_errors.items())),
            "tool_calls_denied_by_budget": self.tool_calls_denied_by_budget,
            "embed_calls": self.embed_calls,
            "embed_texts": self.embed_texts,
            "embed_cache_hits": self.embed_cache_hits,
            "embed_cache_misses": self.embed_cache_misses,
            "agent_stopped_by_budget": self.agent_stopped_by_budget,
            "ring_seen": dict(sorted(self.ring_seen.items())),
            "ring_kept": dict(sorted(self.ring_kept.items())),
            "relations_kept": self.relations_kept,
            "relations_cut": self.relations_cut,
            "cited_rings": dict(sorted(self.cited_rings.items())),
            "cited_without_ring": self.cited_without_ring,
            "cited_relation_without_ring": self.cited_relation_without_ring,
            "llm_calls": dict(sorted(self.llm_calls.items())),
            "llm_input_tokens": dict(sorted(self.llm_input_tokens.items())),
            "llm_output_tokens": dict(sorted(self.llm_output_tokens.items())),
            "llm_reasoning_tokens": dict(sorted(self.llm_reasoning_tokens.items())),
            "llm_calls_without_usage": self.llm_calls_without_usage,
            "claims_checked": self.claims_checked,
            "claims_total": self.claims_total,
            "claims_uncited": self.claims_uncited,
            "claims_no_text": self.claims_no_text,
            "claims_unlinked": self.claims_unlinked,
            "claims_link_unknown": self.claims_link_unknown,
            # ★`unlinked_claims`(본문)는 **안 담는다.** 이 dict 는 로그 한 줄로
            #   찍히는데, 주장 문장을 실으면 로그가 답변 사본이 된다(설계서
            #   §13-2 가 프롬프트에 대해 못 박은 것과 같은 이유).
            #   문장은 버킷에서 직접 읽는다 — 평가셋 보고서가 그렇게 한다.
        }


_BUCKET: ContextVar[Optional[Observation]] = ContextVar("observation", default=None)


@contextmanager
def observing() -> Iterator[Observation]:
    """이 블록 안에서 일어난 관측을 모은다.

    ★**요청 하나를 통째로 감싸는 자리에서 연다.** 노드 안에서 열면 LangGraph 가
      노드마다 컨텍스트를 복사하므로 다음 노드의 관측이 안 들어온다
      (`agent_tools.collecting()` 이 State 로 옮겨 담아야 했던 것과 같은 이유).
      여기는 `run_ask()` **바깥**에서 열리므로 그 문제가 없다 — 복사본이 같은
      객체를 물고 들어가고, 우리는 객체를 **변이**시킨다.
    """
    seen = Observation()
    token = _BUCKET.set(seen)
    try:
        yield seen
    finally:
        _BUCKET.reset(token)


def current() -> Optional[Observation]:
    """열려 있으면 버킷, 아니면 `None`. **부르는 쪽이 None 을 견뎌야 한다.**"""
    return _BUCKET.get()


# ══════════════════════════════════════════════════════════════════
#  기록 — 전부 「열려 있으면 담고, 아니면 조용히 넘어간다」
# ══════════════════════════════════════════════════════════════════


def record_tool_calls(count: int) -> None:
    """Agent 가 이번 턴에 **요청한** 도구 호출 수."""
    seen = _BUCKET.get()
    if seen is not None:
        seen.tool_calls += count


def record_agent_stopped_by_budget() -> None:
    """★Agent 루프가 **예산 때문에** 마감으로 전이했다. 도구를 더 부르려 했는데
    못 부른 것이라, 재료가 적은 이유가 「Agent 가 충분하다고 판단해서」가 아니다."""
    seen = _BUCKET.get()
    if seen is not None:
        seen.agent_stopped_by_budget = True


def record_tool_calls_denied_by_budget(count: int) -> None:
    """한 턴이 상한을 넘겨 **실행하지 않은** 호출 수.

    ★`record_tool_error` 와 갈라 둔다. 저기 쌓이는 것은 도구가 스스로 거부한
      것(범위 밖 key 등)이고, 여기 쌓이는 것은 **예산이 막은** 것이다. 섞으면
      「Agent 가 범위를 벗어난다」(프롬프트 문제)와 「상한이 낮다」(예산 문제)가
      같은 숫자가 되어, 어느 쪽을 고쳐야 하는지 알 수 없다.
    """
    seen = _BUCKET.get()
    if seen is not None and count:
        seen.tool_calls_denied_by_budget += int(count)


def record_tool(tool: str, items: int) -> None:
    """도구 하나가 결과를 냈다. `items` 는 돌려준 건수다."""
    seen = _BUCKET.get()
    if seen is not None:
        seen.tools_used[tool] += 1
        seen.tool_items[tool] += items


def record_tool_error(tool: str) -> None:
    """도구가 거부했다 — 범위 밖 key 등. **재료를 늘리지 않은 호출이다.**"""
    seen = _BUCKET.get()
    if seen is not None:
        seen.tool_errors[tool] += 1


def record_embed(*, texts: int, hits: int, misses: int) -> None:
    """임베딩 한 번. `misses` 가 0 이 아니면 그 실행은 **계산을 했다**."""
    seen = _BUCKET.get()
    if seen is not None:
        seen.embed_calls += 1
        seen.embed_texts += texts
        seen.embed_cache_hits += hits
        seen.embed_cache_misses += misses


def record_claim_links(checked: Sequence, unlinked: Sequence) -> None:
    """주장 연결성 판정의 결과. **재기만 한다 — 아무것도 안 지운다.**

    ★`STRIP_UNLINKED_CLAIMS` 를 켤지 정하려면 **오탐률**이 필요하고, 오탐률은
      개수가 아니라 **문장**을 봐야 정해진다. 그래서 본문과 근거 id 를 담는다.

    ★`checked` 는 `claim_check.check()` 의 결과 전부, `unlinked` 는 그중
      `claim_check.unlinked()` 가 고른 것이다. **부르는 쪽이 이미 갈라 놓은
      것을 다시 갈라 세지 않는다** — `unlinked()` 가 이 저장소에서 유일하게
      판정에 가까운 함수이고, 여기서 그 규칙을 흉내 내면 두 벌이 된다.

    ★`intent_linked is None`(판정 불가)을 **따로 센다.** 「연결 없음」과 섞으면
      관계 질의가 통째로 차단된 것처럼 보인다 — `claim_check.summarize` 가
      `unlinked` 와 `link_unknown` 을 가른 것과 같은 이유다.

    ★**호출은 요청당 한 번**이다(`check_claims` 노드). `record_rings` 와 달리
      중복을 접지 않는 이유가 그것이다 — 접을 반복 호출이 없다.
    """
    seen = _BUCKET.get()
    if seen is None:
        return
    # ★개수는 여기서 안 센다 — `record_claims(summarize(...))` 가 주인이다.
    #   둘 다 늘리면 `check_claims` 가 둘을 연달아 불러 값이 두 배가 된다.
    for claim in unlinked:
        seen.unlinked_claims.append(
            (str(getattr(claim, "text", "")),
             tuple(str(i) for i in getattr(claim, "evidence_ids", ()) or ())))


def record_rings(by_ring: Mapping[int, Sequence], kept: Sequence,
                 cut_count: int) -> None:
    """관계를 링으로 갈라 본 결과. **자르기 전 분포와 남은 것을 함께** 담는다.

    ★`kept` 는 원시 row 리스트다(`_relation_dto` 로 만들기 전). `edge_id` 와
      링을 짝지어 둬야 나중에 **인용된 관계의 링**을 되짚을 수 있다.

    ★**`edge_id` 로 중복을 접는다 — 「관계 몇 개」이지 「호출 × 관계」가 아니다.**

      이 함수는 `get_relations` **호출마다** 불린다. 접지 않고 더하면 Agent 가
      같은 기업으로 도구를 두 번 부를 때 같은 관계가 두 번 세어진다. 그런데
      **몇 번 부를지는 LLM 이 정한다** — 그러면 링 수치가 랭킹이 아니라 도구
      선택에 흔들리고, 「링 분포가 왜 달라졌나」에 답할 수 없게 된다.

      실측(2026-08-28): 같은 20 케이스에서 `get_relations` 호출이 7회였던 실행과
      3회였던 실행이 있었고, 그 사이 `ring_seen` R1 이 754 → 746, R3 가 63 → 50
      으로 움직였다. 랭킹은 그 사이 **링 안 순서만** 바뀌었는데(41bb1bb),
      `relation_selector.order()` 는 길이를 보존하므로 링별 **개수**를 바꿀 수
      없다 — 즉 그 움직임은 전부 이 중복 계수 탓이었다.

      ★`app/services/embedding_cache.py` 가 임베딩 값에 대해 막아 둔 것과 **같은
        종류의 귀속 문제**다. 2차의 완료 기준이 평가셋 점수라, 점수 차이를
        무엇에 귀속시킬지 못 정하면 기준 자체가 성립하지 않는다.
    """
    seen = _BUCKET.get()
    if seen is None:
        return
    for ring, rows in by_ring.items():
        for row in rows:
            edge_id = row.get("edge_id")
            if not edge_id:
                continue
            edge_id = str(edge_id)
            if edge_id in seen.ring_by_edge:
                continue                      # 이미 본 관계 — 두 번 세지 않는다
            seen.ring_by_edge[edge_id] = ring
            seen.ring_seen[ring] += 1
    for row in kept:
        edge_id = row.get("edge_id")
        if not edge_id:
            continue
        edge_id = str(edge_id)
        if edge_id in seen._kept_edges:
            continue                          # 같은 관계가 두 호출에서 남았다
        ring = seen.ring_by_edge.get(edge_id)
        if ring is None:
            continue
        seen._kept_edges.add(edge_id)
        seen.ring_kept[ring] += 1
        seen.relations_kept += 1
    # ★`cut` 은 접지 않는다 — 같은 관계가 한 호출에서 남고 다른 호출에서 잘릴 수
    #   있어 「어느 쪽이 참인가」가 없다. 자른 **횟수**로 읽어야 하는 값이다.
    seen.relations_cut += cut_count


def record_cited_relations(edge_ids: Sequence[Optional[str]],
                           without_ring: int = 0) -> None:
    """최종 답변이 인용한 관계의 링을 센다.

    `edge_ids` 는 **관계로 되짚힌 인용**이고, `without_ring` 은 부르는 쪽이 이미
    「관계가 아니다」로 판정한 인용 수다(`answer.verify_sources`).

    ★링을 못 찾은 것은 **0 으로 세지 않는다.** Ring 0 으로 뭉뚱그리면
      「워크스페이스 안쪽이 인용됐다」는 거짓 신호가 된다.

    ★**「링이 없다」를 두 통으로 가른다**(2026-08-29 · Phase 11):

          cited_without_ring            관계가 아닌 근거 — **정상**
          cited_relation_without_ring   관계인데 링을 못 찾음 — **결함 신호**

      섞어 두면 `cited_rings {}` 를 읽을 수가 없다. 「인용이 전부 사건·뉴스
      근거였다」와 「되짚기가 끊겼다」가 같은 숫자로 보이기 때문이다.

    ★**`evidence_ids` 배열로 2차 조회하지 않는다** — 한 번 넣었다가 뺐다
      (2026-08-29 · Phase 13 → 15). 이유는 계약이다:

          `app/tools/dto.py` 의 `RelationDTO.evidence_id` 가 못 박는다 —
          「`evidence_ids` 배열은 여러 근거의 합집합이라 **이 관계 하나의 출처가
          아니다**」. 배열을 안 싣는 것은 결함이 아니라 **결정**이다.

      이 저장소는 **수집은 넓게, 귀속은 좁게** 로 일관돼 있다. 수집(`graph_searcher.
      _evidence_refs` · `relation_service._evidence`)은 배열을 포함하지만, 귀속
      (`RelationDTO` · `prompt.about` · `prompt._edge_id_for`)은 **단수만** 본다.

      관측만 배열로 되짚으면 **응답과 갈린다** — 배열 근거가 인용되면
      `Source.edge_id` 는 `None` 인데 여기서만 링을 붙이게 된다. 이 모듈은
      **재기만 하는 자리**이고, 재는 대상은 실제로 일어난 일이어야 한다.

      그리고 `ring_by_edge` 는 자르기 **전**(`by_ring` 전체)을 담으므로, 2차 조회는
      **잘린 관계**의 링까지 인용 분포에 넣는다 — 보고서의 「본 것 / kept / 인용」
      세 열이 같은 모집단을 전제하는데 그것이 깨진다(kept 0 인데 인용 > 0).
    """
    seen = _BUCKET.get()
    if seen is None:
        return
    for edge_id in edge_ids:
        if not edge_id:
            # ★`edge_ids` 에 오는 것은 **전부 관계**다(부르는 쪽이 이미 갈라 놨다).
            #   그러니 빈 값도 「관계가 아니다」가 아니라 **결함**이다.
            seen.cited_relation_without_ring += 1
            continue
        ring = seen.ring_by_edge.get(str(edge_id))
        if ring is None:
            seen.cited_relation_without_ring += 1
        else:
            seen.cited_rings[ring] += 1
    seen.cited_without_ring += without_ring


def record_llm_message(message: Any) -> None:
    """LLM 응답 하나의 **사용량**을 담는다. 호출부는 Agent 턴과 답변 생성 둘이다.

    ★**모양을 아는 자리를 한 곳으로 둔다.** 부르는 자리가 둘이라 파싱을 각자
      하면 한쪽만 0 으로 남고, 그 0 은 「안 썼다」와 구별되지 않는다 —
      `pipeline/llm.py` 가 적어 둔 「같은 20줄이 복사되며 실패 표시가 빠졌다」와
      같은 종류의 사고다.

    ★**모델명은 응답에서 읽는다.** `config.AGENT_MODEL` 을 쓰면 「부르려 했던
      것」이 남는데, 비용을 되짚으려면 **실제로 답한 스냅샷**이 있어야 한다.

    ★`usage_metadata` 가 없으면 **호출만 세고 따로 표시한다.** 토큰 0 으로
      더해 버리면 그 실행이 공짜로 돌았던 것처럼 보인다.
    """
    seen = _BUCKET.get()
    if seen is None:
        return
    metadata = getattr(message, "response_metadata", None) or {}
    model = metadata.get("model_name") or "알수없음"
    seen.llm_calls[model] += 1

    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        seen.llm_calls_without_usage += 1
        return
    seen.llm_input_tokens[model] += int(usage.get("input_tokens") or 0)
    seen.llm_output_tokens[model] += int(usage.get("output_tokens") or 0)
    # ★추론 토큰은 **출력 토큰 안에 이미 포함돼** 있다. 따로 더하지 말고
    #   「그중 얼마가 추론이었나」로만 읽는다 — 더하면 비용이 두 번 세어진다.
    details = usage.get("output_token_details") or {}
    seen.llm_reasoning_tokens[model] += int(details.get("reasoning") or 0)


def record_claims(summary: Mapping) -> None:
    """`claim_check.summarize()` 의 결과 중 **넷**을 담는다.

    ★넷만 담는다 — `summarize()` 는 15개를 계산하지만, 여기 필요한 것은
      「uncited 비율」을 모델 교체 전후로 비교하는 데 쓰는 값뿐이다. 나머지는
      로그에 그대로 남아 있고, 관측 버킷이 판정에 안 쓰는 값까지 이고 다니면
      보고서 표가 읽히지 않는다.

    ★**빈 것도 기록한다.** 부르는 쪽이 「주장 0건」일 때도 이걸 부르므로
      `claims_checked` 가 켜진다 — 그래야 「0건이었다」와 「그 노드를 안
      지났다」가 갈린다.
    """
    seen = _BUCKET.get()
    if seen is None:
        return
    seen.claims_checked = True
    seen.claims_total += int(summary.get("claims") or 0)
    seen.claims_uncited += int(summary.get("uncited") or 0)
    seen.claims_no_text += int(summary.get("no_text") or 0)
    seen.claims_unlinked += int(summary.get("unlinked") or 0)
    seen.claims_link_unknown += int(summary.get("link_unknown") or 0)
