from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from neo4j import Driver, GraphDatabase, Session

from app.core.config import NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from pipeline.normalizer.base import load_dart_corp_code_to_info

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
NORMALIZED_DIR = os.path.join(BASE_DIR, "data", "normalized")
ETF_LIST_PATH = os.path.join(BASE_DIR, "data", "company_list", "company_list_etf.json")
API_NAMES = ("shareholders", "executives", "investments")

_NORMALIZED_FILENAME_RE = re.compile(r"^(?P<corp_code>\d+)_(?P<api_name>[a-z_]+)\.json$")
_RELATIONSHIP_IDENT_PROPERTIES: dict[str, tuple[str, ...]] = {
    "OWNS_SHARE": ("share_type",),
}

# ---------------------------------------------------------------------------
# 1. 정규화된 DART 데이터 적재
# ---------------------------------------------------------------------------

def load_normalized_file(corp_code: str, api_name: str) -> dict[str, list[dict[str, Any]]]:
    """`data/normalized/{corp_code}_{api}.json`을 읽는다. 없으면 빈 문서를 반환한다."""
    file_path = os.path.join(NORMALIZED_DIR, f"{corp_code}_{api_name}.json")
    if not os.path.exists(file_path):
        print(f"파일이 없습니다: {file_path}")
        return {"entities": [], "relationships": []}

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all_normalized(corp_code: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """3개 API의 normalized JSON을 모두 읽어 entities/relationships를 하나로 합친다."""
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    for api_name in API_NAMES:
        document = load_normalized_file(corp_code, api_name)
        entities.extend(document.get("entities", []))
        relationships.extend(document.get("relationships", []))
    return entities, relationships


def resolve_ref(
    ref: str, corp_code: str, name_to_corp_code: dict[str, str]
) -> tuple[str, dict[str, str]]:
    """`from`/`to` 참조 문자열을 파싱해 라벨과 식별자 딕셔너리를 반환한다."""
    ref_type, _, ref_key = ref.partition(":")
    if ref_type == "Company" and ref_key == corp_code:
        return ref_type, {"corp_code": ref_key}
    if ref_type == "Company" and ref_key in name_to_corp_code:
        return ref_type, {"corp_code": name_to_corp_code[ref_key]}
    return ref_type, {"name": ref_key}


def ensure_constraints(driver: Driver) -> None:
    """Company.corp_code 유니크 제약. MERGE 정합성과 조회 성능을 위한 Neo4j 기본 사례다."""
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(
            "CREATE CONSTRAINT company_corp_code IF NOT EXISTS "
            "FOR (c:Company) REQUIRE c.corp_code IS UNIQUE"
        )


def ensure_company_node(session: Session, corp_code: str, company_name: str) -> None:
    """적재 대상 회사(마스터)의 Company 노드를 생성/보정한다."""
    session.run(
        "MERGE (c:Company {corp_code: $corp_code}) SET c.name = $name",
        corp_code=corp_code, name=company_name,
    )


def import_entities(session: Session, entities: list[dict[str, Any]]) -> int:
    """`entities[]`를 순회하며 `type`을 라벨로, `properties`를 속성으로 MERGE한다(§2)."""
    if not entities:
        return 0

    prepared = []
    for entity in entities:
        properties = entity["properties"]
        corp_code = properties.get("corp_code")
        if corp_code:
            ident = {"corp_code": corp_code}
            on_create = dict(properties)
            on_match = {k: v for k, v in properties.items() if k != "name"}
            on_match["dart_name"] = properties.get("name")
        else:
            ident = {"name": properties.get("name")}
            on_create = dict(properties)
            on_match = dict(properties)

        prepared.append({
            "labels": [entity["type"]],
            "ident": ident,
            "on_create": on_create,
            "on_match": on_match,
        })

    query = """
    UNWIND $rows AS row
    CALL (row) {
        CALL apoc.merge.node(row.labels, row.ident, row.on_create, row.on_match) YIELD node
        RETURN node
    }
    RETURN count(*) AS n
    """
    session.run(query, rows=prepared)
    return len(prepared)


def import_relationships(
    session: Session,
    corp_code: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> int:
    """`relationships[]`를 순회하며 `from`/`to`를 파싱해 두 노드를 MATCH/MERGE하고,
    `type`을 관계 타입으로, `properties`를 관계 속성으로 SET한다(§2).
    """
    if not relationships:
        return 0

    name_to_corp_code = {
        e["properties"]["name"]: e["properties"]["corp_code"]
        for e in entities
        if e["type"] == "Company" and e["properties"].get("corp_code")
    }

    prepared = []
    for rel in relationships:
        from_label, from_ident = resolve_ref(rel["from"], corp_code, name_to_corp_code)
        to_label, to_ident = resolve_ref(rel["to"], corp_code, name_to_corp_code)
        rel_type = rel["type"]
        ident_fields = _RELATIONSHIP_IDENT_PROPERTIES.get(rel_type, ())
        rel_ident = {field: rel["properties"].get(field) for field in ident_fields}

        prepared.append({
            "from_label": from_label,
            "from_ident": from_ident,
            "to_label": to_label,
            "to_ident": to_ident,
            "rel_type": rel_type,
            "rel_ident": rel_ident,
            "properties": rel["properties"],
        })

    query = """
    UNWIND $rows AS row
    CALL (row) {
        CALL apoc.merge.node([row.from_label], row.from_ident, {}, {}) YIELD node
        RETURN node AS fromNode
    }
    CALL (row) {
        CALL apoc.merge.node([row.to_label], row.to_ident, {}, {}) YIELD node
        RETURN node AS toNode
    }
    CALL (fromNode, toNode, row) {
        CALL apoc.merge.relationship(
            fromNode, row.rel_type, row.rel_ident, row.properties, toNode, row.properties
        ) YIELD rel
        RETURN rel
    }
    RETURN count(*) AS n
    """
    session.run(query, rows=prepared, corp_code=corp_code)
    return len(prepared)


def discover_normalized_corp_codes() -> list[str]:
    """`data/normalized/`에서 적재 가능한 corp_code를 발견한다."""
    corp_codes: set[str] = set()
    if not os.path.isdir(NORMALIZED_DIR):
        return []
    for filename in os.listdir(NORMALIZED_DIR):
        match = _NORMALIZED_FILENAME_RE.match(filename)
        if match and match.group("api_name") in API_NAMES:
            corp_codes.add(match.group("corp_code"))
    return sorted(corp_codes)


def resolve_company_name(corp_code: str) -> str:
    """corp_code에 해당하는 회사명을 반환한다. 없으면 corp_code를 그대로 반환한다."""
    info = load_dart_corp_code_to_info().get(corp_code)
    return info[0] if info is not None else corp_code

# 회사 단위 적재를 수행하는 내부 함수.
def _import_company(session: Session, corp_code: str, company_name: str) -> None:
    print(f"\n=== {company_name} 그래프 적재 시작 ===")

    ensure_company_node(session, corp_code, company_name)
    entities, relationships = load_all_normalized(corp_code)

    entity_count = import_entities(session, entities)
    if entity_count:
        print(f"엔티티 {entity_count}건 MERGE 완료")

    relationship_count = import_relationships(session, corp_code, entities, relationships)
    if relationship_count:
        print(f"관계 {relationship_count}건 MERGE 완료")

    print(f"=== {company_name} 적재 완료 ===")

# neo4j에 적재하는 외부 진입점 함수. 
def import_to_neo4j(corp_code: str, company_name: str) -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        ensure_constraints(driver)
        with driver.session(database=NEO4J_DATABASE) as session:
            _import_company(session, corp_code, company_name)
    finally:
        driver.close()


def import_all_to_neo4j(corp_codes: Optional[list[str]] = None) -> None:
    codes = corp_codes if corp_codes is not None else discover_normalized_corp_codes()
    if not codes:
        print(f"적재할 corp_code를 찾지 못했습니다: {NORMALIZED_DIR}")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        ensure_constraints(driver)
        with driver.session(database=NEO4J_DATABASE) as session:
            for corp_code in codes:
                company_name = resolve_company_name(corp_code)
                _import_company(session, corp_code, company_name)
    finally:
        driver.close()

    print(f"\n=== 전체 {len(codes)}개 회사 적재 완료 ===")

# ---------------------------------------------------------------------------
# 2. ETF 구성종목 마스터 리스트 적재
# ---------------------------------------------------------------------------

def load_etf_company_list() -> list[dict[str, Any]]:
    if not os.path.exists(ETF_LIST_PATH):
        print(f"✗ 에러: {ETF_LIST_PATH} 파일이 존재하지 않습니다.")
        return []
    with open(ETF_LIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("companies", [])


def import_etf_master_list() -> None:
    """ETF 구성종목 마스터 리스트를 Neo4j에 적재한다."""
    companies = load_etf_company_list()
    if not companies:
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session(database=NEO4J_DATABASE) as session:
        print("기존 마스터 데이터 초기화 시작...")

        clear_query = """
        MATCH (n)
        WHERE n:Sector OR n:ETF
        DETACH DELETE n
        """
        try:
            session.run(clear_query)
            print("✓ 기존 마스터 데이터 삭제 완료!")
        except Exception as e:
            print(f"✗ 초기화 중 오류 발생: {e}")

        print(f"\n총 {len(companies)}개 기업 마스터 데이터 적재 시작...")

        query = """
        UNWIND $list AS row

        MERGE (c:Company {corp_code: row.corpCode})
        SET c.name = row.companyName,
            c.stock_code = row.stockCode,
            c.market = row.market,
            c.created_at = timestamp()

        WITH c, row
        UNWIND row.sector AS sector_name
        MERGE (s:Sector {name: sector_name})
        MERGE (c)-[:BELONGS_TO_SECTOR]->(s)

        WITH c, row
        UNWIND row.etfList AS etf_name
        MERGE (e:ETF {name: etf_name})
        MERGE (c)-[:CONSTITUENT_OF]->(e)
        """

        try:
            session.run(query, list=companies)
            print("ETF 구성종목 마스터 그래프 적재 완료!")
        except Exception as e:
            print(f"✗ 적재 중 오류 발생: {e}")

    driver.close()
