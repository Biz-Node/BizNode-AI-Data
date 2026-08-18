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
from pipeline.normalizer.product_registry import record as record_products
from pipeline.normalizer.product_names import canonical_product
from pipeline.normalizer.product_names import norm_key as product_key
from pipeline.normalizer.name_judge import judge_names
from pipeline.normalizer.subtype_registry import get_registry
from pipeline.normalizer.relations import normalize, reclassify_sues

# 대칭 엣지 — 키 사전순 단방향 저장(방법서 §11)
_SYMMETRIC = {"PARTNERS_WITH", "COMPETES_WITH"}
# 사건 엣지 — occurred_at 사용
_EVENT_EDGES = {"ACQUIRES", "SUES", "HAS_EVENT", "IMPACTS"}


_AMOUNT_TYPES = frozenset({
    "ACQUIRES", "SUPPLIES_TO", "REGULATES", "SUES", "OWNS_STAKE_IN",
})


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
    conn=None,
) -> tuple[NormalizedDocument, list[EvidenceRecord], list[dict]]:
    """추출 관계 → (문서, evidence, 미매핑목록).

    매트릭스 위반은 staging이 재검증해 걸러낸다(2단 방어).
    미매핑목록은 unmapped_relations에 쌓여 렉시콘 개정 근거가 된다.

    ★`conn`을 주면 subtype 레지스트리가 붙는다(2026-08-13). 사전(`_SUBTYPE_CANON`
      48개)만으로는 실제 2,040종을 못 덮는다 — 레지스트리가 이미 그래프에 있는
      표현으로 새 표현을 흡수한다. 없으면 사전까지만 적용한다(하위 호환).
    """
    registry = get_registry(conn) if conn is not None else None

    # ★설명형 판정 2단 — 문법으로 거른 뒤 남은 것만 모델에게 묻는다(2026-08-13).
    #
    #   1차(`is_generic_name`)는 문법·닫힌 목록이라 무료다. 그런데 「글로벌 빅테크」
    #   같은 **의미상 설명형**은 문법으로 못 가른다. 전에는 키워드 목록으로 잡았는데
    #   부분 문자열이 실명을 때렸다(「총파**업계**획」·「한국**정보**통신」).
    #
    #   이 기사에 나온 이름을 한 번에 모아 물으므로 기사당 호출이 1회 늘어난다
    #   (0.25원 — 추출 14.7원 대비 1.7%). 판정은 이름 단위로 캐시된다.
    #   ★타입에 따라 기준이 다르다 — 제품은 카테고리(「감속기」·「휴머노이드 로봇」)도
    #     이름으로 인정한다. 한 기준으로 묶었다가 「휴머노이드 로봇」이 「일반명사
    #     조합」으로 버려졌다. 우리 그래프는 제품군을 일부러 노드로 쓴다.
    proper: dict[str, bool] = {}
    if conn is not None:
        by_kind: dict[str, set[str]] = {"entity": set(), "product": set()}
        for r in relations:
            for nm, ntype in (
                (_resolve_name(r.source, r.source_type, article_title), r.source_type),
                (_resolve_name(r.target, r.target_type, article_title), r.target_type),
            ):
                if nm and not is_generic_name(nm):
                    by_kind["product" if ntype == "Product" else "entity"].add(nm)
        for kind, names in by_kind.items():
            if names:
                proper.update(judge_names(conn, sorted(names), kind))

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
        # 2차 — 모델이 「설명이다」라고 한 것은 노드로 만들지 않는다.
        # (판정이 없으면 통과 — 판정기가 죽었다고 실명을 버리지 않는다)
        if not proper.get(src_name, True) or not proper.get(tgt_name, True):
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
                         registry=registry, conn=conn,
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

        # ★`raw_expression`은 **엣지에 싣지 않는다.**
        #
        #   2026-08-11에 한 번 실었다가 되돌렸다. 「추출기가 뽑는데 로더가 버린다」가
        #   이유였는데, 다시 보니 **버리는 게 맞았다.** 이 필드는 원래
        #   「12종에 안 맞는 관계」의 탈출구다(프롬프트 규칙 4) — `map_other`가
        #   이걸 보고 되살리고, 실패하면 `unmapped_relations`에 쌓여 무엇을
        #   놓치는지 추적한다. 그 일은 아래 `unmapped`에서 이미 하고 있다.
        #
        #   적재에 성공한 엣지에는 답할 질문이 없다. 원문이 필요하면 `evidence`가
        #   문장 그대로 있고, 요약이 필요하면 `subtype`이 있다. 가운데 값은
        #   **아무도 읽지 않는다** — 그리고 안 읽히는 필드는 썩는다. subtype이
        #   딱 그렇게 3개월 만에 「영향」 899건이 됐다.

        # ★HAS_EVENT의 role — 당사자(subject)와 단순 언급(mentioned)을 가른다.
        #   없으면 mentioned로 본다(과하게 당사자로 만들면 「이 기업에 난 일」
        #   집계가 부풀려진다).
        if edge_type == "HAS_EVENT":
            props["role"] = r.role or "mentioned"

        # ★지분율 — `OWNS_STAKE_IN`은 DART가 채우지만 뉴스에도 나온다.
        #   `ACQUIRES`는 **담을 칸이 아예 없어** 「지분 61.6%」가 subtype으로
        #   밀려 있었다(2026-08-12). 61.6%면 경영권, 5%면 단순 투자라 전혀 다른
        #   사실인데 문자열로는 조회도 비교도 안 된다.
        if r.ratio is not None and edge_type in ("OWNS_STAKE_IN", "ACQUIRES"):
            props["ratio"] = float(r.ratio)

        # ★거래 규모 — 「420억원 규모 공급계약」과 「4억원 규모」는 리스크가 전혀
        #   다른데 담을 칸이 없어 둘 다 그냥 SUPPLIES_TO였다(2026-08-12 신설).
        #   타입마다 세는 대상이 다르다 — 인수 대금 / 계약 규모 / 과징금 / 청구액.
        #   `ontology.AMOUNT_RULES` 참고. 그 밖의 타입은 금액이 뜻을 갖지 않는다.
        if r.amount is not None and edge_type in _AMOUNT_TYPES:
            props["amount"] = float(r.amount)

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

    # ★적재한 제품명을 레지스트리에 남긴다(2026-08-13).
    #   다음 추출 때 프롬프트로 되돌아가 **같은 이름을 다시 쓰게** 한다.
    #   제품은 이름이 곧 식별자라, 표기가 갈리면 그 제품의 관계도 갈린다.
    if conn is not None:
        names = [e.properties.get("name") for e in entities.values()
                 if e.type == "Product" and e.properties.get("name")]
        if names:
            record_products(conn, names)

    return NormalizedDocument(list(entities.values()), rels), evs, unmapped
