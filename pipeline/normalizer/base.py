# 정규화/정제 관련 유틸리티 모음. 
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Optional

_MISSING_STRINGS = ("-", "")

_PAREN_JU_RE = re.compile(r"\(주\)")
_PAREN_YU_RE = re.compile(r"\(유\)")
_JUSIK_HOESA_RE = re.compile(r"주식회사")
_YUHAN_HOESA_RE = re.compile(r"유한회사")
_FOOTNOTE_RE = re.compile(r"\(\*\d+\)")
_NOTE_RE = re.compile(r"\(주\d+\)")
_STATUS_RE = re.compile(
    r"\((상장|비상장|코스피|코스닥|KOSPI|KOSDAQ)\)"
)

def clean_missing(raw: Optional[str]) -> Optional[str]:
    """"-", "", None -> None. 그 외에는 앞뒤 공백만 제거한 문자열을 반환한다."""
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped in _MISSING_STRINGS:
        return None
    return stripped

def collapse_whitespace(text: Optional[str]) -> Optional[str]:
    """개행 포함 연속 공백을 단일 공백으로 축약하고 앞뒤를 trim한다."""
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None

def clean_name(raw: Optional[str]) -> Optional[str]:
    """이름류(`nm`, `inv_prm`) 정제: 결측치 정규화 + 개행/중복 공백 제거."""
    name = collapse_whitespace(clean_missing(raw))

    if name is None:
        return None

    name = _FOOTNOTE_RE.sub("", name)
    name = _NOTE_RE.sub("", name)
    name = _STATUS_RE.sub("", name)

    return name.strip()


def clean_position(raw: Optional[str]) -> Optional[str]:
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    parts = [part for part in (collapse_whitespace(p) for p in cleaned.split("\n")) if part]
    return " | ".join(parts) if parts else None


def clean_bullet_text(raw: Optional[str]) -> Optional[str]:
    cleaned = collapse_whitespace(clean_missing(raw))
    if cleaned is None:
        return None
    if not cleaned.startswith("ㆍ"):
        return cleaned
    parts = [part.strip() for part in cleaned.split("ㆍ") if part.strip()]
    return " | ".join(parts) if parts else None

# ---------------------------------------------------------------------------
# 총계 행 / 회사·개인 판별
# ---------------------------------------------------------------------------

_TOTAL_ROW_NAMES = {"계", "합계"}

def is_total_row(name: Optional[str]) -> bool:
    """최대주주/타법인출자 리스트의 총계 행("계"/"합계") 여부."""
    return (name or "").strip() in _TOTAL_ROW_NAMES

# 법인격 표기는 `normalizer/legal_forms.py` 한 곳에서만 정한다(2026-08-13).
# 전에는 이 파일·`entities.py`·`repair/node_identity.py`에 여섯 벌로 흩어져 있었다.
from pipeline.normalizer.legal_forms import CORP_MARKERS as _COMPANY_NAME_MARKERS

def is_company_name(name: str) -> bool:
    if any(marker in name for marker in _COMPANY_NAME_MARKERS):
        return True
    corp_code, _, _ = resolve_investee_corp_code(name)
    return corp_code is not None

# ---------------------------------------------------------------------------
# 숫자 변환
# ---------------------------------------------------------------------------

def parse_int(raw: Optional[str]) -> Optional[int]:
    """"1,234" 형태의 문자열을 정수로 변환한다. 값이 없거나 "-"이면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    return int(cleaned.replace(",", ""))


def parse_float(raw: Optional[str]) -> Optional[float]:
    """"12.3" 형태의 문자열을 실수로 변환한다. 값이 없거나 "-"이면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    return float(cleaned.replace(",", ""))

# ---------------------------------------------------------------------------
# 날짜 / 연월 -> ISO 8601
# ---------------------------------------------------------------------------

_DOTTED_DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
_KOREAN_DATE_RE = re.compile(r"^(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일$")
_KOREAN_YEAR_MONTH_RE = re.compile(r"^(\d{4})년\s*(\d{1,2})월$")
_TENURE_MONTHS_RE = re.compile(r"(\d+)\s*개월")

def convert_dotted_date(raw: Optional[str]) -> Optional[str]:
    """`frst_acqs_de`("1977.01.01") -> "1977-01-01". 형식이 다르면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None or not _DOTTED_DATE_RE.match(cleaned):
        return None
    return cleaned.replace(".", "-")

def convert_korean_date(raw: Optional[str]) -> Optional[str]:
    """`tenure_end_on`("2028년 03월 18일") -> "2028-03-18". 형식이 다르면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    match = _KOREAN_DATE_RE.match(cleaned)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"

def convert_korean_year_month(raw: Optional[str]) -> Optional[str]:
    """`birth_ym`("1960년 12월") -> "1960-12". 형식이 다르면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    match = _KOREAN_YEAR_MONTH_RE.match(cleaned)
    if not match:
        return None
    year, month = match.groups()
    return f"{year}-{int(month):02d}"

def parse_tenure_months(raw: Optional[str]) -> Optional[int]:
    """`hffc_pd`("10개월") -> 10. 형식이 다르면 None."""
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    match = _TENURE_MONTHS_RE.search(cleaned)
    if not match:
        return None
    return int(match.group(1))

KNOWN_CHANGE_REASONS: list[str] = [
    "시간외매매",
    "장내매매",
    "장외매매",
    "상속",
    "증여",
    "신규 선임",
    "이사 퇴임",
    "이사 임기만료",
    "자사주 상여금",
]

def _normalize_for_matching(text: str) -> str:
    """패턴 매칭 전 공백을 모두 제거. ("장내 매매" -> "장내매매")."""
    return re.sub(r"\s+", "", text)

def match_change_reason(remark: Optional[str]) -> Optional[str]:
    if not remark:
        return None
    normalized_remark = _normalize_for_matching(remark)
    matched = [
        reason
        for reason in KNOWN_CHANGE_REASONS
        if _normalize_for_matching(reason) in normalized_remark
    ]
    if not matched:
        return None
    return " | ".join(matched)

# ---------------------------------------------------------------------------
# 회사명 ↔ corp_code 매칭 
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "company_list")
_ETF_COMPANY_LIST_PATH = os.path.join(_DATA_DIR, "company_list_etf.json")
_DART_COMPANY_MAP_PATH = os.path.join(_DATA_DIR, "company_map.json")

# (corp_code, stock_code, market) — 미상장/미확인 필드는 None.
InvesteeMatch = tuple[str, Optional[str], Optional[str]]
# (name, stock_code, market) — corp_code 기준 역인덱스 조회 결과.
CorpCodeInfo = tuple[str, Optional[str], Optional[str]]

# 영문 법인격 접미어 제거 — 한글 `㈜`·`주식회사` 제거와 같은 일이다.
# 이걸 안 떼면 "Applied Materials, Inc"와 "Applied Materials Inc."가 **다른 노드**가 된다
# (stub Company는 norm_name이 곧 식별자다). 실제로 해외 자회사·거래처에서 다발했다.
# 목록은 `legal_forms.py`가 갖고 있고, **제거용은 `Holdings`를 뺀 좁은 집합**이다.
from pipeline.normalizer.legal_forms import strip_en_legal_suffix as _strip_en_legal_suffix


def normalize_company_name(name: str) -> str:
    """MERGE 키로 쓰는 정규화명. 표기 변형이 같은 값으로 모여야 한다.

    한글 법인격 + 영문 법인격 + 구두점 + 대소문자 + 공백을 모두 흡수한다.
    """
    cleaned = name

    cleaned = cleaned.replace("㈜", "")
    cleaned = _PAREN_JU_RE.sub("", cleaned)
    cleaned = _PAREN_YU_RE.sub("", cleaned)
    cleaned = _JUSIK_HOESA_RE.sub("", cleaned)
    cleaned = _YUHAN_HOESA_RE.sub("", cleaned)
    cleaned = _FOOTNOTE_RE.sub("", cleaned)
    cleaned = _NOTE_RE.sub("", cleaned)
    cleaned = _STATUS_RE.sub("", cleaned)

    cleaned = _strip_en_legal_suffix(cleaned)

    # 남은 구두점 제거 + 소문자화(영문 표기 흔들림 흡수) + 공백 제거
    #
    # ★슬래시(`/`)도 지운다. `norm_name` 은 corp_code 가 없는 회사의 **키**이고,
    #   그 키가 URL 경로에 들어간다 — `/companies/한국s/w공제조합` 은 경로가
    #   갈라져 조회가 안 된다(2026-08-17 실측: 12곳이 404).
    #   `%2F` 로 인코딩해도 서버가 정규화해서 소용없다. **키에 슬래시를 안 넣는 게**
    #   유일한 해법이다.
    cleaned = re.sub(r"[,.·'\"`/]+", "", cleaned)
    key = re.sub(r"\s+", "", cleaned).strip().lower()

    # 해외 기업 한글·영문 표기 통일 (「Netlist」/「넷리스트」가 별개 노드가 되는 것 방지)
    from pipeline.normalizer.foreign_aliases import apply_alias

    return apply_alias(key)


@lru_cache(maxsize=1)
def load_etf_name_to_corp_code() -> dict[str, InvesteeMatch]:
    """1차 소스: `company_list_etf.json`(ETF 구성종목 64개, 신뢰도 높음)의
    정규화된 이름 -> (corp_code, stock_code, market).
    """
    if not os.path.exists(_ETF_COMPANY_LIST_PATH):
        return {}
    with open(_ETF_COMPANY_LIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        normalize_company_name(company["companyName"]): (
            company["corpCode"],
            company.get("stockCode"),
            company.get("market"),
        )
        for company in data.get("companies", [])
    }


@lru_cache(maxsize=1) 
def _load_dart_company_map_raw() -> dict[str, dict[str, Any]]:
    """`company_map.json` 파일 로드. """
    if not os.path.exists(_DART_COMPANY_MAP_PATH):
        return {}
    with open(_DART_COMPANY_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_dart_name_to_corp_code() -> dict[str, InvesteeMatch]:
    """`company_map.json`의 정규화된 이름 -> (corp_code, stock_code, market)."""
    return {
        normalize_company_name(name): (info["corp_code"], info.get("stock_code"), info.get("market"))
        for name, info in _load_dart_company_map_raw().items()
    }


@lru_cache(maxsize=1)
def load_dart_corp_code_to_info() -> dict[str, CorpCodeInfo]:
    """`company_map.json`의 corp_code -> (name, stock_code, market)."""
    return {
        info["corp_code"]: (info.get("formal_name") or name, info.get("stock_code"), info.get("market"))
        for name, info in _load_dart_company_map_raw().items()
    }

def resolve_investee_corp_code(name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """회사명으로 corp_code/stock_code/market을 조회한다."""
    normalized = normalize_company_name(name)
    matched = load_etf_name_to_corp_code().get(normalized)
    if matched is not None:
        return matched
    matched = load_dart_name_to_corp_code().get(normalized)
    if matched is not None:
        return matched
    return (None, None, None)


def _fill_if_missing(properties: dict[str, Any], key: str, value: Optional[str]) -> None:
    """`properties[key]`가 이미 truthy이면 덮어쓰지 않고, value가 None이면 무시한다."""
    
    if properties.get(key):
        return
    if value is None:
        return
    properties[key] = value


def enrich_company_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Company 엔티티의 corp_code/stock_code/market 정보를 보강한다."""

    for entity in entities:
        if entity.get("type") != "Company":
            continue

        properties = entity.get("properties", {})
        name = properties.get("name")
        corp_code = properties.get("corp_code")

        if corp_code:
            info = load_dart_corp_code_to_info().get(corp_code)
            if info is None:
                print(f"[enrich_company_entities] 매칭 실패: corp_code={corp_code!r} name={name!r} path=corp_code_index")
                continue
            _, stock_code, market = info
            _fill_if_missing(properties, "stock_code", stock_code)
            _fill_if_missing(properties, "market", market)
            print(f"[enrich_company_entities] 매칭 성공: corp_code={corp_code!r} name={name!r} path=corp_code_index")
            continue

        if not name:
            continue

        matched_corp_code, matched_stock_code, matched_market = resolve_investee_corp_code(name)
        if matched_corp_code is None:
            print(f"[enrich_company_entities] 매칭 실패: name={name!r} path=unmatched")
            continue

        path = "etf_name" if normalize_company_name(name) in load_etf_name_to_corp_code() else "dart_map_name"
        _fill_if_missing(properties, "corp_code", matched_corp_code)
        _fill_if_missing(properties, "stock_code", matched_stock_code)
        _fill_if_missing(properties, "market", matched_market)
        print(f"[enrich_company_entities] 매칭 성공: name={name!r} corp_code={matched_corp_code!r} path={path}")

    return entities
