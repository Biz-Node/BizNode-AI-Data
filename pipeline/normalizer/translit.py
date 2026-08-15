"""한글 음차 ↔ 영문 표기를 **규칙으로** 맞춘다. 손 목록 없이.

★왜 필요한가 (2026-08-14)

해외 기업은 DART에 없어 **이름이 곧 노드 식별자**다. 그래서 표기가 갈리면
같은 회사가 별개 노드가 되고, 그 회사를 지나는 공급망 경로가 끊긴다.

    마이크론(78) ⟷ 마이크론테크놀러지(0) ⟷ 마이크론 테크놀로지(10)
    보스턴다이내믹스(41) ⟷ 보스톤 다이나믹스(1) ⟷ 보스턴다이나믹스(4)

`foreign_aliases.py`가 이걸 손으로 65쌍 적어 막고 있었다. 그런데 해외 노드가
2,250곳이라 손으로는 못 따라간다. 그리고 **처음 나오는 기업**은 목록에 있을 리가
없어서, 손 목록은 구조적으로 항상 뒤늦다.

★「엔비디아 ↔ NVIDIA」는 **임의 대응이 아니라 음차**다. 계산할 수 있다.

    엔비디아 → enbidia → 자음 골격 nbd
    NVIDIA  → nvidia  → 자음 골격 nbd      (v→b)

무료라서 후보 생성의 1차로 쓴다. 재현율은 `canonical_name.py` 참고 —
거기가 이 규칙과 정식명 방식을 같은 정답지로 비교해 둔 자리다.

★★그런데 **규칙만으로 합치면 안 된다.** 이게 이 모듈의 가장 중요한 한계다.

  같은 규칙을 그래프 전체에 돌리니 113무리가 나왔는데, 명백한 오탐이 섞였다:

      npt   엔비디아(68) ⟷ 윈보드(2) ⟷ 에너베이트(2)     ← 전부 다른 회사
      ntr   인텔(32) ⟷ 유니트리(1)
      prnt  브런트 ⟷ 팔란티어 ⟷ 폴란드
      rstm  알스톰 ⟷ 로사톰

  모음을 지운 자음 골격은 **아는 쌍을 확인할 때는 정확하고, 모르는 쌍을 제안할
  때는 헐겁다.** 그래서 이 모듈은 **후보만 만든다.** 최종 판정은 모델이 한다
  (`batch/repair/foreign_merge.py`).

★규칙으로 **영영** 안 되는 것이 있다 — 문자에 정보가 없기 때문이다:
      두문자어   taiwansemiconductor → TSMC · changxin… → CXMT
      다른 이름  alphabet → 구글 · samsungelectronics → 삼성전자
  이건 `canonical_name.py`(정식명 묻기)가 맡는다.
"""

from __future__ import annotations

import re

# 국어의 로마자 표기법 자모 표 (초성 19 · 중성 21 · 종성 28)
_CHO = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j",
        "jj", "ch", "k", "t", "p", "h")
_JUNG = ("a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
         "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i")
_JONG = ("", "k", "k", "k", "n", "n", "n", "t", "l", "l", "l", "l", "l", "l",
         "l", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t")

# 뒤에 붙는 일반 명사 — 한쪽에만 있는 경우가 흔하다
# (「마이크론」 vs 「마이크론 테크놀로지」)
_SUFFIX = ("technologies", "technology", "테크놀로지", "테크놀러지", "테크놀로지스",
           "semiconductor", "세미컨덕터", "corporation", "industries",
           "materials", "머티리얼즈", "머티어리얼즈", "holdings", "홀딩스",
           "incorporated", "limited", "group", "그룹", "inc", "ltd", "llc", "co")

# 같은 소리로 나는 글자 짝. 한글 음차는 유성/무성이 흔들린다(도쿄/tokyo).
_FOLD = (("c", "k"), ("q", "k"), ("x", "ks"), ("v", "b"), ("f", "p"),
         ("l", "r"), ("z", "j"), ("y", "i"), ("w", "u"),
         ("d", "t"), ("g", "k"), ("b", "p"), ("sh", "s"))

# 자음 골격이 이보다 짧으면 **비교하지 않는다.**
# 「소니」와 「Sanyo」가 둘 다 `sn`이 되어 우연히 같아진다(실측).
MIN_SKELETON = 3


def romanize(text: str) -> str:
    """한글을 로마자로. 한글이 아닌 글자는 소문자로 그대로 둔다."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            i = code - 0xAC00
            out.append(_CHO[i // 588] + _JUNG[(i % 588) // 28] + _JONG[i % 28])
        elif ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


def skeleton(text: str) -> str:
    """비교용 자음 골격. 표기·음차 차이를 흡수한다.

    한글 음차에는 영문에 없는 삽입 모음이 생긴다(브로드컴 → beurodeukeom).
    모음을 지우면 그 차이가 통째로 사라진다 — 대신 헐거워지므로 `MIN_SKELETON`과
    **완전 일치**를 함께 써야 한다.
    """
    t = (text or "").strip()
    low = t.lower()
    for suf in _SUFFIX:
        if low.endswith(suf) and len(low) > len(suf) + 2:
            t = t[: len(t) - len(suf)]
            break
    s = romanize(t) if any("가" <= c <= "힣" for c in t) else t.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    s = s.replace("eu", "")
    s = re.sub(r"s$", "", s)                 # 복수형
    for a, b in _FOLD:
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)          # 겹친 글자
    return re.sub(r"[aeiou]", "", s)


def is_candidate(a: str, b: str) -> bool:
    """같은 회사일 **가능성**이 있는가. 확정이 아니다 — 모델이 판정해야 한다."""
    ka, kb = skeleton(a), skeleton(b)
    if len(ka) < MIN_SKELETON or len(kb) < MIN_SKELETON:
        return False
    return ka == kb
