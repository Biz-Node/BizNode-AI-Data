"""`business_segments.revenue`의 **단위 오류**를 고친다.

★왜 필요한가 (2026-08-01)

부문 매출액이 56개사 중 **39개사(70%)에서 틀렸다.** 실측:

    SK하이닉스   부문합     97,146,675   전사 97,146,675,000,000   100만배
    심텍        부문합  1,410,560,000   전사  1,410,559,919,451     1,000배
    정상 17 · 1,000배 28 · 100만배 6 · 기타 5

원인은 **LLM에게 단위 환산을 시킨 것**이다. 사업보고서의 품목별 매출 표는
「(단위: 백만원)」 「(단위: 천원)」이 표 밖 캡션에 있어 본문만 보면 알 수 없고,
프롬프트로 "천원 단위면 ×1000 하세요"라고 부탁해도 지켜지지 않는다.

★고치는 방법 — LLM을 다시 부르지 않는다. 전사 매출액을 `financials`에서 이미
  **정확히** 알고 있으므로, 부문합과의 비율을 보면 배수가 드러난다:

      배수 = 전사매출 / 부문합  →  {1, 1000, 1_000_000} 중 가까운 값으로 스냅

  스냅 후 오차가 ±25% 안에 들어올 때만 적용한다. 애매하면 **건드리지 않고
  보고만 한다** — 틀린 값을 다른 틀린 값으로 바꾸는 게 제일 나쁘다.

  부문합이 전사와 안 맞는 이유는 단위 말고도 있다(부문 일부 누락, 내부거래
  제거 전 금액). 그래서 스냅 실패는 오류가 아니라 **판단 보류**로 남긴다.

    python -m batch.repair.segment_units --dry-run
    python -m batch.repair.segment_units
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 사업보고서 표에 실제로 쓰이는 단위. 원 · 천원 · 백만원 세 가지뿐이다.
_SCALES = (1, 1_000, 1_000_000)

# 스냅한 뒤 전사 매출과 이만큼까지 어긋나는 건 받아들인다. 부문 매출 합은
# 내부거래 제거·기타매출 때문에 전사와 정확히 같지 않다.
_TOL_LO, _TOL_HI = 0.75, 1.35

_FETCH = """
SELECT s.corp_code, co.name, s.bsns_year,
       sum(s.revenue)                      AS seg_sum,
       count(*) FILTER (WHERE s.revenue IS NOT NULL) AS n_rev,
       count(*)                            AS n_seg,
       max(f.revenue)                      AS total
  FROM business_segments s
  JOIN companies  co ON co.corp_code = s.corp_code
  LEFT JOIN financials f
         ON f.corp_code = s.corp_code AND f.bsns_year = s.bsns_year
 GROUP BY s.corp_code, co.name, s.bsns_year
 ORDER BY co.name
"""

_APPLY = """
UPDATE business_segments SET revenue = revenue * %s, revenue_trusted = TRUE,
       trust_reason = NULL
 WHERE corp_code = %s AND bsns_year = %s AND revenue IS NOT NULL
"""

# 못 믿는 값은 **지우지 않고 표시만** 한다. 부문 이름은 여전히 쓸모가 있고,
# 나중에 원문을 다시 보면 고칠 수 있다. 화면에서 감추는 건 조회 쪽 몫이다.
_DISTRUST_REV = """
UPDATE business_segments
   SET revenue_trusted = FALSE, trust_reason = %s
 WHERE corp_code = %s AND bsns_year = %s
"""
# ★`concat_ws(' · ', trust_reason, %s)`처럼 함수 인자로 바로 넣으면 Postgres가
#   파라미터 타입을 못 정한다(IndeterminateDatatype). ::text로 못 박는다.
_DISTRUST_RATIO = """
UPDATE business_segments
   SET ratio_trusted = FALSE,
       trust_reason  = concat_ws(' · ', trust_reason, %s::text)
 WHERE corp_code = %s AND bsns_year = %s
"""
_TRUST_RATIO = """
UPDATE business_segments SET ratio_trusted = TRUE
 WHERE corp_code = %s AND bsns_year = %s
"""

# 비중 합계가 이 범위를 벗어나면 못 믿는다. 실측: 에스피지 200%(수출/내수를
# 각각 100%로 잡음) · 현대차증권 6%(증권사라 매출 구조가 제조업과 다름).
_RATIO_LO, _RATIO_HI = 85.0, 115.0

_RATIOS = """
SELECT s.corp_code, co.name, s.bsns_year, sum(s.revenue_ratio) AS total, count(*) AS n
  FROM business_segments s JOIN companies co ON co.corp_code = s.corp_code
 WHERE s.revenue_ratio IS NOT NULL
 GROUP BY s.corp_code, co.name, s.bsns_year
"""


def _pick_scale(seg_sum: int, total: int) -> int | None:
    """부문합 × scale 이 전사매출에 가장 가까워지는 배수. 없으면 None."""
    best, best_err = None, None
    for sc in _SCALES:
        ratio = (seg_sum * sc) / total
        if not (_TOL_LO <= ratio <= _TOL_HI):
            continue
        err = abs(ratio - 1.0)
        if best_err is None or err < best_err:
            best, best_err = sc, err
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with postgres_connection() as conn:
        rows = conn.execute(_FETCH).fetchall()

    fixed, already, unknown, no_total = [], [], [], []
    for code, name, year, seg_sum, n_rev, n_seg, total in rows:
        if not total or not seg_sum:
            no_total.append((code, name, year, n_seg,
                             "전사 매출액이 없어 대조 불가" if not total
                             else "부문 매출액이 전부 비어 있음"))
            continue
        scale = _pick_scale(int(seg_sum), int(total))
        if scale is None:
            unknown.append((code, name, year, int(seg_sum), int(total), total / seg_sum))
        elif scale == 1:
            already.append(name)
        else:
            fixed.append((code, name, year, scale, int(seg_sum), int(total), n_rev))

    print(f"대상 {len(rows)}개사 · 정상 {len(already)} · 교정 {len(fixed)} · "
          f"판단보류 {len(unknown)} · 비교불가 {len(no_total)}\n")

    if fixed:
        print(f"{'기업':14}{'배수':>10}  {'부문합(교정 후)':>20}{'전사매출':>20}{'차이':>8}")
        print("─" * 76)
        for _, name, _, sc, ss, tot, _ in sorted(fixed, key=lambda x: -x[3]):
            after = ss * sc
            print(f"{name:14}{sc:>10,}  {after:>20,}{tot:>20,}"
                  f"{after / tot * 100 - 100:>7.0f}%")

    if unknown:
        print(f"\n■ 판단보류 {len(unknown)}건 — 어떤 배수로도 전사매출에 안 맞는다."
              f" 값은 그대로 두고 revenue_trusted=FALSE로 표시한다.")
        for _, name, _, ss, tot, m in sorted(unknown, key=lambda x: -x[5])[:15]:
            print(f"   {name:14} 부문합 {ss:>18,} · 전사 {tot:>18,} · 배수 {m:>12,.1f}")

    if no_total:
        print(f"\n■ 비교불가 {len(no_total)}건")
        for _, name, _, n, why in no_total[:10]:
            print(f"   {name:14} 부문 {n}개 — {why}")

    # ── 비중 합계 검사 ────────────────────────────────────────
    # 단위(금액)와 **따로** 본다. 금액이 맞아도 비중만 틀린 경우가 있다.
    with postgres_connection() as conn:
        ratios = conn.execute(_RATIOS).fetchall()
    bad_ratio = [(c, n, y, float(t)) for c, n, y, t, _ in ratios
                 if not (_RATIO_LO <= float(t) <= _RATIO_HI)]
    print(f"\n■ 비중 합계 검사 — 정상 {len(ratios)-len(bad_ratio)} · "
          f"이상 {len(bad_ratio)}건 ({_RATIO_LO:.0f}~{_RATIO_HI:.0f}% 밖)")
    for _, name, _, t in sorted(bad_ratio, key=lambda x: -x[3]):
        print(f"   {name:14} 합계 {t:>6.0f}%")

    if args.dry_run:
        print("\n[dry-run] 변경하지 않았습니다.")
        return 0

    n = 0
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            for code, _, year, scale, *_ in fixed:
                cur.execute(_APPLY, (scale, code, year))
                n += cur.rowcount
            # 교정 못 한 것은 **지우지 않고 못 믿는다고 표시**한다
            for code, _, year, _, _, m in unknown:
                cur.execute(_DISTRUST_REV,
                            (f"부문 매출 합이 전사 매출의 1/{m:,.0f} — "
                             f"단위를 판정할 수 없음", code, year))
            for code, _, year, _, why in no_total:
                cur.execute(_DISTRUST_REV, (why, code, year))
            for code, name, year, total in bad_ratio:
                cur.execute(_DISTRUST_RATIO,
                            (f"비중 합계 {total:.0f}% — 100%에서 벗어남", code, year))
            ok_ratio = {(c, y) for c, _, y, _, _ in ratios} - {
                (c, y) for c, _, y, _ in bad_ratio}
            for code, year in ok_ratio:
                cur.execute(_TRUST_RATIO, (code, year))

    print(f"\n✅ {len(fixed)}개사 · {n}개 부문의 매출액 단위 교정")
    if unknown or no_total:
        print(f"⚠ {len(unknown)+len(no_total)}개사는 revenue_trusted=FALSE — "
              f"화면에서 금액을 감추고 비중만 쓰세요.")
    if bad_ratio:
        print(f"⚠ {len(bad_ratio)}개사는 ratio_trusted=FALSE — 비중을 감추세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
