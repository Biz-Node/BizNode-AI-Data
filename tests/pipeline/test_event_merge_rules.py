"""merge 규칙의 두 방어선 — **주체 기업**과 **되풀이 주기**.

왜 테스트로 못 박나 (2026-08-29)

이 두 검사는 없어도 파이프라인이 조용히 돌아간다. 그래서 한 번 빠지면 아무도
모른다. 실제로 두 군데 다 빠져 있었고, 실측으로만 드러났다:

    주체가 둘 이상인 Event      66건   「자사주 소각」 = 한미반도체 + NAVER + 삼성전자
    시점폭 12개월 이상 Event    75건   2022년 파업과 2026년 파업이 한 노드

`batch/repair/event_merge.py`는 후보를 만들기 전에, `pipeline/importer/event_er.py`는
병합 직전에 거부한다. 둘의 판단이 어긋나면 한쪽이 합친 것을 다른 쪽이 못 가른다.

★거부는 **양쪽 다 주체를 알 때만** 한다. 한쪽이 비어 있으면 판단할 근거가 없는
  것이지 다르다는 뜻이 아니다 — 여기서 막으면 stub 기업이 붙은 사건이 영영
  안 합쳐진다.
"""

from __future__ import annotations

import pytest

from batch.repair.event_merge import (_RECURRENT_MONTH_CAP, _blocked, _is_recurrent,
                                      _label, _months_apart)
from pipeline.importer.event_er import _subject_conflict


def _ev(name, subs, period="2026-01", *extra_periods):
    """`months`는 그 사건이 걸쳐 있는 달 전부다 — 막는 기준이 **합친 뒤의 폭**이라
    이미 넓은 노드는 가장 이른 달만으로는 판단할 수 없다."""
    months = [int(p[:4]) * 12 + int(p[5:7]) for p in (period, *extra_periods)]
    return {"name": name, "subs": subs, "subjects": subs, "period": period,
            "months": months, "corps": subs}


# ── 주체 기업 ────────────────────────────────────────────────

@pytest.mark.parametrize("a_subs, b_subs, conflict", [
    (["한미반도체"], ["naver"],             True),   # 각자 자기 자사주를 소각
    (["삼성전자"],   ["삼성전자"],           False),  # 같은 기업
    (["삼성전자"],   ["삼성전자", "sk하이닉스"], False),  # 하나라도 겹치면 통과
    ([],            ["naver"],             False),  # 한쪽이 비면 판단하지 않는다
    (["한미반도체"], [],                    False),
    ([],            [],                    False),
])
def test_주체가_어긋날_때만_거부한다(a_subs, b_subs, conflict):
    a, b = _ev("자사주 소각", a_subs), _ev("자사주 소각", b_subs)
    assert _subject_conflict(a, b) is conflict
    assert bool(_blocked(a, b)) is conflict


def test_두_구현이_같은_판단을_한다():
    """적재 단계와 보정 단계가 어긋나면 한쪽이 합친 것을 다른 쪽이 못 가른다."""
    a, b = _ev("자사주 소각", ["한미반도체"]), _ev("자사주 소각", ["naver"])
    assert _subject_conflict(a, b) and _blocked(a, b).startswith("주체")


# ── 되풀이 주기 ──────────────────────────────────────────────

@pytest.mark.parametrize("name, recurrent", [
    ("중대재해 사망사고", True),
    ("노조 총파업", True),
    ("임단협 교섭 난항", True),
    ("과징금 부과", True),
    ("3분기 실적 발표", True),
    ("세종 신사옥 준공", False),      # 착공→준공은 한 프로젝트
    ("HBM4 양산 개시", False),
    ("레인보우로보틱스 인수", False),
])
def test_해를_넘겨_되풀이되는_유형을_가려낸다(name, recurrent):
    assert _is_recurrent(name) is recurrent


@pytest.mark.parametrize("gap_months, blocked", [
    (0, False),
    (6, False),    # 한 해 안이면 같은 쟁의의 국면일 수 있다
    (11, False),
    (12, True),    # 해를 넘기면 「올해 또 난 것」
    (48, True),
])
def test_되풀이형은_1년_넘게_벌어지면_막는다(gap_months, blocked):
    a = _ev("노조 파업", ["삼성전자"], "2022-01")
    y, m = divmod(gap_months, 12)
    b = _ev("노조 파업", ["삼성전자"], f"{2022 + y:04d}-{m + 1:02d}")
    assert bool(_blocked(a, b)) is blocked


def test_이미_넓은_노드가_옛_사건을_더_빨아들이지_못한다():
    """★가장 이른 달끼리 비교하면 이 경우가 뚫린다.

    「삼성전자 노조 파업」이 2022~2024에 걸쳐 있으면 가장 이른 달은 2022-01이라
    2022년 사건과 gap 0으로 보인다. 그래서 흡수하고 폭이 더 벌어졌다(실측 50개월).
    판단해야 할 것은 **합치면 얼마나 벌어지나**다.
    """
    wide = _ev("삼성전자 노조 파업", ["삼성전자"], "2022-01", "2024-06")
    old = _ev("2022년 파업 위기", ["삼성전자"], "2022-03")
    assert _months_apart(wide["period"], old["period"]) < _RECURRENT_MONTH_CAP
    assert _blocked(wide, old).startswith("되풀이형")


def test_장기전개형은_멀어도_막지_않는다():
    """착공→준공은 4년이 걸려도 한 사건이다."""
    a = _ev("세종 신사옥 착공", ["레인보우로보틱스"], "2022-03")
    b = _ev("세종 신사옥 준공", ["레인보우로보틱스"], "2026-03")
    assert _blocked(a, b) == ""


# ── 모델에 보여 주는 것 ──────────────────────────────────────

def test_프롬프트에_주체와_연월이_들어간다():
    """이름만 넘기던 것이 기업 혼재·반복 융합의 원인이었다."""
    line = _label(_ev("자사주 소각", ["한미반도체"], "2026-02"))
    assert "자사주 소각" in line and "한미반도체" in line and "2026-02" in line
