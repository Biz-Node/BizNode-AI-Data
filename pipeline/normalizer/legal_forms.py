"""법인격·기관 표기 — **한 곳에서만 정한다**.

★왜 모았나 (2026-08-13)

「이 이름이 회사인가, 기관인가」를 판별하는 목록이 **여섯 벌로 흩어져** 있었다:

    base.py           _COMPANY_NAME_MARKERS   8개
    entities.py       _COMPANY_MARKERS 11 · _ORG_MARKERS 8
    node_identity.py  _CORP_MARKERS 9 · _ORG_MARKERS 10 · _CORP_SUFFIXES 17

같은 질문에 여섯 개의 답이 있으니 경로마다 판정이 달랐다. 실측으로 확인한 차이:

    회사 표기   `entities`에만 영문(Inc·Ltd·Corp)
               `node_identity`에만 그룹·홀딩스
    기관 표기   공통이 셋뿐 — `entities`는 연구기관 계열,
               `node_identity`는 노조·단체 계열

★기관 목록 둘은 **다른 개념이었는데 같은 라벨로 간다.**
  연구원·협회도 `:Organization`이고 노조·조합원도 `:Organization`이다.
  그래서 서로의 빈칸이 그대로 버그였다:

      entities.looks_like_organization("전국금속노동조합")   → False  ✗ 노조를 못 잡음
      node_identity._looks_organization("한국전자기술연구원") → False  ✗ 연구원을 못 잡음

  실제로 그래프에 연구기관 4곳이 `Company`로 들어와 있었다:
      인공지능연구원 · 한국전자기술연구원 · 포항공과대학교 가속기연구소 …

  둘을 합치면 양쪽 빈칸이 동시에 메워진다.

★영문 접미어는 **쓰임이 둘**이라 집합도 둘로 나눈다.
    탐지용 `EN_LEGAL_SUFFIXES`      「Holdings가 붙었으니 법인이다」   ← 넓게
    제거용 `EN_STRIPPABLE_SUFFIXES` 「Holdings를 떼고 같은 회사로 본다」 ← 좁게
  이 둘을 한 집합으로 쓰면 「Simmtech Holdings」와 「Simmtech」가 합쳐진다.
  법인격(Inc·Ltd)은 상호의 일부가 아니지만 **Holdings는 상호의 일부**다.
"""

from __future__ import annotations

import re

# ── 한글 법인격 표기 ────────────────────────────────────────
# 이 표기가 있으면 개인이 아니라 법인·단체다.
CORP_MARKERS: tuple[str, ...] = (
    "㈜", "(주)", "(유)", "주식회사", "유한회사", "회사",
    "재단", "조합", "홀딩스", "그룹",
)

# ── 영문 법인격 접미어 ──────────────────────────────────────
# 탐지용 — 이름 끝에 이게 붙으면 법인으로 본다.
EN_LEGAL_SUFFIXES: frozenset[str] = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "llp", "lp", "plc",
    "gmbh", "ag", "sa", "nv", "bv", "as", "ab", "oy", "spa", "srl", "sas",
    "pty", "pte", "kk", "kabushiki", "kaisha", "bhd", "sdn",
    "holdings", "holding",
})

# 제거용 — 정규화 키를 만들 때 **떼어도 되는** 것만.
# ★`holdings`·`holding`을 뺐다(2026-08-13). 실측:
#       Simmtech Holdings → simmtech = Simmtech 와 같은 키가 됨
#       심텍홀딩스         → 심텍홀딩스 ≠ 심텍       (한글은 안 뗌)
#   심텍과 심텍홀딩스는 **각각 상장된 다른 법인**인데 영문으로 들어오면 합쳐졌다.
#   한글·영문이 정반대로 동작하던 것도 이걸로 맞춰진다.
EN_STRIPPABLE_SUFFIXES: frozenset[str] = EN_LEGAL_SUFFIXES - {"holdings", "holding"}

# ── 기관·단체 표기 ─────────────────────────────────────────
# `:Organization`으로 보내야 하는 것. 연구기관 계열과 노조·단체 계열을 합쳤다.
ORG_MARKERS: tuple[str, ...] = (
    # 연구·학술·협회
    "연구원", "연구소", "재단법인", "사단법인", "협회", "진흥원", "진흥회",
    "위원회", "학회", "산학협력단",
    # 노동·단체
    "노동조합", "노조", "조합원", "연맹", "지부", "근로자", "직원",
)

_TOKEN_SPLIT_RE = re.compile(r"[\s,.()\[\]]+")


def looks_like_organization(name: str) -> bool:
    """비기업 기관·단체인가.

    ★법인격 표기가 있으면 회사다 — 「㈜인공지능연구원」·「디엠비마케팅연구소㈜」는
      이름에 연구원/연구소가 들어가도 주식회사다.
    """
    if any(m in name for m in ("㈜", "(주)", "(유)", "주식회사", "유한회사")):
        return False
    return any(m in name for m in ORG_MARKERS)


def looks_like_company(name: str) -> bool:
    """이름 표기만으로 **법인이 확실한가**. 애매하면 False.

    ★기관 검사를 **먼저** 한다. 「조합원」이 「조합」에 걸려 회사가 되는 일이
      실제로 있었다:
          삼성전자 DX부문 조합원 -SUES/가처분-> 초기업노조(:Organization)
      상대는 Organization인데 이쪽만 Company가 되면 노조 분쟁이 기업 소송으로 보인다.
    """
    if looks_like_organization(name):
        return False
    if any(m in name for m in CORP_MARKERS):
        return True
    tokens = [t.strip(".,()").lower()
              for t in _TOKEN_SPLIT_RE.split(name) if t.strip(".,()")]
    return len(tokens) > 1 and tokens[-1] in EN_LEGAL_SUFFIXES


def strip_en_legal_suffix(name: str) -> str:
    """뒤쪽 법인격 토큰을 반복 제거. 'ROBOTIS Beijing Co., Ltd.' → 'ROBOTIS Beijing'.

    **뒤에서부터만** 떼므로 이름 가운데의 'Co'는 건드리지 않는다
    (예: 'Coway'는 토큰이 통째로 'coway'라 대상 아님).
    `Holdings`는 떼지 않는다 — 상호의 일부다(위 주석 참고).
    """
    tokens = [t for t in _TOKEN_SPLIT_RE.split(name) if t]
    while tokens and tokens[-1].lower() in EN_STRIPPABLE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) if tokens else name
