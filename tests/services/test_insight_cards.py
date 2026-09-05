"""인사이트 카드의 **두 규칙**을 못 박는다 — 시점 자리 배분과 근거 열쇠.

왜 테스트로 남기나 (2026-09-04)

기존 8종 중 6종이 **구조**였다. 구조는 잘 안 바뀌어서 홈에 두면 어제와 오늘이
같다. 그래서 시점이 있는 카드 넷을 넣었다(`inbound_risk`·`event_ongoing`·
`bottleneck`·`contract_expiring`).

넣고 나서 **두 번 헛발질했다.** 둘 다 조용히 실패해서 실측으로만 드러났다:

  ① `order` 를 앞으로 당겨 시점 카드를 위로 올리려 했는데 소용이 없었다 —
     `order` 는 **3차 기준**이고 1차가 `shared` 다. 「밖에서 온 사건이 담은
     1곳에 닿았다」(shared=1)는 「4곳이 같은 곳에 판다」(shared=4)에 무조건 밀린다.
     자리를 떼어 줘야 한다.

  ② 자리를 떼어 주고 위에서 잘랐더니 **한 종류가 다 먹었다.** 로봇 워크스페이스
     실측: `inbound_risk` 3장이 두 자리를 다 가져가고 나머지 둘은 0장.
     셋 다 `shared=1` 이라 순위로는 안 갈린다.

★그리고 카드는 **눌러서 근거로 갈 수 있어야** 한다. `why` 가 숫자로 된 근거라면
  `edge_ids` 는 **원문으로 가는 열쇠**다. 관계에서 나온 카드인데 비어 있으면
  화면이 「근거 보기」를 띄우고도 아무것도 못 보여 준다.
"""

from __future__ import annotations

import pytest

from app.api.schemas import InsightCard, InsightKind
from app.services.insight_service import (_FRESH_SLOTS, _HUB_DEGREE, _card,
                                          _months_ago, _round_robin)

_FRESH = ("inbound_risk", "event_ongoing", "contract_expiring")


def _c(kind: str, name: str = "x"):
    return _card(kind, "h", "w", [name], [name], 4)


# ── ① 시점 카드 자리 배분 ────────────────────────────────────

def test_한_종류가_자리를_다_먹지_않는다():
    """실측한 실패 그대로 — inbound 3장 · 나머지 각 1장."""
    cards = ([_c("inbound_risk", f"a{i}") for i in range(3)]
             + [_c("event_ongoing")] + [_c("contract_expiring")])
    got = _round_robin(cards, _FRESH, _FRESH_SLOTS)
    assert {c["kind"] for c in got} == set(_FRESH), "세 종류가 한 장씩 올라와야 한다"


def test_한_종류만_있으면_그것으로_채운다():
    """다양성은 목표지 제약이 아니다 — 없는 종류를 기다리며 자리를 비우지 않는다."""
    cards = [_c("inbound_risk", f"a{i}") for i in range(5)]
    got = _round_robin(cards, _FRESH, _FRESH_SLOTS)
    assert len(got) == _FRESH_SLOTS
    assert all(c["kind"] == "inbound_risk" for c in got)


def test_종류_안에서는_들어온_순서를_지킨다():
    """`cards` 는 이미 정렬돼 있다. 돌아가며 뽑되 그 순서를 뒤집지 않는다."""
    cards = [_c("inbound_risk", "first"), _c("inbound_risk", "second")]
    got = _round_robin(cards, _FRESH, _FRESH_SLOTS)
    assert [c["names"][0] for c in got] == ["first", "second"]


def test_빈_목록도_터지지_않는다():
    assert _round_robin([], _FRESH, _FRESH_SLOTS) == []


# ── ② 근거로 가는 열쇠 ──────────────────────────────────────

def test_카드에_edge_ids_자리가_있다():
    """`_card` 가 기본값을 주지 않으면 카드마다 있고 없고가 갈린다."""
    assert _card("shared_customer", "h", "w", ["k"], ["n"], 2)["edge_ids"] == []


def test_스키마가_edge_ids를_싣는다():
    """`GET /relations/{edge_id}` 에 그대로 넣을 수 있어야 한다."""
    card = InsightCard(**_card("shared_customer", "h", "w", ["k"], ["n"], 2,
                               edge_ids=["4:abc:1", "4:abc:2"]))
    assert card.edge_ids == ["4:abc:1", "4:abc:2"]


@pytest.mark.parametrize("kind", ["inbound_risk", "event_ongoing",
                                  "bottleneck", "contract_expiring"])
def test_신설_카드_종류가_스키마에_있다(kind):
    """서비스가 내는 `kind` 를 스키마가 모르면 응답이 검증에서 터진다."""
    assert InsightKind(kind).value == kind


# ── ③ 날짜 셈 — 문자열 비교라 형식이 어긋나면 조용히 0건이 된다 ──

@pytest.mark.parametrize("today, months, expect", [
    ("2026-09-04", 12, "2025-09-04"),
    ("2026-09-04", -12, "2027-09-04"),   # 앞날 (만료 임박)
    ("2026-01-15", 1, "2025-12-15"),     # 해를 넘는다
    ("2026-12-15", -1, "2027-01-15"),
    ("2026-09-04", 0, "2026-09-04"),
])
def test_개월_셈이_해를_넘어도_맞는다(today, months, expect):
    assert _months_ago(today, months) == expect


# ── ④ 허브를 다리로 쓰지 않는다 ─────────────────────────────

def test_허브_상한이_실측_허브보다_낮다():
    """실측(2026-09-03) 거래 연결 수 — 삼성전자 359 · SK하이닉스 162 · LG전자 156.

    이들을 병목으로 인정하면 「거의 모든 회사에 닿는다」가 되어 카드가 무의미해진다.
    한미반도체(38)·엔비디아(34) 같은 중간 규모는 통과해야 한다.
    """
    assert _HUB_DEGREE < 156, "대형 허브는 병목에서 빠져야 한다"
    assert _HUB_DEGREE > 38, "중간 규모는 병목으로 인정해야 한다"


# ── ⑤ 조사 — 「LG이노텍로」로 나갔었다 ──────────────────────

@pytest.mark.parametrize("word, expect", [
    ("LG이노텍", "으로"),    # 받침 있음
    ("한미반도체", "로"),     # 받침 없음
    ("서울", "로"),          # ★ㄹ받침은 예외
    ("코닝", "으로"),
    ("엔비디아", "로"),
])
def test_로_으로가_받침을_본다(word, expect):
    from app.services.insight_service import _ro
    assert _ro(word) == expect


# ── ⑥ 화면이 감춘 관계를 카드가 되살리면 안 된다 ────────────

def test_카드_쿼리가_상세와_같은_조건을_쓴다():
    """★실측(2026-09-05)으로 드러난 계약 위반.

    `/relations/{edge_id}` 는 검증에서 걸렸거나 종료된 관계에 **404** 를 준다.
    그런데 인사이트 쿼리가 그 조건을 따로 적고 있어서 어긋났다 —
    카드가 404 나는 `edge_ids` 를 실어 보냈고(53개 중 5개), 더 나쁘게는
    **2024-12-31 에 끝난 거래를 「지금 납품합니다」로** 말했다.

    조건은 `_live()` 한 곳에서만 나온다. 쿼리마다 갈라 적으면 또 어긋난다.
    """
    from app.services.insight_service import (_BOTTLENECK_Q, _EXPIRY_Q, _WS_Q,
                                              _live)

    cond = _live("r")
    assert "grounding_suspect" in cond, "근거 검증에서 걸린 관계"
    assert "is_current" in cond, "종료된 관계"
    assert "valid_until" in cond, "유효기간이 지난 관계"

    # 관계를 훑는 쿼리는 셋 다 같은 세 조건을 지나야 한다
    for name, q, aliases in [("_WS_Q", _WS_Q, ["r"]),
                             ("_BOTTLENECK_Q", _BOTTLENECK_Q, ["r1", "r2"])]:
        for a in aliases:
            for key in ("grounding_suspect", "is_current", "valid_until"):
                assert f"{a}.{key}" in q, f"{name} 가 {a}.{key} 를 안 본다"
    # 만료 카드는 `valid_until` 이 **있는** 것을 찾으므로 조건이 반대다.
    # 나머지 둘은 그대로 지켜야 한다.
    for key in ("grounding_suspect", "is_current"):
        assert key in _EXPIRY_Q, f"_EXPIRY_Q 가 {key} 를 안 본다"


# ── ⑦ 경로를 말로만 두지 않는다 ─────────────────────────────

def test_파급_경로에_엣지_id가_실린다():
    """★「마이크론을 거쳐 왔다」고 해놓고 확인할 길이 없으면 계산을 못 믿는다.

    `path` 는 사람이 읽는 설명이고, `edge_ids` 는 **되짚을 수 있는 열쇠**다.
    둘이 짝을 이뤄야 화면이 「이 관계를 그래프에서 보기」를 만들 수 있다.
    실측(2026-09-05) 「미국서 담합 혐의 피소」:
        미국서 담합 혐의 피소 → IMPACTS(negative) → 마이크론
                            → SUPPLIES_TO(공급 차질) → 한미반도체
        edge_ids 2개 — IMPACTS 하나, SUPPLIES_TO 하나
    """
    from app.services.graph_service import Propagation

    p = Propagation("한미반도체", 2, 0.07,
                    ["사건", "IMPACTS(negative)", "마이크론",
                     "SUPPLIES_TO(공급 차질)", "한미반도체"],
                    stated=False, channel="supply",
                    edge_ids=["4:a:1", "4:a:2"])
    # 경로는 [노드, 관계, 노드, 관계, 노드] 꼴 — 관계는 홀수 자리다.
    legs = [seg for i, seg in enumerate(p.path) if i % 2 == 1]
    assert len(legs) == len(p.edge_ids), "관계 수와 엣지 id 수가 맞아야 짝지을 수 있다"


def test_Propagation이_edge_ids를_기본값으로_갖는다():
    """자리가 없으면 화면이 카드마다 있고 없고를 따로 다뤄야 한다."""
    from app.services.graph_service import Propagation

    assert Propagation("x", 1, 0.9, ["a", "b"], stated=True).edge_ids == []


def test_api_스키마가_edge_ids를_노출한다():
    from app.api.schemas import Propagation as P

    assert P(target="x", score=0.5, hops=2, stated=False,
             edge_ids=["4:a:1"]).edge_ids == ["4:a:1"]
