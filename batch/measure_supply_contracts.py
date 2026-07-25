"""[Sprint 0 실측] 시드 기업의 「단일판매ㆍ공급계약체결」 공시 보유량 측정.

방법서 §6: SUPPLIES_TO 전체가 이 공시에 의존 → P1 성패 지표.
기준(기업당 최근 2년 평균): >=1 계획대로 / 0.3~1 앵커보강 / <0.3 재선정·P2이관.

부수 산출(방법서 부록 A):
 - 매칭 공시의 pblntf_ty 코드값 실측
 - 파싱 가능성 확인용 샘플 rcept_no

실행: python -m batch.measure_supply_contracts
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from typing import Any

import requests

from app.core.config import DART_KEY, ETF_LIST_PATH

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
BGN_DE = "20240725"   # 최근 2년 (오늘 2026-07-25 기준)
END_DE = "20260725"
PAGE_COUNT = 100
MAX_PAGES = 30        # 안전장치 (대형주도 2년 공시가 이 안에 들어옴)
REQUEST_DELAY = 0.25  # DART 과부하 방지

# 공급계약 공시 식별 키워드 (report_nm 부분일치)
CONTRACT_KEYWORD = "공급계약"


def load_seed() -> list[dict[str, Any]]:
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        return json.load(f)["companies"]


def fetch_filings(corp_code: str) -> list[dict[str, Any]]:
    """한 기업의 최근 2년 전체 공시 목록을 페이지네이션으로 수집한다."""
    filings: list[dict[str, Any]] = []
    page = 1
    while page <= MAX_PAGES:
        params = {
            "crtfc_key": DART_KEY,
            "corp_code": corp_code,
            "bgn_de": BGN_DE,
            "end_de": END_DE,
            "page_no": page,
            "page_count": PAGE_COUNT,
        }
        resp = requests.get(LIST_URL, params=params, timeout=30)
        time.sleep(REQUEST_DELAY)
        data = resp.json()
        status = data.get("status")

        if status == "013":        # 조회 결과 없음
            break
        if status != "000":        # 오류
            print(f"    [경고] corp_code={corp_code} status={status} {data.get('message')}")
            break

        filings.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
    return filings


def is_supply_contract(report_nm: str) -> bool:
    return CONTRACT_KEYWORD in report_nm


def main() -> int:
    seed = load_seed()
    print(f"시드 {len(seed)}개 · 기간 {BGN_DE}~{END_DE}\n")

    per_company: list[tuple[str, str, int, int, int]] = []  # name, market, 체결, 해지·정정, 전체공시
    pblntf_ty_counter: Counter = Counter()
    samples: list[dict[str, str]] = []

    for i, company in enumerate(seed, 1):
        corp_code = company["corpCode"]
        name = company["companyName"]
        market = company.get("market", "")

        filings = fetch_filings(corp_code)
        contracts = [f for f in filings if is_supply_contract(f.get("report_nm", ""))]

        # 체결 vs 해지/정정 구분
        signed = [f for f in contracts if "해지" not in f.get("report_nm", "")]
        others = [f for f in contracts if "해지" in f.get("report_nm", "")]

        for f in contracts:
            pblntf_ty_counter[f.get("pblntf_ty", "?")] += 1
        for f in signed[:1]:  # 기업당 샘플 1건
            if len(samples) < 12:
                samples.append({
                    "corp_name": name, "rcept_no": f["rcept_no"],
                    "report_nm": f["report_nm"], "rcept_dt": f["rcept_dt"],
                    "pblntf_ty": f.get("pblntf_ty", "?"),
                })

        per_company.append((name, market, len(signed), len(others), len(filings)))
        print(f"  [{i:2}/{len(seed)}] {name:18} 체결 {len(signed):3}  해지·정정 {len(others):2}  (전체공시 {len(filings)})")

    # ── 집계 ──────────────────────────────────────────────────
    total_signed = sum(c[2] for c in per_company)
    n = len(per_company)
    avg = total_signed / n if n else 0
    zero = sum(1 for c in per_company if c[2] == 0)

    print("\n" + "=" * 60)
    print(f"총 체결 공시 : {total_signed}건")
    print(f"기업당 평균  : {avg:.2f}건/사")
    print(f"0건 기업     : {zero}/{n}개")

    # 시장별
    print("\n[시장별 평균]")
    for mk in ("KOSPI", "KOSDAQ", "비상장"):
        rows = [c for c in per_company if c[1] == mk]
        if rows:
            s = sum(r[2] for r in rows)
            print(f"  {mk:6}: {s}건 / {len(rows)}사 = {s/len(rows):.2f}건/사")

    # 방법서 §6 판정
    print("\n[방법서 §6 판정]")
    if avg >= 1:
        print(f"  ✅ 평균 {avg:.2f} >= 1.0 → 계획대로 진행")
    elif avg >= 0.3:
        print(f"  ⚠️  평균 {avg:.2f} (0.3~1.0) → 대형주 앵커 추가 + 사업보고서 비중 확대")
    else:
        print(f"  🔴 평균 {avg:.2f} < 0.3 → 시드 재선정 또는 SUPPLIES_TO를 P2 뉴스로 이관")

    print("\n[매칭 공시의 pblntf_ty 코드 분포]")
    for code, cnt in pblntf_ty_counter.most_common():
        print(f"  {code}: {cnt}건")

    print("\n[파싱 검증용 샘플 rcept_no]")
    for s in samples:
        print(f"  {s['rcept_no']} | {s['rcept_dt']} | ty={s['pblntf_ty']} | {s['corp_name']} | {s['report_nm']}")

    # 상위 5개사
    print("\n[체결 공시 상위 5개사]")
    for name, mk, signed, _, _ in sorted(per_company, key=lambda x: -x[2])[:5]:
        print(f"  {name:18} {signed}건 ({mk})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
