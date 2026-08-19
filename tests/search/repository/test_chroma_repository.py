"""ChromaRepository 테스트 — 실제 Docker Compose ChromaDB 데이터 대상(mock 없음).

실측 근거(2026-08-09):
  - company 컬렉션(count=2219): id="co_00126380" 삼성전자, metadata에
    corp_code/name/sector/market/stock_code/induty/has_profile 존재
  - evidence 컬렉션(count=8410): metadata keys=edge_type/occurred_at/rcept_no/
    source_corp/subtype/target_corp. source_corp="00126380"(삼성전자)로 다수 존재
    (예: DEVELOPS 5건 이상 — tv/냉장고/세탁기/에어컨/스마트폰)
  - where={"corp_code": {"$in": [...]}} 필터가 정상 동작함을 사전 확인함
"""


def test_real_db_connection(chroma_repo):
    result = chroma_repo.search_company("반도체")
    assert "ids" in result


def test_search_company_returns_results(chroma_repo):
    result = chroma_repo.search_company("반도체를 만드는 기업", n_results=5)
    assert len(result["ids"][0]) > 0


def test_search_company_with_corp_codes_filter(chroma_repo):
    """PostgreSQL 선필터링 결과(corp_code 목록)로 ChromaDB 검색 범위를 좁힌다
    (기술설계서 §10-4 조합 패턴).
    """
    result = chroma_repo.search_company(
        "기업", n_results=10, corp_codes=["00126380", "00126186"]
    )
    ids = result["ids"][0]
    assert len(ids) > 0
    assert all(cid in ("co_00126380", "co_00126186") for cid in ids)


def test_search_company_with_corp_codes_filter_excludes_others(chroma_repo):
    """필터에 없는 corp_code(예: 삼성전자만)로 좁히면 다른 회사는 나오지 않는다."""
    result = chroma_repo.search_company("기업", n_results=10, corp_codes=["00126380"])
    ids = result["ids"][0]
    assert ids == ["co_00126380"]


def test_search_evidence_returns_results(chroma_repo):
    result = chroma_repo.search_evidence("반도체 공급 계약", n_results=5)
    assert len(result["ids"][0]) > 0


def test_search_evidence_with_metadata_filter(chroma_repo):
    """evidence 컬렉션의 실제 metadata 필드(source_corp, edge_type)로 필터링."""
    result = chroma_repo.search_evidence(
        "삼성전자", n_results=5, where={"source_corp": "00126380"}
    )
    metas = result["metadatas"][0]
    assert len(metas) > 0
    assert all(m["source_corp"] == "00126380" for m in metas)


def test_fetch_texts_returns_actual_text(chroma_repo):
    """실측: source_corp=00126380인 evidence 하나(ev_356129297b375923)의 본문."""
    texts = chroma_repo.fetch_texts(["ev_356129297b375923"])
    assert "ev_356129297b375923" in texts
    assert len(texts["ev_356129297b375923"]) > 0


def test_fetch_texts_empty_list_returns_empty_dict(chroma_repo):
    assert chroma_repo.fetch_texts([]) == {}


def test_fetch_texts_unknown_id_omitted(chroma_repo):
    """존재하지 않는 evidence_id는 결과 dict에서 빠진다(예외 아님) —
    pipeline/importer/evidence.fetch_texts()의 기존 동작 그대로.
    """
    texts = chroma_repo.fetch_texts(["ev_존재하지않는가상아이디0000"])
    assert texts == {}


# ── where 필터 (A6, 2026-08-19) ──────────────────────────────────────────────
#   실측: company 컬렉션 2,430건 중 has_profile=True 는 64건.
#   나머지 2,366건은 is_stub=True 이고 문서가 이름뿐인 30자 안팎이다.


def test_search_company_with_where_filter(chroma_repo):
    """호출자가 준 where 를 그대로 적용한다 — 리포지토리는 정책을 갖지 않고
    받기만 한다(has_profile 을 걸지 말지는 VectorSearcher 가 정한다).
    """
    result = chroma_repo.search_company("기업", n_results=10, where={"has_profile": True})
    metas = result["metadatas"][0]
    assert len(metas) > 0
    assert all(m["has_profile"] is True for m in metas)


def test_search_company_merges_where_and_corp_codes(chroma_repo):
    """where 와 corp_codes 를 함께 주면 교집합이어야 한다 — 한쪽이 조용히
    덮이면 선필터가 무력화된다. 삼성전자(프로필 보유)와 삼성전자판매(stub)를
    함께 넘기면 삼성전자만 남는다(2026-08-19 실측).
    """
    result = chroma_repo.search_company(
        "기업", n_results=10, corp_codes=["00126380", "00252074"],
        where={"has_profile": True},
    )
    assert result["ids"][0] == ["co_00126380"]
