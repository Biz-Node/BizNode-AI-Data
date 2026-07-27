"""staged_edges(PostgreSQL) → Neo4j 적재 (ERD §5-2, §2-5).

권위(staged_edges)에서 파생(Neo4j)을 재생성한다. DART 재호출 없이 재적재 가능.
 - Company 식별: 키가 8자리 숫자면 corp_code, 아니면 norm_name (master/stub 자동 구분)
 - Person: person_key
 - 상태 엣지: (src,tgt,type,subtype) MERGE  /  사건 엣지: source_doc 포함 MERGE
 - 노드 ON MATCH는 is_stub을 건드리지 않음(시드 노드 보존)
 - 적재 후 staged_edges.loaded_at 마킹(커밋 마커, 멱등)
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.database import neo4j_session, postgres_connection

STATE_EDGES = {"OWNS_STAKE_IN", "IS_EXECUTIVE_OF", "SUPPLIES_TO",
               "PARTNERS_WITH", "DEVELOPS", "DEPENDS_ON", "COMPETES_WITH", "REGULATES"}
EVENT_EDGES = {"ACQUIRES", "SUES", "HAS_EVENT", "IMPACTS"}

# 엣지에 저장하지 않는 내부/파생 키
_INTERNAL_KEYS = {"__src_node", "__tgt_node", "level_1_category"}


def _company_ident(key: str) -> dict[str, str]:
    """8자리 숫자 = corp_code(master/resolved), 그 외 = norm_name(stub)."""
    if key.isdigit() and len(key) == 8:
        return {"corp_code": key}
    return {"norm_name": key}


def _node_ident(node_type: str, key: str) -> dict[str, str]:
    if node_type == "Person":
        return {"person_key": key}
    if node_type == "Company":
        return _company_ident(key)
    if node_type == "Event":
        return {"event_id": key}
    return {"norm_name": key}


def _rel_ident(edge_type: str, props: dict[str, Any]) -> dict[str, Any]:
    """상태 엣지는 subtype, 사건 엣지는 source_doc으로 MERGE 식별."""
    if edge_type in EVENT_EDGES:
        return {"source_doc": props.get("source_doc")}
    return {"subtype": props.get("subtype")}


# apoc.merge.*의 4번째 인자는 onCreate, 마지막은 onMatch.
# 재적재 시 속성 갱신이 되려면 onMatch에도 같은 props를 넘겨야 한다
# (안 그러면 기존 엣지는 예전 속성을 그대로 유지 — 근거 누락 버그의 원인).
_QUERY = """
UNWIND $rows AS row
CALL apoc.merge.node([row.src_label], row.src_ident, row.src_props, {}) YIELD node AS s
CALL apoc.merge.node([row.tgt_label], row.tgt_ident, row.tgt_props, {}) YIELD node AS t
CALL apoc.merge.relationship(s, row.edge_type, row.rel_ident, row.rel_props, t, row.rel_props)
YIELD rel
RETURN count(*) AS n
"""


def _prepare(staged_row: dict[str, Any]) -> dict[str, Any]:
    props = staged_row["properties"]
    src_node = props.get("__src_node") or {}
    tgt_node = props.get("__tgt_node") or {}
    rel_props = {k: v for k, v in props.items() if k not in _INTERNAL_KEYS}

    return {
        "src_label": staged_row["src_node_type"],
        "src_ident": _node_ident(staged_row["src_node_type"], staged_row["src_key"]),
        "src_props": src_node,
        "tgt_label": staged_row["tgt_node_type"],
        "tgt_ident": _node_ident(staged_row["tgt_node_type"], staged_row["tgt_key"]),
        "tgt_props": tgt_node,
        "edge_type": staged_row["edge_type"],
        "rel_ident": _rel_ident(staged_row["edge_type"], props),
        "rel_props": rel_props,
    }


def load_staged_to_neo4j(only_corp: Optional[str] = None, batch_size: int = 500) -> int:
    """validated=true, loaded_at IS NULL인 staged_edges를 Neo4j로 적재."""
    where = "validated = true AND loaded_at IS NULL"
    params: list[Any] = []
    if only_corp:
        where += " AND source_doc = %s"
        params.append(f"dart:{only_corp}")

    total = 0
    with postgres_connection() as pg:
        with pg.cursor() as cur:
            cur.execute(
                f"SELECT id, src_node_type, src_key, tgt_node_type, tgt_key, "
                f"edge_type, properties FROM staged_edges WHERE {where} ORDER BY id",
                params,
            )
            columns = [d[0] for d in cur.description]
            staged = [dict(zip(columns, r)) for r in cur.fetchall()]

        if not staged:
            print("적재할 staged_edges가 없습니다.")
            return 0

        prepared = [_prepare(s) for s in staged]
        with neo4j_session() as session:
            for i in range(0, len(prepared), batch_size):
                chunk = prepared[i : i + batch_size]
                session.run(_QUERY, rows=chunk)
                total += len(chunk)

        # 커밋 마커 (맨 마지막)
        ids = [s["id"] for s in staged]
        with pg.cursor() as cur:
            cur.execute("UPDATE staged_edges SET loaded_at = now() WHERE id = ANY(%s)", (ids,))

    print(f"Neo4j 적재 완료: {total}건")
    return total
