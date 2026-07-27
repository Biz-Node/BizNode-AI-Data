"""Vector store 어댑터 인터페이스 (ERD §7).

ChromaDB → Qdrant 전환 대비 추상화. 구현체(chroma_store)는 이 계약만 따른다.
evidence·profile·event 컬렉션을 다룬다.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class VectorStore(Protocol):
    def upsert(
        self,
        collection: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """문서를 임베딩해 upsert. id 중복 시 덮어쓴다(멱등)."""
        ...

    def get(self, collection: str, ids: list[str]) -> dict[str, Any]:
        """id로 직접 조회 (팩트체크 — 벡터 미사용)."""
        ...

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """의미 검색 + 메타데이터 필터."""
        ...

    def delete(self, collection: str, ids: list[str]) -> None:
        ...

    def delete_where(self, collection: str, where: dict[str, Any]) -> None:
        """메타데이터 조건 삭제 (프로파일 갱신 시 corp 단위 삭제 등)."""
        ...
