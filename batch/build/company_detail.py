"""사업보고서 → `company_profiles`(개요) · `business_segments`(사업부문) 적재.

서비스 「기업 상세」 화면의 빈 칸을 채운다. 원문이 이미 있어 DART 재호출이 없다.

    python -m batch.build.company_detail --dry-run    # 결과만 보기
    python -m batch.build.company_detail              # 적재
    python -m batch.build.company_detail --force      # 이미 있어도 다시

★건너뛰기 — 기본은 `company_profiles`에 행이 있으면 넘어간다. LLM 호출이
  기업당 40원이라, 재실행할 때마다 다시 부르면 돈만 나간다.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import postgres_connection
from pipeline.extractors.dart.company_detail import (
    SECTION_OVERVIEW, SECTION_SEGMENT, extract_detail,
)
from pipeline.extractors.dart.downloader import DEFAULT_DOWNLOAD_DIR
from pipeline.extractors.dart.text_cleaner import clean_text
from pipeline.extractors.dart.xml_parser import parse_sections

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_COST_KRW = 40

_UPSERT_PROFILE = """
INSERT INTO company_profiles (corp_code, version, text, updated_at)
VALUES (%s, 1, %s, now())
ON CONFLICT (corp_code, version) DO UPDATE
    SET text = EXCLUDED.text, updated_at = now()
"""

_DELETE_SEG = "DELETE FROM business_segments WHERE corp_code = %s AND bsns_year = %s"
_INSERT_SEG = """
INSERT INTO business_segments
    (corp_code, bsns_year, segment_name, revenue, revenue_ratio, source_doc)
VALUES (%s, %s, %s, %s, %s, %s)
"""


def _sections(rcept_no: str) -> tuple[str, str]:
    """이미 받아둔 원문에서 두 절을 꺼낸다. (개요, 사업부문)"""
    files = glob.glob(os.path.join(DEFAULT_DOWNLOAD_DIR, rcept_no, "**", "*.xml"),
                      recursive=True)
    if not files:
        return "", ""
    try:
        secs = parse_sections(max(files, key=os.path.getsize))
    except Exception:
        return "", ""

    def pick(want: str) -> str:
        w = want.replace(" ", "")
        key = next((t for t in secs if w in t.replace(" ", "")), None)
        return clean_text(secs[key]) if key else ""

    return pick(SECTION_OVERVIEW), pick(SECTION_SEGMENT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 추출")
    ap.add_argument("--limit", type=int, help="처리 상한(비용)")
    ap.add_argument("--only", nargs="+", metavar="기업명",
                    help="이 기업만 다시 (부문이 덜 뽑힌 곳을 골라 재추출할 때)")
    ap.add_argument("--untrusted", action="store_true",
                    help="`revenue_trusted=false`인 기업만 다시 — 부문 누락이 원인인 것들")
    args = ap.parse_args()

    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        code2name = {c["corpCode"]: c["companyName"]
                     for c in json.load(f)["companies"]}

    with postgres_connection() as conn:
        docs = conn.execute(
            "SELECT corp_code, rcept_no, rcept_dt FROM documents "
            "WHERE doc_type='사업보고서' ORDER BY corp_code").fetchall()
        done = {r[0] for r in conn.execute(
            "SELECT corp_code FROM company_profiles").fetchall()}

    # ★재추출 대상을 골라 부를 수 있어야 한다. 부문이 덜 뽑힌 5~6개사 때문에
    #   60개사를 전부 다시 돌리면 2,400원이 나간다(기업당 40원).
    pick: set[str] | None = None
    if args.only:
        want = set(args.only)
        pick = {c for c, n in code2name.items() if n in want}
        missing = want - {code2name[c] for c in pick}
        if missing:
            print(f"⚠ 시드 목록에 없는 기업: {' · '.join(sorted(missing))}\n")
    elif args.untrusted:
        with postgres_connection() as conn:
            pick = {r[0] for r in conn.execute(
                "SELECT DISTINCT corp_code FROM business_segments "
                "WHERE revenue_trusted IS false "
                "  AND coalesce(trust_reason,'') NOT LIKE '합계 행%'").fetchall()}
        print(f"■ 부문 매출을 못 믿는 기업 {len(pick)}곳만 다시 뽑습니다 "
              f"(약 {len(pick) * _COST_KRW:,}원)\n")

    targets = [(c, r, d) for c, r, d in docs
               if (pick is not None and c in pick) or (pick is None and (args.force or c not in done))]
    print(f"사업보고서 {len(docs)}건 · 이미 처리 {len(done)}건 · "
          f"대상 {len(targets)}건 (약 {len(targets)*_COST_KRW:,}원)\n")
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("처리할 기업이 없습니다. (--force 로 재추출)")
        return 0

    ok = skip = 0
    rows_profile, rows_seg = [], []
    for code, rcept, dt in targets:
        name = code2name.get(code, code)
        ov, sg = _sections(rcept)
        if not ov and not sg:
            print(f"  {name:14} 절 없음 — 건너뜀")
            skip += 1
            continue
        detail = extract_detail(name, ov, sg)
        if not detail:
            skip += 1
            continue
        year = int(str(dt)[:4]) - 1 if dt else None    # 보고서는 전년도 실적
        ok += 1
        rows_profile.append((code, detail["overview"]))
        # ★같은 부문명이 두 번 나오면 PK((corp_code, bsns_year, segment_name))를
        #   위반해 **적재 전체가 롤백된다**. 실측: 60개사 일괄 실행이 통째로
        #   실패했는데 2개사만 돌리면 성공했다. LLM이 수출/내수를 나눠 같은
        #   품목명을 두 번 내는 경우가 있다. 첫 번째만 남긴다.
        seen_seg: set[str] = set()
        for s in detail["segments"]:
            if s["name"] in seen_seg:
                continue
            seen_seg.add(s["name"])
            rows_seg.append((code, year, s["name"], s["revenue"],
                             s["ratio"], rcept))
        segs = " · ".join(
            f"{s['name'][:14]}({s['ratio']:.0f}%)" if s["ratio"] is not None
            else s["name"][:14] for s in detail["segments"][:4])
        print(f"  {name:14} 개요 {len(detail['overview']):>3}자 · "
              f"부문 {len(detail['segments'])}개  {segs}")

    print(f"\n추출 성공 {ok}건 · 실패·누락 {skip}건 · 부문 총 {len(rows_seg)}개")
    if args.dry_run:
        print("[dry-run] 적재하지 않았습니다.")
        if rows_profile:
            print(f"\n개요 예시 — {code2name.get(rows_profile[0][0])}")
            print(f"  {rows_profile[0][1][:300]}")
        return 0

    # ★한 건이 실패해도 나머지가 들어가게 기업 단위로 커밋한다.
    #   executemany로 한 번에 넣었더니 부문명 중복 하나 때문에 **60개사가 통째로
    #   롤백**됐고, 예외가 상위로 올라가 성공 메시지도 안 나왔다(조용한 실패).
    loaded_p = loaded_s = failed = 0
    by_corp: dict[str, list] = {}
    for r in rows_seg:
        by_corp.setdefault(r[0], []).append(r)
    for code, overview in rows_profile:
        try:
            with postgres_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(_UPSERT_PROFILE, (code, overview))
                    segs = by_corp.get(code, [])
                    for year in {s[1] for s in segs}:
                        cur.execute(_DELETE_SEG, (code, year))
                    for s in segs:
                        cur.execute(_INSERT_SEG, s)
            loaded_p += 1
            loaded_s += len(by_corp.get(code, []))
        except Exception as exc:
            failed += 1
            print(f"  ✗ 적재 실패 {code2name.get(code, code)}: {exc!r}")
    print(f"✅ company_profiles {loaded_p}건 · business_segments {loaded_s}건 적재"
          + (f" · 실패 {failed}건" if failed else ""))

    # ★적재 직후 **단위 검증을 붙여서 끝낸다**(2026-08-03).
    #
    #   사업보고서의 품목별 매출 표는 「(단위: 백만원)」이 표 밖 캡션에 있어
    #   본문만 보는 LLM이 환산을 놓친다. 실측으로 60개사 중 39개사가 틀렸고,
    #   그걸 나중에 따로 고쳤다 — 그 사이 화면에 100만배 틀린 금액이 나갔다.
    #
    #   검증을 적재의 **일부**로 만들어 두면 신규 기업이 들어와도 그 일이 안 생긴다.
    #   `segment_units`는 전량을 다시 보지만 무료·수초라 매번 돌려도 된다.
    if loaded_s and not args.dry_run:
        print("\n─ 부문 매출 단위 검증 ─")
        rc = subprocess.run([sys.executable, "-m", "batch.repair.segment_units"]).returncode
        if rc != 0:
            print("⚠ 단위 검증이 실패했습니다 — 금액을 화면에 쓰기 전에 확인하세요")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
