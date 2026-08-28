# SEMANTIC 검색 결과 가중치 처리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `RetrieveResponse`에 검색 결과가 그래프 정확 매칭(`EXACT`)으로 찾은 것인지 벡터 의미 유사도(`SEMANTIC`)로 찾은 것인지 노출하고, `AnswerService`가 프롬프트에서 이 둘을 다르게(SEMANTIC은 확정된 사실처럼 말하지 않도록) 취급하게 만든다.

**Architecture:** `SearchOrchestrator.search()`는 이미 `SearchResult.mode`(`NAME`/`RELATIONSHIP`/`SEMANTIC`)를 채워서 돌려주지만 `RetrieveService.retrieve()`가 이 값을 버리고 있다. `RetrieveResponse`에 새 `MatchType`(`EXACT`/`SEMANTIC`) 필드를 추가하고, `RetrieveService`에서 `SearchMode`를 이분화해 채운다(NAME·RELATIONSHIP → EXACT, SEMANTIC → SEMANTIC). 그다음 `AnswerService._fact_lines()`가 이 필드를 읽어 `[사실]` 블록 맨 앞에 검색 방식 안내 줄을 붙이고, 시스템 프롬프트에 SEMANTIC일 때 조심스럽게 말하라는 규칙을 추가한다.

**Tech Stack:** Python, Pydantic(`app/api/schemas.py`), pytest.

**Spec:** `docs/BizNode_기술부채_설계검토.md` §4 항목 ① (및 §0 실행순서 ①), 설계 근거는 `docs/BizNode_Search_Layer_설계.md` §11 "SEMANTIC 결과를 같은 무게로 말하지 않는다".

## Global Constraints

- `RetrieveResponse.match_type`은 **필수 필드**(기본값 없음) — 누락하면 실수로 놓친 걸 pydantic이 즉시 잡아야 한다.
- `SearchMode.NAME`·`SearchMode.RELATIONSHIP` → `MatchType.EXACT`, `SearchMode.SEMANTIC` → `MatchType.SEMANTIC` (이분화 매핑 고정값, 기술부채 문서 §4 ①의 결정 그대로).
- 현황서(`BizNode_Search_Layer_현황서.md`) 테스트 개수 갱신은 **이 계획의 범위 밖**이다 — 기술부채 문서 §0가 "①·②(SEMANTIC 답변 품질 검증까지 끝난 뒤 ④에서 한 번에 정리"하기로 이미 정해 놓았다. 이 계획은 ①만 다룬다.
- `app/api/schemas.py`는 기존에 `search.*` 내부 enum을 API 응답에 그대로 노출한 적이 없다(`Freshness`·`EdgeType`·`NodeLabel` 모두 자체 정의) — `SearchMode`를 그대로 쓰지 않고 새 `MatchType` enum을 만든다.

---

### Task 1: `RetrieveResponse.match_type` — 스키마 필드 + `RetrieveService` 전파

**Files:**
- Modify: `app/api/schemas.py:886-902` (챗봇 섹션, `RetrieveResponse` 앞)
- Modify: `app/services/retrieve_service.py:30-100`
- Modify: `tests/services/test_retrieve_service.py:1-44` (`_orchestrator` 헬퍼 + 신규 테스트)
- Modify: `tests/services/test_retrieve_api.py:1-20,69-77` (기존 생성 호출 1건 수정)
- Modify: `tests/services/test_answer_service.py:1-34` (`_retrieved()` 헬퍼 1건 수정 — Task 2가 곧바로 재사용)

**Interfaces:**
- Produces: `app.api.schemas.MatchType`(`EXACT`|`SEMANTIC`), `RetrieveResponse.match_type: MatchType` (필수) — Task 2의 `AnswerService`가 `retrieved.match_type`으로 읽는다.
- Produces: `tests/services/test_answer_service.py`의 `_retrieved(*, evidence=(), relations=(), match_type=MatchType.EXACT)` — Task 2가 `match_type=MatchType.SEMANTIC`로 오버라이드해서 쓴다.

- [ ] **Step 1: 실패하는 테스트부터 작성**

`tests/services/test_retrieve_service.py`의 import와 `_orchestrator` 헬퍼를 바꾸고, 신규 테스트 3개를 추가한다.

```python
# 파일 상단 import 블록 교체
from app.api.schemas import AskRequest, MatchType
```

```python
# _orchestrator 함수 교체 — mode 파라미터 추가, 기존 기본값(RELATIONSHIP)은 그대로 유지해
# 기존 테스트 전부가 변경 없이 계속 통과한다.
def _orchestrator(hits, *, mode=SearchMode.RELATIONSHIP):
    orch = MagicMock()
    query = SearchQuery(raw_query="q", normalized_query="q",
                        mode=mode, today=_TODAY)
    result = SearchResult(query="q", mode=mode, hits=list(hits),
                          total=len(hits), took_ms=1, cache_hit=False,
                          used_semantic_fallback=False)
    orch.search.return_value = (query, result)
    return orch
```

파일 끝(Tier B 섹션 시작 줄, `# ══...  Tier B ...` 위)에 추가:

```python
# ── match_type ─────────────────────────────────────────────────────────

def test_match_type_is_exact_for_relationship_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.RELATIONSHIP)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_exact_for_name_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.NAME)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.EXACT


def test_match_type_is_semantic_for_semantic_mode(stub_services):
    orch = _orchestrator([], mode=SearchMode.SEMANTIC)
    got = RetrieveService(orch).retrieve(AskRequest(question="q"))
    assert got.match_type == MatchType.SEMANTIC
```

- [ ] **Step 2: 실패 확인**

Run: `.venv-wsl/bin/python -m pytest tests/services/test_retrieve_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'MatchType' from 'app.api.schemas'` (컬렉션 단계에서 전체 파일이 에러로 뜬다. 정상 — 아직 `MatchType`이 없다).

- [ ] **Step 3: 최소 구현 — 스키마 + 서비스 전파 + 기존 픽스처 수정**

`app/api/schemas.py:886-903`, 기존:
```python
# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


# ★**답변을 만들지 않는다.** 사실과 근거만 준다 — 문장 생성은 추론 담당 몫이고,
# 경계를 섞으면 「누가 지어냈나」를 못 가린다.
class RetrieveResponse(BaseModel):
    """추론 계층이 쓰는 재료."""

    question: str = Field(examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    companies: list[RelationEndpoint] = Field(default_factory=list, description="질문에서 찾아낸 기업")
```
다음으로 교체:
```python
# ══════════════════════════════════════════════════════════════════
#  챗봇
# ══════════════════════════════════════════════════════════════════


class MatchType(str, Enum):
    """검색이 이 결과를 어떤 경로로 찾았는가 — 설계서 §11 "SEMANTIC 결과를 같은
    무게로 말하지 않는다"를 추론 계층이 지킬 수 있도록 노출한다. 내부
    `search.model.enums.SearchMode`(NAME/RELATIONSHIP/SEMANTIC)를 그대로 쓰지
    않고 이분화한다 — 추론 계층에 필요한 건 「그래프에서 정확히 찾았나, 의미
    유사도로 찾았나」뿐이다."""

    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"


# ★**답변을 만들지 않는다.** 사실과 근거만 준다 — 문장 생성은 추론 담당 몫이고,
# 경계를 섞으면 「누가 지어냈나」를 못 가린다.
class RetrieveResponse(BaseModel):
    """추론 계층이 쓰는 재료."""

    question: str = Field(examples=["SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?"])
    match_type: MatchType = Field(
        description="EXACT — 이름/관계가 그래프에서 정확히 일치해 찾았다. "
                    "SEMANTIC — 프로필 문서와의 의미 유사도로 골랐다(설계서 §11, "
                    "같은 무게로 말하면 안 된다)")
    companies: list[RelationEndpoint] = Field(default_factory=list, description="질문에서 찾아낸 기업")
```

`app/services/retrieve_service.py:30-31`, 기존:
```python
from app.api.schemas import (AskRequest, Event, Evidence, Propagation, Relation,
                             RelationEndpoint, RetrieveResponse)
from app.services import company_service, relation_service
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.model.enums import EntityType
```
다음으로 교체:
```python
from app.api.schemas import (AskRequest, Event, Evidence, MatchType, Propagation, Relation,
                             RelationEndpoint, RetrieveResponse)
from app.services import company_service, relation_service
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.model.enums import EntityType, SearchMode
```

같은 파일, `_MAX_RISK_EVENTS_FOR_PROPAGATION = 3` 다음(빈 줄 하나 두고) 매핑 함수 추가:
```python
_MATCH_TYPE_BY_MODE: dict[SearchMode, MatchType] = {
    SearchMode.NAME: MatchType.EXACT,
    SearchMode.RELATIONSHIP: MatchType.EXACT,
    SearchMode.SEMANTIC: MatchType.SEMANTIC,
}


def _match_type_of(result: SearchResult) -> MatchType:
    return _MATCH_TYPE_BY_MODE[result.mode]
```

`retrieve()` 메서드의 반환문(`app/services/retrieve_service.py:93-100`), 기존:
```python
        return RetrieveResponse(
            question=request.question,
            companies=companies,
            events=events,
            relations=relations,
            propagation=propagation,
            evidence=evidence,
        )
```
다음으로 교체:
```python
        return RetrieveResponse(
            question=request.question,
            match_type=_match_type_of(result),
            companies=companies,
            events=events,
            relations=relations,
            propagation=propagation,
            evidence=evidence,
        )
```

`tests/services/test_retrieve_api.py` — import에 `MatchType` 추가(`from app.api.schemas import MatchType, RetrieveResponse`), 그리고 69-77번 줄 기존:
```python
def test_route_delegates_to_the_service(client, monkeypatch):
    """라우트에 로직이 없다 — 서비스가 준 것을 그대로 내보낸다(설계서 Rule 5)."""
    payload = RetrieveResponse(question="바꿔치기")
```
다음으로 교체:
```python
def test_route_delegates_to_the_service(client, monkeypatch):
    """라우트에 로직이 없다 — 서비스가 준 것을 그대로 내보낸다(설계서 Rule 5)."""
    payload = RetrieveResponse(question="바꿔치기", match_type=MatchType.EXACT)
```

`tests/services/test_answer_service.py` — import에 `MatchType` 추가(`from app.api.schemas import AskResponse, Evidence, MatchType, Relation, RelationEndpoint, Source`), 그리고 33-34번 줄 기존:
```python
def _retrieved(*, evidence=(), relations=()):
    return RetrieveResponse(question="q", evidence=list(evidence), relations=list(relations))
```
다음으로 교체:
```python
def _retrieved(*, evidence=(), relations=(), match_type=MatchType.EXACT):
    return RetrieveResponse(question="q", evidence=list(evidence), relations=list(relations),
                            match_type=match_type)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv-wsl/bin/python -m pytest tests/services/ -q`
Expected: PASS — 새 테스트 3개 포함 전부 통과. (Tier B 실 DB/실 OpenAI 테스트는 환경에 따라 skip 될 수 있음 — 그 경우도 정상.)

- [ ] **Step 5: 커밋**

```bash
git add app/api/schemas.py app/services/retrieve_service.py \
       tests/services/test_retrieve_service.py tests/services/test_retrieve_api.py \
       tests/services/test_answer_service.py
git commit -m "feat: expose match_type (EXACT/SEMANTIC) on RetrieveResponse"
```

---

### Task 2: `AnswerService` — `match_type`에 따라 다르게 말하기

**Files:**
- Modify: `app/services/answer_service.py:22-72`
- Modify: `tests/services/test_answer_service.py` (신규 테스트, Task 1이 이미 `_retrieved(match_type=...)`를 노출해 둠)

**Interfaces:**
- Consumes: `RetrieveResponse.match_type: MatchType`(Task 1), `tests/services/test_answer_service.py`의 `_retrieved(*, match_type=MatchType.EXACT)`(Task 1).
- Produces: `app.services.answer_service._match_type_note(match_type: MatchType) -> str` — 이 태스크 안에서만 쓰임, 외부 소비자 없음.

- [ ] **Step 1: 실패하는 테스트부터 작성**

`tests/services/test_answer_service.py` — import 줄 3에 `MatchType` 추가(`from app.api.schemas import AskResponse, Evidence, MatchType, Relation, RelationEndpoint, Source`). `test_system_prompt_tells_model_evidence_blocks_are_data` 함수 바로 다음에 추가:

```python
def test_fact_lines_hedges_when_match_type_is_semantic():
    retrieved = _retrieved(match_type=MatchType.SEMANTIC)
    lines = as_module._fact_lines(retrieved)
    assert "SEMANTIC" in lines
    assert "확정된 사실처럼 말하지 마세요" in lines


def test_fact_lines_states_exact_when_match_type_is_exact():
    retrieved = _retrieved(match_type=MatchType.EXACT)
    lines = as_module._fact_lines(retrieved)
    assert "EXACT" in lines


def test_fact_lines_still_reports_no_facts_found_when_empty():
    retrieved = _retrieved()
    lines = as_module._fact_lines(retrieved)
    assert "(찾은 사실 없음)" in lines


def test_user_prompt_includes_match_type_note():
    retrieved = _retrieved(match_type=MatchType.SEMANTIC)
    prompt = as_module._build_user_prompt("q", retrieved)
    assert "SEMANTIC" in prompt


def test_system_prompt_tells_model_to_hedge_semantic_matches():
    assert "SEMANTIC" in as_module._SYSTEM_PROMPT
```

- [ ] **Step 2: 실패 확인**

Run: `.venv-wsl/bin/python -m pytest tests/services/test_answer_service.py -v -k "match_type or hedge or no_facts_found or semantic_matches"`
Expected: FAIL — `test_fact_lines_hedges_when_match_type_is_semantic`·`test_fact_lines_states_exact_when_match_type_is_exact`·`test_user_prompt_includes_match_type_note`·`test_system_prompt_tells_model_to_hedge_semantic_matches`가 `AssertionError`("SEMANTIC" not in ... / "EXACT" not in ...)로 실패. `test_fact_lines_still_reports_no_facts_found_when_empty`는 이미 통과할 수 있음(현재 구현이 그대로 만족) — 그래도 그대로 둔다, 회귀 방지용.

- [ ] **Step 3: 최소 구현**

`app/services/answer_service.py:17`, 기존:
```python
from app.api.schemas import AskRequest, AskResponse, Evidence, Relation, RetrieveResponse, Source
```
다음으로 교체:
```python
from app.api.schemas import AskRequest, AskResponse, Evidence, MatchType, Relation, RetrieveResponse, Source
```

시스템 프롬프트(`app/services/answer_service.py:22-38`), 기존 규칙 6 다음 줄:
```python
6. 주어진 사실과 근거만으로 답할 수 없으면 모른다고 답하세요. 근거 밖의
   사실을 지어내지 마세요.

질문에 대한 답을 한국어 자연어 문장으로 작성하세요."""
```
다음으로 교체:
```python
6. 주어진 사실과 근거만으로 답할 수 없으면 모른다고 답하세요. 근거 밖의
   사실을 지어내지 마세요.
7. [사실] 맨 앞의 "검색 방식" 줄이 SEMANTIC이면, 그 아래 기업·관계는 이름이나
   키워드가 정확히 일치해서 찾은 게 아니라 의미가 비슷해서 찾은 것입니다.
   "~일 수 있습니다"처럼 조심스럽게 표현하고 확정된 사실처럼 단정하지
   마세요. EXACT면 이 구분 없이 평소대로 답하세요.

질문에 대한 답을 한국어 자연어 문장으로 작성하세요."""
```

`_fact_lines` 앞에 헬퍼 함수 추가, `app/services/answer_service.py:54` 바로 위:
```python
def _match_type_note(match_type: MatchType) -> str:
    if match_type is MatchType.SEMANTIC:
        return ("검색 방식: SEMANTIC — 이름/키워드가 정확히 일치하지 않아 의미가 "
                "비슷한 문서로 찾은 결과입니다. 확정된 사실처럼 말하지 마세요.")
    return "검색 방식: EXACT — 이름 또는 관계가 그래프에서 정확히 일치한 결과입니다."
```

`_fact_lines` 본문(`app/services/answer_service.py:54-72`), 기존:
```python
def _fact_lines(retrieved: RetrieveResponse) -> str:
    lines: list[str] = []
    if retrieved.companies:
        lines.append("기업: " + ", ".join(f"{c.name}({c.key})" for c in retrieved.companies))
    for event in retrieved.events:
        risk = "위험사건" if event.is_risk else "일반"
        lines.append(f"사건 {event.event_id}: {event.name} ({event.event_type}, "
                     f"{event.occurred_at}, {risk}) 근거: {', '.join(event.evidence_ids) or '없음'}")
    for relation in retrieved.relations:
        lines.append(
            f"관계 {relation.edge_id}: {relation.source.name} --{relation.type.value}"
            f"({relation.subtype or '-'})--> {relation.target.name} "
            f"(freshness={relation.freshness.value}, score={relation.score}) "
            f"근거: {relation.evidence_id or '없음'}")
    for prop in retrieved.propagation:
        lines.append(
            f"파급: {prop.target} ({prop.hops}홉, stated={prop.stated}, "
            f"경로: {' → '.join(prop.path)})")
    return "\n".join(lines) if lines else "(찾은 사실 없음)"
```
다음으로 교체:
```python
def _fact_lines(retrieved: RetrieveResponse) -> str:
    lines: list[str] = []
    if retrieved.companies:
        lines.append("기업: " + ", ".join(f"{c.name}({c.key})" for c in retrieved.companies))
    for event in retrieved.events:
        risk = "위험사건" if event.is_risk else "일반"
        lines.append(f"사건 {event.event_id}: {event.name} ({event.event_type}, "
                     f"{event.occurred_at}, {risk}) 근거: {', '.join(event.evidence_ids) or '없음'}")
    for relation in retrieved.relations:
        lines.append(
            f"관계 {relation.edge_id}: {relation.source.name} --{relation.type.value}"
            f"({relation.subtype or '-'})--> {relation.target.name} "
            f"(freshness={relation.freshness.value}, score={relation.score}) "
            f"근거: {relation.evidence_id or '없음'}")
    for prop in retrieved.propagation:
        lines.append(
            f"파급: {prop.target} ({prop.hops}홉, stated={prop.stated}, "
            f"경로: {' → '.join(prop.path)})")
    body = "\n".join(lines) if lines else "(찾은 사실 없음)"
    return f"{_match_type_note(retrieved.match_type)}\n{body}"
```

- [ ] **Step 4: 통과 확인**

Run: `.venv-wsl/bin/python -m pytest tests/services/test_answer_service.py tests/services/test_retrieve_service.py tests/services/test_retrieve_api.py -q`
Expected: PASS — 전부 통과(Tier B 실호출 테스트는 `OPENAI_API_KEY` 없으면 skip).

- [ ] **Step 5: 커밋**

```bash
git add app/services/answer_service.py tests/services/test_answer_service.py
git commit -m "feat: hedge answer wording when RetrieveResponse.match_type is SEMANTIC"
```

---

### Task 3: 기술부채 문서 갱신 — ① 상태 `[TODO]` → `[DONE]`

**Files:**
- Modify: `docs/BizNode_기술부채_설계검토.md`

**Interfaces:** 없음(문서 전용 작업).

- [ ] **Step 1: 상태 갱신**

`docs/BizNode_기술부채_설계검토.md`의 §0 표(순서 ① 행)와 §4 표(① 행) 두 곳의 상태 열을 `[TODO]`(결정 완료) → `[DONE]`으로 바꾸고, 마지막 작성 날짜·기준 커밋을 갱신한다(Task 1·2 커밋 SHA 확정 후).

- [ ] **Step 2: 커밋**

```bash
git add docs/BizNode_기술부채_설계검토.md
git commit -m "docs: mark SEMANTIC match_type weighting (item ①) as done"
```

---

## Self-Review

**Spec coverage:** 기술부채 문서 §4 ①의 두 단계 — (1) `SearchQuery.mode`를 `RetrieveResponse`까지 흘려보내기 → Task 1. (2) `AnswerService._fact_lines`/시스템 프롬프트가 `match_type`을 보고 다르게 말하게 하기 → Task 2. 문서 상태 갱신 → Task 3. 3개 태스크로 spec 전체를 덮는다. §0의 ②(답변 품질 최소 검증)·③(운영 이미지 검증)·④(문서 정합성 정리 전체)는 이 계획의 범위 밖 — 설계 문서 자체가 ①이 끝난 뒤 순서대로 하라고 명시했다.

**Placeholder scan:** 없음 — 모든 코드 블록이 실제 diff이고, "TODO"·"add validation" 류 표현 없음.

**Type consistency:** `MatchType`(Task 1에서 정의) → `RetrieveResponse.match_type`(Task 1) → `AnswerService._match_type_note(match_type: MatchType)`(Task 2) 전부 같은 타입·이름을 씀. `_retrieved(*, evidence=(), relations=(), match_type=MatchType.EXACT)`(Task 1에서 추가) 시그니처를 Task 2가 그대로 재사용.
