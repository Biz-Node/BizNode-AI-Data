"""시장 정보 — 주가·거래량·등락률을 받고, 지표는 **뷰로 계산**한다.

★어디서 받나 (2026-08-15)

    주가·거래량·등락률   pykrx        KRX 공개 데이터 · 인증 없음 · 무료
    유통주식수          DART         stockTotqySttus · 이미 키가 있음
    순이익·자본·매출     financials   이미 있음

★왜 시가총액·PER·PBR 을 **저장하지 않나**

  전부 위 셋에서 계산되는 값이다. 저장하면 원본과 어긋난다 — 실제로 엣지에서
  같은 실수를 했다(`ratio_change` 1,306건 중 15건이 안 맞았다).

      시가총액 = 종가 × 유통주식수
      PER     = 시가총액 ÷ 당기순이익
      PBR     = 시가총액 ÷ 자본총계
      PSR     = 시가총액 ÷ 매출액

  그래서 `market_metrics` **뷰**를 만든다. 조회는 한 줄이고 값은 항상 맞는다.

★남의 API 에서 PER 을 받아 오지 않는 이유가 하나 더 있다 — **기준을 모른다.**
  연결인지 별도인지, 어느 분기 실적인지, 우선주를 포함했는지가 제공처마다 다르다.
  우리 `financials` 는 `fs_div` 로 연결·별도를 구분해 두었으므로 직접 계산하면
  **화면에서 「2025년 연결 기준」이라고 밝힐 수 있다.**

★자기주식을 뺀 유통주식수를 쓴다. 회사가 들고 있는 주식은 시장에 없다.

실행:
    python -m batch.build.market_data --dry-run
    python -m batch.build.market_data                # 최근 6개월
    python -m batch.build.market_data --months 12
    python -m batch.build.market_data --shares-only  # 주식수만 갱신
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.dart.shares import fetch_shares

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_SLEEP_DART = 0.2
_SLEEP_KRX = 0.15

# ── 스키마 보강 ───────────────────────────────────────────────
# 기존 표에 없던 것: 시가·고가·저가·등락률. 등락률은 pykrx 가 계산해서 준다.
_ALTER = """
ALTER TABLE market_data ADD COLUMN IF NOT EXISTS open_price  BIGINT;
ALTER TABLE market_data ADD COLUMN IF NOT EXISTS high_price  BIGINT;
ALTER TABLE market_data ADD COLUMN IF NOT EXISTS low_price   BIGINT;
ALTER TABLE market_data ADD COLUMN IF NOT EXISTS change_pct  NUMERIC(8,2);
"""

# 유통주식수는 날짜마다 안 변하므로 따로 둔다. 시세 행마다 넣으면 같은 값이
# 수백 번 복사되고, 주식수가 바뀌면 과거 행까지 고쳐야 한다.
_SHARES_TABLE = """
CREATE TABLE IF NOT EXISTS listed_shares (
    corp_code    CHAR(8) PRIMARY KEY,
    stock_code   VARCHAR(6),
    listed       BIGINT NOT NULL,     -- 유통주식수 (발행총수 - 자기주식)
    issued       BIGINT,              -- 발행한 주식의 총수
    treasury     BIGINT,              -- 자기주식
    bsns_year    SMALLINT,
    reprt_code   VARCHAR(5),
    -- ★DART 원본이 틀릴 수 있다. 실측: LS에코에너지가 30조 주로 들어온다
    --   (회사가 공시에 단위를 잘못 적음). 지우지 않고 표시해 지표에서만 뺀다.
    suspect      BOOLEAN NOT NULL DEFAULT FALSE,
    suspect_why  TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_listed_shares_stock ON listed_shares (stock_code);
"""

# ★지표는 **뷰로 계산한다.** 저장하지 않는 이유는 파일 머리 참고.
#   재무는 가장 최근 연도를 쓰고, 어느 연도·어느 기준인지 함께 돌려준다 —
#   「PER 12.3」만 주면 화면이 그 근거를 못 밝힌다.
_VIEW = """
CREATE OR REPLACE VIEW market_metrics AS
WITH latest_fin AS (
    SELECT DISTINCT ON (corp_code)
           corp_code, bsns_year, fs_div, revenue, net_profit, total_equity
    FROM financials
    ORDER BY corp_code, bsns_year DESC
)
SELECT
    s.corp_code,
    m.stock_code,
    m.trade_date,
    m.close_price,
    m.change_pct,
    m.volume,
    m.trade_value,
    s.listed                                   AS listed_shares,
    m.close_price::numeric * s.listed          AS market_cap,
    f.bsns_year                                AS fin_year,
    f.fs_div,
    CASE WHEN f.net_profit  > 0
         THEN round(m.close_price::numeric * s.listed / f.net_profit,  2) END AS per,
    CASE WHEN f.total_equity > 0
         THEN round(m.close_price::numeric * s.listed / f.total_equity, 2) END AS pbr,
    CASE WHEN f.revenue      > 0
         THEN round(m.close_price::numeric * s.listed / f.revenue,      2) END AS psr
FROM market_data m
JOIN listed_shares s ON s.stock_code = m.stock_code AND NOT s.suspect
LEFT JOIN latest_fin f ON f.corp_code = s.corp_code
"""

_UPSERT_SHARES = """
INSERT INTO listed_shares
    (corp_code, stock_code, listed, issued, treasury, bsns_year, reprt_code,
     suspect, suspect_why)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (corp_code) DO UPDATE SET
    stock_code=EXCLUDED.stock_code, listed=EXCLUDED.listed,
    issued=EXCLUDED.issued, treasury=EXCLUDED.treasury,
    bsns_year=EXCLUDED.bsns_year, reprt_code=EXCLUDED.reprt_code,
    suspect=EXCLUDED.suspect, suspect_why=EXCLUDED.suspect_why,
    updated_at=now()
"""

_UPSERT_PRICE = """
INSERT INTO market_data
    (stock_code, trade_date, open_price, high_price, low_price,
     close_price, volume, trade_value, change_pct, source)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pykrx')
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
    low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
    volume=EXCLUDED.volume, trade_value=EXCLUDED.trade_value,
    change_pct=EXCLUDED.change_pct
"""


def _targets() -> list[tuple[str, str, str]]:
    """(corp_code, stock_code, name) — 종목코드가 있는 상장사만."""
    with neo4j_session() as s:
        return [(r["cc"], r["sc"], r["n"]) for r in s.run(
            """MATCH (c:Company) WHERE c.stock_code IS NOT NULL AND c.corp_code IS NOT NULL
               OPTIONAL MATCH (c)-[e]-()
               RETURN c.corp_code AS cc, c.stock_code AS sc, c.name AS n, count(e) AS d
               ORDER BY d DESC""")]


def run_shares(targets, dry: bool) -> int:
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_SHARES_TABLE)
        cur.execute("SELECT corp_code FROM listed_shares")
        done = {r[0].strip() for r in cur.fetchall()}
    todo = [t for t in targets if t[0] not in done]
    print(f"■ 유통주식수 — 상장사 {len(targets)}곳 · 이미 받음 {len(done)} · 대상 {len(todo)}")
    if dry or not todo:
        return 0
    ok = fail = 0
    with postgres_connection() as conn:
        for i, (cc, sc, nm) in enumerate(todo, 1):
            info = fetch_shares(cc)
            time.sleep(_SLEEP_DART)
            if not info:
                fail += 1
                continue
            with conn.cursor() as cur:
                cur.execute(_UPSERT_SHARES, (cc, sc, info["listed"], info["issued"],
                                             info["treasury"], info["bsns_year"],
                                             info["reprt_code"], info["suspect"],
                                             info["suspect_why"]))
            ok += 1
            if info["suspect"]:
                print(f"   ⚠ {nm[:16]:<18}{info['suspect_why']}")
            if ok <= 3 or i % 100 == 0:
                print(f"   [{i}/{len(todo)}] {nm[:16]:<18}유통 {info['listed']:>15,}주")
    print(f"   ✅ {ok}곳 · 못 구함 {fail}곳")
    return ok


def run_prices(targets, months: int, dry: bool) -> int:
    from pykrx import stock
    end = date.today()
    start = end - timedelta(days=months * 31)
    f, t = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    print(f"\n■ 주가 — {start} ~ {end} ({months}개월) · 종목 {len(targets)}곳")
    print(f"   pykrx 호출 {len(targets)}회 · 약 {len(targets)*_SLEEP_KRX/60:.0f}분 · 무료")
    if dry:
        return 0

    rows_n = ok = empty = 0
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_ALTER)
    with postgres_connection() as conn:
        for i, (cc, sc, nm) in enumerate(targets, 1):
            try:
                df = stock.get_market_ohlcv(f, t, sc)
            except Exception:
                empty += 1
                continue
            time.sleep(_SLEEP_KRX)
            if df is None or df.empty:
                empty += 1
                continue
            with conn.cursor() as cur:
                for d, r in df.iterrows():
                    cur.execute(_UPSERT_PRICE, (
                        sc, d.date(), int(r["시가"]), int(r["고가"]), int(r["저가"]),
                        int(r["종가"]), int(r["거래량"]),
                        int(r.get("거래대금", 0) or 0), float(r["등락률"])))
                    rows_n += 1
            ok += 1
            if ok <= 3 or i % 100 == 0:
                last = df.iloc[-1]
                print(f"   [{i}/{len(targets)}] {nm[:16]:<18}{len(df):>4}일 · "
                      f"최근 종가 {int(last['종가']):>9,}원 ({last['등락률']:+.2f}%)")
    print(f"   ✅ {ok}종목 · {rows_n:,}행 · 시세 없음 {empty}곳")
    return rows_n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--months", type=int, default=6, help="받을 기간(개월). 기본 6")
    ap.add_argument("--shares-only", action="store_true", help="주식수만 갱신")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    targets = _targets()
    if args.limit:
        targets = targets[:args.limit]

    run_shares(targets, args.dry_run)
    if not args.shares_only:
        run_prices(targets, args.months, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] 받지 않았습니다.")
        return 0

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_VIEW)
        cur.execute("""SELECT count(*), count(DISTINCT stock_code),
                       min(trade_date), max(trade_date) FROM market_data""")
        n, c, mn, mx = cur.fetchone()
        print(f"\nmarket_data {n:,}행 · {c}종목 · {mn} ~ {mx}")
        cur.execute("SELECT count(*) FROM market_metrics WHERE per IS NOT NULL")
        print(f"market_metrics 뷰 생성 · PER 계산 가능 {cur.fetchone()[0]:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
