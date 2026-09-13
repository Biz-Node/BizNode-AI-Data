"""`company_merge` 2단(노드 병합)의 판정 경로 — **되돌리기 어려운 쓰기 앞의 관문 전부.**

왜 테스트로 못 박나 (2026-09-13)

노드 병합은 되돌리기 어렵다 — 두 회사의 관계가 한 노드에 섞이면 어느 것이 누구
것이었는지 그래프만 봐서는 모른다. 그래서 관문이 여섯이고, 관계·사건이 **하나도
안 줄어드는지**를 기대값(정본 + stub)으로 미리 계산해 병합 직후 대조한다.

★`plan_merge()` 는 DB 를 안 만진다. 그래프·표를 읽은 모양 그대로 가짜로 준다.
  관계 한 줄 = [유형, 정방향, 상대 elementId, subtype, evidence_id, 상대가 Event, 상대가 정본].
"""

from __future__ import annotations

import pytest

from batch.repair import company_merge as cm

CC, STUB, CANON = "00164742", "현대차", "현대자동차"
CONFIRMED = [(CC, STUB, "같은 회사")]


def _rel(t, out=True, other="o1", sub="", ev=None, is_event=False, to_keep=False):
    return [t, out, other, sub, ev, is_event, to_keep]


def _stub(rels=None, name="현대차"):
    return {"eid": "s", "name": name, "also": [], "rels": rels if rels is not None else [_rel("SUPPLIES_TO")]}


def _keep(rels=None, merged=(), norm=CANON):
    return {"eid": "k", "name": "현대자동차", "norm": norm, "merged": list(merged),
            "rels": rels if rels is not None else [_rel("OWNS_STAKE_IN", other="o9")[:5]]}


def _pg(alias=(CANON, "hand"), se_src=2, se_tgt=3, attr=True, dup=0):
    return {"alias": alias, "se_src": se_src, "se_tgt": se_tgt,
            "se_dup_src": dup, "se_dup_tgt": 0, "attr_stub": attr, "cards": 0}


def _plan(stub=_stub(), keeps=None, pg=None, confirmed=CONFIRMED):
    keeps = [_keep()] if keeps is None else keeps
    return cm.plan_merge(confirmed, {STUB: stub}, {CC: keeps}, {STUB: pg or _pg()})[0]


# ── 대상 범위 ──────────────────────────────────────────────

def test_CONFIRMED_21건만_대상이고_REVIEWED_는_한_건도_안_들어간다():
    keys = {s for _, s, _ in cm.CONFIRMED}
    assert len(keys) == 21
    assert not keys & set(cm.REVIEWED_NOT_MERGED)
    for held in ("퀄컴", "원익pne", "fadutechnology", "ktamerica", "삼성전자아메리카"):
        assert held in cm.REVIEWED_NOT_MERGED and held not in keys


def test_REVIEWED_NOT_MERGED_에_있으면_CONFIRMED_라도_접지_않는다(monkeypatch):
    monkeypatch.setitem(cm.REVIEWED_NOT_MERGED, STUB, "다른 법인 · 테스트")
    d = _plan()
    assert d.action == "skip" and "REVIEWED_NOT_MERGED" in d.reason


# ── 정본·stub 실존 ─────────────────────────────────────────

def test_정본이_없으면_차단된다():
    d = _plan(keeps=[])
    assert d.action == "skip" and "정본 노드 없음" in d.reason


def test_정본이_여럿이면_차단된다():
    d = _plan(keeps=[_keep(), _keep()])
    assert d.action == "skip" and "2개" in d.reason


def test_별칭이_정본을_가리키지_않으면_먼저_1단을_요구한다():
    d = _plan(pg=_pg(alias=(STUB, "first_seen")))
    assert d.action == "skip" and "--only aliases" in d.reason


def test_stub_이_없고_merged_keys_에도_없으면_손대지_않는다():
    d = _plan(stub=None)
    assert d.action == "skip" and "merged_keys 에도 없어" in d.reason


# ── 재실행 ────────────────────────────────────────────────

def test_이미_병합됐고_PG_도_정리됐으면_아무것도_안_한다():
    d = _plan(stub=None, keeps=[_keep(merged=[STUB])], pg=_pg(se_src=0, se_tgt=0, attr=False))
    assert d.action == "already"


def test_이미_병합됐는데_PG_에_stub_키가_남았으면_PG_만_마저_한다():
    """Neo4j 는 됐고 PG 커밋 전에 죽은 실행의 복구 경로."""
    d = _plan(stub=None, keeps=[_keep(merged=[STUB])], pg=_pg(se_src=1, se_tgt=0, attr=True))
    assert d.action == "pg-only" and "2건 남음" in d.reason


# ── 다른 법인 증거 ─────────────────────────────────────────

def test_정본과_직접_관계가_있으면_접지_않는다():
    """FADU 사례 — 파두 -OWNS_STAKE_IN(자회사)-> FADU Technology Incorporated."""
    d = _plan(stub=_stub(rels=[_rel("OWNS_STAKE_IN", out=False, other="k", sub="자회사", to_keep=True)]))
    assert d.action == "skip" and "다른 법인" in d.reason and "FADU" in d.reason


# ── 관계·사건 보존 ─────────────────────────────────────────

def test_기대_연결_수는_정본과_stub_의_합이다_하나도_안_줄어든다():
    stub = _stub(rels=[_rel("SUPPLIES_TO", other="a"), _rel("PARTNERS_WITH", out=False, other="b"),
                       _rel("HAS_EVENT", other="e1", is_event=True)])
    keep = _keep(rels=[_rel("OWNS_STAKE_IN", other="c")[:5], _rel("HAS_EVENT", other="e2")[:5]])
    d = _plan(stub=stub, keeps=[keep])
    assert d.action == "merge"
    assert d.stub_deg == 3 and d.stub_ev == 1
    assert d.expect_deg == 2 + 3
    assert d.expect_ev == 1 + 1


def test_사건은_HAS_EVENT_정방향만_센다():
    stub = _stub(rels=[_rel("HAS_EVENT", other="e1", is_event=True),
                       _rel("IMPACTS", out=False, other="e2", is_event=True)])
    d = _plan(stub=stub)
    assert d.stub_ev == 1 and d.stub_deg == 2


def test_같은_유형_상대_관계는_세기만_하고_접지_않는다():
    """subtype 이 다른 36건 — mergeRels:false 로 그대로 옮기고 edges.py 몫으로 남긴다."""
    stub = _stub(rels=[_rel("SUPPLIES_TO", out=False, other="rotem", sub="전동차", ev="e1"),
                       _rel("SUPPLIES_TO", out=False, other="rotem", sub="광역철도 전동차", ev="e2"),
                       _rel("SUPPLIES_TO", other="x", sub="a", ev="e3")])
    keep = _keep(rels=[_rel("SUPPLIES_TO", out=False, other="rotem", sub="광역철도 전동차", ev="e2")[:5]])
    d = _plan(stub=stub, keeps=[keep])
    assert d.action == "merge"
    assert d.collide == 2          # 유형·방향·상대가 같은 stub 관계
    assert d.exact_dup == 1        # 그중 subtype·evidence 까지 같은 것
    assert d.expect_deg == 1 + 3   # 접지 않으므로 전부 더한다
    assert "edges.py" in d.reason


# ── PG 정합 ──────────────────────────────────────────────

def test_staged_edges_와_company_attributes_변경_건수를_판정에_싣는다():
    d = _plan(pg=_pg(se_src=21, se_tgt=48, attr=True))
    assert d.action == "merge"
    assert "staged_edges 21+48행" in d.reason and "company_attributes 1행" in d.reason


def test_company_attributes_에_stub_행이_없으면_0행이다():
    d = _plan(pg=_pg(attr=False))
    assert "company_attributes 0행" in d.reason


def test_retarget_SQL_은_Company_행만_바꾸고_삭제는_corp_code_없는_행만_지운다():
    """`node_identity` 와 같은 필터. 정본 행(corp_code 키)은 어떤 경우에도 안 지운다."""
    assert "src_node_type = 'Company'" in cm._SE_RETARGET.format(side="src")
    assert "tgt_node_type = 'Company'" in cm._SE_RETARGET.format(side="tgt")
    assert "corp_code IS NULL" in cm._ATTR_DELETE


def test_병합_Cypher_는_관계를_접지_않고_정본_속성을_지킨다():
    assert "mergeRels: false" in cm._MERGE_NODE
    assert "properties: 'discard'" in cm._MERGE_NODE
    assert "merged_keys" in cm._MERGE_NODE


# ── 실제 그래프 대조 (적용 뒤에만 의미가 있다) ───────────────

@pytest.mark.needs_db
def test_적용_뒤_21개_stub_은_남아_있지_않고_정본이_흡수했다():
    from app.core.database import neo4j_session, postgres_connection
    keys = [s for _, s, _ in cm.CONFIRMED]
    with neo4j_session() as s:
        left = s.run("MATCH (c:Company) WHERE c.corp_code IS NULL AND c.norm_name IN $k "
                     "RETURN count(c) AS n", k=keys).single()["n"]
        assert left == 0
        for cc, stub, _ in cm.CONFIRMED:
            r = s.run("MATCH (k:Company {corp_code: $cc}) RETURN k.merged_keys AS m, "
                      "k.also_names AS a", cc=cc).single()
            assert stub in (r["m"] or []), stub
            assert stub in (r["a"] or []), stub
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM staged_edges WHERE (src_node_type='Company' AND src_key = ANY(%s)) "
                    "OR (tgt_node_type='Company' AND tgt_key = ANY(%s))", (keys, keys))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM company_attributes WHERE node_key = ANY(%s)", (keys,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM company_aliases WHERE alias_key = ANY(%s) AND source = 'hand'", (keys,))
        assert cur.fetchone()[0] == 21
