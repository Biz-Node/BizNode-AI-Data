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

    def update_metadata(
        self,
        collection: str,
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """메타데이터만 갱신. **재임베딩하지 않는다.**

        ★`upsert` 와 갈라 두는 이유 — `upsert` 는 문서를 받아 매번 임베딩을 다시
          만든다. 메타 한 칸을 채우려고 1만 청크를 재임베딩하면 돈이 나간다.
          채우는 값이 문서 본문에서 온 것이 아닐 때(엣지가 아는 `source_type`
          등) 벡터는 바뀔 이유가 없다.

        기존 메타에 **덮어쓰기(merge)** 다 — 넘기지 않은 키는 남는다.
        """
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
