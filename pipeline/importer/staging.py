"""경로 A 정규화 결과 → PostgreSQL staged_edges 적재 (ERD §6-1, §5).

corp 단위로 3종(최대주주·임원·출자)을 묶어 처리한다:
 1) 3종 정규화 + 도메인 검증
 2) person_key 통합 — 임원(생년월 有)과 주주(생년월 無)의 동일인 병합
 3) 노드-엣지 매트릭스 검증
 4) staged_edges 적재 (엔드포인트 노드 props 임베드 → Neo4j 재생성 가능)

멱등: source_doc="dart:{corp}"로 태깅, 재실행 시 corp 단위 삭제 후 재삽입.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from schemas.dart_schemas import EntityDTO, NormalizedDocument, RelationshipDTO
from pipeline.normalizer.executive_normalizer import normalize_executives
from pipeline.normalizer.investment_normalizer import normalize_investments
from pipeline.normalizer.majorstock_normalizer import normalize_majorstock
from pipeline.normalizer.shareholder_normalizer import normalize_shareholders
from pipeline.validators.executive_validator import validate_executives
from pipeline.validators.investment_validator import validate_investments
from pipeline.validators.shareholder_validator import validate_shareholders
from pipeline.validators.matrix import level_1_category, validate_edge

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw_dart")

API_PIPELINE = {
    "shareholders": (normalize_shareholders, validate_shareholders),
    "executives": (normalize_executives, validate_executives),
    "investments": (normalize_investments, validate_investments),
    "majorstock": (normalize_majorstock, validate_shareholders),  # 5% 대량보유
}


def _load_rows(corp_code: str, api_name: str) -> list[dict[str, Any]]:
    path = os.path.join(RAW_DIR, f"{corp_code}_{api_name}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("list", [])


def _reconcile_persons(
    entities: list[EntityDTO], relationships: list[RelationshipDTO]
) -> list[EntityDTO]:
    """동일 인물의 person_key 분열 병합.
    주주(생년월 無 → name@corp)와 임원(생년월 有 → name|birth)이 같은 이름이면
    생년월 기반 키로 통일하고 관계 참조를 재작성한다.
    """
    persons = [e for e in entities if e.type == "Person"]
    by_name: dict[str, list[EntityDTO]] = {}
    for e in persons:
        by_name.setdefault(e.properties.get("name"), []).append(e)

    remap: dict[str, str] = {}  # "Person:old" -> "Person:canonical"
    dropped_keys: set[str] = set()
    for _, group in by_name.items():
        if len(group) < 2:
            continue
        canonical = next((e for e in group if "|" in e.key), None)
        if canonical is None:
            continue
        for e in group:
            if e.key == canonical.key:
                continue
            remap[f"Person:{e.key}"] = f"Person:{canonical.key}"
            dropped_keys.add(e.key)
            # 누락 속성 보강 (생년월 키 쪽에 없는 값 채움)
            for k, v in e.properties.items():
                if canonical.properties.get(k) is None and v is not None:
                    canonical.properties[k] = v

    if remap:
        for r in relationships:
            r.from_key = remap.get(r.from_key, r.from_key)
            r.to_key = remap.get(r.to_key, r.to_key)

    return [e for e in entities if not (e.type == "Person" and e.key in dropped_keys)]


def build_corp_document(corp_code: str) -> NormalizedDocument:
    """corp의 3종 정규화·검증·person 통합을 거친 통합 문서."""
    entities: list[EntityDTO] = []
    relationships: list[RelationshipDTO] = []

    for api_name, (normalize_fn, validate_fn) in API_PIPELINE.items():
        rows = _load_rows(corp_code, api_name)
        if not rows:
            continue
        doc = normalize_fn(rows, corp_code)
        validated, _report = validate_fn(doc)
        entities.extend(validated.entities)
        relationships.extend(validated.relationships)

    entities = _reconcile_persons(entities, relationships)
    return NormalizedDocument(entities=entities, relationships=relationships)


_INSERT_SQL = """
INSERT INTO staged_edges
  (src_node_type, src_key, tgt_node_type, tgt_key, edge_type, subtype,
   properties, origin, source_doc, validated, validation_error)
VALUES
  (%(src_type)s, %(src_key)s, %(tgt_type)s, %(tgt_key)s, %(edge_type)s, %(subtype)s,
   %(properties)s, 'dart', %(source_doc)s, %(validated)s, %(validation_error)s)
"""


def stage_corp(conn, corp_code: str) -> tuple[int, int]:
    """corp의 관계를 staged_edges에 적재. (적재건수, 매트릭스위반건수) 반환.
    엔드포인트 노드 props를 properties JSONB의 __src_node/__tgt_node에 임베드한다.
    """
    doc = build_corp_document(corp_code)
    props_by_ref = {f"{e.type}:{e.key}": e.properties for e in doc.entities}
    source_doc = f"dart:{corp_code}"

    with conn.cursor() as cur:
        cur.execute("DELETE FROM staged_edges WHERE source_doc = %s", (source_doc,))

        rows = []
        invalid = 0
        self_loops = 0
        for rel in doc.relationships:
            # 자기참조 루프 스킵 — 회사가 자기 자신에 지분/임원 관계를 가질 수 없음
            # (출자 대상·주주명이 모회사 corp_code로 매칭되는 경우)
            if rel.from_key == rel.to_key:
                self_loops += 1
                continue

            src_type, src_key = rel.from_key.split(":", 1)
            tgt_type, tgt_key = rel.to_key.split(":", 1)
            ok, why = validate_edge(src_type, rel.type, tgt_type)
            if not ok:
                invalid += 1

            props = dict(rel.properties)
            props["level_1_category"] = level_1_category(rel.type)
            props["source_doc"] = source_doc
            props["__src_node"] = props_by_ref.get(rel.from_key, {})
            props["__tgt_node"] = props_by_ref.get(rel.to_key, {})

            rows.append({
                "src_type": src_type, "src_key": src_key,
                "tgt_type": tgt_type, "tgt_key": tgt_key,
                "edge_type": rel.type, "subtype": rel.properties.get("subtype"),
                "properties": json.dumps(props, ensure_ascii=False),
                "source_doc": source_doc,
                "validated": ok, "validation_error": None if ok else why,
            })

        cur.executemany(_INSERT_SQL, rows)

    return len(rows), invalid
