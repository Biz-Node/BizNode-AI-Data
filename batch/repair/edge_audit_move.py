"""엣지의 **검사 사유·이력**을 PostgreSQL 로 옮긴다.

왜 (2026-08-15)

엣지 속성 97가지 중 절반쯤이 「파이프라인이 무엇을 했는지」의 기록이다. 그런데
그 안에서도 성격이 갈린다:

    조회할 때 쓴다 → 엣지에 남긴다
        *_checked_at      증분 배치의 「이미 봤다」 표시. 지우면 11,115건 재검사
        *_suspect         조회에서 거른다 — `graph_service._HIDE`
        grounding_verdict confirmed / mistyped

    사람이 한 번 읽을 뿐이다 → PostgreSQL 로
        grounding_reason · grounding_verdict_why · ended_reason · retype_rejected
        subtype_backfilled · cluster_rep_fixed · loaded_at …

**사유 문장은 조회에 안 쓴다.** 「왜 이렇게 판정했지」를 사람이 확인할 때만
필요하므로 그래프에 둘 이유가 없다. 엣지 삭제 기록(`purged_edges`)이 이미
같은 방식이다.

★키를 `evidence_id` 로 못 쓴다 — 11,115건 중 서로 다른 값이 9,242개다(한 근거가
  여러 엣지를 낳는다). 그래서 **출발·도착·유형·근거를 함께** 적어 되짚는다.

실행:
    python -m batch.repair.edge_audit_move --dry-run
    python -m batch.repair.edge_audit_move
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 옮길 속성 — 값은 「무엇을 기록한 것인가」
MOVE = {
    "grounding_reason":       "1차 근거검증이 그렇게 본 이유",
    "grounding_verdict_why":  "2차 판정 근거",
    "ended_reason":           "관계가 끝났다고 본 이유",
    "retype_rejected":        "타입 교정 제안을 거절한 사유",
    "parsed_suspect_why":     "공시 파싱을 의심한 이유",
    "field_suspect_why":      "값이 이상하다고 본 이유",
    "retarget_why":           "엣지 상대를 옮긴 이유",
    "subtype_backfilled":     "subtype 을 소급해 채웠다",
    "subtype_moved_to_node":  "subtype 정보를 상대 노드로 옮겼다",
    "subtype_corrected_from": "고치기 전 subtype",
    "cluster_rep_fixed":      "대표값 선정을 손봤다",
    "retype_source":          "타입 교정을 제안한 주체",
    "retype_rechecked":       "교정 재확인 완료",
    "retype_stale_cleared":   "오래된 교정 표시 해제",
    "title_source":           "직위를 알아낸 방법",
    "first_seen_note":        "first_seen 을 추정한 이유",
    "first_seen_estimated":   "first_seen 이 추정값이다",
    # ★`loaded_at` 은 옮기면 안 된다(2026-08-15에 옮겼다가 되돌림).
    #   운영 흔적처럼 보이지만 **검사가 쓰는 값**이다 — `audit/freshness.run_dart`가
    #   기업별 최신 적재 시각과 견줘 「DART 재적재에서 사라진 엣지」를 찾는다.
    #   옮긴 뒤 그 검사가 「적재시각 있는 것 0건」으로 아무것도 못 찾았다.
    "ratio_from_subtype":     "ratio 를 subtype 에서 뽑았다",
    "suspect_edge_type":      "의심되는 엣지 타입",
    "converted_from":         "다른 엣지에서 변환됐다",
    "split_from":             "분리 전 노드",
}

_TABLE = """
CREATE TABLE IF NOT EXISTS edge_audits (
    id          BIGSERIAL PRIMARY KEY,
    src_name    TEXT,
    edge_type   TEXT NOT NULL,
    tgt_name    TEXT,
    evidence_id TEXT,
    source_doc  TEXT,
    trail       JSONB NOT NULL,     -- {속성: 값} — 옮긴 것 전부
    moved_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_edge_audits_ev
    ON edge_audits (evidence_id) WHERE evidence_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edge_audits_type ON edge_audits (edge_type);
"""

_FIND = """
MATCH (a)-[r]->(b)
WITH r, a, b, [k IN keys(r) WHERE k IN $props] AS hit
WHERE size(hit) > 0
RETURN elementId(r) AS id, type(r) AS t,
       coalesce(a.name, '') AS src, coalesce(b.name, '') AS tgt,
       r.evidence_id AS ev, r.source_doc AS doc,
       [k IN hit | [k, toString(r[k])]] AS trail
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    props = list(MOVE)
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND, props=props)]
        counts = {}
        for p in props:
            n = s.run(f"MATCH ()-[r]->() WHERE r.`{p}` IS NOT NULL "
                      f"RETURN count(*) AS n").single()["n"]
            if n:
                counts[p] = n

    print(f"■ 검사 사유·이력을 PG 로 — 엣지 {len(rows):,}건 · 속성값 "
          f"{sum(counts.values()):,}개")
    for p, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {p:<26}{n:>6}   {MOVE[p]}")
    if args.dry_run or not rows:
        print("\n[dry-run] 옮기지 않았습니다.")
        return 0

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_TABLE)
        for r in rows:
            cur.execute("""INSERT INTO edge_audits
                (src_name, edge_type, tgt_name, evidence_id, source_doc, trail)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (r["src"], r["t"], r["tgt"], r["ev"], r["doc"],
                 json.dumps(dict(r["trail"]), ensure_ascii=False)))
    print(f"  → edge_audits {len(rows):,}행 기록")

    with neo4j_session() as s:
        for p in counts:
            s.run(f"MATCH ()-[r]->() WHERE r.`{p}` IS NOT NULL REMOVE r.`{p}`")
        left = [r["k"] for r in s.run(
            "MATCH ()-[r]->() UNWIND keys(r) AS k RETURN DISTINCT k AS k")]
    print(f"  → 엣지에서 {len(counts)}종 삭제 · 남은 속성 {len(left)}가지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
