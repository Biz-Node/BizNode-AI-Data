"""key ↔ name 해소 — 설계서 §16 의 **식별 규약**을 코드로 옮긴 것.

두 방향이 있고 **서로 반대**다.

    names_of(keys)        key  → name   워크스페이스 기업을 화면·문구에 쓰려고
    find_by_names(names)  name → key    ①b 해소가 corp_code 로 실패했을 때

★`find_by_names()` 가 왜 필요한가 (2026-08-25 실측 · 현황서 §8-5)

  `TSMC`·`마이크론` 은 Neo4j 에 Company 로 있는데 `corp_code_master` 에 없다.
  그래서 `EntityResolver`(PostgreSQL)가 해소에 실패한다. 이걸 그대로 두면
  「저희 데이터에서 찾지 못했습니다」(설계서 §14-4)가 **거짓말**이 된다 —
  그래프에는 있기 때문이다.

Tier A 는 세션을 가짜로 세워 조립 규칙만 본다. Tier B 는 실 Neo4j 로 돈다 —
건수를 박지 않고 재적재로도 흔들리지 않는 불변식만 본다
(`tests/services/test_company_service_events.py` 와 같은 층 나눔).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import pytest

from app.core.database import neo4j_session
from app.services import company_service, workspace_service

_SAMSUNG = "00126380"
_HYNIX = "00164779"


def _stub_rows(monkeypatch, module, rows):
    @contextmanager
    def _session():
        class _Session:
            def run(self, query, **params):
                self.params = params
                return iter(rows)
        yield _Session()

    monkeypatch.setattr(module, "neo4j_session", _session)


# ══════════════════════════════════════════════════════════════════════
#  Tier A — names_of()  (key → name)
# ══════════════════════════════════════════════════════════════════════

def _stub_lookup(monkeypatch, found: dict, captured: Optional[list] = None):
    """★`names_of()` 는 조회를 `company_service.names_by_keys()` 에 위임한다 —
    질의를 두 벌 두면 key 판별 규약이 갈리기 때문이다. 그래서 **그 이음매를**
    가로챈다. `workspace_service.neo4j_session` 을 잡으면 위임된 조회가 실 DB 로
    새어 나가 테스트가 우연히 통과한다."""
    def _names_by_keys(keys):
        if captured is not None:
            captured.append(list(keys))
        return {k: found[k] for k in keys if k in found}

    monkeypatch.setattr(workspace_service.company_service, "names_by_keys",
                        _names_by_keys)


def test_names_of_returns_empty_for_no_keys(monkeypatch):
    """★빈 워크스페이스는 조회하지 않는다 — 부를 이유가 없다."""
    calls = []
    _stub_lookup(monkeypatch, {}, calls)
    assert workspace_service.names_of([]) == {}
    assert calls == []


def test_names_of_maps_key_to_name(monkeypatch):
    _stub_lookup(monkeypatch, {_SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"})
    assert workspace_service.names_of([_SAMSUNG, _HYNIX]) == {
        _SAMSUNG: "삼성전자", _HYNIX: "SK하이닉스"}


def test_names_of_keeps_keys_it_could_not_name(monkeypatch):
    """★못 찾은 key 를 **조용히 지우지 않는다**(설계서 §16-3). 그래프에 없는
    기업이 워크스페이스에 담겨 있을 수 있다 — 이름 없이 key 로 남긴다."""
    _stub_lookup(monkeypatch, {_SAMSUNG: "삼성전자"})
    assert workspace_service.names_of([_SAMSUNG, "99999999"]) == {
        _SAMSUNG: "삼성전자", "99999999": "99999999"}


def test_names_of_logs_the_keys_it_could_not_name(monkeypatch, caplog):
    _stub_lookup(monkeypatch, {})
    with caplog.at_level("INFO"):
        workspace_service.names_of(["99999999"])
    assert "99999999" in caplog.text


def test_names_of_deduplicates_keys_without_losing_order(monkeypatch):
    """같은 key 를 두 번 보내도 조회는 한 번이다."""
    calls = []
    _stub_lookup(monkeypatch, {_SAMSUNG: "삼성전자"}, calls)
    workspace_service.names_of([_SAMSUNG, _SAMSUNG])
    assert calls == [[_SAMSUNG]]


# ══════════════════════════════════════════════════════════════════════
#  Tier A — find_by_names()  (name → key)
# ══════════════════════════════════════════════════════════════════════

def test_find_by_names_returns_none_without_candidates(monkeypatch):
    """★후보가 없으면 **조회하지 않는다.** 빈 IN 절은 전체 스캔이 된다."""
    called = []

    @contextmanager
    def _session():
        called.append(True)
        yield None

    monkeypatch.setattr(company_service, "neo4j_session", _session)
    assert company_service.find_by_names([]) is None
    assert company_service.find_by_names(["", "   "]) is None
    assert called == []


def test_find_by_names_returns_none_when_graph_has_nothing(monkeypatch):
    _stub_rows(monkeypatch, company_service, [])
    assert company_service.find_by_names(["없는회사"]) is None


def test_find_by_names_prefers_corp_code_as_key(monkeypatch):
    """★식별 우선순위는 `corp_code` → `norm_name`(설계서 §16-1)."""
    _stub_rows(monkeypatch, company_service,
               [{"norm_name": "삼성전자", "key": _SAMSUNG, "name": "삼성전자",
                 "corp_code": _SAMSUNG}])
    assert company_service.find_by_names(["삼성전자"]) == {
        "key": _SAMSUNG, "name": "삼성전자", "corp_code": _SAMSUNG}


def test_find_by_names_falls_back_to_norm_name_as_key(monkeypatch):
    """★`corp_code` 가 없는 기업(TSMC·마이크론)은 `norm_name` 이 key 다."""
    _stub_rows(monkeypatch, company_service,
               [{"norm_name": "tsmc", "key": "tsmc", "name": "TSMC", "corp_code": None}])
    found = company_service.find_by_names(["TSMC"])
    assert found == {"key": "tsmc", "name": "TSMC", "corp_code": None}


def test_find_by_names_keeps_input_order(monkeypatch):
    """★후보가 여럿 걸리면 **먼저 준 것**이 이긴다 — 같은 질문에 매번 다른
    앵커가 잡히면 안 된다(`evidence_selector.select` 와 같은 규약)."""
    _stub_rows(monkeypatch, company_service, [
        {"norm_name": "sk하이닉스", "key": _HYNIX, "name": "SK하이닉스", "corp_code": _HYNIX},
        {"norm_name": "삼성전자", "key": _SAMSUNG, "name": "삼성전자", "corp_code": _SAMSUNG},
    ])
    assert company_service.find_by_names(["삼성전자", "SK하이닉스"])["key"] == _SAMSUNG
    assert company_service.find_by_names(["SK하이닉스", "삼성전자"])["key"] == _HYNIX


def test_find_by_names_normalizes_before_lookup(monkeypatch):
    """★`norm_name` 으로 조회한다 — 정규화를 건너뛰면 **조용히 0건**이 된다
    (설계서 §16-1)."""
    captured = {}

    @contextmanager
    def _session():
        class _Session:
            def run(self, query, **params):
                captured.update(params)
                return iter([])
        yield _Session()

    monkeypatch.setattr(company_service, "neo4j_session", _session)
    company_service.find_by_names(["TSMC"])
    assert captured["n"] == ["tsmc"]


# ══════════════════════════════════════════════════════════════════════
#  Tier B — 실 Neo4j (불변식만)
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def graph():
    try:
        with neo4j_session() as s:
            s.run("RETURN 1").single()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Neo4j 없음: {exc}")


def test_names_of_reads_real_workspace_names(graph):
    names = workspace_service.names_of([_SAMSUNG, _HYNIX])
    assert names[_SAMSUNG] == "삼성전자"
    assert names[_HYNIX] == "SK하이닉스"


def test_find_by_names_resolves_a_company_without_corp_code(graph):
    """★실측이 이 함수를 요구한 바로 그 경우(현황서 §8-5) — `corp_code_master`
    에 없어서 `EntityResolver` 가 놓치는 기업이 그래프에는 있다."""
    found = company_service.find_by_names(["TSMC"])
    assert found is not None
    assert found["corp_code"] is None
    assert found["key"] == "tsmc"


def test_find_by_names_agrees_with_names_of(graph):
    """★두 방향이 **같은 key 체계**를 쓴다. 어긋나면 앵커로 잡은 기업을
    이름으로 되돌리지 못한다."""
    found = company_service.find_by_names(["삼성전자"])
    assert workspace_service.names_of([found["key"]])[found["key"]] == found["name"]
