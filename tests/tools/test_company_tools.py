"""기업 사실 도구 — **4원칙 + 계산값에 근거 id 를 붙이지 않는가.**

① 기업명이 아니라 key 만 받고, 범위 밖은 거부한다
② 표기가 끝난 DTO 를 돌려준다
③ `limit` 을 인자로 받지 않는다
④ 빈 결과와 실패를 구별한다
"""

from __future__ import annotations

import inspect

import pytest

from app.tools import company_tools as ct
from app.tools import scope
from app.tools.dto import (PER_NOTE_LOSS, PER_NOTE_NO_FINANCIALS,
                           BusinessOverviewDTO, FilingDTO, MarketDTO)
from app.tools.errors import KeyNotResolved, OutOfScopeKey

_SAMSUNG = "00126380"

_OVERVIEW = {"corp_code": _SAMSUNG, "bsns_year": 2025,
             "overview_text": "당사는 반도체를 만든다", "products_text": None,
             "source_doc": "20260310002820"}
_FILING = {"rcept_no": "20260310002820", "doc_type": "사업보고서",
           "title": "사업보고서 (2025.12)", "rcept_dt": "2026-03-10",
           "url": "https://dart.fss.or.kr/x"}


def _market_row(**over):
    row = {"trade_date": "2026-08-14", "close_price": 71800, "market_cap": 428600000000,
           "per": 35.39, "pbr": 1.18, "psr": 1.62, "fin_year": 2025, "fs_div": "CFS"}
    row.update(over)
    return row


@pytest.fixture
def stub(monkeypatch):
    """DB 없이 도구만 본다."""
    def _install(*, resolves=True, corp_code=_SAMSUNG, overview=_OVERVIEW,
                 filings=(_FILING,), market=None):
        monkeypatch.setattr(ct.company_service, "norm_names_by_keys",
                            lambda keys: {k: k for k in keys} if resolves else {})
        monkeypatch.setattr(ct.company_service, "corp_codes_by_keys",
                            lambda keys: {k: corp_code for k in keys} if corp_code else {})
        monkeypatch.setattr(ct.company_service, "business_overview_of",
                            lambda key, *, year=None: overview)
        monkeypatch.setattr(ct.company_service, "filings_of",
                            lambda key, limit=20: list(filings))
        monkeypatch.setattr(ct.company_service, "market_of",
                            lambda key: market or {"latest": None,
                                                   "unavailable_reason": "not_collected"})
    return _install


# ══════════════════════════════════════════════════════════════════
#  ① 범위 — 인자가 아니라 스코프가 정한다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("call", [
    lambda: ct.get_business_overview(_SAMSUNG),
    lambda: ct.get_filings(_SAMSUNG),
    lambda: ct.get_market(_SAMSUNG),
])
def test_every_tool_refuses_a_key_outside_the_scope(stub, call):
    stub()
    with scope.anchor_scope(["00164779"]):        # 다른 기업만 허용
        with pytest.raises(OutOfScopeKey):
            call()


@pytest.mark.parametrize("call", [
    lambda: ct.get_business_overview(_SAMSUNG),
    lambda: ct.get_filings(_SAMSUNG),
    lambda: ct.get_market(_SAMSUNG),
])
def test_every_tool_refuses_when_no_scope_is_set(stub, call):
    """★스코프가 **안 세워진 것**과 비어 있는 것은 다르다."""
    stub()
    with pytest.raises(OutOfScopeKey):
        call()


# ══════════════════════════════════════════════════════════════════
#  ④ 해소 실패와 「정말 없다」를 가른다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("call", [
    lambda: ct.get_business_overview(_SAMSUNG),
    lambda: ct.get_filings(_SAMSUNG),
    lambda: ct.get_market(_SAMSUNG),
])
def test_unresolved_key_is_an_error_not_an_empty_result(stub, call):
    """★0건으로 돌려주면 「자료가 없는 기업」으로 읽힌다."""
    stub(resolves=False)
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(KeyNotResolved):
            call()


def test_missing_overview_is_none_not_an_error(stub):
    """★입력은 맞고 정말로 없다 — 64개사에만 있다."""
    stub(overview=None)
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_business_overview(_SAMSUNG) is None


def test_no_filings_is_an_empty_list(stub):
    stub(filings=())
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_filings(_SAMSUNG) == []


# ══════════════════════════════════════════════════════════════════
#  ② 표기가 끝난 DTO
# ══════════════════════════════════════════════════════════════════

def test_tools_return_dtos_not_raw_rows(stub):
    stub(market={"latest": _market_row(), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        assert isinstance(ct.get_business_overview(_SAMSUNG), BusinessOverviewDTO)
        assert all(isinstance(f, FilingDTO) for f in ct.get_filings(_SAMSUNG))
        assert isinstance(ct.get_market(_SAMSUNG), MarketDTO)


def test_business_overview_keeps_source_doc(stub):
    """★`source_doc` 은 되짚을 수 있는 값이다 — 없으면 인용도 승격도 못 한다."""
    stub()
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_business_overview(_SAMSUNG).source_doc == "20260310002820"


def test_business_overview_passes_the_year_through(stub, monkeypatch):
    seen: dict = {}

    def _spy(key, *, year=None):
        seen["year"] = year
        return _OVERVIEW

    stub()
    monkeypatch.setattr(ct.company_service, "business_overview_of", _spy)
    with scope.anchor_scope([_SAMSUNG]):
        ct.get_business_overview(_SAMSUNG, year=2024)
    assert seen["year"] == 2024


# ══════════════════════════════════════════════════════════════════
#  ③ `limit` 은 인자가 아니다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("fn", [ct.get_business_overview, ct.get_filings, ct.get_market])
def test_no_tool_takes_a_limit_argument(fn):
    """★부르는 쪽이 LLM 이 되면 상한이 협상 대상이 된다."""
    params = set(inspect.signature(fn).parameters)
    assert not (params & {"limit", "n_results", "top_k", "max_results"}), params


def test_filings_limit_is_an_internal_constant(stub, monkeypatch):
    seen: dict = {}
    stub()
    monkeypatch.setattr(ct.company_service, "filings_of",
                        lambda key, limit=20: seen.setdefault("limit", limit) and [])
    with scope.anchor_scope([_SAMSUNG]):
        ct.get_filings(_SAMSUNG)
    assert seen["limit"] == ct._MAX_FILINGS


# ══════════════════════════════════════════════════════════════════
#  ★시장 — **계산값이라 근거 id 를 붙이지 않는다**
# ══════════════════════════════════════════════════════════════════

def test_market_dto_has_no_evidence_id():
    """★계산값에 근거 id 를 발급하면 원본이 갱신될 때 id 가 가리키는 값과
    실제 값이 어긋난다 — `ratio_change` 를 제거했던 것과 같은 실수다."""
    assert "evidence_id" not in MarketDTO.model_fields
    assert "evidence_ids" not in MarketDTO.model_fields


def test_market_carries_the_calculation_coordinates(stub):
    """★근거 id 대신 **어느 날 시세를 · 어느 해 재무로** 나눴는지를 담는다."""
    stub(market={"latest": _market_row(), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        got = ct.get_market(_SAMSUNG)
    assert (got.trade_date, got.fin_year, got.fs_div) == ("2026-08-14", 2025, "CFS")


def test_per_note_tells_loss_apart_from_missing_financials(stub):
    """★`per: null` 의 이유가 둘이라 뭉뚱그리면 안 된다."""
    stub(market={"latest": _market_row(per=None, fin_year=2025), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_market(_SAMSUNG).per_note == PER_NOTE_LOSS

    stub(market={"latest": _market_row(per=None, fin_year=None), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_market(_SAMSUNG).per_note == PER_NOTE_NO_FINANCIALS


def test_per_note_is_none_when_per_exists(stub):
    stub(market={"latest": _market_row(per=35.39), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_market(_SAMSUNG).per_note is None


def test_market_is_none_for_a_company_without_corp_code(stub):
    """★해외 기업 — `corp_code` 가 DART 값이라 없다. 시세가 원리적으로 없다."""
    stub(corp_code=None, market={"latest": _market_row(), "unavailable_reason": None})
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_market(_SAMSUNG) is None


def test_market_is_none_when_metrics_are_missing(stub):
    """★상장인데 지표를 못 만든 경우 — 오류가 아니다."""
    stub(market={"latest": None, "unavailable_reason": "unreliable_shares"})
    with scope.anchor_scope([_SAMSUNG]):
        assert ct.get_market(_SAMSUNG) is None


# ══════════════════════════════════════════════════════════════════
#  ★도구 인자에서 **빼야 하는** 것 — 조용한 오답을 만든다
# ══════════════════════════════════════════════════════════════════

def _all_new_tools():
    from app.tools import search_tools as st
    return [ct.get_business_overview, ct.get_filings, ct.get_market,
            st.search_news, st.search_dart]


@pytest.mark.parametrize("fn", _all_new_tools())
def test_no_tool_exposes_an_entity_kind_filter(fn):
    """★해외가 **9곳뿐**이고 엔비디아·TSMC·인텔·ASML 이 전부 `기업` 으로 박혀
    있다. Agent 가 「해외 공급사만」으로 필터하면 **조용히 0건**이 나온다 —
    탐지 불가능한 오답이다."""
    params = set(inspect.signature(fn).parameters)
    assert "entity_kind" not in params, params


@pytest.mark.parametrize("fn", _all_new_tools())
def test_no_tool_exposes_a_market_filter(fn):
    """★`market` 은 DART 가 출처라 **해외 시장 값이 없다.** 비어 있다고 해외가
    아닌데, 인자로 두면 Agent 가 그걸로 국내/해외를 가르게 된다."""
    params = set(inspect.signature(fn).parameters)
    assert not (params & {"market", "markets", "is_foreign", "domestic"}), params
