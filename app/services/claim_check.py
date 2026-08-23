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


@dataclass
class ClaimCheck:
    text: str
    evidence_ids: list[str]
    status: str
    score: Optional[float] = None
    missing: list[str] = field(default_factory=list)


def _texts_of(evidence_ids: Sequence[str],
              evidence_by_id: Mapping[str, Any]) -> list[str]:
    """든 근거들의 원문. 못 찾거나 missing 인 것은 뺀다."""
    out = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or getattr(evidence, "missing", False):
            continue
        text = getattr(evidence, "text", "") or ""
        if text.strip():
            out.append(text)
    return out


def check(claims: Sequence[Mapping[str, Any]],
          evidence_by_id: Mapping[str, Any]) -> list[ClaimCheck]:
    """주장마다 (상태, 겹침 점수, 없는 토큰). **통과/불통과를 내지 않는다.**

    한 주장이 근거를 여럿 들면 **합쳐서** 본다 — 나눠 재면 각 근거가 주장의
    일부만 담고 있을 때 둘 다 낮게 나온다.
    """
    out: list[ClaimCheck] = []
    for claim in claims:
        text = str(claim.get("text") or "")
        evidence_ids = [str(i) for i in (claim.get("evidence_ids") or [])]

        if not evidence_ids:
            out.append(ClaimCheck(text, evidence_ids, STATUS_UNCITED))
            continue

        texts = _texts_of(evidence_ids, evidence_by_id)
        if not texts:
            out.append(ClaimCheck(text, evidence_ids, STATUS_NO_TEXT))
            continue

        # ★날짜 정규화는 **양쪽에 똑같이** 건다 — 한쪽만 걸면 맞을 것도 어긋난다.
        #   토큰은 문장용으로 뽑는다(조사·어미 제거). 기본 토크나이저를 쓰면
        #   「삼성전자에」가 「삼성전자」를 담은 근거에서도 없는 토큰이 된다
        #   (Step4a 실측: 그게 낮은 점수의 지배적 원인이었다).
        score, missing = overlap(normalize_dates("\n".join(texts)),
                                 normalize_dates(text),
                                 tokenizer=sentence_tokens)
        out.append(ClaimCheck(text, evidence_ids, STATUS_SCORED,
                              score=score, missing=missing))
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
    }
