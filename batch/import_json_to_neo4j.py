# [STEP 3] Normalized JSON -> Neo4j 적재 실행 스크립트 (CLI 진입점)

from __future__ import annotations

import argparse
from typing import Optional

from pipeline.importer.neo4j_importer import import_all_to_neo4j


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corp-code",
        help="특정 corp_code 하나만 적재한다. 생략하면 data/normalized/에서 발견되는 "
        "모든 corp_code를 적재한다.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    import_all_to_neo4j([args.corp_code] if args.corp_code else None)


if __name__ == "__main__":
    main()
