"""질문 의도로 관계 순서를 정한다 — **`evidence_selector` 와 대칭이다.**

★왜 필요한가 (설계서 §11 · 현황서 §5-4)

  사건에는 의도 선택이 있는데 관계에는 없었다.

      사건   events_of() → evidence_selector 가 질문 의도로 고른다        ✅
      관계   relations_of(key) → 8종을 점수순으로 자를 뿐                 🔴

  게다가 `SearchQuery.edge_types`·`direction` 을 `retrieve_service` 가 한 번도
  참조하지 않았다 — **질문이 무슨 관계를 물었는지가 Retrieve 경계에서
  사라진다.** 관계가 상한을 넘는 기업에서는 질문이 물은 엣지가 점수순 상위에
  못 들면 빠지고, 그러면 `[사실]` 블록에 구조가 없어 LLM 이 관계를 **근거
  원문에서 읽어내야** 한다. 그건 ③(그래프)과 ⑥(근거)을 섞는 일이다
  (설계서 §10 「관계를 찾는 단계와 근거를 확인하는 단계를 혼동하지 않는 규칙」).

★`evidence_selector` 와 **다른 점 하나 — 아무것도 버리지 않는다.**

  flow ④a 의 금지사항이 「관계를 **지우지** 않는다(없는 것으로 읽힌다)」다.
  그래서 `select()`(kept, cut)가 아니라 `order()`(순서만)이다. 자르는 것은
  `retrieve_service` 의 링 상한이 이미 하고 있고 로그도 거기 있다.

★**Search Layer 를 고치지 않는다.** 필요한 신호(`edge_types`·`direction`)가
  이미 `SearchQuery` 에 있고 Retrieve 손에 와 있다(설계서 §11).

★**링(ring) 순서를 이기지 않는다.** 이 모듈은 링 **안에서만** 줄을 세운다 —
  링을 가로질러 의도를 우선할지는 아직 `[DECIDE]` 다(현황서 §5-17·§7-3).
  실측 없이 그 숫자를 정하지 않는다.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, Sequence


class _QueryLike(Protocol):
    edge_types: Optional[list[str]]
    direction: Optional[Any]


def matched_edge_types(query: _QueryLike) -> frozenset[str]:
    """질의가 지목한 엣지 타입들. 없으면 빈 집합(= 티어 없음).

    ★`evidence_selector.matched_event_types()` 와 같은 규약이다 — **hard filter 가
      아니다.** 안 걸린 관계도 자리가 남으면 그대로 살아남고, 순서만 뒤로 간다.
    """
    return frozenset(query.edge_types or ())


def _direction_matches(row: dict, direction: Any, anchor_keys: set[str]) -> bool:
    """이 관계가 질의가 물은 방향인가.

    ★`symmetric` 이면 **항상 참으로 본다.** PARTNERS_WITH·COMPETES_WITH 의
      화살표는 Neo4j 가 무방향을 저장 못 해 「키 작은 쪽 → 큰 쪽」으로 고정한
      **인공 방향**이다(설계서 §9-3 ⓐ). 그 방향으로 줄을 세우면 없는 신호로
      순서를 정하게 된다.
    """
    if direction is None or not anchor_keys:
        return True
    if row.get("symmetric"):
        return True
    anchored_end = "source" if getattr(direction, "value", direction) == "outgoing" else "target"
    return row[anchored_end]["key"] in anchor_keys


def order(rows: Sequence[dict], *, matched: frozenset[str],
          direction: Any = None, anchor_keys: Optional[set[str]] = None) -> list[dict]:
    """질문이 물은 관계를 위로. **버리지 않고 순서만 바꾼다.**

    약한 신호부터 차례로 정렬한다(파이썬 정렬은 안정적이라 뒤 정렬이 이긴다):

        입력 순서(=점수순) → 방향 일치 → 의도 엣지 타입

    동점이면 입력 순서가 남는다 — 같은 질문에 매번 다른 답이 나오면 안 된다
    (`evidence_selector.select` 와 같은 규약).
    """
    ordered = list(rows)
    if not matched:
        # 의도가 없으면 순서를 건드리지 않는다 — 점수순이 그대로다.
        return ordered
    keys = anchor_keys or set()
    ordered.sort(key=lambda r: not _direction_matches(r, direction, keys))
    ordered.sort(key=lambda r: 0 if r["type"] in matched else 1)
    return ordered
