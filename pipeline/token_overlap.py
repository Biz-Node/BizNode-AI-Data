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
from functools import lru_cache
from typing import Callable

# 흔한 조사·접미어라 근거 대조에 의미 없는 토큰
STOP = {"주식회사", "코퍼레이션", "그룹", "홀딩스", "사건", "관계", "계약",
        "co", "ltd", "inc"}

_MIN_TOKEN_LEN = 2


def tokens(name: str) -> list[str]:
    """이름에서 대조할 핵심 토큰 — 2글자 이상, 불용어 제외.

    ★**기본 토크나이저다. 동작을 바꾸지 마라.** `batch/audit/grounding.py` 의
      `_GROUND_THRESHOLD = 0.34` 가 이 동작에 맞춰 잡힌 값이다. 문장을 다루려면
      `sentence_tokens` 를 `overlap(..., tokenizer=...)` 로 주입한다.
    """
    parts = re.split(r"[\s\-_·,.()\[\]「」『』/]+", name or "")
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= _MIN_TOKEN_LEN and p.lower() not in STOP:
            out.append(p)
    return out


def overlap(text: str, *names: str,
            tokenizer: Callable[[str], list[str]] = tokens,
            ) -> tuple[float, list[str]]:
    """이름 토큰이 근거에 얼마나 나오는가. (비율, 없는 토큰들)

    `tokenizer` 를 주면 토큰 뽑는 방식만 바뀐다 — 대조 방식은 그대로다.
    기본값은 위 `tokens` 라 **기존 호출자의 결과가 변하지 않는다.**
    """
    flat = re.sub(r"\s+", "", text or "").lower()
    toks = [t for n in names for t in tokenizer(n)]
    if not toks:
        return 1.0, []
    missing = [t for t in toks
               if re.sub(r"\s+", "", t).lower() not in flat]
    return 1 - len(missing) / len(toks), missing


# ── 문장용 (Step4b, 2026-08-23) ─────────────────────────────────────────
#
# Step4a 실측: 낮은 겹침 점수의 지배적 원인이 근거 부실이 아니라 **한국어 조사**
# 였다. 걸린 토큰이 전부 조사 붙은 기업명이었다 —
#
#     "삼성전자에 납품하는 기업으로 SFA반도체가 있다"          score 0.00
#     없는 토큰 ['삼성전자에','납품하는','기업으로','SFA반도체가','있다']
#
# 삼성전자도 SFA반도체도 근거에 있다. 「에」·「가」가 붙어 못 맞춘 것뿐이다.
# 날짜도 같은 종류다 — 주장은 「2026년 3월 18일」, 근거는 「2026-03-18」.

# 낱말 대조에 쓸 내용어 품사. 조사(J*)·어미(E*)·용언파생접미사(XSV/XSA)·
# 명사파생접미사(XSN)·의존명사(NNB)·용언(VV/VA/VX)은 **전부 뺀다** — 이것들이
# 조사와 서술 꼬리의 정체다. 용언을 빼도 내용은 남는다: 「발주하였다」는
# 발주/NNG + 하/XSV + 었/EP + 다/EF 라 「발주」가 살아남는다.
_CONTENT_TAGS = frozenset({"NNG", "NNP", "NR", "SN", "SL", "SH"})

# ★문장 **전용** 불용어. `STOP` 에 섞지 않는다 — 그건 `batch/audit/grounding.py`
#   와 공유하는 집합이라 건드리면 그쪽 판정이 조용히 바뀐다.
#
#   실측(claim 84건, 2026-08-23)에서 못 맞춘 토큰의 상위가 전부 이 부류였다:
#   발생(7)·기업(5)·진행(3)·보도(3)·발표(3)·기록(2)·관련(2)·제기(2)·직면(2).
#   어느 기업 기사에나 붙는 서술 명사라 **있고 없고가 아무것도 말해 주지 않는다.**
#   반대로 납품·공급·차질·사고·장비 같은 낱말은 변별력이 있어 남긴다 —
#   여기 더 담을수록 검출력을 잃는다.
_SENTENCE_STOP = frozenset({
    "발생", "진행", "기록", "관련", "제기", "직면", "상황", "기업", "회사",
    "사례", "경우", "시작", "완료", "공식", "규모", "체계", "이후", "처음",
    "보도", "발표", "확인", "예정", "가능", "존재", "대한", "위한",
})

# 이미 정규화된 ISO 날짜 — Kiwi 에 넣으면 하이픈에서 쪼개지므로 통째로 뽑아 둔다.
_ISO_DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")

_YMD = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_YM = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월")
_DOTTED = re.compile(r"\b(\d{4})[./](\d{1,2})[./](\d{1,2})\b")


def normalize_dates(text: str) -> str:
    """날짜 표기를 ISO 로 맞춘다. **양쪽에 똑같이 걸어야** 의미가 있다.

    「2026년 3월 18일」·「2026.03.18」 → `2026-03-18`,
    「2026년 3월」 → `2026-03`.
    """
    if not text:
        return text
    out = _YMD.sub(lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", text)
    out = _DOTTED.sub(lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", out)
    return _YM.sub(lambda m: f"{m[1]}-{int(m[2]):02d}", out)


@lru_cache(maxsize=1)
def _kiwi():
    """Kiwi 로드는 1.3초다(실측 2026-08-22, `search/service/anchor_extractor.py`
    같은 근거) — 프로세스당 한 번만 만든다. tokenize 자체는 문장당 0.14ms 다.

    ★`anchor_extractor` 도 자기 인스턴스를 따로 들고 있다. 합치려면 Search
      Layer 를 고쳐야 하는데 지금은 범위 밖이라 중복 로드를 감수한다.
    """
    from kiwipiepy import Kiwi

    return Kiwi()


def _merge_adjacent(analyzed) -> list[str]:
    """붙어 있던 내용어를 도로 붙인다.

    ★Kiwi 가 사명을 쪼갠다 — `SFA반도체가` → SFA/SL + 반도체/NNG + 가/JKS,
      `SK하이닉스에` → SK/SL + 하이닉스/NNP + 에/JKB. 쪼갠 채로 대조하면
      「SFA」와 「반도체」가 따로 걸려 뜻이 흐려진다. **원문에서 사이가 벌어져
      있지 않았던 것만** 다시 붙인다.
    """
    merged: list[str] = []
    end_of_previous = -1
    for token in analyzed:
        if token.tag not in _CONTENT_TAGS:
            end_of_previous = -1        # 조사·어미가 끼면 이어 붙이지 않는다
            continue
        if merged and token.start == end_of_previous:
            merged[-1] += token.form
        else:
            merged.append(token.form)
        end_of_previous = token.start + len(token.form)
    return merged


def sentence_tokens(text: str) -> list[str]:
    """문장에서 대조할 내용어 토큰 — 조사·어미를 떼고, 쪼개진 사명을 도로 붙인다.

    Kiwi 가 없거나 죽으면 기본 `tokens()` 로 물러선다 — /ask 요청 경로에서
    도는 코드라 여기서 예외를 올리면 답변이 통째로 실패한다.
    """
    if not text:
        return []
    # ISO 날짜는 Kiwi 에 넘기지 않는다 — 하이픈에서 쪼개진다.
    dates = _ISO_DATE.findall(text)
    try:
        words = _merge_adjacent(_kiwi().tokenize(_ISO_DATE.sub(" ", text)))
    except Exception:  # noqa: BLE001 — 형태소 분석 실패로 답변을 죽이지 않는다
        return tokens(text)
    return dates + [w for w in words
                    if len(w) >= _MIN_TOKEN_LEN
                    and w.lower() not in STOP and w not in _SENTENCE_STOP]
