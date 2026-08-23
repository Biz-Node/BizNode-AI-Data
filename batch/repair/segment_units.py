"""`business_segments.revenue`의 **단위 오류**를 고친다.

왜 필요한가 (2026-08-01)

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
  JOIN company_attributes co ON co.corp_code = s.corp_code
  LEFT JOIN financials f
         ON f.corp_code = s.corp_code AND f.bsns_year = s.bsns_year
 WHERE coalesce(s.trust_reason,'') NOT LIKE '합계 행%'
 GROUP BY s.corp_code, co.name, s.bsns_year
 ORDER BY co.name
"""

_APPLY = """
UPDATE business_segments SET revenue = revenue * %s, revenue_trusted = TRUE,
       trust_reason = NULL
 WHERE corp_code = %s AND bsns_year = %s AND revenue IS NOT NULL
   AND coalesce(trust_reason,'') NOT LIKE '합계 행%%'
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

# ★합계 행은 집계에서 뺀다(`trust_reason`으로 표시해 둔 것).
_NOT_TOTAL = "coalesce(s.trust_reason,'') NOT LIKE '합계 행%'"

_RATIOS = f"""
SELECT s.corp_code, co.name, s.bsns_year, sum(s.revenue_ratio) AS total, count(*) AS n
  FROM business_segments s JOIN company_attributes co ON co.corp_code = s.corp_code
 WHERE s.revenue_ratio IS NOT NULL AND {_NOT_TOTAL}
 GROUP BY s.corp_code, co.name, s.bsns_year
"""


# ── 2차: 비중으로 금액을 되짚는다 ────────────────────────────
#
# ★배수 스냅이 실패해도 포기할 게 아니다(2026-08-03 추가). 실측:
#
#     LG전자   부문합 8.79조 · 전사 89.2조 · 배수 10.1  ← 표준 단위가 아니라 스냅 실패
#              그런데 **비중 합계는 99%**다 — 부문은 다 뽑혔고 금액만 틀렸다는 뜻
#     클로봇   배수 1.8 · 비중 100%
#     뉴로메카  배수 100.0 · 비중 100%
#
#   비중이 100% 근처면 **전사매출 × 비중**으로 금액을 다시 만들 수 있다.
#   이건 파싱된 금액보다 믿을 만하다 — 전사매출은 `financials`에서 온
#   확정 수치이고, 비중은 표 안에 백분율로 적혀 있어 단위 문제가 없다.
#
#   되짚은 값은 원문 그대로가 아니므로 **출처를 표시**한다(`trust_reason`).
_RATIO_TIGHT_LO, _RATIO_TIGHT_HI = 95.0, 105.0

# ── 합계 행 걷어내기 ──────────────────────────────────────────
#
# ★사업보고서 표의 **맨 아래 합계 줄을 부문으로 뽑는 일**이 있다(2026-08-03).
#
#     에스피지  「AC Motor 외,Condenser/Contr…」  100.0%   ← 합계 줄
#              AC Motor 외 35.8% · BLDC 21.1% · Condenser 20.5% · 기타 15.2% · DC 7.3%
#                                                  나머지 합 99.9%
#
#   합계 줄이 섞이면 비중 합이 200%가 되어 「비중을 못 믿는다」로 넘어간다.
#   실제로는 나머지가 정확히 99.9%라 **되짚기가 되는 데이터**였다.
#
#   판별: 비중이 100% 근처인 행이 있고, **그 행을 뺀 나머지 합도 100% 근처**면
#   그 행은 부문이 아니라 합계다. 지우지 않고 `is_total`처럼 표시만 남긴다.
_TOTAL_REASON = "합계 행 — 부문이 아니라 표의 총계 줄입니다(집계에서 제외)"

_MARK_TOTAL = """
UPDATE business_segments SET ratio_trusted = FALSE, revenue_trusted = FALSE,
       trust_reason = %s
 WHERE corp_code = %s AND bsns_year = %s AND segment_name = %s
"""

_SEG_ROWS = """
SELECT corp_code, bsns_year, segment_name, revenue_ratio
  FROM business_segments WHERE revenue_ratio IS NOT NULL
"""


def find_total_rows(conn) -> list[tuple]:
    """합계 줄로 보이는 행 목록. (corp_code, year, segment_name)"""
    groups: dict[tuple, list] = {}
    for code, year, name, ratio in conn.execute(_SEG_ROWS).fetchall():
        groups.setdefault((code, year), []).append((name, float(ratio)))
    out = []
    for (code, year), rows in groups.items():
        if len(rows) < 3:                     # 합계+부문 1개면 판별할 수 없다
            continue
        for name, ratio in rows:
            if not (98.0 <= ratio <= 102.0):
                continue
            rest = sum(r for n, r in rows if n != name)
            if _RATIO_TIGHT_LO <= rest <= _RATIO_TIGHT_HI:
                out.append((code, year, name))
    return out

_APPLY_FROM_RATIO = """
UPDATE business_segments
   SET revenue = round(%s::numeric * revenue_ratio / 100)::bigint,
       revenue_trusted = TRUE,
       trust_reason = '비중으로 되짚은 금액 (원문 금액은 단위가 어긋남)'
 WHERE corp_code = %s AND bsns_year = %s AND revenue_ratio IS NOT NULL
   AND coalesce(trust_reason,'') NOT LIKE '합계 행%%'
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

    # ★합계 행부터 걷어낸다. 그래야 아래 비중 합계가 200%로 뜨지 않는다.
    with postgres_connection() as conn:
        totals = find_total_rows(conn)
        if totals and not args.dry_run:
            with conn.cursor() as cur:
                for code, year, name in totals:
                    cur.execute(_MARK_TOTAL, (_TOTAL_REASON, code, year, name))
        if totals:
            print(f"■ 합계 행으로 판별해 집계에서 뺀 것 {len(totals)}건")
            for code, year, name in totals[:6]:
                print(f"   {name[:46]}")
            print()

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

    # ── 2차: 배수 스냅 실패분을 **비중으로 되짚는다** ──────────────
    with postgres_connection() as conn:
        rsum = {(c, y): float(t) for c, _, y, t, _ in conn.execute(_RATIOS).fetchall()}
    rebuilt, still = [], []
    for code, name, year, seg_sum, total, mult in unknown:
        rt = rsum.get((code, year))
        if rt is not None and _RATIO_TIGHT_LO <= rt <= _RATIO_TIGHT_HI:
            rebuilt.append((code, name, year, total, rt, mult))
        else:
            still.append((code, name, year, seg_sum, total, mult))
    unknown = still

    print(f"대상 {len(rows)}개사 · 정상 {len(already)} · 배수교정 {len(fixed)} · "
          f"비중으로 되짚음 {len(rebuilt)} · 판단보류 {len(unknown)} · "
          f"비교불가 {len(no_total)}\n")

    if rebuilt:
        print(f"■ 비중으로 되짚은 {len(rebuilt)}건 — 부문은 다 뽑혔는데 금액만 어긋난 경우")
        print(f"   (비중 합계가 {_RATIO_TIGHT_LO:.0f}~{_RATIO_TIGHT_HI:.0f}%라 부문 누락이 아님이 확인됨)")
        for _, name, year, total, rt, mult in sorted(rebuilt, key=lambda x: -x[5]):
            print(f"   {name:14}{year}  원문 금액이 전사의 1/{mult:,.0f} · "
                  f"비중합 {rt:.0f}% → 전사 {total:,}원 × 비중으로 재계산")
        print()

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
            # 비중으로 되짚기 — 배수 스냅이 실패했지만 부문은 다 있는 경우
            for code, _, year, total, *_ in rebuilt:
                cur.execute(_APPLY_FROM_RATIO, (int(total), code, year))
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
