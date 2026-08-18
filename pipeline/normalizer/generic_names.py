"""설명형·익명 개체명 판별 (경로 B·C·뉴스 공통).

LLM과 공시 파서 모두 실명 대신 설명형을 뱉는다:
  "글로벌 빅테크" · "해외 SSD 전문업체" · "글로벌 AI반도체 기업" · "고객사"
이런 건 노드가 될 수 없다(누구인지 특정 불가) — 엣지도 stub도 만들지 않는다(방법서 §7).

정확 일치만으로는 못 잡는다("글로벌 항공우주 업체" 등 조합이 무한). 그래서
**수식어 + 일반명사 패턴**으로 판별한다.
"""

from __future__ import annotations

import re

# 실명이 아님을 확정하는 일반명사 (단독으로도 설명형)
_GENERIC_NOUNS = (
    "고객사", "거래처", "협력사", "공급사", "매입처", "구매처", "발주처", "발주사",
    "제조사", "수요처", "수요자", "관련업체", "관련 업체", "복수", "업계",
)

# 수식어 — 뒤에 일반명사가 오면 설명형 ("글로벌 + 기업")
_QUALIFIERS = (
    "글로벌", "해외", "국내", "대형", "주요", "일부", "특정", "익명", "다수", "복수",
    "북미", "유럽", "아시아", "중국", "일본", "미국", "동남아", "중동", "남미",
)
# 수식어와 결합해 설명형이 되는 일반명사
_CATEGORY_NOUNS = (
    "기업", "업체", "회사", "빅테크", "제조사", "고객", "파트너", "선주", "사업자",
    "메이커", "벤더", "공급업체", "전문업체", "대기업",
)

# "글로벌 ... 기업" / "해외 SSD 전문업체" 처럼 수식어와 명사 사이에 내용이 끼는 형태
_DESCRIPTIVE_RE = re.compile(
    rf"(?:{'|'.join(_QUALIFIERS)}).{{0,12}}(?:{'|'.join(_CATEGORY_NOUNS)})"
)

# 익명 처리 표기
_ANON_MARKERS = ("비공개", "기재생략", "기재 생략", "영업기밀", "영업 기밀", "미공개", "익명")

# ★스키마 자리표시자 — LLM이 개체명 대신 **타입명을 그대로** 뱉는 경우.
# 실제로 Event 노드 5개가 이름 "Event"로 만들어졌다. 노드 타입을 이름으로 쓰면
# 서로 무관한 사건들이 한 노드로 뭉친다.
_PLACEHOLDER_NAMES = frozenset({
    "event", "company", "person", "product", "organization",
    "이벤트", "사건", "기업", "회사", "인물", "제품", "기관",
    "n/a", "na", "none", "null", "unknown", "미상", "해당없음",
})


def is_placeholder_name(name: str | None) -> bool:
    """LLM이 실명 대신 타입명·자리표시자를 뱉었는지."""
    return (name or "").strip().lower() in _PLACEHOLDER_NAMES

# ★이니셜 익명 표기 — "L사"·"H사"·"AB사"·"갑사".
# 공시는 상대를 감출 때 머리글자만 남긴다. 이걸 노드로 만들면 **서로 다른 회사의
# "L사"가 한 노드로 뭉쳐** 그래프가 오염된다(하이젠알앤엠의 L사 ≠ 다른 회사의 L사).
# 실명 회사(예: "CJ", "LG")와 달리 뒤에 '사'가 붙는 1~2글자 머리글자만 잡는다.
_INITIAL_ANON_RE = re.compile(r"^(?:[A-Za-z]{1,2}|[가-힣])사$")

_MAX_NAME_LEN = 60


# ★Event 전용 — 사건이 아니라 **시황·주가 움직임**을 사건으로 뱉는 경우.
# "상한가 기록"·"주가 상승"은 기업 관계가 아니라 시세 현상이라 노드가 될 값이 없다.
# 게다가 이름이 범용이라 서로 무관한 사건이 한 노드로 뭉친다.
_MARKET_NOISE = (
    "상한가", "하한가", "급등", "급락", "신고가", "신저가", "강세", "약세",
    "주가상승", "주가하락", "목표주가", "시가총액", "거래대금", "수급",
)
# 목적어 없는 맨동사·범용 명사 — 무엇이 일어났는지 특정 불가
_BARE_ACTION_NAMES = frozenset({
    "출시", "체결", "발표", "공시", "계약", "계약체결", "투자", "인수", "합병",
    "상장", "출하", "양산", "개발", "진출", "확대", "증가", "감소", "성장",
})


# ★프롬프트 예시 유출 차단.
# 추출 프롬프트에 「평택 공장 화재」·「HBM4 양산 개시」를 **형태 예시**로 적었더니
# LLM이 사건 이름을 못 정할 때 이것을 그대로 복사했다(실측 2026-07-29).
#   근거: "한미반도체는 TC본더 가격을 인상했으며…"  → 이름: 「평택 공장 화재」
# 근거는 정확히 인용됐는데 이름만 엉뚱해서, 그래프만 보면 알아채기 어렵다.
# 프롬프트에서 구체명을 뺐지만, 코드에서도 막아 재발을 원천 차단한다.
PROMPT_EXAMPLE_NAMES = frozenset({
    "평택공장화재", "hbm4양산개시", "ai서밋", "루빈플랫폼생산지연",
    "청주공장화재", "hbm4양산", "hbm4양산본격화",
})


def is_leaked_example(name: str | None) -> bool:
    """프롬프트 예시를 그대로 베낀 이름인지."""
    return (name or "").replace(" ", "").lower() in PROMPT_EXAMPLE_NAMES


def is_market_noise_event(name: str | None) -> bool:
    """Event 이름이 시황·맨동사·프롬프트 예시라 사건으로 볼 수 없으면 True."""
    if not name:
        return True
    compact = name.replace(" ", "")
    if compact in _BARE_ACTION_NAMES:
        return True
    if is_leaked_example(name):
        return True
    return any(m in compact for m in _MARKET_NOISE)


def generic_reason(name: str | None) -> str | None:
    """노드가 될 수 없는 사유. 실명이면 None.

    사유를 구분하는 이유: **길이 초과는 실명일 수 있다.**
    (예: 'HYUNDAI ROTEM COMPANY - HYUNDAI EUROTEM … ORTAK GIRISIMI' — 터키 합작
    컨소시엄의 실제 등록명이다.) 신규 적재는 보수적으로 막되, 기존 노드를 지울
    때는 확실한 사유만 근거로 삼는다.
    """
    if not name:
        return "empty"
    compact = name.replace(" ", "")
    if len(compact) < 2:
        return "too_short"
    if is_placeholder_name(name):
        return "placeholder"
    if _INITIAL_ANON_RE.match(compact):
        return "initial_anon"
    if any(m.replace(" ", "") in compact for m in _ANON_MARKERS):
        return "anonymous"
    if any(n.replace(" ", "") in compact for n in _GENERIC_NOUNS):
        return "generic_noun"
    # "글로벌 대형기업", "해외 SSD 전문업체" 등 수식어+일반명사 조합
    if _DESCRIPTIVE_RE.search(name):
        return "descriptive"
    if len(compact) > _MAX_NAME_LEN:
        return "too_long"        # ← 의심스럽지만 실명일 수 있다
    return None


# 이름 자체가 실명이 아님이 **확실한** 사유 (기존 노드 삭제 근거로 쓸 수 있다)
CERTAIN_REASONS = frozenset({
    "empty", "too_short", "placeholder", "initial_anon", "anonymous",
    "generic_noun", "descriptive",
})


def is_generic_name(name: str | None) -> bool:
    """설명형·익명이면 True (노드로 만들면 안 되는 이름). 신규 적재용 — 보수적."""
    return generic_reason(name) is not None
