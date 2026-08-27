"""답변을 쓰는 노드 여섯 — `AnswerService` 의 조립 함수에 위임한다.

★**프롬프트를 옮기지 않았다.** `_SYSTEM_PROMPT` 는 `answer_service` 에서
  import 한다. 300줄짜리라 옮기면 diff 가 프롬프트 전문으로 덮여, 정작 이번에
  바뀐 것(실행 담당)이 안 보인다. 문구가 두 곳에 생기면 갈릴 위험도 진짜다.

★LLM 호출만 어댑터로 바뀐다. `pipeline.llm.ask_json()` → `LLMAdapter.structured()`
  인데, **실패 규약이 같다** — 예외를 던지지 않고 `fallback | {"failed": True}` 를
  돌려준다. 그래서 아래 `verify_sources` 는 예전 코드 그대로 `failed` 만 본다.
"""

from __future__ import annotations

from app.api.schemas import AskResponse
from app.core.trace import trace_logger
from app.graph.state import AskState
from app.llm.adapter import LLMAdapter, LangChainAdapter
from app.llm.schemas import AskAnswer
from app.services import claim_check, evidence_selector
from app.services.answer_service import (_NO_WORKSPACE_MESSAGE, _SAFE_FALLBACK,
                                         _SAFE_MESSAGE, _STRIP_UNLINKED_CLAIMS,
                                         _SYSTEM_PROMPT, _build_user_prompt,
                                         _event_types_by_evidence,
                                         _fallback_sources, _no_material,
                                         _sources_from, _unresolved_message)
from app.services.retrieve_service import _default_embed

log = trace_logger(__name__)

# ★모듈 전역으로 노출한다 — **테스트가 patch 할 이음매**다. 예전 테스트들이
#   `answer_service.ask_json` 을 patch 하던 자리를 이것이 대신한다.
_llm: LLMAdapter = LangChainAdapter()


def bind_llm(adapter: LLMAdapter) -> None:
    """어댑터를 갈아끼운다. 테스트·운영 양쪽이 같은 이음매를 쓴다."""
    global _llm
    _llm = adapter


# ══════════════════════════════════════════════════════════════════
#  ⑨ build_prompt
# ══════════════════════════════════════════════════════════════════


def build_prompt(state: AskState) -> AskState:
    """사용자 프롬프트를 조립한다. `_build_user_prompt()` 그대로다."""
    retrieved = state["retrieved"]
    user = _build_user_prompt(state["request"].question, retrieved, state["decision"])

    # ★프롬프트는 **길이만** 남긴다 — 본문에 시스템 지시문과 근거 원문이 통째로
    #   들어 있어, 그대로 찍으면 로그가 근거 사본이 된다(설계서 §13-2).
    log.info("llm.request match_type=%s companies=%d relations=%d evidence=%d "
             "prompt_chars=%d",
             retrieved.match_type.value, len(retrieved.companies),
             len(retrieved.relations), len(retrieved.evidence), len(user))
    return {"user_prompt": user}


# ══════════════════════════════════════════════════════════════════
#  ⑩ generate
# ══════════════════════════════════════════════════════════════════


def generate(state: AskState) -> AskState:
    """LLM 을 부른다. ★**예외가 올라오지 않는다** — 어댑터가 표시를 붙여 준다."""
    return {"llm_result": _llm.structured(
        _SYSTEM_PROMPT, state["user_prompt"],
        schema=AskAnswer, name="ask_answer", fallback=_SAFE_FALLBACK)}


# ══════════════════════════════════════════════════════════════════
#  ⑪ verify_sources — 화이트리스트 검증 (설계서 §13-2)
# ══════════════════════════════════════════════════════════════════


def verify_sources(state: AskState) -> AskState:
    """LLM 이 든 근거를 **재료 안에서만** 인정한다.

    ★근거 원문(뉴스·공시)은 신뢰 안 된 텍스트라 인젝션이 섞일 수 있다. 구조적
      방어(델리미터 + 시스템 프롬프트)만 걸고, 이 화이트리스트 검증을 실질적
      2차 방어선으로 삼는다.
    """
    retrieved, result = state["retrieved"], state["llm_result"]
    answer = result.get("answer", "")
    cited = result.get("evidence_ids", [])
    # 빈 답변도 실패로 취급한다(설계서 §13-5). 실패면 필터링 근거가 없으니
    # missing 만 뺀 원본 전부를 돌려준다.
    failed = bool(result.get("failed")) or not answer.strip()
    sources = _fallback_sources(retrieved) if failed else _sources_from(cited, retrieved)
    accepted = [source.evidence_id for source in sources]

    # ★「최종 근거가 어디서 만들어졌는가」에 답하는 줄이다. `dropped` 는 LLM 이
    #   들었지만 화이트리스트가 버린 id — 지어낸 것이거나 원문을 못 찾은 것이다.
    log.info("llm.response failed=%s cited=%s accepted=%s dropped=%s answer_chars=%d",
             failed, cited, accepted,
             [eid for eid in dict.fromkeys(cited) if eid not in set(accepted)],
             len(answer))
    return {"answer": answer, "failed": failed, "sources": sources}


# ══════════════════════════════════════════════════════════════════
#  ⑫ check_claims — Step4a. ★관측만 한다
# ══════════════════════════════════════════════════════════════════


def check_claims(state: AskState) -> AskState:
    """★**State 를 바꾸지 않는다.** 임계값도 판정도 없고 문장을 지우지도 않는다.

    `claim_check` 는 검증기가 아니라 **의심 탐지기**라, 낮은 점수가 곧 거짓이
    아니다(의역·동의어·한국어 조사에 걸린다). 대표 질문으로 분포를 모은 뒤에
    임계값을 정한다.

    ★`intent` 를 **State 에서 읽는다** — 여기가 Phase 1 이 고치는 유일한 자리다.
      예전 `answer_service` 는 `decision.anchors` 만으로 다시 계산했는데,
      `source=query` 면 그건 **최고점 1개**라 `resolved_entities`(복수 후보)로
      재료를 고른 것과 어긋날 수 있었다. 「무엇으로 골랐나」와 「무엇으로
      검사하나」가 이제 같다.
    """
    claims = state["llm_result"].get("claims") or []
    if not claims:
        return {}

    retrieved, decision = state["retrieved"], state["decision"]
    intent = state["intent"]
    checked = claim_check.check(
        claims, {e.evidence_id: e for e in retrieved.evidence},
        # ★파급 대상을 넘겨야 claim ⑤(우리가 계산한 파급)와 ⑥(자유 결합)이
        #   갈린다 — 안 넘기면 정상적인 파급 문장이 ⑥ 으로 잘못 세어진다.
        propagation_targets=[p.target for p in retrieved.propagation],
        # ★오귀속 관측 — 「근거가 어느 기업 얘기인지 확인하지 않고 워크스페이스
        #   기업 중 하나로 귀속시키는」 실패를 센다.
        workspace_names=list(decision.workspace_names.values()),
        embed=_default_embed, intent=intent,
        event_types_by_evidence=_event_types_by_evidence(retrieved),
        matched_event_types=evidence_selector.matched_event_types(intent))
    summary = claim_check.summarize(checked)
    log.info("claim.grounding claims=%d uncited=%d no_text=%d scored=%d "
             "min=%s mean=%s max=%s propagation=%d free_combination=%d "
             "misattributed=%d title_only=%d semantic=%s intent_link=%s "
             "unlinked=%d link_unknown=%d names=%s scores=%s",
             summary["claims"], summary["uncited"], summary["no_text"],
             summary["scored"], summary["min"], summary["mean"],
             summary["max"], summary["propagation"],
             summary["free_combination"], summary["misattributed"],
             summary["title_only"], summary["semantic_mean"],
             summary["intent_link_mean"], summary["unlinked"],
             summary["link_unknown"],
             sorted({n for c in checked
                     for n in (*c.misattributed, *c.title_only)}),
             [c.score for c in checked if c.score is not None])

    # ★연결성 없는 주장 — **기본은 관측만** 한다(`_STRIP_UNLINKED_CLAIMS`).
    cut = claim_check.unlinked(checked)
    if cut:
        log.info("claim.unlinked count=%d strip=%s texts=%s",
                 len(cut), _STRIP_UNLINKED_CLAIMS, [c.text for c in cut])
    # ★strip 은 이 노드에 **배선하지 않았다.** 플래그가 `False` 라 지금은 무동작
    #   이고, 켜려면 이 노드가 `answer` 를 돌려주도록 배선해야 한다 — 그건
    #   「관측만 한다」를 깨는 결정이라 사람이 정할 일이다. 조용히 안 되는 일이
    #   없도록 켜져 있으면 경고를 남긴다.
    if cut and _STRIP_UNLINKED_CLAIMS:
        log.warning("claim.strip_not_wired count=%d — 그래프 경로는 답변을 지우지 "
                    "않는다. 켜려면 check_claims 를 배선해야 한다", len(cut))
    return {}


# ══════════════════════════════════════════════════════════════════
#  respond · halt_no_material — 출구 둘
# ══════════════════════════════════════════════════════════════════


def respond(state: AskState) -> AskState:
    """★`anchor_source` 는 LLM 과 무관한 **서버가 아는 결정론적 값**이라
    실패 경로에도 그대로 실린다(설계서 §14-3)."""
    failed = state["failed"]
    return {"response": AskResponse(
        answer=_SAFE_MESSAGE if failed else state["answer"],
        sources=state["sources"], failed=failed,
        anchor_source=state["decision"].source)}


def halt_no_material(state: AskState) -> AskState:
    """재료 없이 내는 응답 — **`failed=false` 다.**

    ★들어오는 길이 둘이고 **사용자가 할 일이 다르다.** 하나는 기업을 추가해야
      하고, 하나는 다른 이름으로 물어야 한다. 어느 길로 왔는지는 State 로
      판정한다 — 워크스페이스가 비었으면 앵커 판정 자체가 없다.
    """
    if not state["request"].workspace_keys:
        return {"response": _no_material(_NO_WORKSPACE_MESSAGE)}
    return {"response": _no_material(_unresolved_message(state["decision"]))}
