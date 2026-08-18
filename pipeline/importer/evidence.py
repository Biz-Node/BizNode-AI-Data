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

# ChromaDB get()의 한 번 요청 상한. 이보다 크게 보내면 응답이 잘린다.
_FETCH_BATCH = 200


def fetch_texts(ids: list[str]) -> dict[str, str]:
    """evidence_id → 근거 본문. 배치로 나눠 받고, 실패한 배치는 건너뛴다.

    ★같은 10줄이 검사 도구 4곳에 복사돼 있었다. 그중 하나가 **중복 id를 그대로
      넘겨** ChromaDB가 `DuplicateIDError`를 던졌고, 그 예외를 조용히 삼키는 바람에
      LLM이 「(근거 없음)」으로 판정해 전건이 판단불가가 된 적이 있다(2026-07-30).
      여기서 **중복을 먼저 없앤다** — 호출부가 잊을 수 없게.
    """
    uniq = list(dict.fromkeys(i for i in ids if i))   # 순서 유지 + 중복 제거
    out: dict[str, str] = {}
    store = get_store()
    for i in range(0, len(uniq), _FETCH_BATCH):
        chunk = uniq[i:i + _FETCH_BATCH]
        try:
            got = store.get(EVIDENCE_COLLECTION, chunk)
        except Exception:
            continue          # 한 배치가 실패해도 나머지는 받는다
        out.update(zip(got.get("ids", []), got.get("documents", [])))
    return out


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


def _keep_validated(conn, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """staged_edges에 **검증 통과 엣지로 존재하는** 근거만 남긴다.

    호출 시점에 이미 stage_document가 끝나 있어야 한다(쓰기 순서 §5-2:
    권위(staged_edges) → 파생(Neo4j·ChromaDB)).
    """
    ids = [r.evidence_id for r in records]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT properties->>'evidence_id' FROM staged_edges "
            "WHERE validated AND properties->>'evidence_id' = ANY(%s)",
            (ids,),
        )
        valid = {row[0] for row in cur.fetchall()}

    kept = [r for r in records if r.evidence_id in valid]
    dropped = len(records) - len(kept)
    if dropped:
        print(f"  [evidence] 검증 미통과 엣지의 근거 {dropped}건 제외")
    return kept


def upsert_evidence(conn, records: list[EvidenceRecord]) -> int:
    """evidence 청크를 ChromaDB에 임베딩·적재하고 vector_chunks에 등록한다.
    (§5-2 순서상 Neo4j 적재 뒤에 호출)
    """
    if not records:
        return 0

    # evidence_id는 결정적 해시라 같은 배치 안에서 충돌할 수 있다(한 기사에서 LLM이
    # 동일 (출발,도착,엣지,subtype) 관계를 두 번 뱉는 경우). ChromaDB는 배치 내
    # 중복 id를 거부하므로 여기서 접는다 — 뒤에 온 것이 더 긴 근거를 담는 경향이
    # 있어 **긴 텍스트 우선**으로 남긴다.
    unique: dict[str, EvidenceRecord] = {}
    for r in records:
        prev = unique.get(r.evidence_id)
        if prev is None or len(r.text) > len(prev.text):
            unique[r.evidence_id] = r
    records = list(unique.values())

    # ★매트릭스 검증을 통과한 엣지의 근거만 임베딩한다.
    # staging은 위반 엣지도 `validated=false`로 기록만 하고 Neo4j에는 올리지 않는다.
    # 그런데 근거는 그대로 임베딩돼서, **엣지가 없는 근거**가 쌓였다(실측 274건).
    # 고아 근거는 의미검색에 계속 걸려 "근거는 있는데 관계가 없는" 상태를 만든다.
    records = _keep_validated(conn, records)
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
