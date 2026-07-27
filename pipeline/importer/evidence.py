"""evidence 청크 적재 — ChromaDB + vector_chunks 레지스트리 (ERD §5, §7).

 - evidence_id는 결정적 해시(§5-3): 재실행해도 같은 id → 엣지-청크 고아 방지
 - ChromaDB evidence 컬렉션 upsert(멱등) + vector_chunks에 등록(레지스트리)
 - vector_chunks가 있어야 "무엇이 임베딩됐는지" RDB가 알고, 갱신·재임베딩 가능
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import EMBEDDING_MODEL
from pipeline.vectorstore.chroma_store import get_store

EVIDENCE_COLLECTION = "evidence"


def make_evidence_id(
    source_doc: str, src_key: str, tgt_key: str, edge_type: str, subtype: Optional[str] = None
) -> str:
    """결정적 evidence_id. 같은 (공시,엣지)면 항상 같은 값(멱등)."""
    raw = f"{source_doc}|{src_key}|{tgt_key}|{edge_type}|{subtype or ''}"
    return "ev_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class EvidenceRecord:
    evidence_id: str
    text: str
    corp_code: Optional[str]          # owner 기업 (조회 편의)
    source_doc: str                   # rcept_no
    metadata: dict[str, Any] = field(default_factory=dict)


_REGISTER_SQL = """
INSERT INTO vector_chunks (chunk_id, chunk_type, collection, owner_key, corp_code,
                           source_doc, embedding_model, content_hash, version, is_active)
VALUES (%(chunk_id)s, 'evidence', %(collection)s, %(owner_key)s, %(corp_code)s,
        %(source_doc)s, %(model)s, %(content_hash)s, 1, true)
ON CONFLICT (chunk_id) DO UPDATE SET
    content_hash=EXCLUDED.content_hash, embedding_model=EXCLUDED.embedding_model,
    is_active=true, embedded_at=now()
"""


def upsert_evidence(conn, records: list[EvidenceRecord]) -> int:
    """evidence 청크를 ChromaDB에 임베딩·적재하고 vector_chunks에 등록한다.
    (§5-2 순서상 Neo4j 적재 뒤에 호출)
    """
    if not records:
        return 0

    store = get_store()
    store.upsert(
        EVIDENCE_COLLECTION,
        ids=[r.evidence_id for r in records],
        documents=[r.text for r in records],
        metadatas=[r.metadata for r in records],
    )

    with conn.cursor() as cur:
        for r in records:
            cur.execute(_REGISTER_SQL, {
                "chunk_id": r.evidence_id,
                "collection": EVIDENCE_COLLECTION,
                "owner_key": r.source_doc,
                "corp_code": r.corp_code,
                "source_doc": r.source_doc,
                "model": EMBEDDING_MODEL,
                "content_hash": _content_hash(r.text),
            })
    return len(records)


def fetch_evidence(evidence_id: str) -> dict[str, Any]:
    """팩트체크 — evidence_id로 원문 스니펫 직접 조회(벡터 미사용)."""
    return get_store().get(EVIDENCE_COLLECTION, [evidence_id])
