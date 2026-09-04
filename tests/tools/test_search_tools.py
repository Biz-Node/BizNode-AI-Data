"""근거 검색 도구 — **뉴스와 공시를 정말 갈라 보는가.**

★두 도구가 **같은 컬렉션**을 본다. 뉴스 전용도 DART 전용도 없고, 0차 백필이
  채워 둔 `source_type` metadata 로만 갈린다. 그래서 「필터가 맞는가」가
  이 도구의 전부다 — 틀리면 보도를 확정 사실로 내보내게 된다.
"""

from __future__ import annotations

import inspect

import pytest

from app.services import company_service
from app.tools import scope
from app.tools import search_tools as st
from app.tools.dto import SOURCE_NOTE, EvidenceHitDTO
from app.tools.errors import KeyNotResolved, OutOfScopeKey, ToolError

_SAMSUNG = "00126380"


def _meta(**over):
    m = {"edge_type": "SUPPLIES_TO", "subtype": "공급계약", "rcept_no": "20260608800436",
         "source_corp": "00161383", "target_corp": _SAMSUNG,
         "source_type": "news", "occurred_at": 20260608}
    m.update(over)
    return m


class FakeRepo:
    """`search_evidence` 만 흉내 낸다. **넘어온 `where` 를 그대로 붙잡아 둔다.**"""

    def __init__(self, hits=None):
        self.calls: list = []
        self._hits = hits if hits is not None else [("ev_1", "본문", _meta())]

    def search_evidence(self, query_text, *, n_results=10, where=None):
        self.calls.append({"query": query_text, "n_results": n_results, "where": where})
        ids = [h[0] for h in self._hits]
        docs = [h[1] for h in self._hits]
        metas = [h[2] for h in self._hits]
        return {"ids": [ids], "documents": [docs], "metadatas": [metas]}


@pytest.fixture
def repo(monkeypatch):
    def _install(*, resolves=True, norm="삼성전자", hits=None):
        fake = FakeRepo(hits)
        monkeypatch.setattr(st, "_repo", lambda: fake)
        # ★key 해소는 `app/tools/keys.py` 가 한다 — 서비스 모듈을 직접 갈아끼운다.
        monkeypatch.setattr(company_service, "norm_names_by_keys",
                            lambda keys: {k: norm for k in keys} if resolves else {})
        return fake
    return _install


def _source_types(where: dict) -> list[str]:
    for clause in where["$and"]:
        if "source_type" in clause:
            return clause["source_type"]["$in"]
    raise AssertionError(f"source_type 절이 없다: {where}")


def _key_forms(where: dict) -> set[str]:
    for clause in where["$and"]:
        if "$or" in clause:
            got: set[str] = set()
            for side in clause["$or"]:
                got |= set(next(iter(side.values()))["$in"])
            return got
    raise AssertionError(f"기업 절이 없다: {where}")


# ══════════════════════════════════════════════════════════════════
#  ★출처 필터 — 이것이 이 도구의 전부다
# ══════════════════════════════════════════════════════════════════

def test_search_news_asks_only_for_news(repo):
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        st.search_news("반도체", [_SAMSUNG])
    assert _source_types(fake.calls[0]["where"]) == ["news"]


def test_search_dart_asks_for_both_filing_kinds(repo):
    """★정기공시(`dart`)와 개별공시(`dart_filing`) 둘 다 확정 사실이다."""
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        st.search_dart("공급계약", [_SAMSUNG])
    assert sorted(_source_types(fake.calls[0]["where"])) == ["dart", "dart_filing"]


def test_the_two_tools_never_overlap(repo):
    """★한쪽에 잡힌 출처가 다른 쪽에 잡히면 보도가 확정 사실로 새어 나간다."""
    assert not set(st.NEWS_TYPES) & set(st.DART_TYPES)


def test_chunks_without_source_type_are_reached_by_neither(repo):
    """★실측 168건이 `source_type` 없이 남아 있다. 0차 백필이 **추측하지 않고**
    남긴 것이라 여기서 뒤집지 않는다 — `$in` 필터라 구조적으로 제외된다."""
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        st.search_news("x", [_SAMSUNG])
        st.search_dart("x", [_SAMSUNG])
    for call in fake.calls:
        assert "$in" in next(c for c in call["where"]["$and"] if "source_type" in c)["source_type"]


# ══════════════════════════════════════════════════════════════════
#  ★기업 필터 — 메타에 두 형태가 섞여 있다
# ══════════════════════════════════════════════════════════════════

def test_both_key_forms_go_into_the_filter(repo):
    """★메타의 `source_corp` 는 `corp_code`(`00161383`)와 `norm_name`(`c.o.k`)이
    섞여 있다. 한 형태로만 거르면 그 기업의 근거 절반이 조용히 사라진다."""
    fake = repo(norm="삼성전자")
    with scope.anchor_scope([_SAMSUNG]):
        st.search_news("반도체", [_SAMSUNG])
    assert _key_forms(fake.calls[0]["where"]) == {_SAMSUNG, "삼성전자"}


def test_filter_looks_at_both_ends_of_the_edge(repo):
    """★기업이 근거의 출발점일 수도 도착점일 수도 있다."""
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        st.search_news("반도체", [_SAMSUNG])
    clause = next(c for c in fake.calls[0]["where"]["$and"] if "$or" in c)
    assert {next(iter(side)) for side in clause["$or"]} == {"source_corp", "target_corp"}


# ══════════════════════════════════════════════════════════════════
#  ① 범위 · ④ 실패와 빈 결과
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_refuses_a_key_outside_the_scope(repo, fn):
    repo()
    with scope.anchor_scope(["00164779"]):
        with pytest.raises(OutOfScopeKey):
            fn("반도체", [_SAMSUNG])


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_refuses_when_no_scope_is_set(repo, fn):
    repo()
    with pytest.raises(OutOfScopeKey):
        fn("반도체", [_SAMSUNG])


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_unresolved_key_is_an_error_not_an_empty_result(repo, fn):
    repo(resolves=False)
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(KeyNotResolved):
            fn("반도체", [_SAMSUNG])


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_an_empty_query_is_an_error_not_an_empty_result(repo, fn):
    """★검색어가 비어 있는 것은 **부르는 쪽이 틀린 것**이지 「없다」가 아니다."""
    repo()
    with scope.anchor_scope([_SAMSUNG]):
        with pytest.raises(ToolError):
            fn("   ", [_SAMSUNG])


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_no_hits_is_an_empty_list(repo, fn):
    repo(hits=[])
    with scope.anchor_scope([_SAMSUNG]):
        assert fn("없는 이야기", [_SAMSUNG]) == []


def test_no_keys_returns_empty_without_touching_chroma(repo):
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        assert st.search_news("반도체", []) == []
    assert fake.calls == []


# ══════════════════════════════════════════════════════════════════
#  ② 표기 · ③ 상한은 내부 상수
# ══════════════════════════════════════════════════════════════════

def test_returns_dtos_with_the_source_note_attached(repo):
    repo(hits=[("ev_1", " 본문 ", _meta(source_type="dart"))])
    with scope.anchor_scope([_SAMSUNG]):
        got = st.search_dart("공급", [_SAMSUNG])
    assert isinstance(got[0], EvidenceHitDTO)
    assert got[0].source_note == SOURCE_NOTE["dart"]
    assert got[0].text == "본문", "앞뒤 공백은 떼고 준다"


def test_news_hits_report_no_rcept_no(repo):
    """★뉴스 근거의 `rcept_no` 는 빈 문자열이다. `""` 를 그대로 주면 LLM 이
    「접수번호가 있는데 비어 있다」로 읽는다 — 없는 것은 `None` 이다."""
    repo(hits=[("ev_1", "본문", _meta(rcept_no=""))])
    with scope.anchor_scope([_SAMSUNG]):
        assert st.search_news("x", [_SAMSUNG])[0].rcept_no is None


def test_occurred_at_zero_means_unknown_not_year_zero(repo):
    """★적재 때 시점을 못 뽑은 근거의 `occurred_at` 은 `0` 이다."""
    repo(hits=[("ev_1", "본문", _meta(occurred_at=0))])
    with scope.anchor_scope([_SAMSUNG]):
        assert st.search_news("x", [_SAMSUNG])[0].occurred_at is None


def test_occurred_at_is_rendered_as_an_iso_date(repo):
    repo(hits=[("ev_1", "본문", _meta(occurred_at=20260608))])
    with scope.anchor_scope([_SAMSUNG]):
        assert st.search_news("x", [_SAMSUNG])[0].occurred_at == "2026-06-08"


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_no_tool_takes_a_limit_argument(fn):
    params = set(inspect.signature(fn).parameters)
    assert not (params & {"limit", "n_results", "top_k", "max_results"}), params


@pytest.mark.parametrize("fn", [st.search_news, st.search_dart])
def test_the_hit_limit_is_an_internal_constant(repo, fn):
    fake = repo()
    with scope.anchor_scope([_SAMSUNG]):
        fn("반도체", [_SAMSUNG])
    assert fake.calls[0]["n_results"] == st._MAX_HITS


def test_hit_dto_has_no_citation_fields():
    """★`source_doc`·언론사·보도일은 마감 단계(`evidence_for_ids`)가 만든다.
    도구가 따로 지으면 같은 사실이 두 곳에서 만들어지고, 두 벌은 반드시 갈린다."""
    fields = set(EvidenceHitDTO.model_fields)
    assert not (fields & {"source_doc", "press", "published_at"}), fields
    assert "evidence_id" in fields, "이어 붙일 열쇠는 있어야 한다"
