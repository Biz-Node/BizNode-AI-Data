"""한국어 문장 생성 유틸 — evidence 스니펫용.

evidence는 사람이 읽고 "이 관계가 왜 있는지"를 판단하는 근거다. `삼성전자은(는)`
같은 표기는 신뢰를 깎으므로 조사를 제대로 고른다.

한글은 받침으로 정확히 판정할 수 있다. 외국어(Qualcomm·Google)는 **한국어 발음**을
알아야 해서 철자만으로는 못 정한다("Google"은 e로 끝나지만 '구글'이라 받침이 있다).
그래서 외국어 이름 뒤에는 **조사를 붙이지 않는 문장 구조**를 쓴다(콜론·괄호 배치).
"""

from __future__ import annotations

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3

# 숫자의 한국어 읽기 기준 받침 유무 (영·일·삼·육·칠·팔 = 받침 有)
_DIGIT_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False,
                  "5": False, "6": True, "7": True, "8": True, "9": False}


def has_batchim(word: str) -> bool | None:
    """마지막 글자에 받침이 있는지. 판정 불가(외국어)면 None."""
    if not word:
        return None
    last = word.strip()[-1:]
    if not last:
        return None
    code = ord(last)
    if _HANGUL_BASE <= code <= _HANGUL_LAST:
        return (code - _HANGUL_BASE) % 28 != 0
    if last in _DIGIT_BATCHIM:
        return _DIGIT_BATCHIM[last]
    return None            # 라틴문자 등 — 발음을 알아야 하므로 판정하지 않는다


def josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """단어 + 알맞은 조사. 판정 불가면 조사를 생략한다(어색한 '은(는)' 방지).

    >>> josa("삼성전자", "은", "는")   # 받침 없음
    '삼성전자는'
    >>> josa("한미반도체", "은", "는") # 받침 없음
    '한미반도체는'
    >>> josa("SK하이닉스", "이", "가")
    'SK하이닉스가'
    """
    batchim = has_batchim(word)
    if batchim is None:
        return word
    return word + (with_batchim if batchim else without_batchim)


def eun_neun(word: str) -> str:
    return josa(word, "은", "는")


def i_ga(word: str) -> str:
    return josa(word, "이", "가")


def eul_reul(word: str) -> str:
    return josa(word, "을", "를")

