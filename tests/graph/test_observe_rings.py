"""관측이 **관계를 세는가, 호출을 세는가** — 링 수치의 귀속을 못 박는다.

★이 테스트가 있는 이유(2026-08-28). `record_rings` 는 `get_relations` **호출마다**
  불린다. 중복을 안 접으면 Agent 가 같은 기업으로 도구를 두 번 부를 때 같은 관계가
  두 번 세어지는데, **몇 번 부를지는 LLM 이 정한다.** 그러면 링 수치가 랭킹이 아니라
  도구 선택에 흔들려 「링 분포가 왜 달라졌나」에 답할 수 없다.

  실제로 그렇게 됐다 — 같은 20 케이스에서 `get_relations` 호출이 7회였던 실행과
  3회였던 실행 사이에 `ring_seen` R1 이 754 → 746 으로 움직였고, 그 사이 랭킹
  변경(41bb1bb)은 **링 안 순서만** 바꿨다. `relation_selector.order()` 는 길이를
  보존하므로 링별 개수를 바꿀 수 없다 — 움직임은 전부 중복 계수 탓이었다.

  DB 를 쓰지 않는다. `record_rings` 에 원시 row 를 직접 먹인다.
"""

from __future__ import annotations

from app.core import observe


def _row(edge_id: str) -> dict:
    return {"edge_id": edge_id}


def test_same_relation_seen_twice_counts_once():
    """★같은 관계를 두 호출에서 보면 **한 번** 센다 — 호출 수가 아니라 관계 수다."""
    rows = [_row("e1"), _row("e2"), _row("e3")]
    with observe.observing() as seen:
        # Agent 가 같은 기업으로 두 번 불렀다고 하자
        observe.record_rings({1: rows}, rows, cut_count=0)
        observe.record_rings({1: rows}, rows, cut_count=0)

    assert seen.ring_seen[1] == 3, f"호출 수에 오염됐다: {dict(seen.ring_seen)}"
    assert seen.ring_kept[1] == 3
    assert seen.relations_kept == 3


def test_call_count_does_not_change_ring_numbers():
    """★**호출을 몇 번 하든 링 수치가 같아야 한다.** 이것이 귀속의 핵심이다 —
    같지 않으면 링 분포 차이를 랭킹 변경에 귀속시킬 수 없다."""
    rows = [_row(f"e{i}") for i in range(10)]

    def measure(times: int) -> tuple[dict, int]:
        with observe.observing() as seen:
            for _ in range(times):
                observe.record_rings({0: rows[:3], 1: rows[3:]}, rows[:6],
                                     cut_count=4)
            return dict(seen.ring_seen), seen.relations_kept

    assert measure(1) == measure(5), "호출 횟수가 링 수치를 바꾼다"


def test_distinct_relations_across_calls_all_count():
    """중복만 접는다 — **서로 다른 관계는 전부 센다.**"""
    with observe.observing() as seen:
        observe.record_rings({1: [_row("a"), _row("b")]}, [_row("a")], cut_count=1)
        observe.record_rings({1: [_row("b"), _row("c")]}, [_row("c")], cut_count=1)

    assert seen.ring_seen[1] == 3, "a·b·c 세 관계를 봤다"
    assert seen.ring_kept[1] == 2, "a·c 가 남았다"


def test_a_relation_kept_in_one_call_is_not_recounted_when_kept_again():
    """한 관계가 두 호출 모두에서 살아남아도 kept 는 **한 번**이다."""
    rows = [_row("x")]
    with observe.observing() as seen:
        observe.record_rings({2: rows}, rows, cut_count=0)
        observe.record_rings({2: rows}, rows, cut_count=0)

    assert seen.relations_kept == 1
    assert seen.ring_kept[2] == 1


def test_cut_is_counted_per_call_not_deduped():
    """★`cut` 만은 접지 않는다 — 같은 관계가 한 호출에서 남고 다른 호출에서
    잘릴 수 있어 「어느 쪽이 참인가」가 없다. 자른 **횟수**로 읽는 값이다."""
    with observe.observing() as seen:
        observe.record_rings({3: [_row("z")]}, [], cut_count=1)
        observe.record_rings({3: [_row("z")]}, [], cut_count=1)

    assert seen.relations_cut == 2
    assert seen.ring_seen[3] == 1, "본 것은 접힌다"


def test_ring_of_a_relation_is_remembered_from_the_first_sighting():
    """`ring_by_edge` 는 **인용된 관계의 링**을 되짚는 열쇠다 — 첫 목격을 남긴다."""
    with observe.observing() as seen:
        observe.record_rings({1: [_row("k")]}, [_row("k")], cut_count=0)
        observe.record_cited_relations(["k"])

    assert seen.cited_rings[1] == 1
    assert seen.cited_without_ring == 0
    assert seen.cited_relation_without_ring == 0


# ══════════════════════════════════════════════════════════════════
#  ★「링이 없다」는 두 가지 다른 사건이다 (2026-08-29 · Phase 11)
# ══════════════════════════════════════════════════════════════════

def test_citation_without_a_known_relation_is_not_ring_zero():
    """★링을 못 찾은 인용을 **Ring 0 으로 뭉뚱그리지 않는다** — 0 으로 세면
    「워크스페이스 안쪽이 인용됐다」는 거짓 신호가 된다."""
    with observe.observing() as seen:
        observe.record_cited_relations(["모르는edge"])

    assert seen.cited_rings == {}
    assert 0 not in seen.cited_rings


def test_a_relation_that_lost_its_ring_is_counted_apart_from_normal_ones():
    """★**관계인데 링을 못 찾은 것**은 결함 신호다 — 정상과 같은 통에 넣지 않는다.

    `record_rings` 는 `get_relations` 가 돌려준 관계를 전부 `ring_by_edge` 에
    담으므로, 인용된 edge_id 가 거기 없다는 것은 위쪽 규칙이 바뀌었다는 뜻이다.
    """
    with observe.observing() as seen:
        observe.record_cited_relations(["모르는edge"])

    assert seen.cited_relation_without_ring == 1
    assert seen.cited_without_ring == 0, "관계가 아닌 근거를 세는 통은 안 는다"


def test_non_relation_citations_go_to_the_normal_bucket():
    """★사건·검색히트·뉴스 근거는 **링이 없는 것이 정상**이다.

    부르는 쪽(`answer.verify_sources`)이 「관계가 아니다」로 판정해 `without_ring`
    으로 넘긴다 — 그 수는 결함 통을 늘리지 않는다.
    """
    with observe.observing() as seen:
        observe.record_cited_relations([], without_ring=3)

    assert seen.cited_without_ring == 3
    assert seen.cited_relation_without_ring == 0


def test_the_two_buckets_never_absorb_each_other():
    """★**섞이면 `cited_rings {}` 를 읽을 수가 없다.**

    「인용이 전부 사건·뉴스 근거였다」(정상)와 「관계를 인용했는데 되짚기가
    끊겼다」(결함)가 같은 값으로 보이기 때문이다. 한 번에 둘 다 일어나도
    각자의 통에만 담긴다.
    """
    with observe.observing() as seen:
        observe.record_rings({2: [_row("known")]}, [_row("known")], cut_count=0)
        observe.record_cited_relations(["known", "잃어버린edge"], without_ring=5)

    assert seen.cited_rings == {2: 1}
    assert seen.cited_relation_without_ring == 1
    assert seen.cited_without_ring == 5


def test_summary_carries_both_buckets():
    """★보고서·로그가 읽는 dict 에 **둘 다** 실린다 — 하나만 실으면 갈라 둔
    뜻이 문서에서 다시 합쳐진다."""
    with observe.observing() as seen:
        observe.record_cited_relations(["모르는edge"], without_ring=2)

    summary = seen.summary()
    assert summary["cited_without_ring"] == 2
    assert summary["cited_relation_without_ring"] == 1


def test_observation_is_a_noop_when_no_bucket_is_open():
    """★버킷이 안 열려 있으면 아무 일도 안 한다 — 운영 경로에 비용이 없다."""
    assert observe.current() is None
    observe.record_rings({1: [_row("q")]}, [_row("q")], cut_count=0)
    observe.record_tool("get_relations", 3)
    observe.record_cited_relations(["q"])      # 죽지 않는다


# ══════════════════════════════════════════════════════════════════
#  ★배열 근거 2차 조회는 **두지 않는다** (Phase 13 → 15 에서 되돌림)
# ══════════════════════════════════════════════════════════════════

def test_array_evidence_is_not_attributed_to_the_edges_ring():
    """★엣지의 `evidence_ids` **배열**로 링을 되짚지 **않는다.** 계약이다.

    `app/tools/dto.py` 의 `RelationDTO.evidence_id` 가 못 박는다 — 「배열은 여러
    근거의 합집합이라 **이 관계 하나의 출처가 아니다**」. 이 저장소는 **수집은 넓게,
    귀속은 좁게** 로 일관돼 있고(수집: `graph_searcher._evidence_refs` ·
    `relation_service._evidence` / 귀속: `RelationDTO` · `prompt.about` ·
    `prompt._edge_id_for`), 관측만 배열로 되짚으면 **응답과 갈린다** —
    `Source.edge_id` 는 `None` 인데 여기서만 링이 붙는다.

    ★한 번 넣었다가 뺐다(2026-08-29). 되돌린 이유가 평가셋 0건이 아니라 **계약**
      이라는 것을 이 테스트가 남긴다 — 다시 넣으려는 사람이 여기서 멈추도록.
    """
    row = {"edge_id": "e1", "evidence_id": "ev_single",
           "evidence_ids": ["ev_single", "ev_array"]}
    with observe.observing() as seen:
        observe.record_rings({2: [row]}, [row], cut_count=0)
        # 부르는 쪽은 배열 id 를 관계로 못 알아본다 → 「관계가 아니다」로 넘어온다
        observe.record_cited_relations([], without_ring=1)

    assert seen.cited_rings == {}, "★배열 근거에 링을 붙이지 않는다"
    assert seen.cited_without_ring == 1, "정상 통으로 간다 — 응답의 edge_id=None 과 같다"
    assert seen.cited_relation_without_ring == 0, "결함 신호는 아니다"


def test_a_cut_relations_ring_never_enters_the_citation_distribution():
    """★**잘린 관계의 링이 인용 분포에 들어가면 안 된다.**

    `ring_by_edge` 는 자르기 **전**(`by_ring` 전체)을 담는다. 근거 id 로 2차 조회를
    하면 잘린 관계까지 걸려, 보고서의 「본 것 / kept / 인용」 세 열이 같은 모집단이라는
    전제가 깨진다 — kept 0 인데 인용 > 0 이 나온다(평가 문서 §9-3 오염).
    """
    kept_row = {"edge_id": "keep", "evidence_id": "ev_keep"}
    cut_row = {"edge_id": "cut", "evidence_id": "ev_cut"}
    with observe.observing() as seen:
        observe.record_rings({1: [kept_row], 2: [cut_row]}, [kept_row], cut_count=1)
        # 잘린 관계의 근거가 검색 히트로 인용됐다고 하자
        observe.record_cited_relations([], without_ring=1)

    assert seen.ring_kept == {1: 1}, "R2 는 잘렸다"
    assert 2 not in seen.cited_rings, "★kept 0 인 링이 인용 분포에 나타나지 않는다"
    assert seen.cited_without_ring == 1
