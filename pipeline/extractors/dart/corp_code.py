"""DART 고유번호(corpCode.xml) 수집 → PostgreSQL `corp_code_master` 적재.

전 종목(10만+)을 관계 수집은 안되어도 검색이 가능한 상태로 만든다.
이 테이블의 `corp_name`에 걸린 pg_trgm GIN 인덱스가 이후 개체 해소(ER)의
Lexical 블로킹 기반이 된다.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Iterator, Optional
from xml.etree import ElementTree

import requests

from app.core.config import DART_KEY

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_REQUEST_TIMEOUT_SECONDS = 60


def fetch_corp_code_zip() -> bytes:
    """corpCode.xml(zip)을 내려받아 바이트로 반환한다."""
    if not DART_KEY:
        raise RuntimeError("DART_KEY가 설정되지 않았습니다. .env를 확인하세요.")

    response = requests.get(
        CORP_CODE_URL, params={"crtfc_key": DART_KEY}, timeout=_REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()

    # 키 오류 등은 200 + XML 에러 본문으로 오는 경우가 있어 zip 여부로 판별한다.
    if not response.content.startswith(b"PK"):
        raise RuntimeError(f"zip이 아닌 응답을 받았습니다: {response.content[:200]!r}")
    return response.content


def _clean(value: Optional[str]) -> Optional[str]:
    """공백뿐인 값(비상장사의 stock_code 등)은 None으로 정규화한다."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_modify_date(value: Optional[str]) -> Optional[str]:
    """'20170630' -> '2017-06-30'. 형식이 다르면 None."""
    cleaned = _clean(value)
    if cleaned is None or len(cleaned) != 8 or not cleaned.isdigit():
        return None
    return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}"


def parse_corp_codes(zip_bytes: bytes) -> Iterator[dict[str, Any]]:
    """zip 안의 CORPCODE.xml을 순회하며 레코드를 뽑는다."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xml_name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
        with archive.open(xml_name) as xml_file:
            # 10만 건 이상이라 전체를 메모리에 올리지 않고 스트리밍 파싱한다.
            for _, element in ElementTree.iterparse(xml_file, events=("end",)):
                if element.tag != "list":
                    continue

                corp_code = _clean(element.findtext("corp_code"))
                corp_name = _clean(element.findtext("corp_name"))
                if corp_code and corp_name:
                    yield {
                        "corp_code": corp_code,
                        "corp_name": corp_name,
                        "stock_code": _clean(element.findtext("stock_code")),
                        "modify_date": _parse_modify_date(element.findtext("modify_date")),
                    }
                element.clear()


_UPSERT_SQL = """
INSERT INTO corp_code_master (corp_code, corp_name, stock_code, modify_date)
VALUES (%(corp_code)s, %(corp_name)s, %(stock_code)s, %(modify_date)s)
ON CONFLICT (corp_code) DO UPDATE SET
    corp_name   = EXCLUDED.corp_name,
    stock_code  = EXCLUDED.stock_code,
    modify_date = EXCLUDED.modify_date
"""


def upsert_corp_code_master(conn, rows: Iterator[dict[str, Any]], batch_size: int = 5000) -> int:
    """`corp_code_master`에 UPSERT. 배치 단위로 executemany 한다."""
    total = 0
    buffer: list[dict[str, Any]] = []

    with conn.cursor() as cursor:
        for row in rows:
            buffer.append(row)
            if len(buffer) >= batch_size:
                cursor.executemany(_UPSERT_SQL, buffer)
                total += len(buffer)
                print(f"  {total:,}건 적재...")
                buffer.clear()

        if buffer:
            cursor.executemany(_UPSERT_SQL, buffer)
            total += len(buffer)

    return total
