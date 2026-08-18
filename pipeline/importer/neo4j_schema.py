"""Neo4j 제약·인덱스 셋업 (ERD §2-5).

노드 고유성 제약(MERGE 정합성) + 조회 인덱스.
sector는 배열이라 Neo4j 인덱스 제외 — 섹터 필터는 PostgreSQL JSONB GIN 담당.
전부 IF NOT EXISTS라 반복 실행 안전.

실행: python -m pipeline.importer.neo4j_schema
"""

from __future__ import annotations

from app.core.database import neo4j_session

# 노드 고유성 제약
CONSTRAINTS = [
    "CREATE CONSTRAINT company_corp_code IF NOT EXISTS "
    "FOR (c:Company) REQUIRE c.corp_code IS UNIQUE",
    "CREATE CONSTRAINT person_key IF NOT EXISTS "
    "FOR (p:Person) REQUIRE p.person_key IS UNIQUE",
    "CREATE CONSTRAINT product_norm_name IF NOT EXISTS "
    "FOR (p:Product) REQUIRE p.norm_name IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS "
    "FOR (e:Event) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT org_norm_name IF NOT EXISTS "
    "FOR (o:Organization) REQUIRE o.norm_name IS UNIQUE",
]

# 조회 인덱스 (sector는 제외 — PostgreSQL 담당)
NODE_INDEXES = [
    "CREATE INDEX company_norm_name IF NOT EXISTS FOR (c:Company) ON (c.norm_name)",
    "CREATE INDEX company_name IF NOT EXISTS FOR (c:Company) ON (c.name)",
    "CREATE INDEX company_stub IF NOT EXISTS FOR (c:Company) ON (c.is_stub)",
    "CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name)",
]

# 관계 속성 인덱스 (신선도·subtype 필터) — 상태 엣지 타입별
REL_INDEXES = [
    "CREATE INDEX rel_owns_subtype IF NOT EXISTS "
    "FOR ()-[r:OWNS_STAKE_IN]-() ON (r.subtype)",
    "CREATE INDEX rel_owns_current IF NOT EXISTS "
    "FOR ()-[r:OWNS_STAKE_IN]-() ON (r.is_current)",
    "CREATE INDEX rel_supplies_current IF NOT EXISTS "
    "FOR ()-[r:SUPPLIES_TO]-() ON (r.is_current)",
    "CREATE INDEX rel_supplies_subtype IF NOT EXISTS "
    "FOR ()-[r:SUPPLIES_TO]-() ON (r.subtype)",
    "CREATE INDEX rel_exec_subtype IF NOT EXISTS "
    "FOR ()-[r:IS_EXECUTIVE_OF]-() ON (r.subtype)",
]


def setup_schema() -> None:
    all_statements = CONSTRAINTS + NODE_INDEXES + REL_INDEXES
    with neo4j_session() as session:
        for stmt in all_statements:
            session.run(stmt)
            label = stmt.split()[2]  # CONSTRAINT/INDEX 이름
            print(f"  ✓ {label}")
    print(f"Neo4j 스키마 셋업 완료 ({len(all_statements)}건)")


if __name__ == "__main__":
    setup_schema()
