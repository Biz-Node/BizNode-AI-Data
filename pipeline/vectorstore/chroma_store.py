"""ChromaDB 구현체 + OpenAI 임베딩 (VectorStore 어댑터).

임베딩은 애플리케이션에서 계산해 전달한다(Chroma 서버가 아니라). 모델 교체 시
vector_chunks.embedding_model로 재임베딩 대상을 특정하기 위함.
"""

from __future__ import annotations

from typing import Any, Optional

import chromadb
import openai

from app.core.config import (
    CHROMA_HOST,
    CHROMA_PORT,
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
)

_EMBED_BATCH = 100  # OpenAI 임베딩 배치 크기


class ChromaStore:
    def __init__(self) -> None:
        self._client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        self._oai = openai.OpenAI(api_key=OPENAI_API_KEY)
        self._collections: dict[str, Any] = {}

    # ── 임베딩 ────────────────────────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트 → 임베딩 벡터 리스트 (배치)."""
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            chunk = texts[i : i + _EMBED_BATCH]
            resp = self._oai.embeddings.create(model=EMBEDDING_MODEL, input=chunk)
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    def _col(self, collection: str):
        if collection not in self._collections:
            self._collections[collection] = self._client.get_or_create_collection(collection)
        return self._collections[collection]

    # ── VectorStore 인터페이스 ────────────────────────────────
    def upsert(
        self,
        collection: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        embeddings = self.embed(documents)
        self._col(collection).upsert(
            ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
        )

    def get(self, collection: str, ids: list[str]) -> dict[str, Any]:
        return self._col(collection).get(ids=ids)

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        embedding = self.embed([query_text])[0]
        return self._col(collection).query(
            query_embeddings=[embedding], n_results=n_results, where=where
        )

    def delete(self, collection: str, ids: list[str]) -> None:
        if ids:
            self._col(collection).delete(ids=ids)

    def delete_where(self, collection: str, where: dict[str, Any]) -> None:
        self._col(collection).delete(where=where)


# 지연 싱글턴 — 배치 내 재사용
_store: Optional[ChromaStore] = None


def get_store() -> ChromaStore:
    global _store
    if _store is None:
        _store = ChromaStore()
    return _store
