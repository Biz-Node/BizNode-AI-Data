"""[Sprint 1D] 대량보유(majorstock, 5%룰) → data/raw_dart/{corp}_majorstock.json.

지분공시 종합정보 API는 corp_code만 필요(bsns_year 없음).
실행: python -m batch.fetch_ownership
"""

from __future__ import annotations

import json
import os
import sys
import time

import requests

from app.core.config import DART_KEY, ETF_LIST_PATH

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw_dart")
MAJORSTOCK_URL = "https://opendart.fss.or.kr/api/majorstock.json"


def main() -> int:
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)["companies"]

    ok = empty = 0
    for i, company in enumerate(seed, 1):
        corp_code = company["corpCode"]
        try:
            resp = requests.get(MAJORSTOCK_URL,
                                params={"crtfc_key": DART_KEY, "corp_code": corp_code}, timeout=30)
            data = resp.json()
        except Exception as exc:
            print(f"  [{i}/{len(seed)}] {company['companyName']}: 실패 {exc!r}")
            continue
        time.sleep(0.25)

        n = len(data.get("list", [])) if data.get("status") == "000" else 0
        if n:
            ok += 1
        else:
            empty += 1
        with open(os.path.join(RAW_DIR, f"{corp_code}_majorstock.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [{i}/{len(seed)}] {company['companyName']}: {n}행")

    print(f"\n대량보유 수집 완료: 보유 {ok}개사, 없음 {empty}개사")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
