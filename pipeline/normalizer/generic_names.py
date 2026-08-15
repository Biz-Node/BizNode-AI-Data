"""설명형·익명 개체명 판별 (경로 B·C·뉴스 공통).

LLM과 공시 파서 모두 실명 대신 설명형을 뱉는다:
  "글로벌 빅테크" · "해외 SSD 전문업체" · "글로벌 AI반도체 기업" · "고객사"
이런 건 노드가 될 수 없다(누구인지 특정 불가) — 엣지도 stub도 만들지 않는다(방법서 §7).

정확 일치만으로는 못 잡는다("글로벌 항공우주 업체" 등 조합이 무한). 그래서
**수식어 + 일반명사 패턴**으로 판별한다.
"""

from __future__ import annotations

import re

# ★의미 키워드 목록을 걷어냈다(2026-08-13).
#
#   전에는 여기 세 목록이 있었다:
#       _GENERIC_NOUNS   고객사·거래처·협력사·수요처 …   15개
#       _QUALIFIERS      글로벌·해외·국내·대형 …        19개
#       _CATEGORY_NOUNS  기업·업체·회사·빅테크 …        14개
#   그리고 이 셋을 조합한 `_DESCRIPTIVE_RE`가 「글로벌 + 기업」 꼴을 잡았다.
#
#   ★왜 뺐나 — **열린 어휘를 닫힌 목록으로 막으려는 것**이라 양쪽으로 실패한다.
#     ① 놓친다  「국내 유수의 배터리 소재 공급 파트너」는 조합이 달라 통과
#     ② **친다** 부분 문자열이 실명을 때린다. 실측 2건:
#           「삼성전자 총파업 계획」 ← 「총파**업계**획」의 '업계'에 걸림
#           「한국정보통신」 등 417곳 ← supply_contract의 '정보'(2026-08-12)
#       ②가 특히 나쁘다 — 실명이 조용히 버려지고 엣지가 아예 안 만들어진다.
#
#   대신 **모델에게 묻는다**(`normalizer/name_judge.py`). 문법 규칙으로 싸게
#   거르고, 남는 것 중 corp_code로 해소 안 된 새 이름만 물어본다(0.25원/건).
#
#   ★아래 목록들은 **닫혀 있어서** 그대로 둔다:
#     · 익명 표기(비공개·기재생략) — 공시가 쓰는 정형 표현
#     · 자리표시자(Event·N/A)     — 우리 스키마 타입명
#     · 정규식(문장·복수·이니셜)    — 한국어 문법

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
#
# ★꼬리말을 붙인 형태도 잡는다(2026-08-11). 원래 `…사$`로 끝을 못 박아서
#   **「L사 外」·「G사 등」이 통과했다.** 삼현 사업보고서에서 5건이 노드가 됐고
#   전부 연결 1개짜리 섬이 됐다:
#       L사 外 · G사 外 · G사 등 · I사 外 · K사 등
#   「外」·「등」은 「그 외 여러 곳」이라는 뜻이라 오히려 **더 특정 불가**다.
_INITIAL_ANON_RE = re.compile(r"^(?:[A-Za-z]{1,2}|[가-힣])사(?:\s*(?:外|외|등))?$")

# ★집합·복수 표기 — **여럿을 한 덩이로 부른 것**이라 노드가 될 수 없다(2026-08-12).
#
#   실측으로 걸린 것들:
#       「인도 기업들」 · 「로보틱스 기업들」 · 「지주 등 관계 기업들」
#       「미국 소비자 14명과 중소 PC조립·유통업체 3곳」
#       「소비자와 오프라인 소매업체를 대표하는 원고들」
#       「벨벳제1호 유한회사 등 2개사」
#
#   왜 나쁜가 — 서로 **다른 회사들**이 한 노드로 뭉친다. A기사의 「인도 기업들」과
#   B기사의 「인도 기업들」이 같은 노드가 되는데 실제로는 아무 관계가 없다.
#   그리고 「삼성전자를 제소한 곳」을 세면 「원고들」이 한 곳으로 잡혀 집계가 틀린다.
#
#   기존 `_DESCRIPTIVE_RE`는 「수식어+명사」 2단어만 봐서 이 형태를 다 놓쳤다.
_PLURAL_TAIL_RE = re.compile(
    r"(들|일동)\s*$"                          # 「…기업들」·「원고들」
    r"|등\s*\d+\s*(개사|곳|명|사)\s*$"          # 「…등 2개사」
    r"|\d+\s*(개사|곳|명)\s*$"                 # 「…업체 3곳」
    r"|(와|과)\s*[^,]{0,20}\d+\s*(개사|곳|명)"  # 「A와 B 3곳」
)

# ★문장이 통째로 노드가 된 경우.
#   「Cloud 스토리지간 Data를 실시간으로 이전할 수 있는 기술 연구」
#   조사·어미가 들어가면 이름이 아니라 서술이다.
#   ★조사만으로는 못 가른다 — 「아이디브이 글로벌 넥스트 유니콘 펀드」가 「가 」에
#     걸렸다(실측 오탐). **용언 어미**가 있어야 서술이다.
_SENTENCE_RE = re.compile(
    r"(을|를)\s+\S+(하|되|시키|만들|이전|제공|개발|연구)"   # 「…를 …하는」
    r"|(할|하는|되는|있는|없는|위한|따른|관한)\s+\S"        # 관형형 어미 + 말
)

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
    # 「…기업들」·「…등 2개사」·「…업체 3곳」 — 여럿을 한 덩이로 부른 것
    if _PLURAL_TAIL_RE.search(name):
        return "plural_group"
    # 조사·어미가 들어간 문장이 통째로 이름이 된 것
    if _SENTENCE_RE.search(name):
        return "sentence"
    if len(compact) > _MAX_NAME_LEN:
        return "too_long"        # ← 의심스럽지만 실명일 수 있다
    return None


# 이름 자체가 실명이 아님이 **확실한** 사유 (기존 노드 삭제 근거로 쓸 수 있다)
CERTAIN_REASONS = frozenset({
    "empty", "too_short", "placeholder", "initial_anon", "anonymous",
    "plural_group", "sentence",
})


def is_generic_name(name: str | None) -> bool:
    """설명형·익명이면 True (노드로 만들면 안 되는 이름). 신규 적재용 — 보수적."""
    return generic_reason(name) is not None
