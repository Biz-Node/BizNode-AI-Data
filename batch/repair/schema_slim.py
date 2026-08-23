"""Company 노드 속성 정리 — 49가지 → 12가지.

왜 (2026-08-15)

노드가 속성 49가지를 이고 있었다. 실제로 한 노드가 드는 건 평균 13개인데,
그 13개조차 대부분 **그래프가 쓰지 않는 것**이었다 — 대표자명·설립일·업종 설명은
경로를 따라갈 때 안 쓰고 상세 화면에서 한 건씩 볼 뿐이다.

정리 기준은 하나다: **Neo4j 에는 화면에 띄우거나 노드·엣지를 가르는 데
쓰는 것만 둔다.** 나머지는 PostgreSQL 이 갖는다.

    남김(12)  name · norm_name · entity_kind · is_stub · first_seen
              corp_code · stock_code · market
              candidate_corp_codes · candidate_count
              ksic · also_names

일곱 단계로 나눈다. 각 단계는 혼자 돌려도 되지만 **순서를 지켜야 한다** —
옮기기 전에 지우면 값을 잃는다.

    1  후보 목록 복구      뭉개진 42곳을 variants 에서 되살린다
    2  사업부문 병합       12곳을 부모 회사로 흡수 (part_of_* 3필드가 같이 사라짐)
    3  재무 → 엣지         total_assets/net_profit 을 OWNS_STAKE_IN 으로
    4  PG 이관            12필드를 company_attributes 표로 (신설)
    5  고아 정리          엣지 0 인 노드를 purged_nodes 에 남기고 삭제
    6  분류 채우기         entity_kind 빈칸을 규칙으로 (해외 포함)
    7  속성 삭제          위에서 옮긴 것 + 신뢰도·출처 메타 + 병합 흉터

실행:
    python -m batch.repair.schema_slim --dry-run     # 무엇이 바뀌는지만
    python -m batch.repair.schema_slim               # 전 단계
    python -m batch.repair.schema_slim --step 2      # 한 단계만
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.database import neo4j_session, postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── PG 로 옮길 필드 → companies 컬럼 ────────────────────────────
# 값 이름이 바뀌는 둘: revenue_snapshot → revenue, fin_year_snapshot → revenue_year
PG_MOVE = {
    "induty": "induty",
    "ceo_nm": "ceo_nm",
    "est_dt": "est_dt",
    "name_en": "name_en",
    "sector_label": "sector_label",
    "sector": "sector",
    "etf_list": "etf_list",
    "is_seed": "is_seed",
    "vehicle_type": "vehicle_type",
    "resolution_note": "resolution_note",
    "revenue_snapshot": "revenue_snapshot",
    "fin_year_snapshot": "revenue_year",
}

# 노드에서 완전히 지울 속성 — 옮긴 것 + 신뢰도/출처 메타 + 병합 흉터
DROP = [
    # PG 로 옮긴 것
    *PG_MOVE.keys(),
    # OWNS_STAKE_IN 엣지로 옮긴 것 (3단계)
    "total_assets", "net_profit",
    # 신뢰도·출처 메타 — 실서비스 화면에 안 쓴다. 다시 뽑는 게 60원이라 골라 뽑을 이유가 없다
    "ksic_source", "sector_confidence", "sector_source", "first_seen_estimated",
    # 계산으로 대체 — corp_code 와 candidate_corp_codes 가 같이 있으면 ambiguous 다
    "resolution_status",
    # also_names 로 합침
    "merged_names", "split_from",
    # 타입 오염 — Person 이었다가 옮겨온 흔적. relabeled_from 이 이미 이력을 남긴다
    "person_key", "absorbed_person",
    # 사업부문 병합으로 불필요
    "part_of_name", "part_of_unit", "part_of_corp_code",
    # 고아 → PG purged_nodes
    "is_orphan", "orphan_since", "orphan_reason",
    # 병합 흉터. candidate_corp_codes_variants 는 1단계에서 원본 복구에 쓰고 지운다
    "resolution_status_variants", "name_variants", "merged_names_variants",
    "ksic_source_variants", "is_stub_variants", "first_seen_variants",
    "ksic_variants", "candidate_corp_codes_variants",
]

# ★corp_code 가 없는 회사(해외·미등록)도 담아야 하므로 PK 를 못 쓴다.
#   corp_code_master 를 참조하는 기존 FK 도 걸림돌이라 별도 표로 뺀다.
_PROFILE_TABLE = """
CREATE TABLE IF NOT EXISTS company_attributes (
    node_key    TEXT PRIMARY KEY,   -- corp_code 또는 norm_name (Neo4j 식별자와 같음)
    corp_code   CHAR(8),
    name        TEXT NOT NULL,
    norm_name   TEXT,
    induty      TEXT,
    ceo_nm      TEXT,
    est_dt      DATE,
    name_en     TEXT,
    sector_label TEXT,
    sector      JSONB,
    etf_list    JSONB,
    is_seed     BOOLEAN NOT NULL DEFAULT FALSE,
    vehicle_type TEXT,
    resolution_note TEXT,
    revenue_snapshot BIGINT,
    revenue_year  SMALLINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_company_attributes_corp
    ON company_attributes (corp_code) WHERE corp_code IS NOT NULL;
"""

_PURGED_TABLE = """
CREATE TABLE IF NOT EXISTS purged_nodes (
    id         BIGSERIAL PRIMARY KEY,
    purged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    label      TEXT NOT NULL,
    node_key   TEXT,
    name       TEXT,
    reason     TEXT,
    props      JSONB
);
"""


# ─────────────────────────────────────────────────────────────
def step1_restore_candidates(dry: bool) -> None:
    """뭉개진 후보 목록을 되살린다.

    ★병합 배치의 「목록을 대표값 스칼라로 되돌리기」가 **원래 목록이어야 할
      필드에까지** 적용됐다. 42곳의 후보가 첫 번째 하나로 잘려 있고 원본은
      `candidate_corp_codes_variants` 에만 남아 있다.
          세종  count=9  ·  candidate_corp_codes="01315732"  ← 문자열 하나
    """
    with neo4j_session() as s:
        n = s.run("""MATCH (c:Company)
            WHERE c.candidate_corp_codes_variants IS NOT NULL
            RETURN count(*) AS n""").single()["n"]
        print(f"  후보 목록이 뭉개진 노드 {n}곳")
        if dry or not n:
            return
        s.run("""MATCH (c:Company)
            WHERE c.candidate_corp_codes_variants IS NOT NULL
            SET c.candidate_corp_codes = c.candidate_corp_codes_variants""")
        print(f"  → {n}곳 복구")


def step2_merge_divisions(dry: bool) -> None:
    """사업부문 노드를 부모 회사로 흡수한다.

    ★왜 노드로 두지 않나: 사업부문은 **별개 법인이 아니다.** 「삼성전자 MX 사업부」는
      삼성전자가 보유한 회사가 아니라 삼성전자 자신이다. 그래서 12종 엣지 어디에도
      「~의 사업부문이다」를 담을 자리가 없고, 지금은 `part_of_*` 세 필드만
      그 사실을 알고 있다. 노드를 없애면 그 세 필드도 같이 없어진다.

    ★병합 전에 두 가지를 실측으로 확인했다:
        사업부문과 부모가 같은 사건에 동시에 붙은 경우   0건  → 엣지 중복 없음
        사업부문과 부모 사이의 직접 엣지                0건  → 자기고리 없음
      그래서 그냥 합쳐도 거짓이 생기지 않는다.
          삼성전자 오스틴 반도체공장 -HAS_EVENT-> 노동자 부상 소송
            → 삼성전자 -HAS_EVENT-> 노동자 부상 소송     (사실이다)

    ★부문 이름은 `also_names` 에 남긴다 — 「오스틴 공장」으로 검색해도 찾히게.
    """
    with neo4j_session() as s:
        rows = list(s.run("""MATCH (d:Company) WHERE d.part_of_corp_code IS NOT NULL
            MATCH (p:Company {corp_code: d.part_of_corp_code})
            OPTIONAL MATCH (d)-[r]-()
            RETURN d.name AS div, p.name AS parent, count(r) AS deg ORDER BY deg DESC"""))
        for r in rows:
            print(f"  {r['div'][:28]:<30}→ {r['parent'][:12]:<14}엣지 {r['deg']}")
        print(f"  사업부문 {len(rows)}곳")
        if dry or not rows:
            return

        # 자기고리·중복 엣지가 생길 수 있는지 다시 확인한다(실측은 0이었지만
        # 확장으로 늘었을 수 있다). 있으면 멈춘다 — 조용히 합치면 거짓이 생긴다.
        loop = s.run("""MATCH (d:Company)-[]-(p:Company)
            WHERE d.part_of_corp_code IS NOT NULL AND p.corp_code = d.part_of_corp_code
            RETURN count(*) AS n""").single()["n"]
        if loop:
            print(f"  ⛔ 사업부문↔부모 직접 엣지 {loop}건 — 자기고리가 생깁니다. 중단합니다.")
            return

        s.run("""MATCH (d:Company) WHERE d.part_of_corp_code IS NOT NULL
            MATCH (p:Company {corp_code: d.part_of_corp_code})
            SET p.also_names = coalesce(p.also_names, []) + [d.name]""")
        # properties:'discard' = 부모 값을 지킨다. mergeRels = 같은 상대와 같은
        # 타입인 엣지가 겹치면 하나로 합친다.
        n = s.run("""MATCH (d:Company) WHERE d.part_of_corp_code IS NOT NULL
            MATCH (p:Company {corp_code: d.part_of_corp_code})
            CALL apoc.refactor.mergeNodes([p, d],
                 {properties:'discard', mergeRels:true}) YIELD node
            RETURN count(node) AS n""").single()["n"]
        print(f"  → {n}곳 흡수")


def step3_move_financials(dry: bool) -> None:
    """노드의 total_assets / net_profit 을 OWNS_STAKE_IN 엣지로 옮긴다.

    ★왜: 이 값은 **출자사의 공시에서 뽑은 피투자사 재무**다. 한 회사를 여러
      출자사가 보고하므로(실측 최대 6곳) 노드에 쓰면 마지막 것만 남고,
      누가 언제 보고한 값인지 되짚을 수 없다. 엣지에는 이미 `source_doc` 과
      `settlement_date` 가 있어 출처가 붙는다.
    """
    with neo4j_session() as s:
        n = s.run("""MATCH (c:Company)
            WHERE c.total_assets IS NOT NULL OR c.net_profit IS NOT NULL
            RETURN count(*) AS n""").single()["n"]
        multi = s.run("""MATCH (a)-[r:OWNS_STAKE_IN]->(c:Company)
            WHERE c.total_assets IS NOT NULL
            WITH c, count(DISTINCT a) AS inv WHERE inv > 1
            RETURN count(*) AS n""").single()["n"]
        print(f"  재무가 붙은 노드 {n}곳 · 그중 출자사가 둘 이상인 곳 {multi}곳")
        if dry or not n:
            return
        # 들어오는 OWNS_STAKE_IN 이 하나뿐인 경우에만 엣지로 옮긴다.
        # 여럿이면 어느 출자사가 보고한 값인지 모르므로 옮기지 않고 버린다
        # (원본은 DART 공시에 있으므로 재수집으로 정확히 복원할 수 있다).
        moved = s.run("""MATCH (a)-[r:OWNS_STAKE_IN]->(c:Company)
            WHERE c.total_assets IS NOT NULL OR c.net_profit IS NOT NULL
            WITH c, collect(r) AS rs WHERE size(rs) = 1
            UNWIND rs AS r
            SET r.investee_total_assets = c.total_assets,
                r.investee_net_profit   = c.net_profit
            RETURN count(*) AS n""").single()["n"]
        print(f"  → 엣지로 이동 {moved}건 (출자사가 여럿인 곳은 출처 불명이라 버림)")


def step4_to_postgres(dry: bool) -> None:
    """12필드를 PostgreSQL company_attributes 로 옮긴다.

    ★`companies` 를 안 쓰고 새 표를 만드는 이유: `companies.corp_code` 가
      `corp_code_master` 를 참조하는 PK 라 **corp_code 없는 회사(해외 2,300곳)를
      담을 수 없다.** 노드 식별자(corp_code 또는 norm_name)를 키로 쓰는 표가 따로 필요하다.
    """
    with neo4j_session() as s:
        rows = list(s.run("""MATCH (c:Company)
            WHERE c.induty IS NOT NULL OR c.ceo_nm IS NOT NULL OR c.est_dt IS NOT NULL
               OR c.name_en IS NOT NULL OR c.sector_label IS NOT NULL OR c.sector IS NOT NULL
               OR c.etf_list IS NOT NULL OR c.is_seed IS NOT NULL OR c.vehicle_type IS NOT NULL
               OR c.resolution_note IS NOT NULL OR c.revenue_snapshot IS NOT NULL
            RETURN coalesce(c.corp_code, c.norm_name) AS node_key, c.corp_code AS corp_code,
                   c.name AS name, c.norm_name AS norm_name, c.induty AS induty,
                   c.ceo_nm AS ceo_nm, toString(c.est_dt) AS est_dt, c.name_en AS name_en,
                   c.sector_label AS sector_label, c.sector AS sector, c.etf_list AS etf_list,
                   c.is_seed AS is_seed, c.vehicle_type AS vehicle_type,
                   c.resolution_note AS resolution_note,
                   c.revenue_snapshot AS revenue_snapshot,
                   c.fin_year_snapshot AS revenue_year"""))
    print(f"  옮길 노드 {len(rows)}곳")
    if dry or not rows:
        return
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_PROFILE_TABLE)
        for r in rows:
            cur.execute("""INSERT INTO company_attributes
                (node_key, corp_code, name, norm_name, induty, ceo_nm, est_dt, name_en,
                 sector_label, sector, etf_list, is_seed, vehicle_type, resolution_note,
                 revenue_snapshot, revenue_year)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (node_key) DO UPDATE SET
                    corp_code=EXCLUDED.corp_code, name=EXCLUDED.name,
                    norm_name=EXCLUDED.norm_name, induty=EXCLUDED.induty,
                    ceo_nm=EXCLUDED.ceo_nm, est_dt=EXCLUDED.est_dt,
                    name_en=EXCLUDED.name_en, sector_label=EXCLUDED.sector_label,
                    sector=EXCLUDED.sector, etf_list=EXCLUDED.etf_list,
                    is_seed=EXCLUDED.is_seed, vehicle_type=EXCLUDED.vehicle_type,
                    resolution_note=EXCLUDED.resolution_note,
                    revenue_snapshot=EXCLUDED.revenue_snapshot,
                    revenue_year=EXCLUDED.revenue_year, updated_at=now()""",
                (r["node_key"], r["corp_code"], r["name"], r["norm_name"], r["induty"],
                 r["ceo_nm"], r["est_dt"], r["name_en"], r["sector_label"],
                 json.dumps(r["sector"], ensure_ascii=False) if r["sector"] else None,
                 json.dumps(r["etf_list"], ensure_ascii=False) if r["etf_list"] else None,
                 bool(r["is_seed"]), r["vehicle_type"], r["resolution_note"],
                 r["revenue_snapshot"], r["revenue_year"]))
    print(f"  → company_attributes {len(rows)}행")


def step5_purge_orphans(dry: bool) -> None:
    """엣지가 0 인 노드를 PG 에 남기고 지운다.

    ★`is_orphan` 표시는 「지우지 않고 남긴다」는 원칙에서 나왔는데, 그 표시가
      노드에 붙어 있으니 노드는 계속 늘어난다. 기록을 PG 로 옮기면 원칙을 지키면서
      그래프는 깨끗해진다(엣지에 이미 `purged_edges` 가 같은 방식으로 있다).

    ★표시가 낡은 것도 함께 고친다 — 24곳은 나중에 엣지가 다시 붙었는데
      플래그가 안 지워졌다.
    """
    with neo4j_session() as s:
        rows = list(s.run("""MATCH (n) WHERE n.is_orphan AND NOT (n)--()
            RETURN labels(n)[0] AS label,
                   coalesce(n.corp_code, n.norm_name, n.person_key, n.event_id) AS key,
                   n.name AS name, n.orphan_reason AS reason, properties(n) AS props"""))
        stale = s.run("MATCH (n) WHERE n.is_orphan AND (n)--() RETURN count(*) AS n").single()["n"]
    by_label: dict[str, int] = {}
    for r in rows:
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1
    print(f"  삭제 대상 {len(rows)}곳  " + " · ".join(f"{k} {v}" for k, v in sorted(by_label.items())))
    print(f"  표시가 낡은 곳(엣지가 다시 붙음) {stale}곳 → 플래그만 해제")
    if dry or not rows:
        return
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_PURGED_TABLE)
        for r in rows:
            props = {k: str(v) for k, v in (r["props"] or {}).items()}
            cur.execute("""INSERT INTO purged_nodes (label, node_key, name, reason, props)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (r["label"], r["key"], r["name"], r["reason"],
                         json.dumps(props, ensure_ascii=False)))
    with neo4j_session() as s:
        s.run("MATCH (n) WHERE n.is_orphan AND NOT (n)--() DELETE n")
    print(f"  → purged_nodes {len(rows)}행 기록 후 삭제")


def step6_drop_props(dry: bool) -> None:
    """옮긴 속성과 메타 필드를 노드에서 지운다."""
    with neo4j_session() as s:
        alive = []
        for p in DROP:
            n = s.run(f"MATCH (c:Company) WHERE c.`{p}` IS NOT NULL RETURN count(*) AS n").single()["n"]
            if n:
                alive.append((p, n))
        for p, n in sorted(alive, key=lambda x: -x[1]):
            print(f"    {p:<32}{n:>5}곳")
        print(f"  지울 속성 {len(alive)}종")
        if dry or not alive:
            return
        for p, _ in alive:
            s.run(f"MATCH (c:Company) WHERE c.`{p}` IS NOT NULL REMOVE c.`{p}`")
        # ★「항상 있어야 하는」 속성의 빈칸을 메운다. 없으면 「아니다」와
        #   「아직 안 봤다」가 조회에서 구분되지 않는다.
        f1 = s.run("""MATCH (c:Company) WHERE c.first_seen IS NULL
                      SET c.first_seen = date() RETURN count(*) AS n""").single()["n"]
        f2 = s.run("""MATCH (c:Company) WHERE c.is_stub IS NULL
                      SET c.is_stub = true RETURN count(*) AS n""").single()["n"]
        print(f"  → {len(alive)}종 삭제 · first_seen 채움 {f1} · is_stub 채움 {f2}")


def step7_fill_entity_kind(dry: bool) -> None:
    """`entity_kind` 의 빈칸을 메운다 — 규칙으로 되는 것부터.

    ★왜 비면 안 되나: `불명` 이라는 값이 따로 있는데 속성 자체가 없으면
      「봤는데 못 정했다」와 「아직 안 봤다」가 조회에서 똑같이 null 이 된다.
      화면에서 「기업만」을 거르면 778곳이 조용히 빠진다 — 엑시콘(코스닥
      상장사)도 거기 들어 있었다.

    ★해외를 `market` 이 아니라 여기에 두는 이유(2026-08-15 실측):
      market 값이 KOSPI·KOSDAQ·KONEX·비상장·펀드뿐이고 NASDAQ 같은 해외 시장이
      **하나도 없다.** DART 만 쓰므로 해외 상장 시장을 채울 소스가 아예 없다.
      market 으로는 국내/해외를 못 가른다.

    남는 것만 `불명` 으로 명시한다 — LLM 을 다시 부르지 않는다.
    """
    RULES = [
        # (조건, 값)  — 위에서부터 먼저 맞는 것
        ("c.vehicle_type IS NOT NULL OR c.market = '펀드'", "펀드·조합"),
        ("c.corp_code IS NULL AND c.candidate_corp_codes IS NULL", "해외"),
        ("c.corp_code IS NOT NULL", "기업"),
    ]
    with neo4j_session() as s:
        n0 = s.run("MATCH (c:Company) WHERE c.entity_kind IS NULL RETURN count(*) AS n").single()["n"]
        print(f"  분류가 빈 노드 {n0}곳")
        for cond, val in RULES:
            q = f"MATCH (c:Company) WHERE c.entity_kind IS NULL AND ({cond}) RETURN count(*) AS n"
            print(f"    {val:<10}{s.run(q).single()['n']:>5}곳   ({cond[:52]})")
        rest = s.run(f"""MATCH (c:Company) WHERE c.entity_kind IS NULL
            AND NOT ({' OR '.join('('+c+')' for c, _ in RULES)}) RETURN count(*) AS n""").single()["n"]
        print(f"    {'불명':<10}{rest:>5}곳   (규칙에 안 걸림)")
        if dry or not n0:
            return
        for cond, val in RULES:
            s.run(f"MATCH (c:Company) WHERE c.entity_kind IS NULL AND ({cond}) "
                  f"SET c.entity_kind = $v", v=val)
        s.run("MATCH (c:Company) WHERE c.entity_kind IS NULL SET c.entity_kind = '불명'")
        print(f"  → {n0}곳 채움")


STEPS = [
    ("후보 목록 복구",  step1_restore_candidates),
    ("사업부문 병합",   step2_merge_divisions),
    ("재무 → 엣지",     step3_move_financials),
    ("PG 이관",        step4_to_postgres),
    ("고아 정리",       step5_purge_orphans),
    ("분류 채우기",     step7_fill_entity_kind),
    ("속성 삭제",       step6_drop_props),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="무엇이 바뀌는지만 보고 끝")
    ap.add_argument("--step", type=int, metavar="N", help="한 단계만 (1~7)")
    args = ap.parse_args()

    todo = [(i, n, f) for i, (n, f) in enumerate(STEPS, 1)
            if args.step is None or i == args.step]
    if args.dry_run:
        print("[dry-run] 아무것도 바꾸지 않습니다\n")
    for i, name, fn in todo:
        print("=" * 62)
        print(f"[{i}/{len(STEPS)}] {name}")
        print("=" * 62)
        fn(args.dry_run)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
