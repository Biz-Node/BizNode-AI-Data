"""staged_edges(PostgreSQL) → Neo4j 적재 (ERD §5-2, §2-5).

권위(staged_edges)에서 파생(Neo4j)을 재생성한다. DART 재호출 없이 재적재 가능.
 - Company 식별: 키가 8자리 숫자면 corp_code, 아니면 norm_name (master/stub 자동 구분)
 - Person: person_key
 - 상태 엣지: (src,tgt,type,subtype) MERGE  /  사건 엣지: source_doc 포함 MERGE
 - 노드 ON MATCH는 is_stub을 건드리지 않음(시드 노드 보존)
 - 적재 후 staged_edges.loaded_at 마킹(커밋 마커, 멱등)
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    """MERGE 식별자 — 상태·사건 모두 **subtype 기준**.

    ★2026-07-28 교정: 사건 엣지를 `source_doc`(기사 URL)으로 식별하고 있었다.
      "같은 회사를 여러 번 인수할 수 있으니 사건마다 구분하자"는 의도였는데,
      실제로는 **같은 사건의 반복 보도**를 구분하고 있었다.
      실측: 「삼성전자 -ACQUIRES-> 레인보우로보틱스」가 **32개 엣지**
            (하나의 인수를 32개 기사가 보도 — 2024-12-31에만 10건)

      같은 상대에게 M&A를 두 번 하는 일은 드물고, 있더라도 subtype·시점이 다르다.
      반복 보도를 32개 엣지로 만드는 손해가 훨씬 크다.
      여러 출처가 같은 관계를 말하면 **엣지는 하나, 근거는 목록**으로 쌓는다.
    """
    return {"subtype": props.get("subtype")}


# apoc.merge.*의 4번째 인자는 onCreate, 마지막은 onMatch.
# 재적재 시 속성 갱신이 되려면 onMatch에도 같은 props를 넘겨야 한다
# (안 그러면 기존 엣지는 예전 속성을 그대로 유지 — 근거 누락 버그의 원인).
# ★`loaded_at`을 배치 단위로 찍는다 — **재적재 때 사라진 관계를 알아내기 위함**.
#
#   DART를 다시 적재하면 「작년엔 있었는데 올해 보고서엔 없는」 지분·거래처가 생긴다.
#   그건 관계가 끝났다는 뜻인데, MERGE만 하면 옛 엣지가 그대로 남는다.
#   같은 적재 배치의 엣지는 같은 `loaded_at`을 갖게 되므로, 나중에
#   `batch/audit/freshness.py`가 「이번 배치에서 갱신되지 않은 것」을 찾아낸다.
#
#   `last_seen`은 **공시 날짜**(사건이 일어난 때)이고 `loaded_at`은 **우리가 본 때**다.
#   둘은 다르다 — 국민연금 5% 공시는 2024년 것이어도 오늘 적재될 수 있다.
#
# ★`first_seen`은 **처음 본 때**이고 두 값 어느 것으로도 대신할 수 없다(2026-08-04).
#   `loaded_at`은 배치마다 덮어써지고 `last_seen`은 공시 날짜라, 「지난번 이후 새로
#   생긴 관계」를 물으면 둘 다 답을 못 한다. 홈의 「알림」과 인사이트의 「변화」 축이
#   전부 여기 걸려 있는데 실측 보유율이 **0%**였다.
#
#   `apoc.merge.relationship`의 onCreate(4번째)에만 넣고 onMatch(6번째)에는 넣지
#   않는다 — 그래야 이미 있던 엣지의 최초 시각이 재적재로 덮이지 않는다.
_QUERY = """
UNWIND $rows AS row
CALL apoc.merge.node([row.src_label], row.src_ident,
     apoc.map.merge(row.src_props, {first_seen: date($today)}), {}) YIELD node AS s
CALL apoc.merge.node([row.tgt_label], row.tgt_ident,
     apoc.map.merge(row.tgt_props, {first_seen: date($today)}), {}) YIELD node AS t
CALL apoc.merge.relationship(s, row.edge_type, row.rel_ident,
     apoc.map.merge(row.rel_props, {first_seen: date($today)}),
     t, row.rel_props)
YIELD rel
SET rel.loaded_at = $loaded_at
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
        # 배치 전체가 **같은** loaded_at을 갖게 한다. 행마다 datetime()을 부르면
        # 미세하게 달라져 "이번 배치에서 갱신됐나"를 판정할 수 없다.
        loaded_at = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).date().isoformat()
        with neo4j_session() as session:
            for i in range(0, len(prepared), batch_size):
                chunk = prepared[i : i + batch_size]
                session.run(_QUERY, rows=chunk, loaded_at=loaded_at, today=today)
                total += len(chunk)

        # 커밋 마커 (맨 마지막)
        ids = [s["id"] for s in staged]
        with pg.cursor() as cur:
            cur.execute("UPDATE staged_edges SET loaded_at = now() WHERE id = ANY(%s)", (ids,))

    print(f"Neo4j 적재 완료: {total}건")
    return total
