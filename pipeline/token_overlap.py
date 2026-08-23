"""주장에 쓴 낱말이 근거 안에 실제로 있는가 — **무료 1차 대조.**

`batch/audit/grounding.py` 가 「근거-주장 정합성 2단 검사」의 1차로 쓰던 함수를
여기로 옮겼다. 옮긴 이유는 하나다 — `app/services/claim_check.py` 가 같은 것을
쓰는데, `app` 이 `batch` 를 임포트한 전례가 없다(확인: 0곳). `pipeline` 은 양쪽이
이미 쓰는 공용 계층이라 여기 둔다. **구현은 그대로다** — 복제하면 갈라진다.

★이것은 **의미 판정기가 아니다.** 재는 것은 낱말이 있느냐뿐이라 의역·동의어·
  한국어 조사에 그대로 걸린다(「SK하이닉스의」는 「SK하이닉스」를 담은 근거에서도
  없는 토큰으로 잡힌다). 2단 검사에서 이걸 **의심 후보를 좁히는 무료 단계**로만
  쓰는 이유다 — 판정은 다음 단계 몫이다.
"""

from __future__ import annotations

import re

# 흔한 조사·접미어라 근거 대조에 의미 없는 토큰
STOP = {"주식회사", "코퍼레이션", "그룹", "홀딩스", "사건", "관계", "계약",
        "co", "ltd", "inc"}


def tokens(name: str) -> list[str]:
    """이름에서 대조할 핵심 토큰 — 2글자 이상, 불용어 제외."""
    parts = re.split(r"[\s\-_·,.()\[\]「」『』/]+", name or "")
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2 and p.lower() not in STOP:
            out.append(p)
    return out


def overlap(text: str, *names: str) -> tuple[float, list[str]]:
    """이름 토큰이 근거에 얼마나 나오는가. (비율, 없는 토큰들)"""
    flat = re.sub(r"\s+", "", text or "").lower()
    toks = [t for n in names for t in tokens(n)]
    if not toks:
        return 1.0, []
    missing = [t for t in toks
               if re.sub(r"\s+", "", t).lower() not in flat]
    return 1 - len(missing) / len(toks), missing
