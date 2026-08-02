"""뉴스 관계 → 노드·엣지·evidence 변환.

P1 인프라(staged_edges → evidence → Neo4j)를 그대로 재사용한다.
차이점:
  · source_type="news", confidence=LLM 점수 (DART 1.0과 구분)
  · 개체 해소는 resolver(corp_code) → 실패 시 unresolved stub (§13)
  · evidence는 **기사 원문 문장**(LLM이 인용) + URL
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Optional

from schemas.dart_schemas import (
    EntityDTO,
    EventDTO,
    NormalizedDocument,
    OrganizationDTO,
    ProductDTO,
    RelationshipDTO,
    make_entity_ref,
    make_person_key,
    standard_edge_meta,
)
from pipeline.importer.evidence import EvidenceRecord, make_evidence_id
from pipeline.news.extractor import ExtractedRelation
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.entities import build_company
from pipeline.normalizer.generic_names import (
    is_generic_name,
    is_market_noise_event,
    is_placeholder_name,
)
from pipeline.normalizer.product_names import canonical_product
from pipeline.normalizer.product_names import norm_key as product_key
from pipeline.normalizer.relations import normalize, reclassify_sues

# 대칭 엣지 — 키 사전순 단방향 저장(방법서 §11)
_SYMMETRIC = {"PARTNERS_WITH", "COMPETES_WITH"}
# 사건 엣지 — occurred_at 사용
_EVENT_EDGES = {"ACQUIRES", "SUES", "HAS_EVENT", "IMPACTS"}


def _resolve_name(name: str, node_type: str, article_title: str) -> Optional[str]:
    """이름 검수. 노드로 쓸 수 없으면 None(→ 관계 폐기).

    Event는 자리표시자면 기사 제목으로 살린다 — 기사 하나가 곧 그 사건인 경우가 많다.
    다만 시황("상한가 기록")·맨동사("출시")는 살리지 않는다. 사건이 아니라
    시세 현상이거나 무엇이 일어났는지 특정할 수 없어서, 노드로 만들면 서로 무관한
    건들이 한 이름으로 뭉친다.
    """
    if node_type == "Event" and is_market_noise_event(name):
        return None
    if not is_placeholder_name(name):
        return name
    if node_type == "Event" and article_title:
        return article_title.strip()[:120]
    return None


def _event_id(name: str, occurred: Optional[str]) -> str:
    """사건 식별자 — **이름 + 발생 연월** 해시.

    ★기사 URL을 넣으면 안 된다. 같은 사건을 두 매체가 보도하면 노드가 둘로 갈리고,
    그러면 사건이 여러 기업에 미치는 영향(IMPACTS)이 한 노드에 모이지 못한다.
    Event의 값은 바로 그 **전파 구조**에 있다
    (예: 「루빈 플랫폼 생산 지연」 ← 엔비디아, → 삼성전자·SK하이닉스).

    연월을 섞는 이유: 이름이 같아도 해가 다르면 다른 사건이다("AI 서밋" 2026 vs 2027).
    일(day) 단위로 끊으면 같은 사건을 며칠에 걸쳐 보도한 기사들이 다시 갈린다.
    """
    key = normalize_company_name(name) or name
    period = (occurred or "")[:7]           # YYYY-MM
    digest = hashlib.sha1(f"{key}|{period}".encode("utf-8")).hexdigest()[:12]
    return f"evt_news_{digest}"


def _build_node(name: str, node_type: str, article_url: str,
                occurred: Optional[str] = None) -> tuple[EntityDTO, str]:
    """개체명 + 타입 → (EntityDTO, ref). Company는 corp_code ER 시도."""
    norm = normalize_company_name(name) or name

    if node_type == "Company":
        return build_company(name)          # resolver → stub 폴백
    if node_type == "Person":
        key = make_person_key(name, None, "news")
        from schemas.dart_schemas import PersonDTO
        return (EntityDTO("Person", key, PersonDTO(name=name, person_key=key).to_properties()),
                make_entity_ref("Person", key))
    if node_type == "Organization":
        dto = OrganizationDTO(name=name, norm_name=norm, org_type="기관")
        return EntityDTO("Organization", norm, dto.to_properties()), make_entity_ref("Organization", norm)
    if node_type == "Product":
        # ★Product는 이름이 곧 식별자다. `normalize_company_name`만으로는
        #   「D램」과 「DRAM」이 갈린다 — 한/영 동의어를 여기서 통일한다.
        display = canonical_product(name)
        key = product_key(name)
        dto = ProductDTO(name=display, norm_name=key, category="기술")
        return (EntityDTO("Product", key, dto.to_properties()),
                make_entity_ref("Product", key))
    # Event — 이름+연월 해시 (기사가 달라도 같은 사건이면 같은 노드로 모인다)
    event_id = _event_id(name, occurred)
    props = EventDTO(event_id=event_id, event_type="뉴스이슈", title=name,
                     source_doc=article_url).to_properties()
    props["name"] = name          # 프론트·조회에서 라벨로 쓰도록 name도 채운다
    return EntityDTO("Event", event_id, props), make_entity_ref("Event", event_id)


def build_news_document(
    relations: list[ExtractedRelation],
    article_url: str,
    article_title: str,
    published_at: Optional[date],
) -> tuple[NormalizedDocument, list[EvidenceRecord], list[dict]]:
    """추출 관계 → (문서, evidence, 미매핑목록).

    매트릭스 위반은 staging이 재검증해 걸러낸다(2단 방어).
    미매핑목록은 unmapped_relations에 쌓여 렉시콘 개정 근거가 된다.
    """
    entities: dict[str, EntityDTO] = {}
    rels: list[RelationshipDTO] = []
    evs: list[EvidenceRecord] = []
    unmapped: list[dict] = []
    seen_edges: set[str] = set()
    observed = published_at.isoformat() if published_at else None

    for r in relations:
        # LLM이 타입명("Event")을 이름으로 뱉으면 Event만 기사 제목으로 대체한다.
        # 사건은 기사 자체가 사건인 경우가 많아 제목이 최선의 이름이다.
        src_name = _resolve_name(r.source, r.source_type, article_title)
        tgt_name = _resolve_name(r.target, r.target_type, article_title)
        # 설명형 개체("고객사", "글로벌 빅테크")는 실명이 아니라 노드가 될 수 없다
        if src_name is None or tgt_name is None:
            continue
        if is_generic_name(src_name) or is_generic_name(tgt_name):
            continue

        # SUES 재분류 — 규제기관이 주체면 REGULATES, 단순 의사표시면 폐기
        edge_in = r.edge_type
        if edge_in == "SUES":
            verdict = reclassify_sues(src_name, r.subtype)
            if verdict == "DROP":
                continue
            if verdict:
                edge_in = verdict

        # 관계 정규화 — subtype 대표형 통일 + OTHER 재판정
        norm = normalize(edge_in, r.subtype, r.confidence,
                         raw_expression=r.raw_expression, evidence=r.evidence)
        if norm is None:
            # 12종에 못 넣은 표현 — 버리되 무엇을 버렸는지는 남긴다
            unmapped.append({
                "expression": r.raw_expression or r.subtype or "",
                "source_name": r.source, "target_name": r.target,
                "evidence": r.evidence, "source_doc": article_url,
            })
            continue
        edge_type, subtype, confidence = norm.edge_type, norm.subtype, norm.confidence

        src_entity, src_ref = _build_node(src_name, r.source_type, article_url, observed)
        tgt_entity, tgt_ref = _build_node(tgt_name, r.target_type, article_url, observed)
        if src_ref == tgt_ref:
            continue
        entities.setdefault(src_entity.key, src_entity)
        entities.setdefault(tgt_entity.key, tgt_entity)

        # 대칭 엣지는 키 사전순 고정
        if edge_type in _SYMMETRIC:
            src_ref, tgt_ref = sorted([src_ref, tgt_ref])
            direction = "symmetric"
        else:
            direction = "outbound"

        src_key, tgt_key = src_ref.split(":", 1)[1], tgt_ref.split(":", 1)[1]
        ev_id = make_evidence_id(article_url, src_key, tgt_key, edge_type, subtype)

        is_event = edge_type in _EVENT_EDGES
        meta = standard_edge_meta(
            source_doc=article_url,
            source_type="news",
            confidence=confidence,
            valid_from=None if is_event else observed,
            occurred_at=observed if is_event else None,
            observed_at=observed,
            evidence_id=ev_id,
        )
        # 한 기사에서 같은 관계를 LLM이 두 번 뱉으면 evidence_id가 충돌한다.
        # 먼저 나온 것을 남긴다(추출 순서 = 기사 서술 순서라 앞쪽이 본문 핵심).
        if ev_id in seen_edges:
            continue
        seen_edges.add(ev_id)

        props = {"subtype": subtype, "direction": direction, "sign": r.sign, **meta}
        if norm.remapped:
            props["remapped_from"] = "OTHER"    # 추출기 판단을 뒤집은 엣지 — 감사 흔적
        rels.append(RelationshipDTO(edge_type, src_ref, tgt_ref, props))

        # evidence = 기사 원문 문장 (LLM이 인용) + 출처
        text = r.evidence or f"{r.source} - {r.target} ({edge_type})"
        evs.append(EvidenceRecord(
            evidence_id=ev_id,
            text=f"{text}\n\n— 「{article_title}」 {article_url}",
            corp_code=src_key if src_key.isdigit() else None,
            source_doc=article_url,
            metadata={
                "edge_type": edge_type, "subtype": subtype,
                "source_corp": src_key, "target_corp": tgt_key,
                "rcept_no": "", "source_type": "news",
                "occurred_at": int(observed.replace("-", "")) if observed else 0,
            },
        ))

    return NormalizedDocument(list(entities.values()), rels), evs, unmapped
