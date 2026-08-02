"""Product 표기 통일 — 같은 제품이 이름만 달라 갈리는 것을 막는다.

Company는 `norm_name` + DART corp_code로 개체해소가 되지만, **Product는 이름이 곧
식별자**다. 그런데 `normalize_company_name`만 거치므로 띄어쓰기·대소문자만 정리되고
한글/영문 동의어는 그대로 갈린다. 실측(2026-07-29, 643개 중):

    HBM(8) · 고대역폭메모리(HBM)(4) · 고대역폭 메모리(2)      ← 셋 다 같은 것
    D램(6) · DRAM(3)                                     ← 같은 것
    낸드플래시(6) · NAND Flash(3) · NAND(1)                ← 같은 것
    TC 본더(7) · TC본더(TC4) · 열압착(TC) 본더               ← 같은 것

★그런데 **합치면 안 되는 것**이 훨씬 많다. 이게 이 모듈의 어려운 부분이다:

    HBM3 · HBM3E · HBM4 · HBM4E · HBM5      세대가 다르면 다른 제품
    DRAM · 범용 DRAM · 서버 D램 · DDR5 D램    세그먼트가 다르면 다른 제품
    로봇 · 협동로봇 · 산업용 로봇 · 양팔로봇     종류가 다르면 다른 제품
    메모리 · 비메모리                         정반대다

즉 **부분 문자열이 겹친다는 이유로 합치면 절대 안 된다.** 「HBM4」는 「HBM」을 포함하지만
다른 제품이고, 「비메모리」는 「메모리」로 끝나지만 반대말이다.

그래서 부분일치를 일절 쓰지 않고 3층으로만 처리한다:

  1층 괄호 병기   「고대역폭메모리(HBM)」 → 괄호 안이 아는 약어면 그것을 대표로
  2층 전체 별칭   정규화한 **이름 전체**가 사전에 있을 때만 치환 (부분일치 금지)
  3층 토큰 치환   띄어쓰기로 나눈 **낱말 단위**로만 치환
                  「서버 D램」→「서버 DRAM」 ✓   「비메모리」는 한 낱말이라 그대로 ✓

3층이 필요한 이유: 「서버 D램」과 「서버 DRAM」이 둘 다 나타나면 갈린다. 하지만
낱말 경계를 지키므로 「비메모리」·「HBM4」는 건드리지 않는다.
"""

from __future__ import annotations

import re

# ── 2층: 이름 **전체**가 이것이면 대표형으로 (부분일치 아님) ────────
# 키는 `_key()`가 만드는 정규화 결과(소문자·공백/구두점 제거).
FULL_ALIASES: dict[str, str] = {
    # 고대역폭메모리
    "hbm": "HBM",
    "고대역폭메모리": "HBM",
    "고대역폭메모리hbm": "HBM",
    "highbandwidthmemory": "HBM",
    # D램
    "dram": "DRAM",
    "d램": "DRAM",
    "디램": "DRAM",
    "dynamicrandomaccessmemory": "DRAM",
    # 낸드
    "nand": "낸드플래시",
    "nandflash": "낸드플래시",
    "낸드": "낸드플래시",
    "낸드플래시": "낸드플래시",
    "낸드플레시": "낸드플래시",
    # TC 본더 (열압착 본더)
    "tc본더": "TC 본더",
    "tcb": "TC 본더",
    "tcb장비": "TC 본더",
    "열압착tc본더": "TC 본더",
    "열압착장비tc본더": "TC 본더",
    "열압착본더": "TC 본더",
    # 하이브리드 본더
    "하이브리드본더": "하이브리드 본더",
    "hybridbonder": "하이브리드 본더",
    # 노광·전공정 장비
    "euv": "EUV 장비",
    "euv장비": "EUV 장비",
    "euv노광장비": "EUV 장비",
    "extremeultraviolet": "EUV 장비",
    # 웨이퍼
    "실리콘웨이퍼": "실리콘 웨이퍼",
    "siliconwafer": "실리콘 웨이퍼",
    # SSD
    "essd": "eSSD",
    "기업용ssd": "eSSD",
    "enterprisessd": "eSSD",
    # 기타 자주 갈리는 것
    "메모리반도체": "메모리반도체",
    "memorysemiconductor": "메모리반도체",
    "파운드리": "파운드리",
    "foundry": "파운드리",
    "협동로봇": "협동로봇",
    "cobot": "협동로봇",
    "collaborativerobot": "협동로봇",
    "휴머노이드로봇": "휴머노이드 로봇",
    "humanoid": "휴머노이드 로봇",
    "humanoidrobot": "휴머노이드 로봇",
}

# ── 3층: **낱말 단위**로만 치환 (수식어는 그대로 보존) ──────────────
# 「서버 D램」→「서버 DRAM」. 낱말 경계를 지키므로 「비메모리」는 안 건드린다.
TOKEN_ALIASES: dict[str, str] = {
    "d램": "DRAM",
    "디램": "DRAM",
    "dram": "DRAM",
    "낸드": "낸드플래시",
    "nand": "낸드플래시",
    "tc본더": "TC 본더",
    "hbm": "HBM",
}

# 1층에서 괄호 안 약어로 인정할 것 — 아는 것만. 모르는 약어는 손대지 않는다.
KNOWN_ABBREV: frozenset[str] = frozenset({
    "HBM", "DRAM", "NAND", "SSD", "EUV", "TC", "TCB", "AP", "MCU",
    "SiC", "GaN", "CIS", "LPDDR", "GDDR", "DDR",
})

# 「AI 연산에 필수적인 고대역폭메모리(HBM)」처럼 설명이 붙은 이름을 가려낸다.
# 정규화 대상이 아니라 **추출 품질 문제**라 표시만 하고 사람이 본다.
_DESCRIPTIVE = re.compile(r"(필수적인|위한|관련된|사용되는|불리는|이라고|등의|같은)")


def _key(name: str) -> str:
    """비교용 정규화키 — 소문자, 공백·구두점 제거."""
    return re.sub(r"[^0-9a-z가-힣]", "", (name or "").lower())


def _paren_abbrev(name: str) -> str | None:
    """1층 — 「고대역폭메모리(HBM)」에서 괄호 안 약어를 꺼낸다.

    괄호 안이 **아는 약어**일 때만 쓴다. 「PCIe Gen3 NVMe SSD(Bravo)」의
    `Bravo`처럼 모르는 것은 코드명일 수 있어 손대지 않는다.

    ★괄호가 **이름 끝에 있을 때만** 적용한다. 뒤에 낱말이 더 있으면 괄호는
      이름 전체가 아니라 **일부만** 풀어 쓴 것이다:
        「고대역폭메모리(HBM)」   → HBM       ✓ 괄호가 끝 = 전체를 가리킴
        「열압착(TC) 본더」       → TC        ✗ 「본더」가 날아간다
      실제로 이 검사가 없어 「열압착(TC) 본더」가 「TC」로 잘렸다.
    """
    m = re.search(r"[（(]\s*([^)）]{1,12})\s*[)）]\s*$", (name or "").strip())
    if not m:
        return None
    token = m.group(1).strip()
    if token.upper() not in {a.upper() for a in KNOWN_ABBREV}:
        return None
    # 괄호 앞이 그 약어의 풀이름일 때만 (괄호가 부가정보인 경우 제외)
    before = _key(re.split(r"[（(]", name)[0])
    if before and (before in FULL_ALIASES or len(before) >= 3):
        return token.upper() if token.isupper() or token.islower() else token
    return None


def canonical_product(name: str) -> str:
    """제품 표시명 → 대표 표시명. 모르는 것은 **그대로** 돌려준다.

    ★부분일치를 쓰지 않는다. 「HBM4」가 「HBM」으로 접히면 세대 구분이 사라진다.
    """
    raw = (name or "").strip()
    if not raw:
        return raw

    # 1층 — 괄호 병기
    abbrev = _paren_abbrev(raw)
    if abbrev:
        canon = FULL_ALIASES.get(_key(abbrev))
        return canon if canon else abbrev

    # 2층 — 이름 전체 일치
    canon = FULL_ALIASES.get(_key(raw))
    if canon:
        return canon

    # 3층 — 낱말 단위 치환 (수식어 보존)
    parts = [p for p in re.split(r"\s+", raw) if p]
    if len(parts) < 2:
        return raw                      # 한 낱말은 2층에서 이미 봤다
    changed = False
    out: list[str] = []
    for p in parts:
        repl = TOKEN_ALIASES.get(_key(p))
        if repl and _key(p) != _key(repl):
            out.append(repl)
            changed = True
        else:
            out.append(p)
    return " ".join(out) if changed else raw


def norm_key(name: str) -> str:
    """그래프의 `norm_name`으로 쓸 키 — 대표형의 정규화 결과."""
    return _key(canonical_product(name))


def is_descriptive(name: str) -> bool:
    """제품명이 아니라 **설명문**인가 (추출 품질 문제로 표시)."""
    raw = (name or "").strip()
    return bool(_DESCRIPTIVE.search(raw)) or len(raw) > 40


# ── 자기검사: 한 번 더 돌려도 결과가 같아야 한다 ────────────────────
# 대표형이 또 다른 대표형으로 접히면(연쇄) 실행 순서에 따라 결과가 달라진다.
# 사전을 고칠 때 이 검사가 import 시점에 바로 잡아준다.
def _check_fixed_points() -> None:
    bad: list[str] = []
    for alias, canon in FULL_ALIASES.items():
        again = canonical_product(canon)
        if _key(again) != _key(canon):
            bad.append(f"FULL_ALIASES[{alias!r}] = {canon!r} → {again!r}")
    for token, canon in TOKEN_ALIASES.items():
        again = canonical_product(canon)
        if _key(again) != _key(canon):
            bad.append(f"TOKEN_ALIASES[{token!r}] = {canon!r} → {again!r}")
    # 절대 합쳐지면 안 되는 쌍 — 회귀 방지
    must_differ = [
        ("HBM", "HBM3"), ("HBM", "HBM4"), ("HBM3", "HBM3E"), ("HBM4", "HBM4E"),
        ("DRAM", "범용 DRAM"), ("DRAM", "서버 D램"), ("메모리", "비메모리"),
        ("로봇", "협동로봇"), ("로봇", "산업용 로봇"),
        ("TC 본더", "TC 본더 4.5 그리핀"),
    ]
    for a, b in must_differ:
        if norm_key(a) == norm_key(b):
            bad.append(f"합쳐지면 안 되는 쌍이 같아졌다: {a!r} ≡ {b!r}")
    # 낱말이 통째로 사라지지 않았는지 — 괄호 규칙이 뒷말을 삼킨 적이 있다
    must_equal = [
        ("열압착(TC) 본더", "TC 본더"),
        ("열압착장비(TC본더)", "TC 본더"),
        ("고대역폭메모리(HBM)", "HBM"),
        ("NAND Flash", "낸드플래시"),
        ("서버 D램", "서버 DRAM"),
    ]
    for a, b in must_equal:
        if norm_key(a) != norm_key(b):
            bad.append(f"같아져야 하는데 갈렸다: {a!r} → {canonical_product(a)!r} ≠ {b!r}")
    if bad:
        raise RuntimeError(
            "product_names 사전이 고정점이 아닙니다:\n  " + "\n  ".join(bad))


_check_fixed_points()
