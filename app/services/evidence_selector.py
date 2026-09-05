"""질문 의도로 사건을 골라낸다 — **근거를 줄이는 것이 목적이다.**

★왜 필요한가 (실측 2026-08-23)

  Step1 에서 기업별 evidence scope 를 분리했지만, 그 뒤로도 질문이 무엇이든
  같은 재료가 나갔다.

      「SK하이닉스」                        사건 69 · 근거 82 · 13,916자
      「SK하이닉스 노조 관련 리스크 알려줘」  사건 69 · 근거 82 · 13,916자  ← 동일
      「삼성전자와 SK하이닉스의 담합 소송」    사건 155 · 근거 205 · 34,430자

  `_MAX_COMPANIES`·`MAX_RELATIONS_PER_COMPANY` 는 있는데 **사건에는 상한이
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

# ★오늘을 **직접 부르지 않는다** — 창을 자른 기준일과 프롬프트가 말하는 오늘이
#   같아야 한다(`app/core/clock.py`). `select()` 자체는 여전히 순수하다:
#   시각을 읽는 것은 `recent_window()` 하나뿐이고, `select()` 는 그 **결과 문자열**을
#   인자로 받는다.
from app.core import clock

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
    # ★`인명피해|인명` 추가 (2026-09-02) — 「공장에서 인명 피해 난 곳 있어?」가
    #   사고재해를 **하나도 못 켜고** 「공장」 때문에 사업확장만 켰다. 상한 10을
    #   증설 사건이 다 먹어 적중 0/10 이었다.
    #   ★**`인명피해` 가 `인명` 보다 앞에 있어야 한다.** 교대는 왼쪽부터 시도하고
    #     `인명` 은 뒤 경계를 거는 낱말이라(`_GUARDED`), 순서가 뒤집히면 붙여 쓴
    #     「인명피해」가 `인명`+`피해` 로 걸린 뒤 경계에 막혀 통째로 꺼진다.
    "사고재해": re.compile(r"사고|재해|누출|화재|폭발|안전|부상|사망|인명피해|인명"),
    # ★`다툼` 추가 (2026-09-02) — 「요즘 법적 다툼 있는 곳?」이 빈 집합이었다.
    #   ❌ `법적` 은 넣지 않는다 — 「합법적」·「불법적」에 걸리는데 그 둘은 **앞**
    #      충돌이라 뒤 경계로 막을 수 없다(앞 경계는 복합명사를 죽여 금지했다).
    "분쟁소송": re.compile(r"소송|제소|피소|분쟁|가처분|특허\s*침해|승소|패소|다툼"),
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

# ── 형태소 경계 (2026-09-02) ─────────────────────────────────────────────
# ★위 패턴들은 **맨 부분일치**였다. 경계 검사가 한 곳도 없어 낱말 중간에 걸렸다.
#
#       회사를 **사고**판 사례 있어?     → 사고재해   (사고팔다)
#       **투자**증권 계열사 뭐 있어?     → 사업확장   (사명)
#       **기술**보증기금 지원받은 곳?    → 제품기술   (기관명)
#       조**사**료 사업하는 기업?       → 규제수사
#       **노사**연 콘서트 후원 기업?     → 노무      (인명)
#
#   probe 12건을 만들어 재 보니 **12건 전부** 오검출이었다(2026-09-02).
#
# ★**오탐은 누락보다 훨씬 나쁘다.** 규칙 티어는 `select()` 의 **최상위 정렬 키**라,
#   틀린 type 이 켜지면 그 티어가 상한 10 을 다 먹고 정답이 통째로 밀려난다 —
#   임베딩이 그 아래에 깔려 **구제하지 못한다.** 실측: 「공장에서 인명 피해 난 곳
#   있어?」가 `사업확장` 을 켜서 상위 10건이 전부 증설 사건이 됐다(적중 0/10).
#   반대로 규칙이 아무것도 못 켜면(`∅`) 임베딩·위험·최근창이 그대로 순위를 만든다.
_HANGUL = re.compile(r"[가-힣]")

# 명사 뒤에 **정상적으로 붙는 것** — 표준 조사와 「하다」계 용언 활용.
# 이것으로 시작하면 앞의 낱말은 온전한 명사다(「인수한 곳」의 `인수`).
_JOSA = tuple(sorted([
    "이라는", "이라고", "라는", "라고", "이라", "에서", "에게", "으로", "부터", "까지",
    "이나", "이든", "만큼", "처럼", "보다", "조차", "마저", "밖에", "한테", "께서",
    "가", "이", "은", "는", "을", "를", "에", "로", "와", "과", "도", "만", "의",
    "나", "든", "야", "요", "랑", "든지", "이랑",
    "한", "해", "했", "하", "함", "하는", "하고", "하며", "하여", "해서", "했다",
    "중", "됨", "된", "되", "돼",
], key=len, reverse=True))

# ★경계를 **거는 낱말**. 손으로 고른 것이 아니라 규칙으로 유도한다:
#
#       ① per-keyword 정밀도 < 90%      (batch 없이 `Event.name` 1,074건으로 실측)
#       ② probe 로 형태소 충돌이 실측된 것
#
#   ① 사고86 · 투자79 · 공장76 · 기술7 · 매출40 · 조사78 · 안전40 · 주식67
#     · 보안50 · 인수71 · 연구20 · 개발30 · 규제83 · 유출77          (14개)
#   ② 노사 — 정밀도는 100% 지만 「노사연」(사람 이름)에서 충돌          (1개)
#     인명 — 사건명 표본이 0건이라 ①로는 판정 불가. 「인명구조」에서 충돌 실측  (1개)
#
# ★**정밀도가 높은 낱말은 걸지 않는다.** 한때 임금(100%)·품질(100%)·지분(92%)을
#   함께 걸었다가 「임금협약」·「임금교섭」이 죽었다 — 잃는 정답이 3 → 1 로 줄었다.
#   목록을 다시 유도하려면 정밀도부터 다시 재는 것이 순서다.
_GUARDED = ("사고", "투자", "공장", "기술", "매출", "조사", "안전", "주식",
            "보안", "인수", "연구", "개발", "규제", "유출",
            "노사", "인명")


def _ok_tail(text: str, end: int) -> bool:
    """매치 **뒤**가 낱말의 끝인가. 조사·어미가 붙은 것은 끝으로 본다.

    ★**앞은 보지 않는다.** 한국어 복합명사는 **뒤가 머리**다.

            설비 + 투자   투자가 머리다        ← 정당한 질의. 앞을 막으면 죽는다
            투자 + 증권   투자는 수식일 뿐이다   ← 다른 낱말. 뒤를 막으면 걸린다

      실제로 앞뒤를 다 막아 봤더니 「최근 대규모 **설비투자** 사례 알려줘」가
      죽어 정답이 18 → 17 로 떨어졌다(2026-09-02). 필요한 것은 뒤쪽뿐이다.
    """
    tail = text[end:]
    if not tail or not _HANGUL.match(tail[0]):
        return True
    return tail.startswith(_JOSA)


# ★규칙 티어가 **구조적으로 지목할 수 없는** 종류. 「기타」의 패턴 `(?!)` 는
#   아무것에도 안 걸리므로 `matched_event_types` 가 **영원히 안 담는다**.
#
#   「기타」는 「분류를 못 했다」는 뜻이지 「그 종류가 아니다」가 아니다. 읽는 쪽이
#   이 둘을 섞으면 **판정 불가가 연결 없음으로 샌다** — `claim_check._intent_linked`
#   의 docstring 이 하지 말라고 적어 둔 바로 그것이다. 문자열을 거기 또 적지
#   않도록 **사실이 있는 자리인 여기서** 이름을 준다.
UNCLASSIFIED_EVENT_TYPES: frozenset[str] = frozenset({"기타"})

# ★**`event_type` 과는 다른 축이다**(2026-08-30). ERD 가 「`event_type` 12종과
#   `is_risk` 는 별개 축」이라고 못 박아 뒀는데, 질문에서 읽어내는 쪽에는
#   `event_type` 축밖에 없었다 — 「리스크」라는 말에 걸리는 패턴이
#   `_EVENT_TYPE_KEYWORDS` 11개 중 **하나도 없다.**
#
#   그래서 「이 회사 최근 리스크 어때?」가 `matched_event_types() == ∅` 이 되고,
#   규칙 티어가 통째로 꺼져 **코사인 유사도가 단독 정렬 키**가 됐다. 실측
#   (2026-08-30 · 삼성전자 후보 128건): 뽑힌 10건 중 위험사건 **3건**, 최근
#   12개월 **4건**, 유사도 3위가 **2021년 「협력회사 온라인 채용박람회」**였다.
#
#   ★**`_EVENT_TYPE_KEYWORDS` 에 「리스크」를 넣지 않는다.** 그러면
#     `matched_event_types()` 의 뜻이 「질문이 지목한 **사건 종류**」에서 벗어나고,
#     그 값을 **같이 쓰는** `claim_check._intent_linked` 의 판정까지 조용히
#     움직인다(`app/graph/nodes/answer.py::check_state_claims`). 축이 둘이면 함수도
#     둘이어야 한다.
#
#   ★「이슈」는 **일부러 뺐다.** 「최근 이슈 있어?」는 위험을 묻는 말이지만
#     「HBM 이슈」는 그냥 주제다. 가르지 못하는 말은 `_EVENT_TYPE_KEYWORDS` 와
#     같은 규약으로 **임베딩에 맡긴다** — 티어를 잘못 켜면 위험 아닌 사건이
#     통째로 밀려난다.
_RISK_INTENT = re.compile(r"리스크|위험|악재|우려|부정적|문제|논란")

# ★**세 번째 축 — 시간**(2026-08-30). 「최근」은 `event_type` 도 `is_risk` 도
#   아니다. 지금까지 이 말은 `intent_of()` 를 지나 임베딩에 들어갔을 뿐,
#   **아무도 해석하지 않았다.**
#
#   ★「올해」·「이번」을 넣었지만 「작년」·「2024년」 같은 **과거 지목**은 안 넣는다.
#     그건 「최근을 우선하라」가 아니라 「그 시점만 보라」는 뜻이라 티어가 아니라
#     필터여야 하고, 필터는 이 모듈이 하지 않는다(scope 의 일이다).
_RECENT_INTENT = re.compile(r"최근|요즘|요새|최신|근래|올해|이번")

# 「최근」의 폭. ★**실측 근거 없는 잠정치다** — 사람이 「최근」을 몇 달로 읽는지
#   재 본 적이 없다. 12개월로 두는 근거는 하나뿐이다: 실측(2026-08-30)에서
#   삼성전자 위험사건 57건 중 **22건**이 이 안에 들어와, 상한 10건을 채우고도
#   남으면서 전부를 삼키지는 않는다. 좁히면 재료가 마르고 넓히면 티어가 무의미해진다.
#   `batch/audit/ranking_baseline.py` 가 바꿨을 때의 영향을 잰다.
_RECENT_MONTHS = 12


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
    """의도 문자열이 지목하는 event_type 들. 못 잡으면 빈 집합(= 티어 없음).

    ★`search` 가 아니라 `finditer` 다(2026-09-02) — **어디서 걸렸는지**를 알아야
      `_GUARDED` 낱말에 뒤 경계를 걸 수 있다. 한 패턴 안에서 경계를 통과하는
      매치가 하나라도 있으면 그 type 은 켜진다.

    ★**키워드 표는 그대로다.** 이번에 바꾼 것은 매칭 방식뿐이고, 어휘 추가·삭제는
      별도 단계다 — 둘을 같이 넣으면 순위가 움직였을 때 「경계가 낸 것」과
      「어휘가 낸 것」을 대조에서 못 가른다.
    """
    if not intent.strip():
        return frozenset()
    matched: set[str] = set()
    for event_type, pattern in _EVENT_TYPE_KEYWORDS.items():
        for hit in pattern.finditer(intent):
            if hit.group(0) in _GUARDED and not _ok_tail(intent, hit.end()):
                continue
            matched.add(event_type)
            break
    return frozenset(matched)


def recent_intent(intent: str) -> bool:
    """질문이 **최근을 물었나.** 위험 축과도, `event_type` 축과도 별개다.

    ★「최근」은 지금까지 **아무도 해석하지 않았다.** `intent_of()` 가 이 말을
      지우지 않고 그대로 남기지만, 남은 뒤에 그것을 읽는 코드가 없었다 —
      임베딩에 들어가 잡음이 될 뿐이었다.
    """
    return bool(_RECENT_INTENT.search(intent))


def recent_window(months: int = _RECENT_MONTHS) -> str:
    """「최근」의 시작 연월(`YYYY-MM`). `occurred_at` 과 **문자열로** 견준다.

    ★`occurred_at` 은 Neo4j date 가 **아니라 문자열**이다(ERD §1-5 · 노드가
      아니라 `HAS_EVENT` 엣지에 있다). `date()` 로 캐스팅해 비교하면 null 이
      되어 **조용히 0건**이 된다 — 조사 중 실제로 밟은 함정이다(현황서 §8-20).
      `'2026-07-28' >= '2025-08'` 은 사전순으로 옳게 참이다.
    """
    now = clock.today()
    total = now.year * 12 + (now.month - 1) - months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def risk_intent(intent: str) -> bool:
    """질문이 **위험을 물었나.** `matched_event_types()` 와 **다른 축**이다.

    ★둘은 겹칠 수 있고 겹쳐도 된다 — 「노조 관련 리스크」는 `{노무}` 이면서
      동시에 위험 질의다. 그때 규칙 티어가 먼저 자르고 위험 티어가 그 **안에서**
      줄을 세운다(`select` 의 정렬 순서).
    """
    return bool(_RISK_INTENT.search(intent))


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


# ★유사도를 **덩어리로 묶는 폭**(2026-08-30). 실측으로 정했다.
#
#   왜 필요한가 — `select()` 아래쪽의 `위험사건`·`최신순` 키는 유사도가 **동점일
#   때만** 보이는데, float 코사인은 동점이 거의 안 난다. 그래서 두 키가 코드에만
#   있고 **실행되지 않았다.**
#
#   실측(2026-08-30 · 워크스페이스 2곳 × 질의 6종 · 상위 20건의 인접 gap):
#
#       인접 gap 중앙값  0.0034      ← 이웃끼리는 사실상 붙어 있다
#       인접 gap p90     0.0167
#       상위 20건의 폭   0.072 ~ 0.267
#
#   ★**폭이 질의에 따라 갈린다** — 그리고 그 갈림이 곧 「유사도가 되는가」다.
#
#       규칙이 걸리는 질의   「노조 관련 리스크」 0.191 · 「안전사고」 0.189
#                            「소송 상황」 0.150            → 유사도가 **가른다**
#       규칙이 안 걸리는 질의 「최근 리스크」 0.072
#                            「최근 위험한 일」 0.077        → 폭이 **잡음 수준**
#
#   0.05 는 그 사이다. 「최근 리스크」의 상위 20건은 **2덩어리**로 뭉개져 아래
#   키(위험·최신순)가 깨어나고, 「노조 관련 리스크」는 4덩어리 넘게 남아 유사도가
#   계속 줄을 세운다. 잡음(중앙 0.0034)의 15배이고 신호(0.19~0.27)보다는 작다.
#
#   ★**가중합으로 가지 않았다.** `a·sim + b·risk + c·recency` 는 계수 셋을 실측
#     없이 정해야 하고, 「왜 이게 뽑혔나」가 로그에서 안 읽힌다. 이 모듈이
#     사전식 티어인 것은 그 설명 가능성 때문이다(맨 위 독스트링).
#
#   ★**[미확정]** 워크스페이스 2곳 · 질의 6종에서 잰 값이다. 기업이 늘거나 질의
#     성격이 달라지면 다시 재야 한다. `batch/audit/ranking_baseline.py` 가 그
#     대조를 맡는다.
_SIM_BUCKET = 0.05


def _bucketed(sim: float) -> int:
    """유사도를 덩어리 번호로. **가까운 것끼리 진짜 동점으로 만든다.**"""
    return round(sim / _SIM_BUCKET)


# ── 워크스페이스 몫 — 정렬 **위에 얹는 배분 계층** (§6-0 A-1 · 2026-09-05) ──
#
# ★**정렬 규칙을 한 줄도 안 고친다.** 이 계층은 「누가 상한 안에 드는가」만
#   정하고, 고른 것들 사이의 순서는 아래 `select()` 의 정렬 그대로 둔다
#   (`llm/prompt.select_propagation()` 과 같은 규약 — 번갈아 내보내면 읽기
#   어렵고, 전역 1위가 밀려 사실상 티어가 된다).
#
# ★**왜 티어가 아니라 배분인가** — 사전식 티어를 세 자리(규칙 위·아래·유사도
#   위)에 얹어 봤는데 **셋 다 상위 10건을 워크스페이스가 10/10 으로 채웠다**
#   (실측 2026-09-05 · 워크스페이스 2곳이 전역 후보 933행 중 188행 20%).
#   자리를 옮겨도 이진 티어는 상한을 독점한다 — 최종 설계 §19-3 이 금지한
#   hard filter 와 관측상 구별되지 않는다. IMPACTS 를 얹은 다단 티어(T1 이
#   3.1%)도, 1홉 이웃 티어(워크스페이스와 무관하게 33% · 절반이 삼성전자)도
#   같은 이유로 기각했다.
def _in_workspace(event: _EventLike, keys: frozenset[str]) -> bool:
    """`company` 는 **앵커 없는 경로에서만** 찬다(`Event.company`). 앵커 경로의
    사건은 `None` 이라 여기서 자연히 빠진다."""
    company = getattr(event, "company", None)
    return bool(company and company.key in keys)


def _share_tier(event: _EventLike, matched: frozenset[str],
                risk_wanted: bool, recent_since: Optional[str]) -> tuple[int, ...]:
    """워크스페이스보다 **위**에 있는 정렬 키만 추린 서명. 몫은 이 서명이 같은
    덩어리 **안에서만** 나눈다 — 넘어가면 질문이 부른 것을 밀어낸다.

    ★실측(2026-09-05)에서 실제로 밟았다. 서명 없이 전체에서 몫을 떼자
      「최근 주요 투자 이벤트가 뭐야?」에 워크스페이스 **파업 3건**이 들어왔다
      (그 워크스페이스에 사업확장이 2건뿐이라 모자란 몫을 노무가 채웠다).
    """
    tier = [0 if event.event_type in matched else 1]
    if risk_wanted:
        tier.append(0 if event.is_risk else 1)
    if recent_since:
        tier.append(0 if (event.occurred_at or "") >= recent_since else 1)
    return tuple(tier)


def _share_floor(ordered: list, sims: dict[str, float], limit: int,
                 matched: frozenset[str], risk_wanted: bool) -> Optional[int]:
    """워크스페이스 후보가 넘어야 할 관련도 하한. **없으면 None.**

    ★**규칙 티어도 위험 티어도 없을 때만 건다.** 그때는 유사도가 주제를 읽는
      **유일한 신호**라, 몫이 그것을 덮으면 「반도체 업계」를 물었는데 담아 둔
      자동차 회사의 파업이 들어온다(실측 2026-09-05).

    ★반대로 **규칙이 있으면 걸면 안 된다.** 전 질의에 걸어 봤더니 「최근 파업」
      에서 담아 둔 회사의 파업이 통째로 죽었다 — 하한이 「전역 상위 10만큼
      유사할 것」인데 그 상위 10이 이미 그 유형의 최상위라 순환이다.

    ★**새 상수를 만들지 않는다.** 값은 전역 상위 `limit` 건의 최저 유사도
      덩어리에서 나온다 — 질의마다 유사도 대역이 달라(0.1~0.2 대 0.3~0.4)
      절대값 상수로는 애초에 못 쓴다.
    """
    if matched or risk_wanted:
        return None
    return min(_bucketed(sims.get(e.event_id, 0.0)) for e in ordered[:limit])


def _with_workspace_share(
    ordered: list, *, workspace_keys: frozenset[str], sims: dict[str, float],
    matched: frozenset[str], risk_wanted: bool, recent_since: Optional[str],
    limit: int,
) -> list:
    """상한 안의 자리를 워크스페이스와 전역이 **번갈아** 가져간다.

    앞에서부터 자르면 한쪽이 예산을 통째로 먹는다는 것은 이 저장소가 이미 한 번
    밟았고(`select_propagation()` · §5-20), 처방도 같다 — 라운드로빈이다.
    **상수가 없다**: 몫은 나누다 보면 정해지고, 한쪽이 모자라면 그만큼만 가져간다.
    """
    floor = _share_floor(ordered, sims, limit, matched, risk_wanted)

    def eligible(event: _EventLike) -> bool:
        if not _in_workspace(event, workspace_keys):
            return False
        return floor is None or _bucketed(sims.get(event.event_id, 0.0)) >= floor

    groups: dict[tuple[int, ...], list[int]] = {}
    for index, event in enumerate(ordered):
        groups.setdefault(
            _share_tier(event, matched, risk_wanted, recent_since), []).append(index)

    picked: set[int] = set()
    for tier in sorted(groups):
        room = limit - len(picked)
        if room <= 0:
            break
        rows = groups[tier]
        mine = [i for i in rows if eligible(ordered[i])]
        taken = set(mine)
        rest = [i for i in rows if i not in taken]
        n_mine = n_rest = 0
        while n_mine + n_rest < room and (n_mine < len(mine) or n_rest < len(rest)):
            if n_mine < len(mine):
                n_mine += 1
            if n_mine + n_rest < room and n_rest < len(rest):
                n_rest += 1
        picked.update(mine[:n_mine])
        picked.update(rest[:n_rest])

    head = [e for i, e in enumerate(ordered) if i in picked]
    tail = [e for i, e in enumerate(ordered) if i not in picked]
    return head + tail


def select(
    events: Sequence[_EventLike], *, matched: frozenset[str],
    sims: dict[str, float], limit: int, risk_wanted: bool = False,
    recent_since: Optional[str] = None,
    workspace_keys: frozenset[str] = frozenset(),
) -> tuple[list, list]:
    """(남길 것, 잘라낸 것). **잘라낸 것을 버리지 않고 돌려준다** — 호출자가
    「몇 건을 왜 잘랐는지」 로그에 남길 수 있어야 한다.

    약한 신호부터 차례로 정렬한다(파이썬 정렬은 안정적이라 뒤 정렬이 이긴다):

        event_id → 최신순 → 위험사건 → 유사도(덩어리) → 최근창 → 위험 티어 → 규칙 티어

    ★**최근창은 위험 티어 **아래**다**(2026-08-30 · `recent_since`). 「최근
      리스크」에서 물어야 할 것은 「최근인 것 중 위험한 것」이 아니라 **「위험한
      것 중 최근인 것」**이다 — 위험사건이 없으면 최근 아닌 위험사건이라도
      내놓아야지, 위험하지 않은 최근 사건을 내놓을 일이 아니다.

    ★**아래 `최신순` 과 다르다.** 저것은 「하루라도 최신이면 앞」이라 옛 사건
      사이에서도 계속 갈리고, 이것은 **창 안이냐 밖이냐** 두 덩어리로만 나눈다.
      창 안에서의 줄 세우기는 그대로 `최신순` 이 맡는다.

    ★**hard filter 가 아니다** — 창 밖 사건도 자리가 남으면 살아남는다. 창을
      필터로 쓰면 사건이 뜸한 기업이 통째로 빈다(`matched` 와 같은 규약).

    ★**유사도는 덩어리로 본다**(2026-08-30 · `_SIM_BUCKET`). 아래 두 키가
      「동점일 때만」 보이는데 float 유사도는 동점이 안 나서 **닿지 않았다.**
      까닭과 실측은 `_SIM_BUCKET` 주석에 있다.

    ★**위험 티어를 유사도 위에 둔다**(2026-08-30 · `risk_wanted`).

      아래에 이미 `위험사건`(`not e.is_risk`) 키가 있는데 왜 또 두나 — **닿는
      자리가 다르다.** 아래 것은 「다른 신호가 **전부 같을 때** 위험을 앞에」라는
      기본값이고, 유사도가 그 위에 있으므로 **동점일 때만** 보인다. 그런데 float
      유사도는 동점이 거의 안 나므로 실제로는 **닿지 않는 죽은 키**였다.

      실측(2026-08-30 · 삼성전자 후보 128건 · 「이 회사 최근 리스크 어때?」):
      상위 15건의 유사도가 0.3680~0.3088 로 **폭이 0.06**(사실상 잡음)인데도
      동점이 하나도 없어, 뽑힌 10건 중 위험사건이 **3건**뿐이고 3위가 2021년
      「협력회사 온라인 채용박람회」였다. 같은 후보로 `sims` 만 비우면 최근
      위험사건 10건이 정확히 나온다 — **폴백은 이미 답을 알고 있었고 유사도가
      그것을 덮어쓰고 있었다.**

      ★그래서 「질문이 위험을 물었을 때만」 유사도보다 먼저 보게 한다. 아래 키는
        그대로 둔다 — 위험을 안 물은 질의에서의 기본값은 바뀌지 않아야 한다.

      ★**규칙 티어보다는 아래다.** 「안전사고 리스크」에서 사고재해가 아닌
        위험사건이 사고재해를 밀어내면 안 된다. 규칙은 「무엇을」이고 위험은
        「어떤 성격을」이라, 좁은 쪽이 먼저다.

      ★**hard filter 가 아니다** — 위험 아닌 사건도 자리가 남으면 살아남는다
        (`matched` 와 같은 규약).

    ★**맨 아래가 `event_id` 다**(2026-08-28). 전에는 「동점이면 입력 순서가
      남는다」였는데, 입력 순서는 `company_service.events_of()` 가 준 Neo4j 행
      순서이고 그 `ORDER BY` 에는 **동점 해소가 없다.** 실측에서는 안정적이었지만
      계약이 아니라 관측일 뿐이라, 언제든 바뀔 수 있는 것에 결정성을 기대고
      있었다. 이제 모든 신호가 같으면 `event_id` 사전순으로 확정된다.

      ★**정렬 기준을 바꾼 것이 아니다.** 위 네 신호의 우선순위와 방향은 그대로고,
        넷이 **전부 같을 때만** 이 키가 보인다. 실행 간 재현성만 는다.

      ★이것만으로는 부족하다. 순위를 실제로 흔든 것은 동점이 아니라 **임베딩
        값의 흔들림**이었고(`embedding_cache.py`), 그건 값이 달라서 정렬이
        매번 다르게 **확정**되던 것이다. 이 키는 캐시가 못 막는 자리 —
        캐시 미스·임베딩 실패로 `sims` 가 비어 진짜 동점이 되는 경우 — 를 맡는다.
    """
    ordered = list(events)
    ordered.sort(key=lambda e: e.event_id)
    ordered.sort(key=lambda e: e.occurred_at or "", reverse=True)
    ordered.sort(key=lambda e: not e.is_risk)
    ordered.sort(key=lambda e: -_bucketed(sims.get(e.event_id, 0.0)))
    if recent_since:
        ordered.sort(key=lambda e: (e.occurred_at or "") < recent_since)
    if risk_wanted:
        ordered.sort(key=lambda e: not e.is_risk)
    ordered.sort(key=lambda e: 0 if e.event_type in matched else 1)

    # ★위 정렬은 **끝났다.** 아래는 순서를 다시 매기는 것이 아니라 상한 안의
    #   자리를 나누는 것이다. 두 조건 중 하나라도 빠지면 통째로 건너뛴다 —
    #   그러면 이 함수는 이 계층이 생기기 전과 **글자까지 같은 값**을 낸다.
    #
    #   ★`sims` 가 비면 몫을 **강제하지 않는다**(2026-09-05 결정). 임베딩이
    #     죽었거나 의도가 없어 관련도를 못 재는 상태인데, 그때 자리를 떼어 주면
    #     근거 없이 순위를 흔든다(§5-27 과 같은 자리다).
    if workspace_keys and sims:
        ordered = _with_workspace_share(
            ordered, workspace_keys=workspace_keys, sims=sims, matched=matched,
            risk_wanted=risk_wanted, recent_since=recent_since, limit=limit)
    return ordered[:limit], ordered[limit:]
