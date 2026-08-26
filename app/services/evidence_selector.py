"""질문 의도로 사건을 골라낸다 — **근거를 줄이는 것이 목적이다.**

★왜 필요한가 (실측 2026-08-23)

  Step1 에서 기업별 evidence scope 를 분리했지만, 그 뒤로도 질문이 무엇이든
  같은 재료가 나갔다.

      「SK하이닉스」                        사건 69 · 근거 82 · 13,916자
      「SK하이닉스 노조 관련 리스크 알려줘」  사건 69 · 근거 82 · 13,916자  ← 동일
      「삼성전자와 SK하이닉스의 담합 소송」    사건 155 · 근거 205 · 34,430자

  `_MAX_COMPANIES`·`_MAX_RELATIONS_PER_COMPANY` 는 있는데 **사건에는 상한이
  없었다.** 질문과 무관한 근거가 프롬프트를 채우면 LLM 이 엉뚱한 것을 인용한다.

★순위 규칙은 실험 3회로 정했다

  ① 근거 **원문**을 질문과 임베딩 비교 → **실패.** 「안전사고」 질의에서 정작
     사고 근거(TMAH·인산 노출)가 82건 중 최하위(0.155)로 밀렸다. 근거 단편에
     기업명이 없어 질문의 기업명이 유사도를 지배한다.
  ② 사건 **라벨**(name + event_type)로 비교 → 나아졌으나 기업명이 든 라벨이
     여전히 상위를 먹었다(「SK하이닉스 내부 치과」가 안전사고 2위).
  ③ 질문과 라벨 **양쪽에서 앵커 기업명 제거** → 정확해졌다.
        '안전사고'   → 인산·D램 공정·불소·TMAH·질소 누출 (사고재해 5건)
        '소송 상황'   → 전직금지·TC본더·법적 분쟁·특허 침해·퇴직금 (분쟁소송 5건)

★두 신호를 쓰되 규칙이 **우선**이다

  규칙(event_type 키워드)은 티어를 정하고, 임베딩 유사도는 티어 **안에서**
  줄을 세운다. 규칙은 **hard filter 가 아니다** — 안 걸린 사건도 자리가
  남으면 살아남고, 규칙이 못 잡는 표현은 임베딩이 받는다.

★전역 검색을 쓰지 않는다

  `ChromaRepository.search_evidence()` 로 evidence 컬렉션을 전역 검색하면
  Step1 이 막은 오염이 되돌아온다 — 실측으로 「SK하이닉스 노조」 상위 5건에
  현대오토에버·HD현대중공업이 들어왔다. 여기서는 **이미 기업 scope 안으로
  좁혀진 후보만** 다시 줄 세운다. 다른 기업이라는 이유로 버리는 일도 없다 —
  scope 를 정하는 것은 이 모듈의 책임이 아니다(`retrieve_service`).

★임베딩이 죽어도 /ask 는 살아 있어야 한다

  `similarities()` 는 실패하면 예외를 올리지 않고 빈 dict 를 준다. 그러면
  규칙 티어 → 위험사건 → 최신순 폴백으로 순위가 매겨진다.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Iterable, Optional, Protocol, Sequence

Embed = Callable[[list[str]], Sequence[Sequence[float]]]


class _EventLike(Protocol):
    event_id: str
    name: str
    event_type: str
    is_risk: bool
    occurred_at: Optional[str]


# ── 규칙 신호 ────────────────────────────────────────────────────────────
# event_type 12종(실측 2026-08-23 데이터 분포)에 대표 키워드를 건다.
# ★[미확정/저신뢰] — `query_router._SHALLOW_KEYWORDS` 와 같은 성격의 잠정값이다.
#   동의어를 다 담을 수 없다는 걸 전제로 두고, 못 잡는 표현은 임베딩에 맡긴다.
#   그래서 여기 없는 말이 나와도 **아무것도 걸러지지 않는다** — 티어만 못 받는다.
_EVENT_TYPE_KEYWORDS: dict[str, re.Pattern] = {
    "노무": re.compile(r"노조|노동조합|파업|임단협|단체교섭|노사|성과급|임금"),
    "사고재해": re.compile(r"사고|재해|누출|화재|폭발|안전|부상|사망"),
    "분쟁소송": re.compile(r"소송|제소|피소|분쟁|가처분|특허\s*침해|승소|패소"),
    "규제수사": re.compile(r"규제|수사|압수수색|제재|과징금|조사|기소|공정위"),
    "사업확장": re.compile(r"투자|증설|확장|신설|진출|양산|증산|공장|클러스터"),
    "자본거래": re.compile(r"지분|매각|인수|자사주|출자|주식|유상증자|합병"),
    "실적": re.compile(r"실적|영업이익|매출|적자|흑자|손실|어닝"),
    # ★`생산\s*차질` 추가 (2026-08-26) — 「생산 차질 위험」 질의가 **규칙 티어를
    #   통째로 못 받았다**(실측: `matched_event_types` 가 빈 집합). 「공급 차질」만
    #   있었는데 사람은 같은 것을 「생산 차질」이라고도 쓴다.
    "공급망": re.compile(r"(공급|생산)\s*차질|공급망|생산\s*중단|납품|조달|감산"),
    "제품기술": re.compile(r"개발|기술|연구|상용화|신제품"),
    "품질": re.compile(r"품질|결함|불량|오류|리콜"),
    "정보유출": re.compile(r"유출|해킹|보안|개인정보|기술탈취"),
    "기타": re.compile(r"(?!)"),  # 규칙으로 지목하지 않는다 — 임베딩만 본다
}


def intent_of(question: str, anchor_names: Iterable[str]) -> str:
    """질문에서 앵커 기업명을 지운 **의도 부분**.

    ★기업명을 남기면 임베딩이 그쪽으로 쏠린다(실험 ②). 어느 기업인지는 이미
      scope 가 정해 놨으므로, 여기서 물어야 할 것은 「무엇을」뿐이다.

    ★다 지워서 남는 게 없으면 **의도가 없는 것**이다(「SK하이닉스」처럼 기업만
      물은 질의). 그때 원문을 그대로 쓰면 「SK하이닉스」와의 유사도로 줄을
      세우게 되는데 그건 잡음이다 — 실측(2026-08-23)으로 「행복 도시락 사업」이
      상위에 올라왔다. 빈 문자열을 돌려주면 호출측이 유사도를 건너뛰고
      위험사건·최신순 폴백으로 간다.
    """
    intent = question
    for name in anchor_names:
        if name:
            intent = intent.replace(name, " ")
    return " ".join(intent.split()).strip(" 의와과은는이가,")


def matched_event_types(intent: str) -> frozenset[str]:
    """의도 문자열이 지목하는 event_type 들. 못 잡으면 빈 집합(= 티어 없음)."""
    return frozenset(t for t, pattern in _EVENT_TYPE_KEYWORDS.items()
                     if pattern.search(intent))


def event_label(event: _EventLike, anchor_names: Iterable[str]) -> str:
    """유사도 비교용 라벨. **질문에서 지운 것과 같은 항을 여기서도 지운다** —
    한쪽만 지우면 비교가 어긋난다(실험 ③)."""
    label = f"{event.name} ({event.event_type})"
    for name in anchor_names:
        if name:
            label = label.replace(name, " ")
    return " ".join(label.split())


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def similarities(
    events: Sequence[_EventLike], *, intent: str,
    embed: Optional[Embed], anchor_names: Iterable[str],
) -> dict[str, float]:
    """event_id → 의도와의 코사인 유사도. **실패하면 빈 dict.**

    의도와 라벨을 **한 번에** 임베딩한다 — 왕복을 둘로 나눌 이유가 없다
    (실측: 라벨 69건 0.25s, 155건 0.76s, 의도 1건 0.12s).
    """
    # ★의도가 없으면(기업명만 물은 질의) 임베딩을 아예 부르지 않는다 — 잡음일
    #   뿐인 순위에 돈과 시간을 쓸 이유가 없다.
    if embed is None or not events or not intent.strip():
        return {}
    anchors = list(anchor_names)
    labels = [event_label(e, anchors) for e in events]
    try:
        vectors = embed([intent, *labels])
    except Exception:  # noqa: BLE001 — 임베딩이 죽어도 답변은 나가야 한다
        return {}
    if len(vectors) != len(labels) + 1:
        return {}
    query_vector = vectors[0]
    return {e.event_id: _cosine(query_vector, v)
            for e, v in zip(events, vectors[1:])}


def select(
    events: Sequence[_EventLike], *, matched: frozenset[str],
    sims: dict[str, float], limit: int,
) -> tuple[list, list]:
    """(남길 것, 잘라낸 것). **잘라낸 것을 버리지 않고 돌려준다** — 호출자가
    「몇 건을 왜 잘랐는지」 로그에 남길 수 있어야 한다.

    약한 신호부터 차례로 정렬한다(파이썬 정렬은 안정적이라 뒤 정렬이 이긴다):

        최신순 → 위험사건 → 유사도 → 규칙 티어

    동점이면 입력 순서가 남는다 — 같은 질문에 매번 다른 답이 나오면 안 된다.
    """
    ordered = list(events)
    ordered.sort(key=lambda e: e.occurred_at or "", reverse=True)
    ordered.sort(key=lambda e: not e.is_risk)
    ordered.sort(key=lambda e: -sims.get(e.event_id, 0.0))
    ordered.sort(key=lambda e: 0 if e.event_type in matched else 1)
    return ordered[:limit], ordered[limit:]
