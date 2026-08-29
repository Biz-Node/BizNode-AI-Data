"""관측 — **재기만 한다. 아무 동작도 바꾸지 않는다.**

Phase 8 의 완료 기준은 「Agent 가 답을 더 잘 쓰나」가 아니라 **「Agent 가 무엇을
얼마나 썼나」**다. 그걸 재려면 한 질문이 그래프를 지나는 동안 흩어져 일어나는
일들을 한 자리에 모아야 한다:

    도구        몇 번 불렀나 · 어느 도구를 · 거부당한 것은 몇 건인가
    임베딩      몇 번 계산했나 · 캐시가 몇 건 맞고 몇 건 빗나갔나
    링(ring)    도구가 본 관계의 링 분포 · 상한에 남은 것과 잘린 것
    인용        **최종 답변이 인용한 관계**의 링 분포

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
from typing import Iterator, Mapping, Optional, Sequence

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

    def summary(self) -> dict:
        """보고서·로그가 읽는 납작한 dict. **정렬을 여기서 못 박는다** —
        Counter 를 그대로 내보내면 순서가 실행마다 달라져 문서 diff 가 커진다."""
        return {
            "tool_calls": self.tool_calls,
            "tools_used": dict(sorted(self.tools_used.items())),
            "tool_items": dict(sorted(self.tool_items.items())),
            "tool_errors": dict(sorted(self.tool_errors.items())),
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
