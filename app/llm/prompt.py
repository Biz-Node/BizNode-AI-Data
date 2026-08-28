"""프롬프트 조립의 **공용 부분** — 사본을 하나로 줄인다.

★왜 여기인가 (Phase 1.5 계약 5번)

  프롬프트 조립이 두 벌이었다:

      answer_service._build_user_prompt()   1차 · 대조 기준선 · API 스키마를 읽는다
      app/graph/prompt.build_user_prompt()  1.5차 · 운영 경로 · 도구 DTO 를 읽는다

  2차에서 표기가 더 붙는데 사본이 둘이면 **한쪽만 고쳐지고 그 차이를 아무도 못
  본다.** 실제로 그 일이 이미 한 번 났다(공유 사건의 근거 병합).

★**`[사실]` 줄 렌더링은 여기 없다.** 두 경로가 **의도적으로 다르게** 그리기
  때문이다 — 1.5차는 `role_note`·`direction_note`·`effective_confidence`·
  `ratio_text`·`stated_note` 를 붙이고 1차는 안 붙인다(실측 +1,043자). 그건
  사본이 아니라 **서로 다른 두 렌더러**라, 합치면 대조 기준선이 사라진다.

  여기 있는 것은 **두 경로가 글자까지 같은 것들**이다:

      근거 블록 조립      델리미터 중화 · `about` 표기 · missing 제외
      근거 귀속(`about`)  관계 양끝 · 사건 id · 미연결
      화이트리스트        인용 id → `Source` (지어낸 id · missing 제외)
      파급 공평 분배      사건별 라운드로빈
      바깥 껍데기         `질문:` / `[사실]` / `[근거]`

  전부 **보안·귀속에 직접 걸리는 것들**이라(델리미터 무결성 · 오귀속 방지 ·
  인용 화이트리스트) 두 벌로 두면 안 되는 자리이기도 하다.

★**타입을 통일하지 않는다.** 한쪽은 API 스키마(`Relation`·`Event`), 한쪽은
  도구 DTO(`RelationDTO`·`EventDTO`)이고 둘 다 이미 나가 있는 계약이다. 대신
  이 모듈이 필요한 것만 담은 **얇은 참조 타입**(`RelationRef`·`EventRef`)을
  받고, 각 경로가 자기 타입을 거기에 맞춰 넘긴다 — 덕 타이핑으로 두 모양을
  동시에 받으면 어느 쪽이 왔는지 모르는 채로 필드를 더듬게 된다.
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Sequence

from app.api.schemas import AnchorSource, Evidence, MatchType, Source


# ══════════════════════════════════════════════════════════════════
#  얇은 참조 타입 — 두 경로가 자기 타입을 여기에 맞춘다
# ══════════════════════════════════════════════════════════════════


class RelationRef(NamedTuple):
    """근거 귀속·화이트리스트에 필요한 관계 정보만."""

    evidence_id: Optional[str]
    edge_id: str
    source_key: Optional[str]
    source_name: str
    target_key: Optional[str]
    target_name: str


class EventRef(NamedTuple):
    """근거 귀속·연결성 판정에 필요한 사건 정보만."""

    event_id: str
    event_type: str
    evidence_ids: Sequence[str]


# ══════════════════════════════════════════════════════════════════
#  표기 문구 — ★구현이 고쳐 쓸 수 없게 여기 한 벌만 둔다
# ══════════════════════════════════════════════════════════════════

# ★dict 조회로 전수 분기한다 — 미지 값을 조용히 EXACT(="확신을 갖고 말해도
#   된다"는 허가)로 떨어뜨리지 않는다. 매핑에 없는 값이 오면 KeyError 로
#   즉시 죽는다.
NOTE_BY_MATCH_TYPE: dict[MatchType, str] = {
    MatchType.EXACT: "검색 방식: EXACT — 이름 또는 관계가 그래프에서 정확히 일치한 결과입니다.",
    MatchType.SEMANTIC: ("검색 방식: SEMANTIC — 이름/키워드가 정확히 일치하지 않아 의미가 "
                         "비슷한 문서로 찾은 결과입니다. 확정된 사실처럼 말하지 마세요."),
}

TARGET_NOTE_BY_SOURCE: dict[AnchorSource, str] = {
    AnchorSource.QUERY: "답변 대상: 질문 — 질문이 지정한 대상에 대해 답합니다.",
    AnchorSource.WORKSPACE: ("답변 대상: 워크스페이스 — 질문이 대상을 지정하지 않아 "
                             "워크스페이스 기업들을 대상으로 삼았습니다."),
    # `unresolved` 는 애초에 LLM 을 부르지 않는다(설계서 §14-4).
    AnchorSource.UNRESOLVED: "",
}

# ★`[사실]` 의 어느 줄과도 이어지지 않은 근거에 붙는 표기. 검색 히트가 들고
#   왔을 뿐인 근거다 — 실측(현황서 §8-6) 「납품 단가 압박」 38건 중 18건 ·
#   「최근 인수 사례」 140건 중 58건이 **워크스페이스에 닿지 않는다.** 걸러 봤다가
#   되돌린 것이 옳았지만(질문이 물은 사례가 사라졌다) **표기 없이** 들어오면
#   LLM 이 워크스페이스 기업 이야기로 끌어 쓴다.
UNLINKED_EVIDENCE = "미연결"

MAX_PROPAGATION_LINES = 15


def match_type_note(match_type: MatchType) -> str:
    return NOTE_BY_MATCH_TYPE[match_type]


def neutralize_delimiters(text: str) -> str:
    """`<`/`>` 를 그대로 두면 근거 원문 속 `</evidence>` 가 델리미터를 조기에
    닫아버릴 수 있다(설계서 §13-2). 보기엔 비슷하지만 태그로는 안 먹히는
    문자로 바꿔 델리미터 무결성을 지킨다."""
    return text.replace("<", "‹").replace(">", "›")


def membership(key: Optional[str], name: str, workspace_keys: set[str]) -> str:
    """★「이 기업이 사용자의 워크스페이스 안인가」 — 설계서 §12. **표기만 한다.**

    ★`key` 가 없으면 **표기하지 않는다.** 이름만 있고 노드가 없는 대상을
      「바깥」이라고 적으면 모르는 것을 아는 척하는 것이 된다 —
      `_propagation_membership` 이 `key is None` 을 비켜 가는 것과 같은 이유다.
    """
    if not workspace_keys or not key:
        return name
    return f"{name}={'워크스페이스' if key in workspace_keys else '바깥'}"


# ══════════════════════════════════════════════════════════════════
#  근거 귀속 — 「이 근거가 [사실] 의 어느 줄에서 왔나」
# ══════════════════════════════════════════════════════════════════


def evidence_about(relations: Sequence[RelationRef], events: Sequence[EventRef],
                   evidence: Sequence[Evidence],
                   workspace_keys: set[str]) -> dict[str, str]:
    """근거 id → **이 근거가 `[사실]` 의 어느 줄에서 왔는가.**

    ★왜 필요한가 — `<evidence>` 태그에 **기업 속성이 없었다**(id·source_type·
      press·published_at 뿐). 「이 근거가 누구 얘기냐」가 프롬프트에 없어서, 그
      사이에서 **근거가 실제로 말하는 기업이 아니라 워크스페이스 기업 중 하나로
      귀속되는** 오답이 났다.

    ★**증명할 수 있는 것만 적는다.**

          관계 근거   양끝 기업 이름 + 워크스페이스 소속   ← 관계가 키를 들고 있다
          사건 근거   사건 id 까지만                      ← ★사건에 기업 키가 없다
          그 밖       `미연결`                            ← 어느 줄도 참조하지 않는다

      사건 근거를 기업까지 못 짚는 것은 사건 DTO 에 기업 키가 없기 때문이다.
      **지어내지 않고 사건 id 로 넘긴다** — 사건 줄이 `[사실]` 에 있으므로 LLM 이
      그 줄에서 맥락을 읽을 수 있다.
    """
    about: dict[str, list[str]] = {}
    for relation in relations:
        if not relation.evidence_id:
            continue
        about.setdefault(relation.evidence_id, []).extend([
            membership(relation.source_key, relation.source_name, workspace_keys),
            membership(relation.target_key, relation.target_name, workspace_keys)])
    for event in events:
        for evidence_id in event.evidence_ids:
            about.setdefault(evidence_id, []).append(f"사건 {event.event_id}")

    out: dict[str, str] = {}
    for item in evidence:
        marks = about.get(item.evidence_id) or []
        # 같은 기업이 여러 관계에 걸리면 한 번만 적는다 — 순서는 지킨다.
        out[item.evidence_id] = (" · ".join(dict.fromkeys(marks)) if marks
                                 else UNLINKED_EVIDENCE)
    return out


def evidence_block(evidence: Sequence[Evidence], about: dict[str, str]) -> str:
    """`<evidence>` 블록들. ★**신뢰 안 된 텍스트**라 델리미터 중화를 거친다."""
    blocks = []
    for item in evidence:
        if item.missing:
            continue
        # ★`press` 를 안 실으면 「어느 언론이 보도했나」를 답할 수 없다(설계서 §9-3).
        #   ★속성값에 `"` 가 섞이면 태그가 깨지므로 따옴표도 함께 없앤다.
        press = neutralize_delimiters(item.press or "").replace('"', "'")
        # ★`about` 도 같은 중화를 거친다 — 기업·사건 이름은 뉴스 → LLM 추출 →
        #   Neo4j 로 들어온 신뢰 안 된 텍스트다.
        marks = neutralize_delimiters(
            about.get(item.evidence_id, UNLINKED_EVIDENCE)).replace('"', "'")
        blocks.append(
            f'<evidence id="{item.evidence_id}" source_type="{item.source_type}" '
            f'press="{press}" about="{marks}" '
            f'published_at="{item.published_at or ""}">\n'
            f'{neutralize_delimiters(item.text)}\n</evidence>')
    return "\n".join(blocks) if blocks else "(인용 가능한 근거 없음)"


def event_types_by_evidence(events: Sequence[EventRef]) -> dict[str, frozenset[str]]:
    """근거 id → 그 근거가 달린 **사건들의 event_type**.

    ★연결성 판정(`claim_check._intent_linked`)의 재료다. 관계·히트에서만 온
      근거는 여기 없고, 그건 「연결 없음」이 아니라 **「판정 불가」**다.
    """
    out: dict[str, set[str]] = {}
    for event in events:
        for evidence_id in event.evidence_ids:
            out.setdefault(evidence_id, set()).add(event.event_type)
    return {k: frozenset(v) for k, v in out.items()}


# ══════════════════════════════════════════════════════════════════
#  파급 — 사건별 공평 분배
# ══════════════════════════════════════════════════════════════════


def select_propagation(propagation: list, limit: int = MAX_PROPAGATION_LINES):
    """사건별로 **공평하게** 나눠 담는다 — 뺀 몫은 사건 이름별로 돌려준다.

    ★앞에서부터 자르면 **첫 사건이 예산을 통째로 먹는다.** 실측(fixture
      `ask_sk_hynix_production_disruption`): 파급 135건이 3개 위험사건에 45건씩
      고르게 있는데 상한 15줄을 첫 사건이 독점했고, 질문(「생산 차질을 일으킬
      만한 일이 있었나?」)이 물은 바로 그 사건들 — 이천 공장 질소 누출 사고와
      우시 공장 화재 — 의 파급은 **한 줄도 실리지 않았다.**

    ★사건 안에서는 `stated=True` 가 먼저다 — 「기사가 직접 말한 것」이 「우리가
      공급망으로 계산한 것」보다 먼저 잘릴 이유가 없다. 같은 실측에서 stated=True
      3건 중 **2건이 잘리고** stated=False 14건이 자리를 차지했다.

    ★그 밖에는 **입력 순서를 지킨다** — 같은 질문에 매번 다른 순서가 나오면 안
      된다(`evidence_selector.select`·`relation_selector.order` 와 같은 규약).

    ★사건 귀속은 `path[0]` 으로 읽는다. `Propagation` 에 `event_id` 가 없고
      `relation_service.event_impact()` 가 `propagate_risk(사건이름)` 의 경로를
      그대로 싣기 때문이다 — 실측 135건이 정확히 3개 이름으로 갈렸다.

    ★**API 스키마와 도구 DTO 를 둘 다 받는다** — `path`·`stated` 만 읽고, 그
      두 필드는 두 타입에 같은 이름·같은 뜻으로 있다.
    """
    by_event: dict[str, list] = {}
    for prop in propagation:
        by_event.setdefault(prop.path[0] if prop.path else "", []).append(prop)

    # 사건 안에서 `stated=True` 를 앞으로. 파이썬 정렬은 **안정 정렬**이라
    # 나머지 순서는 입력 그대로 남는다.
    for rows in by_event.values():
        rows.sort(key=lambda p: not p.stated)

    # 라운드로빈으로 **몫만** 정한다. 출력은 아래에서 사건별로 묶는다 — 줄이
    # 사건을 오가며 번갈아 나오면 읽기 어렵다.
    quota = dict.fromkeys(by_event, 0)
    remaining = limit
    while remaining > 0:
        took = False
        for origin, rows in by_event.items():
            if quota[origin] >= len(rows):
                continue
            quota[origin] += 1
            remaining -= 1
            took = True
            if remaining == 0:
                break
        if not took:      # 모든 사건이 바닥났다 — 상한에 못 미쳐도 끝이다
            break

    kept = [prop for origin, rows in by_event.items() for prop in rows[:quota[origin]]]
    dropped = {origin: len(rows) - quota[origin]
               for origin, rows in by_event.items() if len(rows) > quota[origin]}
    return kept, dropped


# ══════════════════════════════════════════════════════════════════
#  근거 → 인용 (★화이트리스트 검증 · 설계서 §13-2)
# ══════════════════════════════════════════════════════════════════


def _edge_id_for(evidence_id: str, relations: Sequence[RelationRef]) -> Optional[str]:
    """근거가 관계에서 왔으면 그 관계의 edge_id 를 돌려준다. 없으면 None."""
    for relation in relations:
        if relation.evidence_id == evidence_id:
            return relation.edge_id
    return None


def source_of(item: Evidence, relations: Sequence[RelationRef]) -> Source:
    return Source(
        evidence_id=item.evidence_id,
        edge_id=_edge_id_for(item.evidence_id, relations),
        text=item.text,
        source_doc=item.source_doc,
        source_type=item.source_type,
        published_at=item.published_at,
    )


def sources_from(evidence_ids: Sequence[str], evidence: Sequence[Evidence],
                 relations: Sequence[RelationRef]) -> list[Source]:
    """LLM 이 인용한 evidence_id 를 **재료 안에서만** 찾는다 — 화이트리스트 검증.

    ★없는 id(지어낸 것) · `missing=true`(원문을 못 찾은 것) 는 버린다.
    """
    by_id = {e.evidence_id: e for e in evidence}
    out: list[Source] = []
    for eid in dict.fromkeys(evidence_ids):   # 순서를 지키며 중복 id 제거
        item = by_id.get(eid)
        if item is None or item.missing:
            continue
        out.append(source_of(item, relations))
    return out


def fallback_sources(evidence: Sequence[Evidence],
                     relations: Sequence[RelationRef]) -> list[Source]:
    """LLM 호출이 실패했을 때 — 필터링 근거가 없으니 `missing` 만 뺀 원본 전부."""
    return [source_of(e, relations) for e in evidence if not e.missing]


# ══════════════════════════════════════════════════════════════════
#  바깥 껍데기
# ══════════════════════════════════════════════════════════════════


def with_target_note(facts: str, anchor_source: Optional[AnchorSource]) -> str:
    """「답변 대상」 줄을 `[사실]` 앞머리에 끼운다.

    ★「검색 방식」 **바로 뒤**에 둔다 — 규칙 7·13이 둘 다 `[사실]` 앞머리를
      위치로 참조한다.

    ★`anchor_source` 가 없으면 붙이지 않는다 — 판정이 없는데 형태를 지시하면
      그게 곧 거짓말이다.
    """
    if anchor_source is None:
        return facts
    note = TARGET_NOTE_BY_SOURCE[anchor_source]
    if not note:
        return facts
    head, _, rest = facts.partition("\n")
    return f"{head}\n{note}\n{rest}" if rest else f"{head}\n{note}"


def assemble(question: str, facts: str, evidence: Sequence[Evidence],
             about: dict[str, str]) -> str:
    """`질문:` / `[사실]` / `[근거]` — **바깥 모양은 두 경로가 같다.**"""
    return (f"질문: {question}\n\n"
            f"[사실]\n{facts}\n\n"
            f"[근거]\n{evidence_block(evidence, about)}")
