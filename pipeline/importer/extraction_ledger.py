"""기업별 추출 진행 이력.

64개사를 한 번에 못 돌린다(예산). 여러 번에 나눠 돌리므로 **어디까지 했는지**를
기록해야 한다. 없으면 같은 기업을 두 번 돌리거나 빠뜨린다.

기록하는 것:
  · 언제 · 몇 년치 · 추출 상한 얼마로 돌렸는지
  · 깔때기 각 단계 결과 (수집 → 규칙 → 해석 → 본문 → 라우터 → 엣지)
  · 비용 추정

이걸 보면 "다음에 어느 기업을 얼마나 더 돌려야 하는지"가 나온다.
"""

from __future__ import annotations

from typing import Optional

_CREATE = """
CREATE TABLE IF NOT EXISTS extraction_runs (
    corp_code     CHAR(8)     NOT NULL,
    company_name  TEXT        NOT NULL,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    years         SMALLINT,                -- 수집 대상 기간(년)
    month_split   BOOLEAN     DEFAULT false,
    extract_limit INT,                     -- 추출 상한
    -- 깔때기 실측
    collected     INT,
    rule_passed   INT,
    url_resolved  INT,
    body_ok       INT,
    router_passed INT,
    extracted     INT,                     -- 실제 추출한 기사 수
    edges         INT,                     -- 만들어진 엣지
    cost_krw      INT,                     -- 추정 비용(원)
    note          TEXT,
    PRIMARY KEY (corp_code, run_at)
);
CREATE INDEX IF NOT EXISTS idx_extraction_runs_corp ON extraction_runs (corp_code);
"""

_INSERT = """
INSERT INTO extraction_runs
    (corp_code, company_name, years, month_split, extract_limit,
     collected, rule_passed, url_resolved, body_ok, router_passed,
     extracted, edges, cost_krw, note)
VALUES (%(corp_code)s, %(company_name)s, %(years)s, %(month_split)s, %(extract_limit)s,
        %(collected)s, %(rule_passed)s, %(url_resolved)s, %(body_ok)s, %(router_passed)s,
        %(extracted)s, %(edges)s, %(cost_krw)s, %(note)s)
"""

_SUMMARY = """
SELECT corp_code, company_name,
       count(*)          AS runs,
       max(run_at)       AS last_run,
       sum(extracted)    AS total_extracted,
       sum(edges)        AS total_edges,
       sum(cost_krw)     AS total_cost,
       max(years)        AS max_years
FROM extraction_runs
GROUP BY corp_code, company_name
"""


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE)


def record(conn, **fields) -> None:
    """한 번의 추출 실행을 기록한다."""
    ensure_table(conn)
    payload = {k: fields.get(k) for k in (
        "corp_code", "company_name", "years", "month_split", "extract_limit",
        "collected", "rule_passed", "url_resolved", "body_ok", "router_passed",
        "extracted", "edges", "cost_krw", "note")}
    payload["month_split"] = bool(payload.get("month_split"))
    with conn.cursor() as cur:
        cur.execute(_INSERT, payload)


def summary(conn) -> list[dict]:
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute(_SUMMARY)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def pending(conn, seeds: list[dict], *, min_extracted: int = 1) -> list[dict]:
    """아직 충분히 안 돌린 기업. seeds = [{corpCode, companyName}, ...]"""
    done = {s["corp_code"]: s for s in summary(conn)}
    out = []
    for c in seeds:
        rec = done.get(c["corpCode"])
        if rec is None or (rec["total_extracted"] or 0) < min_extracted:
            out.append({**c, "extracted": (rec or {}).get("total_extracted") or 0})
    return out


def resolve_corp_code(conn, name: str) -> Optional[str]:
    """기업명 → corp_code (시드 우선)."""
    with conn.cursor() as cur:
        cur.execute("SELECT corp_code FROM company_attributes WHERE name = %s AND corp_code IS NOT NULL LIMIT 1", (name,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT corp_code FROM corp_code_master WHERE corp_name = %s "
                    "ORDER BY (stock_code IS NULL) LIMIT 1", (name,))
        row = cur.fetchone()
        return row[0] if row else None
