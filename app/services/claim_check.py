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
화이트리스트(`app/llm/prompt.sources_from()`)는 **인용된 id 만** 검사하므로
인용하지 않은 주장은 원리적으로 못 잡는다. 그 구멍이 여기서 보인다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from app.services.evidence_selector import UNCLASSIFIED_EVENT_TYPES
from pipeline.token_overlap import normalize_dates, overlap, sentence_tokens

# ★`evidence_selector.Embed` 와 같은 모양이다 — 임베딩 구현에 묶이지 않는다.
Embed = Callable[[list[str]], Sequence[Sequence[float]]]

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
    # ★주장이 이름을 부른 워크스페이스 기업 중, **든 근거 어디에도 그 이름이 없는**
    #   것들. 오귀속 의심 신호다 — `missing` 토큰 안에 이미 들어 있지만 거기서는
    #   다른 낱말들에 섞여 보이지 않는다.
    misattributed: list[str] = field(default_factory=list)
    # ★본문에는 없고 **기사 제목 suffix 에만** 있는 것들. 오귀속일 수도, 제목이
    #   주어를 정당하게 밝힌 것일 수도 있어 **갈라만 둔다**.
    title_only: list[str] = field(default_factory=list)
    # ★주장 ↔ 든 근거의 **의미** 유사도. 낱말 겹침이 못 보는 의역·동의어를 받는다.
    #   임베딩이 없거나 죽으면 `None` — 「못 쟀다」와 「0.0」은 다르다.
    semantic: Optional[float] = None
    # ★주장이 든 근거 ↔ **질문 의도**의 유사도. 「이 근거가 질문과 상관이 있나」다.
    #   ★**판정에 쓰지 않는다** — 실측에서 순서가 뒤집혔다(`summarize` 주석).
    intent_link: Optional[float] = None
    # ★질문 의도와 **연결되는가** — `None` 은 「판정할 근거가 없다」(질문이
    #   event_type 을 하나도 지목하지 않았거나, 든 근거의 출처를 모른다)이고
    #   `False` 가 「연결 없음」이다. 셋을 섞으면 판정 불가가 차단으로 샌다.
    intent_linked: Optional[bool] = None


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


# ★기사 제목 suffix — `pipeline/importer/news_loader.py:283` 이 **적재 시점에**
#   붙인다: `f"{text}\n\n— 「{article_title}」 {article_url}"`. 뉴스 근거 전건이
#   대상이고 DART 경로에는 안 붙는다.
_TITLE_SUFFIX = "\n\n— 「"


def _body_of(text: str) -> str:
    """기사 제목 suffix 를 뺀 **추출된 본문**. suffix 가 없으면 그대로."""
    return text.split(_TITLE_SUFFIX)[0]


def _attribution(text: str, texts: Sequence[str],
                 workspace_names: Sequence[str]) -> tuple[list[str], list[str]]:
    """(오귀속 의심, 제목에만 있음) — 주장이 부른 워크스페이스 기업을 두 갈래로.

    ★겨냥하는 실패 — 근거가 실제로 어느 기업을 말하는지 확인하지 않고 **워크스페이스
      기업 중 하나로 귀속시키는 것**이다. 화이트리스트는 「id 가 재료 안에 있나」만
      보므로 귀속이 틀려도 통과하고, 낱말 겹침은 오히려 **반대로** 작동한다 —
      근거에 그 이름이 (다른 맥락으로라도) 있으면 점수가 올라간다.

    ★**둘로 나누는 이유 — 기사 제목 suffix 가 탐지를 막는다.** 실측
      (`ev_4fa6a58a5c293758`):

          본문   「대부분의 투자는 M15x 공장의 HBM4 규격 … 생산 확대에 쓰일 것으로
                  전망된다」            ← SK하이닉스가 **없다**
          제목   「… 삼성전자 SK하이닉스 HBM에 투자 올인」   ← 여기 **있다**

      한 덩어리로 보면 「원문에 있다」가 되어 오귀속이 그대로 통과한다. 그런데
      제목만으로 주어를 확정할 수도 없다 — 이 기사는 두 회사를 함께 다룬다.
      그래서 **판정하지 않고 갈라 센다.** 이 분포가 「제목 suffix 를 계속 실을까」
      `[DECIDE]`(현황서 §7-0)에 필요한 데이터이기도 하다.

    ★**여기서도 판정하지 않는다.** 이름이 없다고 곧 거짓이 아니다 — 별칭·약어·
      대명사로 가리켰을 수 있다. 세는 것은 「의심」이지 「오류」가 아니다.

    ★`about` 표기(`app/llm/prompt.evidence_about()`)가 **예방**이고 이것이 **관측**이다.
      둘은 서로를 대신하지 않는다.
    """
    bodies = [_body_of(t) for t in texts]
    missing_all: list[str] = []
    title_only: list[str] = []
    for name in workspace_names:
        if not name or name not in text:
            continue
        if any(name in body for body in bodies):
            continue                                  # 본문이 말한다 — 정상
        (title_only if any(name in t for t in texts) else missing_all).append(name)
    return missing_all, title_only


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """★`evidence_selector._cosine` 과 같은 식이다. 거기 것을 끌어다 쓰지 않는 이유는
    이 모듈이 **순수 함수 묶음**이라 다른 서비스에 의존하지 않기 때문이다."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _semantic_scores(pairs: Sequence[tuple[str, str]], embed: Optional[Embed],
                     ) -> list[Optional[float]]:
    """(왼쪽, 오른쪽) 쌍마다 코사인. **실패하면 전부 `None`.**

    ★한 번에 임베딩한다 — 쌍마다 부르면 왕복이 주장 수만큼 는다
      (`evidence_selector.similarities` 와 같은 규약).

    ★**임베딩이 죽어도 /ask 는 살아 있어야 한다.** 예외를 올리지 않고 `None` 을
      준다 — 그러면 낱말 겹침만으로 관측이 이어진다.
    """
    usable = [i for i, (left, right) in enumerate(pairs)
              if left.strip() and right.strip()]
    if embed is None or not usable:
        return [None] * len(pairs)
    texts = [t for i in usable for t in pairs[i]]
    try:
        vectors = embed(texts)
    except Exception:  # noqa: BLE001 — 관측이 답변을 막지 않는다
        return [None] * len(pairs)
    if len(vectors) != len(texts):
        return [None] * len(pairs)
    out: list[Optional[float]] = [None] * len(pairs)
    for slot, i in enumerate(usable):
        out[i] = round(_cosine(vectors[slot * 2], vectors[slot * 2 + 1]), 3)
    return out


def _intent_linked(evidence_ids: Sequence[str],
                   event_types_by_evidence: Mapping[str, frozenset[str]],
                   matched: frozenset[str]) -> Optional[bool]:
    """든 근거가 **질문이 지목한 사건 종류**에서 왔는가. 못 정하면 `None`.

    ★**왜 임베딩이 아니라 규칙 티어인가** — 실측(2026-08-26 · claim 23건)에서
      「질문 의도 ↔ 근거 원문」 임베딩(`intent_link`)은 분포가 0.167~0.356 으로
      좁고 **순서가 뒤집혀** 있었다. 가장 좋은 claim 이 0.17(최하위권)이고 질문과
      무관한 claim 이 0.29(상위권)였다. `evidence_selector` 가 이미 **실험 ①의
      실패**로 기록해 둔 것을 그대로 재현한 것이다.

      같은 모듈이 **실험 ③에서 성공한 방식**은 짧은 것끼리 비교하는 것이었다 —
      의도 ↔ **사건 라벨**. 그 산물이 `matched_event_types(intent)` 이고, 이미
      `retrieve_service` 가 사건을 고를 때 쓴다. 여기서는 그것을 **주장 단위로
      되짚기만** 한다.

    ★`None` 을 `False` 와 섞지 않는다.

          matched 가 비었다        질문이 사건 종류를 안 지목했다 → 판정 불가
          출처를 모르는 근거뿐      관계·히트에서 온 근거다 → 판정 불가
          분류 못 한 사건뿐         event_type 이 「기타」다 → 판정 불가
          출처 사건이 있다          그 종류가 matched 에 있나 → True / False

      「판정 불가」를 「연결 없음」으로 떨어뜨리면 **관계 질의가 통째로 차단된다**
      (「삼성전자에 납품하는 기업」은 사건이 아니라 관계가 답이다).
    """
    if not matched:
        return None
    types: set[str] = set()
    for evidence_id in evidence_ids:
        types |= event_types_by_evidence.get(evidence_id, frozenset())
    # ★분류 못 한 사건은 **출처를 모르는 것과 같다.** 규칙 티어가 「기타」를
    #   지목할 수 없으므로(`UNCLASSIFIED_EVENT_TYPES`) 그대로 두면 `matched` 와
    #   절대 안 겹쳐 **구조적으로 늘 `False`** 다 — 근거가 질문과 무관해서가
    #   아니라 종류를 몰라서 그렇다. 실측(2026-08-29 · 평가셋): 「연결 없음」
    #   으로 떨어진 것 중 솔리다임 지분 조사 사건(`기타`)이 매 실행 들어 있었다
    #   — `35ac6a5` 6건 중 2건 · `6d672d1` 5건 중 1건.
    types -= UNCLASSIFIED_EVENT_TYPES
    if not types:
        return None
    return bool(types & matched)


def check(claims: Sequence[Mapping[str, Any]],
          evidence_by_id: Mapping[str, Any],
          *, propagation_targets: Sequence[str] = (),
          workspace_names: Sequence[str] = (),
          embed: Optional[Embed] = None,
          intent: str = "",
          event_types_by_evidence: Mapping[str, frozenset[str]] = MappingProxyType({}),
          matched_event_types: frozenset[str] = frozenset()) -> list[ClaimCheck]:
    """주장마다 (상태, 겹침 점수, 없는 토큰, 유형). **통과/불통과를 내지 않는다.**

    한 주장이 근거를 여럿 들면 **합쳐서** 본다 — 나눠 재면 각 근거가 주장의
    일부만 담고 있을 때 둘 다 낮게 나온다.

    `propagation_targets` 는 `RetrieveResponse.propagation` 의 대상 기업 이름들이다.
    인과 주장이 그중 하나를 가리키면 claim ⑤ 이고, 아니면 ⑥ 자유 결합이다.

    `workspace_names` 는 워크스페이스 기업 이름들이다 — 주장이 그중 하나를 부르면서
    든 근거 원문에는 그 이름이 없으면 **오귀속 의심**으로 센다.

    `embed`·`intent` 를 주면 **두 축의 의미 유사도**를 더 잰다. 낱말 겹침이 못 보는
    의역·동의어를 받기 위한 것이고, 둘은 **서로 다른 질문에 답한다.**

        semantic      주장 ↔ 든 근거      「이 근거가 이 주장을 지지하나」
        intent_link   질문 의도 ↔ 든 근거  「이 근거가 질문과 상관이 있나」

    ★임베딩이 없거나 죽으면 둘 다 `None` 이다 — 「못 쟀다」와 「0.0」은 다르다.
    """
    corpora: list[str] = []          # 주장마다 든 근거를 합친 것 — 의미 비교용
    out: list[ClaimCheck] = []
    for claim in claims:
        text = str(claim.get("text") or "")
        evidence_ids = [str(i) for i in (claim.get("evidence_ids") or [])]
        texts = _texts_of(evidence_ids, evidence_by_id) if evidence_ids else []
        # ★유형은 **점수를 못 내는 경우에도** 붙인다 — 인용조차 없는 인과 주장이
        #   가장 위험한데, 거기서 유형을 비우면 그 자리가 다시 안 보이게 된다.
        claim_type, effect_score = _classify(text, texts, propagation_targets)
        linked = _intent_linked(evidence_ids, event_types_by_evidence,
                                matched_event_types)

        if not evidence_ids:
            corpora.append("")
            out.append(ClaimCheck(text, evidence_ids, STATUS_UNCITED,
                                  claim_type=claim_type, effect_score=effect_score,
                                  intent_linked=linked))
            continue
        if not texts:
            corpora.append("")
            out.append(ClaimCheck(text, evidence_ids, STATUS_NO_TEXT,
                                  claim_type=claim_type, effect_score=effect_score,
                                  intent_linked=linked))
            continue

        # ★날짜 정규화는 **양쪽에 똑같이** 건다 — 한쪽만 걸면 맞을 것도 어긋난다.
        #   토큰은 문장용으로 뽑는다(조사·어미 제거). 기본 토크나이저를 쓰면
        #   「삼성전자에」가 「삼성전자」를 담은 근거에서도 없는 토큰이 된다
        #   (Step4a 실측: 그게 낮은 점수의 지배적 원인이었다).
        score, missing = overlap(normalize_dates("\n".join(texts)),
                                 normalize_dates(text),
                                 tokenizer=sentence_tokens)
        misattributed, title_only = _attribution(text, texts, workspace_names)
        corpora.append("\n".join(texts))
        out.append(ClaimCheck(text, evidence_ids, STATUS_SCORED,
                              score=score, missing=missing,
                              claim_type=claim_type, effect_score=effect_score,
                              misattributed=misattributed, title_only=title_only,
                              intent_linked=linked))

    # ★의미 유사도는 **전부 모아 한 번에** 잰다. 두 축을 한 번의 왕복으로 —
    #   주장 벡터와 의도 벡터가 같은 corpus 벡터를 상대한다.
    semantics = _semantic_scores([(c.text, corpus)
                                  for c, corpus in zip(out, corpora)], embed)
    links = _semantic_scores([(intent, corpus) for corpus in corpora], embed)
    for check_result, semantic, link in zip(out, semantics, links):
        check_result.semantic = semantic
        check_result.intent_link = link
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
        # ★오귀속 의심 — 주장이 부른 워크스페이스 기업이 든 근거 **본문**에 없다.
        #   `title_only` 는 제목 suffix 에만 있는 것이라 갈라 센다(§7-0 `[DECIDE]`).
        "misattributed": sum(1 for c in checked if c.misattributed),
        "title_only": sum(1 for c in checked if c.title_only),
        # ★의미 유사도 — **관측만 한다.**
        #
        #   `intent_link` 로는 **차단하지 못한다**(실측 2026-08-26 · claim 23건).
        #   분포가 0.167~0.356 으로 좁고 **순서가 뒤집혀 있다** — 가장 좋은 claim
        #   (「D램 감산을 발표하며」 · overlap 0.86)이 0.17 로 최하위권이고 질문과
        #   무관한 claim(「심텍 제품 품질 내부고발」 / 질문은 「생산 차질 위험」)이
        #   0.29 로 상위권이었다. `evidence_selector` 가 이미 실패로 기록한
        #   **실험 ①**(근거 원문을 질문과 직접 임베딩 비교)을 재현한 것이다.
        #   연결성 판정은 `intent_linked`(사건 라벨 규칙 티어)가 맡는다.
        "semantic_mean": _mean(c.semantic for c in checked),
        "intent_link_mean": _mean(c.intent_link for c in checked),
        # ★연결성 — `None`(판정 불가)을 따로 센다. 「연결 없음」과 섞으면 관계
        #   질의(「삼성전자에 납품하는 기업」)가 통째로 차단된 것처럼 보인다.
        "unlinked": sum(1 for c in checked if c.intent_linked is False),
        "link_unknown": sum(1 for c in checked if c.intent_linked is None),
    }


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    got = [v for v in values if v is not None]
    return round(sum(got) / len(got), 3) if got else None


def unlinked(checked: Sequence[ClaimCheck]) -> list[ClaimCheck]:
    """질문 의도와 **연결이 없다고 판정된** 주장들. `None`(판정 불가)은 뺀다.

    ★이 모듈에서 **유일하게 판정에 가까운 함수**다. 그래서 이름을 `unlinked` 로
      두고 `supported`/`verdict` 같은 말을 쓰지 않았다 — 재는 것은 「질문이 지목한
      사건 종류에서 온 근거인가」뿐이지 참·거짓이 아니다.

    ★**지우는 것은 여기서 하지 않는다.** 호출측(`app/graph/nodes/answer.py`)이 정한다.
    """
    return [c for c in checked if c.intent_linked is False]
