"""`company_service.business_overview_of()` — 사업보고서 「사업의 내용」 원문.

★왜 별도 함수인가 — `company_profiles` 는 **우리가 쓴 요약**이라 인용하면
  우리 문장을 근거로 삼는 셈이 된다. `business_overview` 는 사업보고서 본문
  그대로라 챗봇이 인용할 수 있는 유일한 재무계 텍스트다.

★`source_doc`(DART 접수번호)이 **반드시 실려야** 한다. 나중에 이 텍스트를
  `Evidence` 로 승격할 때 되짚을 출처가 그것뿐이다. 지금은 승격하지 않는다.

Tier A 는 DB 를 가짜로 세워 조립 규칙만 본다. Tier B 는 실 DB 로 돌며
건수를 박지 않고 **재적재로도 흔들리지 않는 불변식**만 검사한다.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.core.database import postgres_connection
from app.services import company_service

_SAMSUNG = "00126380"
_FIELDS = {"corp_code", "bsns_year", "overview_text", "products_text", "source_doc"}


# ══════════════════════════════════════════════════════════════════════
#  Tier A — 가짜 DB (조립 규칙)
# ══════════════════════════════════════════════════════════════════════

def _stub(monkeypatch, *, node, row):
    """Neo4j 노드 하나와 PostgreSQL 한 줄을 세운다. 실행된 SQL 도 받아 둔다."""
    seen: dict = {}

    @contextmanager
    def _neo():
        class _S:
            def run(self, query, **params):
                class _R:
                    def single(self_inner):
                        return {"p": node} if node else None
                return _R()
        yield _S()

    @contextmanager
    def _pg():
        class _Cur:
            def execute(self, sql, params=None):
                seen["sql"], seen["params"] = sql, params
            def fetchone(self):
                return row
            def __enter__(self): return self
            def __exit__(self, *a): return False
        class _Conn:
            def cursor(self): return _Cur()
        yield _Conn()

    monkeypatch.setattr(company_service, "neo4j_session", _neo)
    monkeypatch.setattr(company_service, "postgres_connection", _pg)
    return seen


def test_returns_all_five_fields(monkeypatch):
    """★다섯 필드를 **전부** 담는다 — 특히 `source_doc`."""
    _stub(monkeypatch, node={"corp_code": _SAMSUNG},
          row=("00126380", 2025, "사업의 개요…", "주요 제품…", "20260310002820"))

    got = company_service.business_overview_of(_SAMSUNG)

    assert set(got) == _FIELDS
    assert got == {"corp_code": "00126380", "bsns_year": 2025,
                   "overview_text": "사업의 개요…", "products_text": "주요 제품…",
                   "source_doc": "20260310002820"}


def test_fixed_width_padding_is_stripped(monkeypatch):
    """`character(8)`·`character(14)` 는 고정폭이라 공백이 붙어 나올 수 있다.
    그대로 두면 `corp_code` 비교가 조용히 어긋난다."""
    _stub(monkeypatch, node={"corp_code": _SAMSUNG},
          row=("00126380  ", 2025, "t", None, "20260310002820  "))

    got = company_service.business_overview_of(_SAMSUNG)

    assert got["corp_code"] == "00126380"
    assert got["source_doc"] == "20260310002820"


def test_missing_products_text_stays_none(monkeypatch):
    """★`products_text` 가 없는 행이 실재한다(실측 64행 중 1행).
    빈 문자열로 바꾸면 「제품 설명이 있다」로 읽힌다 — `None` 으로 둔다."""
    _stub(monkeypatch, node={"corp_code": _SAMSUNG},
          row=("00126380", 2025, "t", None, "20260310002820"))

    assert company_service.business_overview_of(_SAMSUNG)["products_text"] is None


def test_year_none_takes_the_latest(monkeypatch):
    seen = _stub(monkeypatch, node={"corp_code": _SAMSUNG},
                 row=("00126380", 2025, "t", "p", "d"))

    company_service.business_overview_of(_SAMSUNG)

    assert "ORDER BY bsns_year DESC LIMIT 1" in seen["sql"]
    assert seen["params"] == (_SAMSUNG,)


def test_year_given_pins_that_year(monkeypatch):
    seen = _stub(monkeypatch, node={"corp_code": _SAMSUNG},
                 row=("00126380", 2025, "t", "p", "d"))

    company_service.business_overview_of(_SAMSUNG, year=2025)

    assert "bsns_year = %s" in seen["sql"]
    assert "ORDER BY" not in seen["sql"]
    assert seen["params"] == (_SAMSUNG, 2025)


def test_unknown_company_is_none(monkeypatch):
    """★없는 기업에 빈 dict 를 주면 「자료가 있는데 비었다」로 읽힌다."""
    _stub(monkeypatch, node=None, row=None)

    assert company_service.business_overview_of("없는키") is None


def test_company_without_a_corp_code_is_none(monkeypatch):
    """그래프에는 있지만 `corp_code` 가 없는 노드(외국 기업 등)."""
    _stub(monkeypatch, node={"norm_name": "tsmc"}, row=None)

    assert company_service.business_overview_of("tsmc") is None


def test_no_row_for_that_year_is_none(monkeypatch):
    _stub(monkeypatch, node={"corp_code": _SAMSUNG}, row=None)

    assert company_service.business_overview_of(_SAMSUNG, year=1999) is None


# ══════════════════════════════════════════════════════════════════════
#  Tier B — 실 DB (불변식만)
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def live_rows():
    try:
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT corp_code, bsns_year FROM business_overview")
            return cur.fetchall()
    except Exception as exc:                                   # DB 미기동
        pytest.skip(f"PostgreSQL 없음: {exc}")


def test_live_lookup_by_corp_code_and_by_name_agree(live_rows):
    """`key` 는 `corp_code` 든 `norm_name` 이든 같은 것을 가리켜야 한다."""
    if not any(c.strip() == _SAMSUNG for c, _ in live_rows):
        pytest.skip("business_overview 에 삼성전자 행이 없음")

    by_code = company_service.business_overview_of(_SAMSUNG)
    by_name = company_service.business_overview_of("삼성전자")

    assert by_code is not None
    assert by_code == by_name


def test_live_source_doc_is_a_dart_rcept_no(live_rows):
    """★`source_doc` 이 되짚을 수 있는 값이어야 근거로 승격할 수 있다.
    DART 접수번호는 숫자 14자리다."""
    for corp_code, _ in live_rows[:10]:
        got = company_service.business_overview_of(corp_code.strip())
        assert got is not None
        doc = got["source_doc"]
        assert doc and doc.isdigit() and len(doc) == 14, (corp_code, doc)
