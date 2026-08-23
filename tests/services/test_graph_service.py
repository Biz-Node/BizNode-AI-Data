"""graph_service.relations_of() 기준선 — 실 Neo4j 대상, mock 없음.

왜 이 파일이 있나 (2026-08-20)

`relations_of()` 는 프로덕션 Cypher(`_QUERY`)를 직접 들고 있는데, 이걸 지켜 주는
테스트가 한 개도 없었다. Search Layer 쪽 `tests/search/service/test_graph_searcher.py`
는 `monkeypatch.setattr(gs_module, "relations_of", ...)` 로 **함수를 통째로 대체**하므로
Cypher 를 어떻게 바꿔도 아무것도 감지하지 못한다.

Retrieval Layer 작업에서 `_QUERY` 에 `elementId(r)` 와 workspace 범위 절을 넣기
전에, **지금 동작을 먼저 못으로 박아 둔다.** 이 파일이 통과하지 않으면 그 변경이
무엇을 깨뜨렸는지 알 방법이 없다.

건수를 그대로 박지 않는다 — 데이터가 재적재되면 바뀐다. 재적재로도 흔들리지
않아야 하는 **불변식**만 검사한다.
"""

from __future__ import annotations

import pytest

from app.services.graph_service import HIDE_VERDICTS, relations_of
from pipeline.normalizer.base import normalize_company_name

_ANCHOR = "삼성전자"


@pytest.fixture(scope="module")
def anchored():
    return relations_of(norm_name=normalize_company_name(_ANCHOR), limit=200)


@pytest.fixture(scope="module")
def anchorless():
    return relations_of(norm_name=None, limit=400)


# ── 앵커 조회 ────────────────────────────────────────────────────────────

def test_anchored_returns_relations(anchored):
    assert anchored, "삼성전자 관계가 0건이면 데이터가 비었거나 질의가 깨진 것"


def test_every_relation_has_identity_fields(anchored):
    """Task6 에서 추가된 안정 식별자·라벨이 전부 채워져야 한다 —
    GraphSearcher 가 이 값으로 상대 엔티티를 만든다(이름 추측 금지)."""
    for r in anchored:
        assert r.source and r.target
        assert r.edge_type
        assert r.source_id and r.target_id
        assert r.source_entity_type and r.target_entity_type


def test_anchor_is_one_end_of_every_relation(anchored):
    """앵커를 주면 모든 결과의 한쪽 끝이 앵커다 — 이게 깨지면 상대를 정의할 수 없다."""
    norm = normalize_company_name(_ANCHOR)
    for r in anchored:
        assert norm in (normalize_company_name(r.source), normalize_company_name(r.target))


def test_edge_types_filter_applies():
    rels = relations_of(norm_name=normalize_company_name(_ANCHOR),
                        edge_types=["SUPPLIES_TO"], limit=50)
    assert rels
    assert {r.edge_type for r in rels} == {"SUPPLIES_TO"}


def test_limit_applies():
    assert len(relations_of(norm_name=normalize_company_name(_ANCHOR), limit=3)) <= 3


# ── 앵커 없는 조회(§8 anchorless 경로) ──────────────────────────────────

def test_anchorless_returns_relations(anchorless):
    assert anchorless


def test_anchorless_spans_multiple_edge_types(anchorless):
    assert len({r.edge_type for r in anchorless}) > 1


# ── 비-Company 끝이 살아 있는가 ★ ────────────────────────────────────────

def test_non_company_endpoints_exist(anchorless):
    """Person·Organization·Product·Event 끝 관계가 존재한다.

    ★workspace 범위 절을 넣을 때 **이 값들이 통째로 사라지지 않는지**가
      핵심 확인점이다. 범위를 주지 않은 조회에서는 반드시 남아 있어야 한다.
    """
    labels = {r.source_entity_type for r in anchorless} | {r.target_entity_type for r in anchorless}
    assert labels - {"Company"}, f"비-Company 라벨이 하나도 없다: {labels}"


# ── 필터링 규약(재구현 금지 — graph_service 안에서 이미 끝난 것) ─────────

def test_hidden_verdicts_are_excluded(anchored):
    """근거 검증에서 걸린 관계(unfounded·insufficient)는 나오지 않는다."""
    assert all(r.verdict not in HIDE_VERDICTS for r in anchored)


def test_expired_relations_are_excluded_by_default(anchored):
    """기본 exclude_status 는 expired 제외 — 끝난 관계는 현재 질의의 답이 아니다."""
    assert all(r.freshness.status != "expired" for r in anchored)


def test_stale_is_kept_not_dropped(anchorless):
    """stale 은 버리지 않고 점수만 낮춘다. 전량이 current 면 그 규약이 깨진 것이다."""
    statuses = {r.freshness.status for r in anchorless}
    assert statuses, "freshness 가 하나도 안 붙었다"
    assert statuses <= {"current", "stale", "unknown"}


# ── props 보존 — RetrieveService 의 근거 수집이 여기에 의존한다 ──────────

def test_props_preserve_evidence_ids(anchored):
    """`evidence_id`(단수)와 `evidence_ids`(복수)가 둘 다 props 에 남아 있어야 한다.

    ★GraphSearcher 는 지금 단수만 옮기고 복수를 버린다(C8). 그 수정의 전제가
      **원천에 복수가 실제로 있다**는 것이라 여기서 못 박는다.
    """
    assert any(r.props.get("evidence_id") for r in anchored)
    assert any(len(r.props.get("evidence_ids") or []) > 1 for r in anchored), \
        "evidence_ids 복수를 가진 관계가 없다 — C8 수정의 전제가 성립하지 않는다"


def test_score_is_computed(anchored):
    """★1.0 을 넘을 수 있다 — 실측 0.80~1.15.

    `score` = confidence x 신선도 x 뒷받침보정 x 판정페널티 인데 뒷받침 보정이
    최대 x1.2 라 상한이 1.2 다. `app/api/schemas.py` 의 `Relation.score` 는
    `le=1` 이므로 **그대로 실으면 ValidationError 가 난다** — 변환 지점에서
    맞춰야 한다(RetrieveService 조립 시 확인).
    """
    scores = [r.score for r in anchored]
    assert all(s > 0 for s in scores)
    assert max(scores) <= 1.2


# ══════════════════════════════════════════════════════════════════════════
#  Retrieval Layer 를 위해 새로 요구되는 것 (작업계획 Phase 3)
# ══════════════════════════════════════════════════════════════════════════

# 삼성전자 · SFA반도체 · LG전자. 셋 다 corp_code 를 가진 Company 다.
# 삼성전자의 관계 중 이 셋 안에서 양끝이 닫히는 것만 남아야 한다.
_WORKSPACE = ["00126380", "00301246", "00401731"]


# ── edge_id (설계서 §8) ─────────────────────────────────────────────────

def test_edge_id_is_populated(anchored):
    """`RetrieveResponse.relations` 의 `Relation.edge_id` 는 필수 필드다.

    `company_service`·`relation_service`·`workspace_service` 는 이미
    `elementId(r) AS eid` 를 쓰는데 `graph_service._QUERY` 만 안 썼다.
    """
    assert all(r.edge_id for r in anchored)


def test_edge_id_is_unique_per_relation(anchored):
    """★`evidence_id` 로 대신할 수 없는 이유가 이것이다 — 근거는 여러 관계가
    공유하지만(엣지 11,060 : 근거 9,228) edge_id 는 관계 하나를 가리킨다."""
    ids = [r.edge_id for r in anchored]
    assert len(set(ids)) == len(ids)


# ── workspace 는 여기서 거르지 않는다 (2026-08-20 정책 변경) ────────────

def test_relations_of_takes_no_workspace_filter():
    """★워크스페이스는 **랭킹 문맥**이지 필터가 아니다.

    한때 `workspace_keys` 를 받아 양끝이 모두 그 안에 있어야 통과시켰다. 그러면
    「삼성전자 → SK하이닉스」처럼 바깥 상대와의 관계가 통째로 사라지고, corp_code
    를 갖지 않는 Event·Person·Organization·Product 끝은 하나도 남지 않았다.
    후보 생성 단계에서 지우면 랭킹이 되살릴 방법이 없다.
    """
    import inspect

    assert "workspace_keys" not in inspect.signature(relations_of).parameters


def test_limit_is_a_score_ordered_python_slice_not_a_cypher_limit(anchored):
    """★`limit` 을 늘려도 DB 작업량은 같다 — Cypher 에 LIMIT 이 없다.

    워크스페이스 랭킹이 점수 아닌 기준으로 다시 줄 세우므로, 후보를 넉넉히
    가져오는 데 비용이 들지 않는다는 사실이 그 설계의 전제다.
    """
    few = relations_of(norm_name=normalize_company_name(_ANCHOR), limit=5)
    assert few == anchored[:5]
