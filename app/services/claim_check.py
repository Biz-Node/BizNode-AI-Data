"""답변의 주장 하나하나가 **자기가 든 근거 안의 낱말을 쓰고 있는가.**

★이 모듈은 검증기가 아니다. **의심 탐지기(cheap suspicion detector)** 다.

  점수가 낮다고 「거짓」이 아니고 높다고 「참」이 아니다. 재는 것은 오직
  「주장에 쓴 낱말이 인용한 근거 안에 실제로 있는가」뿐이다 —
  `pipeline/token_overlap.py` 가 하는 일이 그게 전부다. 의역·동의어·한국어
  조사에 그대로 걸린다. 실측(2026-08-23): 「SK하이닉스**의**」가 「SK하이닉스」를
  담은 근거에서 *없는 토큰*으로 잡혔다.

  그래서 **여기서는 판정하지 않는다.** `supported`/`verdict` 같은 필드를 두지
  않았고, 임계값도 없다. Step4a 의 일은 분포를 모으는 것뿐이다.

★`batch/audit/grounding.py` 의 `_GROUND_THRESHOLD = 0.34` 를 그대로 쓰지 않는다.

  그 값은 노드 **이름**(토큰 2~3개)을 근거와 대조하려고 잡은 것이고, 여기 대상은
  답변 **문장**(토큰 10개 이상)이다. 모수가 다르면 같은 비율이 같은 뜻이 아니다.
  실측으로 분포를 본 다음에 정한다.

무엇을 잡으려는가 — Step3 실측에서 **실제로 나온** 실패:

    오인용  질소 누출 답변에 HBM3E 양산 근거를 달았다      겹침 0.00
    무인용  인용 0건인데 실질 주장을 여럿 했다             evidence_ids 가 비어 있다

앞의 것은 점수로 보이고, 뒤의 것은 점수 없이 `uncited` 로 바로 드러난다 —
화이트리스트(`answer_service._sources_from`)는 **인용된 id 만** 검사하므로
인용하지 않은 주장은 원리적으로 못 잡는다. 그 구멍이 여기서 보인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from pipeline.token_overlap import normalize_dates, overlap, sentence_tokens

# 상태 셋 — **판정이 아니라 「점수를 낼 수 있었는가」의 구분**이다.
STATUS_UNCITED = "uncited"    # 주장이 근거를 하나도 안 들었다
STATUS_NO_TEXT = "no_text"    # 든 근거의 원문을 못 찾았다(재료 밖이거나 missing)
STATUS_SCORED = "scored"      # 겹침을 쟀다

# ── claim 유형 — 설계서 §13-1 의 5종에 **없던 자리** (2026-08-26) ─────────
#
#   ⑤ Insight/파급 의 검증 원천은 `propagation[]`(target·path·stated)다. 그런데
#   실측 사례의 「이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다」는
#   `propagation[]` 근거 없이 LLM 이 두 사실을 **자유 결합**한 것이라 ⑤ 에도
#   안 들어간다 — 4등급 ④(Insight)에는 속하는데 claim 5종 어디에도 안 걸린다.
#   **검증 원천이 없는 게 아니라 분류가 안 되던 자리**다.
#
# ★**관측·분류만 한다.** strip 여부는 발생률·오탐률을 잰 뒤에 정한다.
# ★**LLM judge 를 쓰지 않는다**(설계서 §13-4) — 어휘 대조와 집합 확인뿐이다.
TYPE_PROPAGATION = "propagation"            # ⑤ — `propagation[]` 이 뒷받침한다
TYPE_FREE_COMBINATION = "free_combination"  # ⑥ — 뒷받침이 없는 자유 결합

# 인과·영향·목적을 주장하는 표현. ★근거 원문에도 흔한 말이라 **이것만으로는
# 아무것도 판정하지 않는다** — 효과 절이 근거에 있는지까지 봐야 한다.
_CAUSAL_MARKERS: tuple[str, ...] = (
    "로 인해", "으로 인해", "때문에", "탓에", "결과로", "영향을 미", "영향으로",
    "여파로", "초래", "야기", "때문", "덕분에", "따라 ", "를 위해", "을 위해",
)


@dataclass
class ClaimCheck:
    text: str
    evidence_ids: list[str]
    status: str
    score: Optional[float] = None
    missing: list[str] = field(default_factory=list)
    # ★`None` 은 「인과를 주장하지 않았다」는 뜻이다 — 대다수가 여기다.
    claim_type: Optional[str] = None
    effect_score: Optional[float] = None   # 효과 절이 근거와 얼마나 겹치나


def _effect_clause(text: str) -> str:
    """인과 표현 **뒤쪽**(= 결과 절). 없으면 빈 문자열.

    ★원인 절은 대개 근거에 있다 — 사례에서도 「질소 누출 사고」는 근거에 실재했다.
      지어낸 것은 **결과 쪽**(「생산에 영향」)이므로 거기만 본다.

    ★**맨 처음** 인과 표현에서 자른다. 마지막에서 자르면 결과 절 한가운데가
      끊긴다 — 「이 사고로 인해 **생산에 영향을 미**쳤을…」에서 「영향을 미」도
      인과 표현이라, 뒤에서 자르면 「쳤을 가능성이 있습니다」만 남아 정작 지어낸
      낱말(「생산」)이 검사 대상에서 빠진다.
    """
    cut = -1
    for marker in _CAUSAL_MARKERS:
        found = text.find(marker)
        if found >= 0 and (cut < 0 or found + len(marker) < cut):
            cut = found + len(marker)
    return text[cut:].strip() if cut >= 0 else ""


def _classify(text: str, texts: Sequence[str],
              propagation_targets: Sequence[str]) -> tuple[Optional[str], Optional[float]]:
    """(유형, 효과 절 겹침). 인과를 주장하지 않았으면 `(None, None)`.

    ★`propagation[]` 이 뒷받침하면 ⑤ 다 — 자유 결합이 아니라 **우리가 계산한
      것**이고 검증 원천이 있다(설계서 §13-2).
    """
    effect = _effect_clause(text)
    if not effect:
        return None, None
    if any(target and target in text for target in propagation_targets):
        return TYPE_PROPAGATION, None
    if not texts:
        # 인용조차 없는 인과 주장 — 화이트리스트가 원리적으로 못 잡는 자리다.
        return TYPE_FREE_COMBINATION, None
    # ★내용어가 하나도 없는 결과 절은 **판정하지 않는다.** 조사·어미만 남으면
    #   겹침이 1.0 으로 나오는데 그건 「근거에 있다」가 아니라 「잴 것이 없다」다.
    if not sentence_tokens(normalize_dates(effect)):
        return None, None
    score, _ = overlap(normalize_dates("\n".join(texts)), normalize_dates(effect),
                       tokenizer=sentence_tokens)
    # 근거가 그 결과를 실제로 말하고 있으면 ② 관측된 인과다(설계서 §12).
    if score >= _EFFECT_GROUNDED:
        return None, score
    return TYPE_FREE_COMBINATION, score


# ★[미확정/저신뢰] — 「결과 절이 근거에 있다」로 볼 최소 겹침. 실측 근거가 아직
#   없어 **관측용 잠정치**다. 이 값으로 무엇도 지우지 않으므로 지금은 무해하고,
#   분포가 모이면 정한다(설계서 §13-4 · 현황서 ⑥ 단계).
_EFFECT_GROUNDED = 0.5


def _texts_of(evidence_ids: Sequence[str],
              evidence_by_id: Mapping[str, Any]) -> list[str]:
    """든 근거들의 대조 corpus. 못 찾거나 missing 인 것은 뺀다.

    ★원문뿐 아니라 `published_at` 도 넣는다 — **모델에게 보여 준 것이 그것**이다.

      프롬프트는 `<evidence id="…" source_type="news" published_at="2026-06-12">`
      로 날짜를 함께 준다. 그런데 채점이 본문만 보던 탓에 「2026년 6월 12일에
      …」라고 쓴 주장이 「없는 근거를 지어냈다」로 잘못 읽혔다. 실측
      (claim 84건, 2026-08-23): 못 맞춘 토큰 123개 중 26개(21.1%)가 날짜였고
      단일 최대 원인이었다.

      corpus 는 **보여 준 것과 같아야 한다.** 보여 준 적 없는 날짜는 여전히
      안 맞는다 — 그건 회귀 테스트가 지킨다.
    """
    out = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or getattr(evidence, "missing", False):
            continue
        text = getattr(evidence, "text", "") or ""
        if not text.strip():
            continue
        published_at = getattr(evidence, "published_at", None)
        out.append(f"{text}\n{published_at}" if published_at else text)
    return out


def check(claims: Sequence[Mapping[str, Any]],
          evidence_by_id: Mapping[str, Any],
          *, propagation_targets: Sequence[str] = ()) -> list[ClaimCheck]:
    """주장마다 (상태, 겹침 점수, 없는 토큰, 유형). **통과/불통과를 내지 않는다.**

    한 주장이 근거를 여럿 들면 **합쳐서** 본다 — 나눠 재면 각 근거가 주장의
    일부만 담고 있을 때 둘 다 낮게 나온다.

    `propagation_targets` 는 `RetrieveResponse.propagation` 의 대상 기업 이름들이다.
    인과 주장이 그중 하나를 가리키면 claim ⑤ 이고, 아니면 ⑥ 자유 결합이다.
    """
    out: list[ClaimCheck] = []
    for claim in claims:
        text = str(claim.get("text") or "")
        evidence_ids = [str(i) for i in (claim.get("evidence_ids") or [])]
        texts = _texts_of(evidence_ids, evidence_by_id) if evidence_ids else []
        # ★유형은 **점수를 못 내는 경우에도** 붙인다 — 인용조차 없는 인과 주장이
        #   가장 위험한데, 거기서 유형을 비우면 그 자리가 다시 안 보이게 된다.
        claim_type, effect_score = _classify(text, texts, propagation_targets)

        if not evidence_ids:
            out.append(ClaimCheck(text, evidence_ids, STATUS_UNCITED,
                                  claim_type=claim_type, effect_score=effect_score))
            continue
        if not texts:
            out.append(ClaimCheck(text, evidence_ids, STATUS_NO_TEXT,
                                  claim_type=claim_type, effect_score=effect_score))
            continue

        # ★날짜 정규화는 **양쪽에 똑같이** 건다 — 한쪽만 걸면 맞을 것도 어긋난다.
        #   토큰은 문장용으로 뽑는다(조사·어미 제거). 기본 토크나이저를 쓰면
        #   「삼성전자에」가 「삼성전자」를 담은 근거에서도 없는 토큰이 된다
        #   (Step4a 실측: 그게 낮은 점수의 지배적 원인이었다).
        score, missing = overlap(normalize_dates("\n".join(texts)),
                                 normalize_dates(text),
                                 tokenizer=sentence_tokens)
        out.append(ClaimCheck(text, evidence_ids, STATUS_SCORED,
                              score=score, missing=missing,
                              claim_type=claim_type, effect_score=effect_score))
    return out


def summarize(checked: Sequence[ClaimCheck]) -> dict:
    """로그 한 줄로 남길 분포 요약. 20개 질문을 모으는 도구이기도 하다."""
    scores = [c.score for c in checked if c.score is not None]
    return {
        "claims": len(checked),
        "uncited": sum(1 for c in checked if c.status == STATUS_UNCITED),
        "no_text": sum(1 for c in checked if c.status == STATUS_NO_TEXT),
        "scored": len(scores),
        "min": round(min(scores), 3) if scores else None,
        "mean": round(sum(scores) / len(scores), 3) if scores else None,
        "max": round(max(scores), 3) if scores else None,
        # ★유형별 분포 — strip 여부를 정하려면 **발생률이 먼저**다.
        "propagation": sum(1 for c in checked
                           if c.claim_type == TYPE_PROPAGATION),
        "free_combination": sum(1 for c in checked
                                if c.claim_type == TYPE_FREE_COMBINATION),
    }
