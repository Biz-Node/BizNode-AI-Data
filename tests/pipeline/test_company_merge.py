"""`company_merge` 손 목록의 판정 경로 — **쓰기 전에 거르는 관문 전부.**

왜 테스트로 못 박나 (2026-09-13)

이 배치가 쓰는 한 행이 `normalize_company_name` 의 반환값을 바꾼다. 잘못 쓰면
그 표기가 들어올 때마다 다른 회사로 붙고, 되돌리려면 그 사이 적재된 것을 다
찾아야 한다. 그래서 관문이 다섯이고, 각 관문이 실제로 막는지를 여기서 본다.
관문이 빠져도 배치는 조용히 돈다 — 실측(dry-run)에서는 22건 전부 통과라
빠진 관문이 드러나지 않는다.

★`plan()` 은 DB 를 안 만진다. 읽어 온 것을 넘기면 판정만 돌려주므로 여기서는
  그래프·표·명부·resolver 를 전부 가짜로 준다.
"""

from __future__ import annotations

import pytest

from batch.repair import company_merge as cm
from pipeline.normalizer import foreign_aliases
from pipeline.normalizer.base import normalize_company_name

CC, STUB, CANON = "00164742", "현대차", "현대자동차"
CONFIRMED = [(CC, STUB, "같은 회사")]


def _graph(*, stub=True, canon_nodes=1, canon_norm=CANON):
    stubs = {STUB: {"key": STUB, "name": "현대차", "deg": 41, "ev": 4}} if stub else {}
    canon = {CC: [{"cc": CC, "norm": canon_norm, "name": "현대자동차", "deg": 87, "ev": 9}
                  for _ in range(canon_nodes)]}
    return stubs, canon


def _plan(registry_rows=None, *, stub=True, canon_nodes=1, canon_norm=CANON,
          master=frozenset({CC}), resolved=CC):
    stubs, canon = _graph(stub=stub, canon_nodes=canon_nodes, canon_norm=canon_norm)
    reg = cm.Registry(rows=dict(registry_rows or {}))
    return cm.plan(CONFIRMED, stubs, canon, reg, set(master), lambda _n: resolved)[0]


# ── 목록 자체의 무결성 ──────────────────────────────────────

def test_손_목록이_서로_어긋나지_않는다():
    keys = [s for _, s, _ in cm.CONFIRMED]
    assert len(keys) == len(set(keys)), "같은 stub 이 두 번"
    assert not set(keys) & set(cm.REVIEWED_NOT_MERGED), "접는다와 안 접는다에 같이 있다"
    for cc, key, why in cm.CONFIRMED:
        assert cc.isdigit() and len(cc) == 8, f"{key}: corp_code 가 8자리 숫자가 아니다"
        # 키는 norm_name 이다 — 소문자·공백 없음. 대문자나 공백이 있으면 노드를 못 찾는다.
        assert key == key.lower() and " " not in key, f"{key}: norm_name 꼴이 아니다"
        assert why, f"{key}: 사유가 비었다"
    for key, why in cm.REVIEWED_NOT_MERGED.items():
        assert why.split(" · ")[0] in ("보류", "확인 필요", "다른 법인"), f"{key}: 분류가 없다"


def test_측정한_24건_중_21건이_CONFIRMED_이고_나머지는_사유가_있다():
    """22건으로 갔다가 FADU 가 파두의 자회사로 드러나 21건이 됐다(2026-09-13)."""
    assert len(cm.CONFIRMED) == 21
    held = [k for k, w in cm.REVIEWED_NOT_MERGED.items() if w.startswith("보류")]
    assert sorted(held) == ["원익pne", "퀄컴"]
    assert "fadutechnology" in cm.REVIEWED_NOT_MERGED
    assert cm.REVIEWED_NOT_MERGED["fadutechnology"].startswith("다른 법인")
    assert sum(w.startswith("다른 법인") for w in cm.REVIEWED_NOT_MERGED.values()) == 42
    assert sum(w.startswith("확인 필요") for w in cm.REVIEWED_NOT_MERGED.values()) == 4
    assert len(cm.REVIEWED_NOT_MERGED) == 48


# ── 판정 경로 ──────────────────────────────────────────────

def test_자기참조_first_seen_행은_hand_로_교체한다():
    d = _plan({STUB: (STUB, "first_seen")})
    assert d.action == "write"
    assert d.canonical == CANON
    assert "first_seen" in d.reason and "교체" in d.reason


def test_행이_없으면_새로_쓴다():
    d = _plan({})
    assert d.action == "write" and d.reason == "새 행"


def test_이미_같은_hand_행이면_다시_쓰지_않는다():
    """멱등성 — 두 번 돌려도 두 번째는 write 가 아니다."""
    d = _plan({STUB: (CANON, "hand")})
    assert d.action == "already"


def test_이미_반영된_것은_stub_노드가_사라졌어도_경고가_아니다():
    """병합 뒤 다시 돌리면 stub 은 없다. 그때 「노드 없음」이 뜨면 봐도 할 게 없는 경고다."""
    d = _plan({STUB: (CANON, "hand")}, stub=False)
    assert d.action == "already"


def test_다른_hand_행이_있으면_덮지_않는다():
    d = _plan({STUB: ("다른회사", "hand")})
    assert d.action == "skip" and "손 목록 충돌" in d.reason


def test_stub_이_다른_별칭의_대표형이면_쓰지_않는다():
    """퀄컴 사례 — qualcomm → 퀄컴 이 있는데 퀄컴 → X 를 넣으면 Qualcomm 이 갈린다."""
    d = _plan({"hyundaimotorcompany": (STUB, "dart")})
    assert d.action == "skip" and "대표형" in d.reason and "hyundaimotorcompany" in d.reason


def test_대표형이_이미_별칭이면_순환이라_쓰지_않는다():
    d = _plan({CANON: ("또다른키", "dart")})
    assert d.action == "skip" and "순환" in d.reason


def test_대표형의_자기참조_행은_순환이_아니다():
    d = _plan({CANON: (CANON, "first_seen")})
    assert d.action == "write"


def test_정본_노드가_없으면_쓰지_않는다():
    stubs, _ = _graph()
    d = cm.plan(CONFIRMED, stubs, {}, cm.Registry(), {CC}, lambda _n: CC)[0]
    assert d.action == "skip" and "정본 노드 없음" in d.reason


def test_같은_corp_code_노드가_둘이면_쓰지_않는다():
    d = _plan(canon_nodes=2)
    assert d.action == "skip" and "2개" in d.reason


def test_정본_norm_name_이_stub_과_같으면_쓰지_않는다():
    d = _plan(canon_norm=STUB)
    assert d.action == "skip"


def test_corp_code_가_명부에_없으면_쓰지_않는다():
    d = _plan(master=frozenset())
    assert d.action == "skip" and "corp_code_master" in d.reason


def test_resolver_가_다른_법인으로_보면_쓰지_않는다():
    """원익피앤이 사례 — 같은 이름이 명부에 둘이라 resolver 가 다른 corp_code 를 고른다."""
    d = _plan(resolved="01020843")
    assert d.action == "skip" and "01020843" in d.reason


def test_resolver_가_해소_못_해도_쓰지_않는다():
    d = _plan(resolved=None)
    assert d.action == "skip" and "해소 못 함" in d.reason


def test_stub_노드가_없으면_보고만_한다():
    d = _plan({}, stub=False)
    assert d.action == "skip" and "stub 노드 없음" in d.reason


def test_REVIEWED_NOT_MERGED_에_있으면_CONFIRMED_라도_쓰지_않는다(monkeypatch):
    monkeypatch.setitem(cm.REVIEWED_NOT_MERGED, STUB, "다른 법인 · 테스트")
    d = _plan({})
    assert d.action == "skip" and "REVIEWED_NOT_MERGED" in d.reason


def test_관문_앞에서_걸리면_resolver_를_부르지_않는다():
    """resolver 는 DB 를 연다. 표에서 이미 걸린 것까지 물으면 느려질 뿐이다."""
    calls = []
    stubs, canon = _graph()
    cm.plan(CONFIRMED, stubs, canon, cm.Registry(rows={STUB: (CANON, "hand")}),
            {CC}, lambda n: calls.append(n) or CC)
    assert calls == []


# ── 표에 쓴 뒤 정규화가 실제로 접는가 ─────────────────────────

def test_hand_행이_들어가면_normalize_company_name_이_정본으로_접는다(monkeypatch):
    """배치가 쓰는 행 = `apply_alias` 가 읽는 사전. 이 연결이 끊기면 배치는 헛일이다.

    ★`_aliases` 는 lru_cache 라 표를 한 번만 읽는다 — 여기서는 사전을 통째로 바꿔
      배치가 쓸 22행이 들어간 상태를 흉내 낸다(`load_aliases` 는 자기참조를 뺀다).
    """
    table = {stub: canon for _, stub, canon in [
        (CC, "현대차", "현대자동차"), ("00266961", "네이버", "naver"), ("00106641", "기아차", "기아")]}
    monkeypatch.setattr(foreign_aliases, "_aliases", lambda: table)

    assert normalize_company_name("현대차") == "현대자동차"
    assert normalize_company_name("㈜현대차") == "현대자동차"      # 법인격 떼고 나서 접는다
    assert normalize_company_name("네이버") == "naver"
    assert normalize_company_name("기아차") == "기아"
    # 대표형은 그대로다 — 한 홉이라 대표형이 또 별칭이면 안 되는 이유
    assert normalize_company_name("현대자동차") == "현대자동차"
    assert normalize_company_name("기아") == "기아"


@pytest.mark.parametrize("stub", [s for _, s, _ in cm.CONFIRMED])
def test_CONFIRMED_키는_정규화해도_그대로다(monkeypatch, stub):
    """키가 `norm_name` 이 아니면 노드를 못 찾는다 — 실측에서 `lg cns`·
    `fadutechnologyincorporated` 로 적었다가 틀렸던 그 자리."""
    monkeypatch.setattr(foreign_aliases, "_aliases", lambda: {})
    assert normalize_company_name(stub) == stub
