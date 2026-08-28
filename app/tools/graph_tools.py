"""Graph 계열 도구 셋 — **기존 Service 를 감싼다.**

    Agent(2차) → Tool(여기) → 기존 Service → Repository

Repository 를 직접 부르지 않고 새 쿼리도 만들지 않는다. 이 계층이 더하는 것은
**재료가 아니라 표기와 방어**다:

    표기   raw row 를 `app/tools/dto.py` 의 DTO 로 — LLM 이 오해할 값에 문구를 붙인다
    방어   ① 범위 밖 key 거부  ② 해소 실패를 0건과 구별  ③ 의심 표시 엣지·사건 제외

★**새 재료를 만들지 않는다.** 1.5차는 순수 리팩터링이라, 도구가 돌려주는 재료
  집합이 1차의 `fetch_*` 와 같아야 한다(`batch/audit/ask_graph_parity.py
  --materials`). 표기가 붙어 프롬프트는 길어지지만 **무엇을 담았나는 그대로**다.

도구 4원칙
──────────────────────────────────────────────────────────────────
① **기업명 문자열을 받지 않는다.** key 만 받고 범위 밖은 거부한다. 도구가 이름을
   다시 해소하면 앵커 판정이 무의미해진다 — `AskResponse.anchor_source` 는
   「LLM 과 무관한 서버가 아는 결정론적 값」이라는 계약이다(설계서 §14-3).
② **표기가 끝난 DTO 를 돌려준다.** raw row 금지.
③ **`limit` 을 인자로 받지 않는다.** 상한은 도구 내부 상수다 — 부르는 쪽이
   LLM 이 되면 상한이 협상 대상이 된다.
④ **빈 결과와 실패를 구별한다.** 해소 실패는 `ToolError`, 정말 없으면 `[]`.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.core.trace import trace_logger
from app.services import (company_service, evidence_selector, relation_selector,
                          relation_service)
from app.services.company_service import _HIDE
from app.services.retrieve_service import _ring_of
from app.tools import scope
from app.tools.dto import (CAUTION_NEWS_DEVELOPS, DIRECTION_NOTE, FRESHNESS_WEIGHT,
                           ROLE_NOTE, SOURCE_NOTE, STATED_NOTE,
                           SYMMETRIC_EDGE_TYPES, EventDTO, EventPhaseDTO,
                           PropagationDTO, RelationDTO)
from app.tools.errors import KeyNotResolved

log = trace_logger(__name__)

# ★상한은 **값을 그대로** 가져온다. 새로 쓰면 두 벌이 되어 조용히 갈린다 —
#   `retrieve_service` 의 그 상수를 그대로 import 한다(같은 객체다).
from app.services.retrieve_service import (  # noqa: E402  (상한 출처를 붙여 둔다)
    _MAX_EVENTS_PER_COMPANY, _MAX_RELATIONS_PER_COMPANY,
    _MAX_RISK_EVENTS_FOR_PROPAGATION)


# ══════════════════════════════════════════════════════════════════
#  key 해소 — ★조용한 0건을 실패로 바꾼다
# ══════════════════════════════════════════════════════════════════


def _resolve(keys: Sequence[str]) -> list[str]:
    """입력 key → **그래프가 아는 `norm_name`.** 못 찾으면 `KeyNotResolved`.

    ★왜 필요한가 — `company_service.events_of()` 는 `corp_code` 든 `norm_name`
      이든 받지만(`WHERE c.corp_code = $k OR c.norm_name = $k`), **틀린 값을
      주면 예외가 아니라 조용히 0건**이다. 그러면 「이 기업에 사건이 없다」와
      구별이 안 된다. 여기서 한 번 확인하고 넘긴다.

    ★`norm_name` 으로 바꿔 넘겨도 **재료가 안 바뀐다**(실측 2026-08-28):
      Company 3,432곳의 `norm_name` 은 **전부 유일**하고(겹치는 이름 0종),
      `corp_code` 와 같은 문자열인 `norm_name` 도 0건이다. 표본 400곳에서
      `corp_code` 로 부를 때와 `norm_name` 으로 부를 때 매칭 노드 수가 갈리는
      기업이 0곳이었다. 겹치는 이름이 생기면 이 전제가 깨지므로
      `tests/tools/test_graph_tools.py` 가 그 불변식을 묶어 둔다.
    """
    wanted = scope.check(keys)          # ① 범위 밖이면 여기서 `OutOfScopeKey`
    if not wanted:
        return []
    found = company_service.norm_names_by_keys(wanted)
    missing = [k for k in wanted if k not in found]
    if missing:
        # ★0건으로 넘어가지 않는다. 「해소됐다 ≠ 그래프에 있다」다.
        raise KeyNotResolved(f"그래프에서 Company 를 못 찾은 key: {missing}")
    return [found[k] for k in wanted]


# ══════════════════════════════════════════════════════════════════
#  관계
# ══════════════════════════════════════════════════════════════════


def _ratio_text(ratio: Optional[float]) -> Optional[str]:
    """★단위를 문자열에 박는다. `0.72` 는 0.72% 지 소수가 아닌데, 0~1 구간에
    진짜 소액지분이 **126건** 실재해서(0차 실측) 값만으로는 구별이 안 된다."""
    if ratio is None:
        return None
    # 정수면 소수점을 안 붙인다 — 「33%」가 「33.0%」보다 읽힌다.
    return f"{ratio:g}%"


def _caution_of(edge_type: str, source_type: str) -> Optional[str]:
    """★뉴스에서 뽑은 `DEVELOPS` 는 절반 가까이 틀린다 — 0차 실측 46.1%
    (근거검증을 거친 672건 중 310건 탈락)."""
    if edge_type == "DEVELOPS" and source_type == "news":
        return CAUTION_NEWS_DEVELOPS
    return None


def _relation_dto(row: dict[str, Any]) -> RelationDTO:
    source_type = row.get("source_type") or "news"
    edge_type = row["type"]
    direction = "symmetric" if edge_type in SYMMETRIC_EDGE_TYPES else "directed"
    freshness = row.get("freshness") or "unknown"
    ratio = row.get("ratio")
    return RelationDTO(
        edge_id=row["edge_id"],
        source=row["source"]["name"], target=row["target"]["name"],
        source_key=row["source"]["key"], target_key=row["target"]["key"],
        edge_type=edge_type, subtype=row.get("subtype") or None,
        evidence_id=row.get("evidence_id"),
        source_type=source_type, source_note=SOURCE_NOTE[source_type],
        direction=direction, direction_note=DIRECTION_NOTE[direction],
        freshness=freshness,
        # ★`score` 를 그대로 쓰면 안 된다 — 저건 corroboration 보정과 wrong_type
        #   벌점까지 곱한 뒤 1.0 에서 잘린 값이라 「confidence × 신선도」가 아니다.
        effective_confidence=round(
            float(row.get("confidence") or 0.7) * FRESHNESS_WEIGHT[freshness], 3),
        caution=_caution_of(edge_type, source_type),
        ratio=ratio, ratio_unit="percent" if ratio is not None else None,
        ratio_text=_ratio_text(ratio),
    )


def get_relations(keys: Sequence[str], edge_types: Optional[Sequence[str]] = None,
                  direction: Optional[str] = None) -> list[RelationDTO]:
    """이 기업들의 관계. **범위 밖 key 는 거부한다.**

    ★`grounding_suspect` 엣지를 뺀다 — `graph_service` 가 파급 계산에서 빼는
      것과 **같은 규칙**이다(의심 표시가 붙었고 `stage1` 이 `wrong_type` 이 아닌
      것). 근거 없는 엣지 하나가 없는 파급을 만들기 때문인데, 도구가 같은 엣지를
      관계로 돌려주면 방어가 반쪽이 된다.

      ★**`wrong_type` 은 남긴다.** 의심 표시가 붙어도 「관계 자체는 실재하고
        유형만 틀린」 것이라 지우지 않고 점수만 깎는 것이 규칙이다
        (`graph_service._HIDE` 도 `stage1='wrong_type'` 을 예외로 둔다).
        원시 `grounding_suspect` 로 거르면 이 58건이 함께 지워진다.

      ★실측(2026-08-28): 이 제외로 **추가로 빠지는 관계는 0건**이다.
        `company_service._relation()` 이 이미 같은 `_HIDE` 를 적용해서
        suspect 507건 중 449건이 Service 에서 빠지고 `wrong_type` 58건만
        남는다. **그래도 여기서 다시 본다** — 위쪽 규칙이 느슨해지면 이 도구가
        조용히 따라 느슨해지면 안 되기 때문이다.

    `edge_types`·`direction` 은 **거르지 않고 순서만** 정한다 — 워크스페이스가
    hard filter 가 아닌 것과 같은 이유다(설계서 §3).
    """
    ctx = scope.context()
    norms = _resolve(keys)
    by_ring: dict[int, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    suspect_dropped = 0
    for norm in norms:
        for row in company_service.relations_of(norm):
            if row["edge_id"] in seen:
                continue
            seen.add(row["edge_id"])
            if row.get("verdict") in _HIDE:
                # ★Service 가 이미 뺐어야 하는 것이 여기 오면 **세어서 남긴다.**
                #   0 이 아니면 위쪽 규칙이 바뀐 것이다.
                suspect_dropped += 1
                continue
            by_ring.setdefault(_ring_of(row, set(ctx.workspace_keys)), []).append(row)
    if suspect_dropped:
        log.info("tools.relations grounding_suspect 제외 %d건 "
                 "(Service 가 이미 빼는 것이 정상 — 0 이 아니면 위쪽 규칙이 바뀐 것)",
                 suspect_dropped)

    # ★**링(ring) 순서로 줄을 세운 뒤에 자른다**(설계서 §3). 점수순으로 먼저
    #   자르면 Ring 0 이 통째로 사라진다 — 실측(2026-08-25) 삼성전자 관계 526건에서
    #   Ring 0 은 137·225·414번째다. 상위 10건만 받으면 워크스페이스 안쪽 관계가
    #   하나도 안 남는다.
    #
    # ★**hard filter 가 아니다.** 워크스페이스와 안 닿는 관계(Ring 3)도 남긴다 —
    #   순서만 뒤로 간다. `edge_types`·`direction` 도 마찬가지로 **순서만** 정하고,
    #   그 정렬은 **링 안에서만** 한다(현황서 §5-17 이 아직 `[DECIDE]` 다).
    matched = frozenset(edge_types or ())
    ordered = [row
               for ring in sorted(by_ring)
               for row in relation_selector.order(
                   by_ring[ring], matched=matched, direction=direction,
                   anchor_keys=set(ctx.anchor_keys))]
    limit = _MAX_RELATIONS_PER_COMPANY * max(len(norms), 1)   # ③ 인자가 아니다
    kept, cut = ordered[:limit], ordered[limit:]
    log.info("tools.relations rings %s -> kept=%d cut=%d matched=%s direction=%s",
             {ring: len(rows) for ring, rows in sorted(by_ring.items())},
             len(kept), len(cut), sorted(matched), direction)
    return [_relation_dto(r) for r in kept]


# ══════════════════════════════════════════════════════════════════
#  사건
# ══════════════════════════════════════════════════════════════════


def _timeline_summary(phases: list[EventPhaseDTO]) -> Optional[str]:
    """★국면을 한 줄로 압축한다. 최대 **13국면**짜리가 있어(0차 실측) 그대로
    프롬프트에 실으면 사건 하나가 재료를 다 먹는다.

    ★그래도 **배열을 문자열로 펴지 않는다** — 요약은 별도 필드다. 편 적이 있어서
      `size()` 가 국면 수가 아니라 글자 수를 센 사고가 있었다(28건).
    """
    if not phases:
        return None
    body = " → ".join(f"{p.period} {p.name}" for p in phases)
    return f"{body} ({len(phases)}국면)"


def _event_dto(row: dict[str, Any]) -> EventDTO:
    role = row.get("role") or "subject"
    phases = [EventPhaseDTO(period=p["period"], name=p["name"])
              for p in (row.get("timeline") or [])]
    return EventDTO(
        event_id=row["event_id"], name=row["name"],
        event_type=row.get("event_type") or "기타", is_risk=bool(row.get("is_risk")),
        # ★Event 노드에 날짜가 없다(0차 실측 1,058건 전부). `events_of()` 가
        #   이미 `HAS_EVENT` 엣지에서 꺼내 주므로 그 값을 그대로 쓴다.
        occurred_at=row.get("occurred_at"),
        evidence_ids=list(row.get("evidence_ids") or []),
        # ★극성은 `IMPACTS` 엣지에서 온다. 짝이 없는 사건이면 `None` 이다 —
        #   0 이나 "neutral" 로 메우면 **모르는 것을 아는 척**하는 것이 된다.
        role=role, role_note=ROLE_NOTE[role], sign=row.get("sign"),
        timeline=phases, timeline_summary=_timeline_summary(phases),
    )


def get_events(keys: Sequence[str], intent: str) -> list[EventDTO]:
    """이 기업들의 사건. `intent` 로 순위를 정한다.

    ★`eventness_suspect` 사건을 뺀다 — 사건이 아닌 것으로 보이는 **83건**에 붙은
      표시다(ERD: 「표시만 하고 안 지운다」). 그중 기업에 붙어 있는 것은 74건 ·
      `HAS_EVENT` 92개 · 기업 42곳이다(2026-08-28 실측). 표시가 있는데 재료로
      쓰면 표시를 한 이유가 없어진다.

    ★**`role` 로 거르지 않는다 — 검색 필터로서의 role 은 두지 않는다.**
      1차 `_events_of()` 가 role 을 안 걸렀고, 재료 집합이 1차와 같아야 한다
      (`ask_graph_parity.py --materials`). 인자로 두면 2차의 Agent 가
      「이 기업에 난 일만 보겠다」를 스스로 정하게 되는데, 그건 **재료 범위를
      LLM 이 정하는 것**이라 도구 4원칙 ① 와 같은 이유로 막는다.

      ★**결과의 role 은 그대로 남는다** — `EventDTO.role`·`role_note` 는
        지우지 않는다. 「이 기업에 난 일」인지는 LLM 이 그 문구를 읽고
        판단한다. 「거르기」와 「표기하기」를 가른다.

    ★선택은 **기업 scope 안에서, 기업마다 따로** 한다. 전부 한 줄로 세워 자르면
      사건이 많은 기업이 상한을 다 먹고 나머지 기업이 통째로 사라진다 — 그건
      「관련 없어서」가 아니라 「다른 기업이라서」 버린 것이다.
    """
    ctx = scope.context()
    norms = _resolve(keys)
    if not norms:
        return []

    by_company: list[tuple[str, list[dict]]] = []
    suspect_dropped = 0
    for norm in norms:
        kept = []
        for row in company_service.events_of(norm):
            if row.get("eventness_suspect"):
                suspect_dropped += 1
                continue
            kept.append(row)
        by_company.append((norm, kept))

    # ★유사도는 **한 번에** 구한다 — 기업마다 부르면 왕복이 기업 수만큼 는다.
    # ★`anchor_names` 를 넘겨야 한다. `event_label()` 이 라벨에서 앵커 기업명을
    #   떼는데, 안 넘기면 「기업명이 든 라벨이 상위를 먹는」 실험 ② 로 되돌아간다
    #   (현황서 §5-23). 넘길 이름은 **서버가 정한 앵커**에서만 온다.
    matched = evidence_selector.matched_event_types(intent)
    flat = [_Row(r) for _, rows in by_company for r in rows]
    sims = evidence_selector.similarities(
        flat, intent=intent, embed=_embed(), anchor_names=list(ctx.anchor_names))

    out: list[dict] = []
    seen: dict[str, dict] = {}
    for _norm, rows in by_company:
        kept, _cut = evidence_selector.select(
            [_Row(r) for r in rows], matched=matched, sims=sims,
            limit=_MAX_EVENTS_PER_COMPANY)             # ③ 인자가 아니다
        for wrapped in kept:
            row = wrapped.raw
            if row["event_id"] in seen:
                continue
            seen[row["event_id"]] = row
            out.append(row)

    if suspect_dropped:
        log.info("tools.events eventness_suspect 제외=%d kept=%d",
                 suspect_dropped, len(out))
    return [_event_dto(r) for r in out]


class _Row:
    """`evidence_selector` 가 요구하는 속성만 노출하는 얇은 어댑터.

    ★raw dict 를 `Event` 로 만들었다가 되돌리지 않는다 — 그러면
      `eventness_suspect` 같은 도구 전용 필드가 pydantic 에서 떨어진다.
    """

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    @property
    def event_id(self) -> str:
        return self.raw["event_id"]

    @property
    def name(self) -> str:
        return self.raw.get("name") or ""

    @property
    def event_type(self) -> str:
        return self.raw.get("event_type") or "기타"

    @property
    def is_risk(self) -> bool:
        return bool(self.raw.get("is_risk"))

    @property
    def occurred_at(self):
        return self.raw.get("occurred_at")


def _embed():
    """`retrieve_service` 와 **같은 임베더**를 늦게 읽는다 — 테스트가
    `_default_embed` 를 monkeypatch 해서 끌 수 있어야 한다."""
    from app.services import retrieve_service

    return retrieve_service._default_embed


# ══════════════════════════════════════════════════════════════════
#  파급
# ══════════════════════════════════════════════════════════════════


def get_propagation(event_ids: Sequence[str]) -> list[PropagationDTO]:
    """사건들이 그래프를 타고 어디까지 번지나.

    ★**빈 결과와 실패를 가른다** — `relation_service.event_impact()` 가 이미
      `None`(사건 노드를 못 찾음) vs `[]`(파급이 없음)로 그 규약을 쓰고 있어
      그대로 따른다. 못 찾은 사건은 **경고를 남기고 건너뛴다**. 여기서 예외를
      던지면 사건 하나가 없다고 나머지 파급이 통째로 사라져 재료가 달라진다.
    """
    out: list[PropagationDTO] = []
    for event_id in list(event_ids)[:_MAX_RISK_EVENTS_FOR_PROPAGATION]:   # ③
        rows = relation_service.event_impact(event_id)
        if rows is None:
            # 사건 노드를 못 찾음 — 조용히 0건으로 두지 않는다
            log.warning("event_impact miss: %s", event_id)
            continue
        for row in rows:
            out.append(PropagationDTO(
                event_id=event_id, target=row["target"], key=row.get("key"),
                score=row["score"], hops=row["hops"], stated=bool(row["stated"]),
                stated_note=STATED_NOTE[bool(row["stated"])],
                path=list(row.get("path") or []),
            ))
    return out
