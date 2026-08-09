"""ChromaRepository — Search Layer의 ChromaDB 접근 계층.

company/evidence 컬렉션 의미 검색과 근거 본문 조회를 담당한다. 검색
orchestration·랭킹·결과 조합은 하지 않는다(기술설계서 §7, Task 2 지침 7절).

기존 pipeline/vectorstore/base.VectorStore Protocol과
pipeline/vectorstore/chroma_store.ChromaStore를 그대로 재사용한다 — 임베딩
호출(OpenAI)과 query()가 이미 구현돼 있고, VectorStore Protocol에 의존하므로
ChromaDB 구현 세부에 종속되지 않는다(Qdrant 전환 대비 설계를 그대로 이어받음).
pipeline/importer/evidence.fetch_texts()도 그대로 재사용한다 — 배치 조회·
중복 제거·부분 실패 허용이 이미 구현돼 있다.
"""

from __future__ import annotations

from typing import Any, Optional

from pipeline.importer.evidence import fetch_texts as _fetch_texts
from pipeline.vectorstore.base import VectorStore
from pipeline.vectorstore.chroma_store import get_store

COMPANY_COLLECTION = "company"
EVIDENCE_COLLECTION = "evidence"


class ChromaRepository:
    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store or get_store()

    def search_company(
        self, query_text: str, *, n_results: int = 10,
        corp_codes: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """company 컬렉션 의미 검색. corp_codes가 주어지면 PostgreSQL이 먼저
        좁힌 후보로만 범위를 제한한다(기술설계서 §10-4 선필터 조합 패턴).
        """
        where = {"corp_code": {"$in": corp_codes}} if corp_codes else None
        return self._store.query(
            COMPANY_COLLECTION, query_text, n_results=n_results, where=where
        )

    def search_evidence(
        self, query_text: str, *, n_results: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """evidence 컬렉션 의미 검색. where는 실제 metadata 필드
        (edge_type/occurred_at/rcept_no/source_corp/subtype/target_corp) 기준.
        """
        return self._store.query(
            EVIDENCE_COLLECTION, query_text, n_results=n_results, where=where
        )

    def fetch_texts(self, evidence_ids: list[str]) -> dict[str, str]:
        """evidence_id 목록 → 본문. pipeline/importer/evidence.fetch_texts() 재사용."""
        return _fetch_texts(evidence_ids)
