"""공시 원문(document.xml) 다운로드 → 파일 스토리지 + documents 메타.

"저장은 전부, 임베딩은 선별" — 원문 전체는 파일로 보관(임베딩 안 함),
근거 스니펫만 ChromaDB로. documents 테이블은 메타(경로·유형·일자)만.
"""

from __future__ import annotations

import io
import os
import zipfile
from typing import Optional

import requests

from app.core.config import DART_KEY, DOCUMENTS_DIR

DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"


def download_document_xml(rcept_no: str, use_cache: bool = True) -> Optional[str]:
    """접수번호로 원문 XML을 받아 파일로 저장하고 텍스트를 반환한다.
    저장 경로: data/documents/{rcept_no}/{rcept_no}.xml
    use_cache=True면 이미 받은 파일을 재사용(재실행 시 DART 재호출 없음).
    """
    cached = raw_path(rcept_no)
    if use_cache and os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            return f.read()

    resp = requests.get(
        DOCUMENT_URL, params={"crtfc_key": DART_KEY, "rcept_no": rcept_no}, timeout=60
    )
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        return None  # zip 아님(오류 응답 등)

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        xml_name = next((n for n in archive.namelist() if n.lower().endswith(".xml")), None)
        if xml_name is None:
            return None
        raw = archive.read(xml_name)

    text = raw.decode("utf-8", errors="replace")

    dest_dir = os.path.join(DOCUMENTS_DIR, rcept_no)
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, f"{rcept_no}.xml"), "w", encoding="utf-8") as f:
        f.write(text)
    return text


def raw_path(rcept_no: str) -> str:
    return os.path.join(DOCUMENTS_DIR, rcept_no, f"{rcept_no}.xml")


_UPSERT_DOC_SQL = """
INSERT INTO documents (rcept_no, corp_code, doc_type, title, rcept_dt, raw_path)
VALUES (%(rcept_no)s, %(corp_code)s, %(doc_type)s, %(title)s, %(rcept_dt)s, %(raw_path)s)
ON CONFLICT (rcept_no) DO UPDATE SET
    corp_code=EXCLUDED.corp_code, doc_type=EXCLUDED.doc_type,
    title=EXCLUDED.title, rcept_dt=EXCLUDED.rcept_dt, raw_path=EXCLUDED.raw_path
"""


def register_document(conn, rcept_no: str, corp_code: str, doc_type: str,
                      title: str, rcept_dt: Optional[str]) -> None:
    """documents 테이블에 원문 메타 등록(멱등)."""
    with conn.cursor() as cur:
        cur.execute(_UPSERT_DOC_SQL, {
            "rcept_no": rcept_no, "corp_code": corp_code, "doc_type": doc_type,
            "title": title, "rcept_dt": rcept_dt, "raw_path": raw_path(rcept_no),
        })
