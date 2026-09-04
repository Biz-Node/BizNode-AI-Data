"""답변을 쓰는 노드 여섯.

★프롬프트와 스키마는 **`app/llm/` 이 갖는다.** 이 파일은 노드 배선과 상태
  전이만 담는다 — 문구가 두 곳에 생기면 갈린다.

★LLM 호출은 어댑터를 지난다. `LLMAdapter.structured()` 는 예외를 던지지 않고
  `fallback | {"failed": True}` 를 돌려주므로 아래 `verify_sources` 는
  `failed` 만 본다.
"""

from __future__ import annotations

from typing import Optional

from app.api.schemas import AnchorSource, AskResponse
from app.core import observe
from app.core.trace import trace_logger
from app.graph.state import AskState
from app.llm.adapter import LLMAdapter, LangChainAdapter
from app.llm.prompt import SAFE_MESSAGE, STRIP_UNLINKED_CLAIMS, SYSTEM_PROMPT
from app.llm.schemas import SAFE_FALLBACK, AskAnswer
from app.services import claim_check, evidence_selector
from app.graph import prompt
from app.services.query_understanding import AnchorDecision
from app.services.retrieve_service import _default_embed

log = trace_logger(__name__)

# ★모듈 전역으로 노출한다 — **테스트가 patch 할 이음매**다.
_llm: LLMAdapter = LangChainAdapter()


def bind_llm(adapter: LLMAdapter) -> None:
    """어댑터를 갈아끼운다. 테스트·운영 양쪽이 같은 이음매를 쓴다."""
    global _llm
    _llm = adapter


# ══════════════════════════════════════════════════════════════════
#  ⑨ build_prompt
# ══════════════════════════════════════════════════════════════════


def build_prompt(state: AskState) -> AskState:
    """사용자 프롬프트를 조립한다. **DTO 를 읽는 `app/graph/prompt.py` 가 쓴다.**

    """
    decision = state["decision"]
    user = prompt.build_user_prompt(
        state["request"].question,
        match_type=state["match_type"], companies=state["companies"],
        events=state["events"], relations=state["relations"],
        propagation=state["propagation"], evidence=state["evidence"],
        anchor_source=decision.source, workspace_names=decision.workspace_names,
        context_names=decision.context_names)

    # ★프롬프트는 **길이만** 남긴다 — 본문에 시스템 지시문과 근거 원문이 통째로
    #   들어 있어, 그대로 찍으면 로그가 근거 사본이 된다(설계서 §13-2).
    log.info("llm.request match_type=%s companies=%d relations=%d evidence=%d "
             "prompt_chars=%d",
             state["match_type"].value, len(state["companies"]),
             len(state["relations"]), len(state["evidence"]), len(user))
    return {"user_prompt": user}


# ══════════════════════════════════════════════════════════════════
#  ⑩ generate
# ══════════════════════════════════════════════════════════════════


def generate(state: AskState) -> AskState:
    """LLM 을 부른다. ★**예외가 올라오지 않는다** — 어댑터가 표시를 붙여 준다."""
    return {"llm_result": _llm.structured(
        SYSTEM_PROMPT, state["user_prompt"],
        schema=AskAnswer, name="ask_answer", fallback=SAFE_FALLBACK)}


# ══════════════════════════════════════════════════════════════════
#  ⑪ verify_sources — 화이트리스트 검증 (설계서 §13-2)
# ══════════════════════════════════════════════════════════════════


def verify_sources(state: AskState) -> AskState:
    """LLM 이 든 근거를 **재료 안에서만** 인정한다.

    ★근거 원문(뉴스·공시)은 신뢰 안 된 텍스트라 인젝션이 섞일 수 있다. 구조적
      방어(델리미터 + 시스템 프롬프트)만 걸고, 이 화이트리스트 검증을 실질적
      2차 방어선으로 삼는다.
    """
    result = state["llm_result"]
    evidence, relations = state["evidence"], state["relations"]
    answer = result.get("answer", "")
    cited = result.get("evidence_ids", [])
    # 빈 답변도 실패로 취급한다(설계서 §13-5). 실패면 필터링 근거가 없으니
    # missing 만 뺀 원본 전부를 돌려준다.
    failed = bool(result.get("failed")) or not answer.strip()
    sources = (prompt.fallback_sources(evidence, relations) if failed
               else prompt.sources_from(cited, evidence, relations))
    accepted = [source.evidence_id for source in sources]

    # ★**최종 인용 관계의 링** — 도구가 본 분포와 다를 수 있고, 그 차이가
    #   「링 순서가 답변까지 살아갔나」다. 근거 id 를 관계로 되짚어 링을 센다.
    #   ★사건·뉴스 근거에는 링이 없다 — Ring 0 으로 뭉뚱그리지 않는다.
    by_evidence = {r.evidence_id: r.edge_id for r in relations if r.evidence_id}
    accepted_set = set(accepted)
    cited_edges = [by_evidence[eid] for eid in accepted_set if eid in by_evidence]
    # ★관계로 못 되짚은 것은 **개수로** 넘긴다. 배열 근거까지 훑는 2차 조회를
    #   한 번 넣었다가 뺐다(Phase 13 → 15) — `RelationDTO.evidence_id` 가
    #   「배열은 이 관계 하나의 출처가 아니다」로 못 박은 계약을 관측만 우회하게
    #   되고, `Source.edge_id`(= `_edge_id_for`, 단수)와도 갈렸다.
    observe.record_cited_relations(
        cited_edges, without_ring=len(accepted_set) - len(cited_edges))

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


def check_state_claims(state: AskState, claims: Optional[list] = None) -> list:
    """State 하나로 `claim_check.check()` 를 부른다 — **인자 조립을 한 곳에.**

    ★`batch/audit/claim_grounding.py` 가 같은 인자로 불러야 분포가 운영과 같은
      것을 잰다. 조립이 두 벌이면 배치가 **다른 것을 재면서 같다고 보고**한다.
    """
    decision, intent = state["decision"], state["intent"]
    return claim_check.check(
        claims if claims is not None else (state["llm_result"].get("claims") or []),
        {e.evidence_id: e for e in state["evidence"]},
        # ★파급 대상을 넘겨야 claim ⑤(우리가 계산한 파급)와 ⑥(자유 결합)이
        #   갈린다 — 안 넘기면 정상적인 파급 문장이 ⑥ 으로 잘못 세어진다.
        propagation_targets=[p.target for p in state["propagation"]],
        # ★오귀속 관측 — 「근거가 어느 기업 얘기인지 확인하지 않고 워크스페이스
        #   기업 중 하나로 귀속시키는」 실패를 센다.
        workspace_names=list(decision.workspace_names.values()),
        embed=_default_embed, intent=intent,
        event_types_by_evidence=prompt.event_types_by_evidence(state["events"]),
        matched_event_types=evidence_selector.matched_event_types(intent))


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
        # ★**빈 것도 기록한다.** 「주장이 0건이었다」와 「이 노드를 안 지났다」가
        #   같은 값으로 보이면 uncited **비율의 분모**를 만들 수 없다.
        # ★키 다섯을 **다 적는다.** `record_claims` 가 읽는 키가 다섯인데 넷만
        #   적으면 이 dict 가 「넷만 읽는다」로 읽힌다. 지금은 `.get(...) or 0`
        #   라 무해하지만, 기본값을 `.get(key)` 로 바꾸는 날 조용히 어긋난다.
        observe.record_claims({"claims": 0, "uncited": 0, "no_text": 0,
                               "unlinked": 0, "link_unknown": 0})
        return {}

    checked = check_state_claims(state, claims)
    summary = claim_check.summarize(checked)
    # ★로그와 **함께** 담는다. 로그는 운영에서 한 건을 되짚는 통로이고, 버킷은
    #   평가셋이 20 케이스를 모아 「uncited 비율」로 읽는 통로다 — 둘 중 하나만
    #   두면 「평가에서는 보이는데 운영에서는 안 보이는」 값이 생긴다.
    observe.record_claims(summary)
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

    # ★연결성 없는 주장 — **기본은 관측만** 한다(`STRIP_UNLINKED_CLAIMS`).
    cut = claim_check.unlinked(checked)
    # ★**버킷에도 담는다**(2026-08-29). 여태 이 값은 아래 로그 한 줄에만 있어서,
    #   평가셋이 「오탐률」을 재려면 사람이 로그를 긁어야 했다. 플래그를 켤지는
    #   오탐률을 보고 정하는데, 그 통로가 없으면 정할 수가 없다.
    #   ★관측일 뿐이다 — 여기서 아무것도 안 지운다.
    observe.record_claim_links(checked, cut)
    if cut:
        log.info("claim.unlinked count=%d strip=%s texts=%s",
                 len(cut), STRIP_UNLINKED_CLAIMS, [c.text for c in cut])
    # ★strip 은 이 노드에 **배선하지 않았다.** 플래그가 `False` 라 지금은 무동작
    #   이고, 켜려면 이 노드가 `answer` 를 돌려주도록 배선해야 한다 — 그건
    #   「관측만 한다」를 깨는 결정이라 사람이 정할 일이다. 조용히 안 되는 일이
    #   없도록 켜져 있으면 경고를 남긴다.
    if cut and STRIP_UNLINKED_CLAIMS:
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
        answer=SAFE_MESSAGE if failed else state["answer"],
        sources=state["sources"], failed=failed,
        anchor_source=state["decision"].source)}


# ★LLM 을 부르지 않고 내는 문구 둘. **결정론적으로 조립한다**(설계서 §14-4·§16-2) —
#   재료가 없으면 해석할 것도 없다.
#
# ★둘을 가르는 이유는 **사용자가 할 일이 다르기 때문**이다. 하나는 기업을
#   추가해야 하고, 하나는 다른 이름으로 물어야 한다.
# ★`_NO_WORKSPACE_MESSAGE` 는 **지웠다**(이번 개정 · 최종 설계 §17-1).
#   「이 워크스페이스에 담긴 기업이 없어 답변할 수 없습니다」는 워크스페이스를
#   검색 경계로 보던 정책의 사용자 문구였다. 이제 담긴 기업이 없어도 Global
#   Search 로 답하므로 그 상태 자체가 없다.

# ★조사를 변수 뒤에 붙이지 않는다 — 「'TSMC' 를」/「'심텍' 을」처럼 받침에 따라
#   갈리는데, 영문·약어는 한글 발음으로 판정해야 해서 규칙이 커진다. 「~에
#   해당하는」은 받침과 무관하게 성립한다.
_UNRESOLVED_TEMPLATE = "질문하신 '{named}' 에 해당하는 기업을 저희 데이터에서 찾지 못했습니다."
_WORKSPACE_HINT_TEMPLATE = "현재 워크스페이스에 담긴 기업은 다음과 같습니다 — {names}"


def _no_material(message: str) -> AskResponse:
    """재료 없이 내는 응답 — **`failed=false` 다.**

    ★`failed` 는 「LLM 호출이 실패했다」는 뜻이다(설계서 §15-4). 여기서는
      애초에 안 불렀으므로 실패가 아니다. 섞으면 화면이 「서버가 고장났다」와
      「그 기업을 못 찾았다」를 구별하지 못한다.
    """
    return AskResponse(answer=message, sources=[], failed=False,
                       anchor_source=AnchorSource.UNRESOLVED)


def _unresolved_message(decision: AnchorDecision) -> str:
    """★대안은 **「제안」까지만**이다(설계서 §14-4) — 워크스페이스 기업 이름을
    보여줄 뿐, 그 기업들에 대해 답하지 않는다. 답하면 그게 곧 조용한 오답이다."""
    lines = [_UNRESOLVED_TEMPLATE.format(named=decision.named)]
    if decision.workspace_names:
        lines.append(_WORKSPACE_HINT_TEMPLATE.format(
            names=" · ".join(decision.workspace_names.values())))
    return "\n".join(lines)


def halt_no_material(state: AskState) -> AskState:
    """재료 없이 내는 응답 — **`failed=false` 다.**

    ★**들어오는 길이 하나로 줄었다**(이번 개정 · 최종 설계 §17-1). 전에는 둘이
      었다 — 「담아 둔 기업도 보고 있는 기업도 없다」와 「대상을 못 찾았다」.
      앞의 것은 워크스페이스를 검색 경계로 보던 게이트라 사라졌고, 이제 이
      노드에 오는 길은 `resolve_anchor` 의 `UNRESOLVED` 뿐이다.

    ★그래서 `state["decision"]` 은 **언제나 있다.** 게이트가 앵커 판정 앞에
      서 있던 시절에만 없을 수 있었다.
    """
    return {"response": _no_material(_unresolved_message(state["decision"]))}
