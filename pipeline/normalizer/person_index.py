"""전역 인물 생년월 인덱스 — 시드 전체 임원 데이터에서 이름→생년월을 모은다.

목적: 주주(최대주주 API)에는 생년월이 없어 person_key가 name@corp로 폴백되고,
같은 사람이 회사마다 다른 노드로 분열된다. 임원 API(생년월 有)에서 얻은 값을
주주에도 적용해 병합률을 높인다.

한계: 총수가 어느 계열사에서도 등기임원이 아니면 여전히 분열된다(이재용).
그 케이스는 근거(뉴스·기업집단)가 필요하므로 P2 ER에서 처리한다(ERD §2-6).
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Optional

from pipeline.normalizer.base import clean_name, convert_korean_year_month

_RAW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "raw_dart"
)
_EXEC_FILE_RE = re.compile(r"^\d+_executives\.json$")


@lru_cache(maxsize=1)
def name_to_birth() -> dict[str, str]:
    """이름 → 생년월(YYYY-MM). 동명이 서로 다른 생년월이면 제외(모호).

    동명이인을 잘못 합치지 않도록, 한 이름에 생년월 후보가 2개 이상이면
    그 이름은 인덱스에서 뺀다(보수적).
    """
    if not os.path.isdir(_RAW_DIR):
        return {}

    candidates: dict[str, set[str]] = {}
    for filename in os.listdir(_RAW_DIR):
        if not _EXEC_FILE_RE.match(filename):
            continue
        try:
            with open(os.path.join(_RAW_DIR, filename), encoding="utf-8") as f:
                rows = json.load(f).get("list") or []
        except (json.JSONDecodeError, OSError):
            continue
        for row in rows:
            name = clean_name(row.get("nm"))
            birth = convert_korean_year_month(row.get("birth_ym"))
            if name and birth:
                candidates.setdefault(name, set()).add(birth)

    return {name: next(iter(births)) for name, births in candidates.items() if len(births) == 1}


def lookup_birth(name: Optional[str]) -> Optional[str]:
    """이름으로 생년월 조회(모호하면 None)."""
    if not name:
        return None
    return name_to_birth().get(name)
