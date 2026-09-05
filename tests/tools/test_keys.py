"""`app/tools/keys.py` — key 해소. ★**정본으로 되짚는다.**

★이 파일은 결함 하나 때문에 생겼다(2026-09-05 · 현황서 §6-0 A-5).
  `norm_names_by_keys()` 는 `corp_code` 와 `norm_name` 을 **둘 다** 매치하지만
  돌려주는 key 는 `corp_code` 를 우선한 **정본 하나**다. 그런데 `resolved()` 가
  **물어본 key** 로 `in found` 를 보고 있어서, 그래프가 찾은 기업을 「못 찾은
  key」로 거부했다 — `corp_code` 가 **있는** 기업을 이름으로 부를 때만 걸려서
  오래 안 보였다.

★그 문구는 Agent 가 읽고 다음 호출을 정하는 값이다(도구 4원칙 ④). 그래서 여기서
  보는 것은 「거부하느냐」가 아니라 **「거부의 이유를 맞게 말하느냐」**다.
"""

from __future__ import annotations

import pytest

from app.tools import keys as keys_module
from app.tools import scope
from app.tools.errors import KeyNotResolved, OutOfScopeKey

_SAMSUNG = "00126380"
_NAME = "삼성전자"


@pytest.fixture
def graph(monkeypatch):
    """그래프가 아는 것을 정한다 — 정본 key → `norm_name`."""
    def _install(table: dict[str, str]):
        def _lookup(asked):
            by_norm = {norm: key for key, norm in table.items()}
            hit = {}
            for k in asked:
                key = k if k in table else by_norm.get(k)
                if key:
                    hit[key] = table[key]
            return hit
        monkeypatch.setattr(keys_module.company_service,
                            "norm_names_by_keys", _lookup)
    return _install


# ══════════════════════════════════════════════════════════════════
#  ★정본 — 같은 기업을 어느 형태로 불러도 같은 답
# ══════════════════════════════════════════════════════════════════


def test_a_corp_code_resolves_to_itself(graph):
    """★기존 동작. 이 줄이 깨지면 고친 게 아니라 부순 것이다."""
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_SAMSUNG]):
        assert keys_module.resolved([_SAMSUNG]) == ([_SAMSUNG], {_SAMSUNG: _NAME})


def test_a_norm_name_resolves_to_the_corp_code(graph):
    """★회귀 그물. 되돌리면 여기서 `KeyNotResolved` 로 깨진다."""
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_NAME]):
        assert keys_module.resolved([_NAME]) == ([_SAMSUNG], {_SAMSUNG: _NAME})


def test_a_company_without_a_corp_code_keeps_its_name_as_the_key(graph):
    """★`corp_code` 가 없는 Company 가 그래프에 2,277곳이다 — 정본이 곧 이름이다."""
    graph({"tsmc": "tsmc"})
    with scope.anchor_scope(["tsmc"]):
        assert keys_module.resolved(["tsmc"]) == (["tsmc"], {"tsmc": "tsmc"})


def test_both_forms_of_one_company_collapse_to_one(graph):
    """★두 형태로 부르면 재료를 **두 번** 모으게 된다."""
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_SAMSUNG, _NAME]):
        canonical, _ = keys_module.resolved([_SAMSUNG, _NAME])
    assert canonical == [_SAMSUNG]


def test_the_order_asked_is_kept(graph):
    graph({_SAMSUNG: _NAME, "00164779": "SK하이닉스"})
    with scope.anchor_scope(["00164779", _NAME]):
        canonical, _ = keys_module.resolved(["00164779", _NAME])
    assert canonical == ["00164779", _SAMSUNG]


# ══════════════════════════════════════════════════════════════════
#  ★부르는 쪽과의 짝 — `found[k]` 가 성립해야 한다
# ══════════════════════════════════════════════════════════════════


def test_every_returned_key_can_be_read_from_the_map(graph):
    """★도구 셋이 전부 `for k in wanted: found[k]` 로 읽는다. 둘의 key 공간이
    갈리면 `KeyError` 로 죽는다 — 거부보다 나쁘다."""
    graph({_SAMSUNG: _NAME, "tsmc": "tsmc"})
    with scope.anchor_scope([_NAME, "tsmc"]):
        canonical, found = keys_module.resolved([_NAME, "tsmc"])
    assert [found[k] for k in canonical] == [_NAME, "tsmc"]


# ══════════════════════════════════════════════════════════════════
#  ★실패는 그대로 실패다 — 느슨해지지 않았다
# ══════════════════════════════════════════════════════════════════


def test_a_key_the_graph_does_not_know_is_still_a_failure(graph):
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope(["없는회사"]):
        with pytest.raises(KeyNotResolved, match="없는회사"):
            keys_module.resolved(["없는회사"])


def test_one_bad_key_among_good_ones_still_fails(graph):
    """★조용히 거르지 않는다 — 거르면 「그 기업은 재료가 없었다」로 읽힌다."""
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_NAME, "없는회사"]):
        with pytest.raises(KeyNotResolved, match="없는회사"):
            keys_module.resolved([_NAME, "없는회사"])


def test_out_of_scope_is_judged_before_the_graph(graph):
    """★범위 검사가 먼저다. 그래프가 아는 이름이어도 범위 밖이면 거부한다."""
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(OutOfScopeKey):
            keys_module.resolved(["00164779"])


def test_an_empty_input_is_not_a_failure(graph):
    graph({_SAMSUNG: _NAME})
    with scope.anchor_scope([_SAMSUNG]):
        assert keys_module.resolved([]) == ([], {})
