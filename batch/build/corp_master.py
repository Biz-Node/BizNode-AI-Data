"""DART 고유번호 전량 → PostgreSQL `corp_code_master` 적재.

실행: python -m batch.build.corp_master
"""

from __future__ import annotations

import sys

from app.core.database import postgres_connection
from pipeline.extractors.dart.corp_code import (
    fetch_corp_code_zip,
    parse_corp_codes,
    upsert_corp_code_master,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    print("DART 고유번호 다운로드 중...")
    try:
        zip_bytes = fetch_corp_code_zip()
    except Exception as exc:
        print(f"✗ 다운로드 실패: {exc}")
        return 1

    print(f"✓ 다운로드 완료 ({len(zip_bytes):,} bytes)")
    print("PostgreSQL 적재 중...")

    try:
        with postgres_connection() as conn:
            total = upsert_corp_code_master(conn, parse_corp_codes(zip_bytes))
    except Exception as exc:
        print(f"✗ 적재 실패: {exc}")
        return 1

    print(f"✓ corp_code_master {total:,}건 적재 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
