# raw_dart JSON -> Normalizer -> Validator -> normalized JSON 저장 스크립트

from __future__ import annotations
import argparse
import json
import os
import re
import sys
from typing import Any, Callable, Optional
from pipeline.normalizer.base import enrich_company_entities
from pipeline.normalizer.executive_normalizer import normalize_executives
from pipeline.normalizer.investment_normalizer import normalize_investments
from pipeline.normalizer.llm_postprocess import run_llm_postprocess
from pipeline.normalizer.shareholder_normalizer import normalize_shareholders
from pipeline.validators.base import ValidationReport
from pipeline.validators.executive_validator import validate_executives
from pipeline.validators.investment_validator import validate_investments
from pipeline.validators.shareholder_validator import validate_shareholders
from schemas.dart_schemas import NormalizedDocument

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw_dart")
NORMALIZED_DIR = os.path.join(BASE_DIR, "data", "normalized")

NormalizeFn = Callable[[list[dict[str, Any]], str], NormalizedDocument]
ValidateFn = Callable[[NormalizedDocument], tuple[NormalizedDocument, ValidationReport]]

API_PIPELINE: dict[str, tuple[NormalizeFn, ValidateFn]] = {
    "shareholders": (normalize_shareholders, validate_shareholders),
    "executives": (normalize_executives, validate_executives),
    "investments": (normalize_investments, validate_investments),
}

_RAW_FILENAME_RE = re.compile(r"^(?P<corp_code>\d+)_(?P<api_name>[a-z_]+)\.json$")

# 주어진 디렉토리에서 API_PIPELINE에 정의된 API의 raw 파일이 있는 corp_code 목록을 찾음.
def discover_corp_codes() -> list[str]:
    corp_codes: set[str] = set()
    if not os.path.isdir(RAW_DIR):
        return []
    for filename in os.listdir(RAW_DIR):
        match = _RAW_FILENAME_RE.match(filename)
        if match and match.group("api_name") in API_PIPELINE:
            corp_codes.add(match.group("corp_code"))
    return sorted(corp_codes)


def load_raw_rows(corp_code: str, api_name: str) -> list[dict[str, Any]]:
    file_path = os.path.join(RAW_DIR, f"{corp_code}_{api_name}.json")
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["list"]

# 주어진 corp_code와 api_name에 해당하는 raw JSON을 읽고, normalizer를 적용하고, validator를 적용한 뒤 normalized JSON을 저장한다. 실패하면 건너뛴다.
def process_one(corp_code: str, api_name: str) -> None:
    normalize_fn, validate_fn = API_PIPELINE[api_name]

    try:
        rows = load_raw_rows(corp_code, api_name)
    except FileNotFoundError:
        print(f"{corp_code} {api_name}: raw 파일 없음, 건너뜀")
        return
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"{corp_code} {api_name}: raw 파일 파싱 실패({exc!r}), 건너뜀")
        return

    try:
        document = normalize_fn(rows, corp_code)
        validated, report = validate_fn(document)
    except Exception as exc:  
        print(f"{corp_code} {api_name}: 정규화/검증 실패({exc!r}), 건너뜀")
        return

    out_dict = validated.to_dict()
    out_dict["entities"] = enrich_company_entities(out_dict["entities"])

    os.makedirs(NORMALIZED_DIR, exist_ok=True)
    out_path = os.path.join(NORMALIZED_DIR, f"{corp_code}_{api_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_dict, f, ensure_ascii=False, indent=2)

    print(
        f"{corp_code} {api_name}: {len(document.relationships)}건 처리, "
        f"{len(report.dropped)}건 드롭, {len(report.warned)}건 경고"
    )


def run(corp_codes: list[str]) -> None:
    for corp_code in corp_codes:
        for api_name in API_PIPELINE:
            process_one(corp_code, api_name)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corp-code",
        help="특정 corp_code 하나만 처리한다. 생략하면 data/raw_dart/에서 발견되는 "
        "모든 corp_code를 처리한다.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="main_career/position/duty LLM 후처리(pipeline.normalizer.llm_postprocess) "
        "단계를 건너뛰고 규칙 기반 결과까지만 저장한다. 디버깅/빠른 반복 작업용.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    corp_codes = [args.corp_code] if args.corp_code else discover_corp_codes()
    if not corp_codes:
        print(f"처리할 corp_code를 찾지 못했습니다: {RAW_DIR}")
        return
    run(corp_codes)

    if not args.skip_llm:
        run_llm_postprocess(NORMALIZED_DIR)


if __name__ == "__main__":
    main()
