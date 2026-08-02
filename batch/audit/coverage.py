"""검사 커버리지 — **무엇을 아직 안 봤는지**를 드러낸다.

`audit_graph`는 "정해둔 항목에 걸린 게 있나"를 본다. 이 도구는 반대다:
**"어떤 것이 아무 검사도 받지 않았나"**를 본다.

이 도구가 왜 필요한지 — 2026-07-29에 있었던 일:
전수 점검을 돌려 「이상 없음」을 확인한 직후, 사용자가 특정 기업 쌍 하나를
짚어 보라고 해서 들여다봤더니 결함이 넷 나왔다. 원인을 갈라보면 이렇다.

  ① 검사기가 고장나 있었다      근거 조회가 예외로 실패했는데 `except`가 삼켜
                                근거 없이 판정하고 있었다 → 전부 「판단불가」.
                                **조용히 실패하면 통과와 구별되지 않는다.**
  ② 그런 검사가 아예 없었다      「A와 B의 계약」을 관계로 오독하는 유형.
                                아무도 생각 못 한 실패 방식이었다.
  ③ 측정은 됐는데 경보가 없었다  사건 ER이 "유형어 없어 제외 159"를 매번 찍고
                                있었다. 79%가 검사 밖이라는 뜻인데 각주로 지나쳤다.
  ④ 대상에서 빠져 있었다        Company만 정규화하고 Product는 안 했다.

②는 사람 눈이 필요하다(표본 심층검사). 그러나 ①③④는 **자동으로 드러낼 수 있다** —
"이 검사가 실제로 몇 건을 봤는가"를 매번 세면 된다. 그게 이 도구다.

    python -m batch.audit.coverage
"""

from __future__ import annotations

import sys

from app.core.database import neo4j_session
from pipeline.importer.event_er import event_type_of
from pipeline.normalizer.product_names import FULL_ALIASES, _key, canonical_product

# 검사 이름 → (대상을 세는 쿼리, 검사된 것을 세는 쿼리)
# 대상은 그 검사가 **봐야 하는** 것, 검사된 것은 실제로 표시가 남은 것.
_CHECKS: list[tuple[str, str, str, str]] = [
    (
        "방향 검사", "batch.audit.relations --scope direction",
        """MATCH ()-[r]->() WHERE r.source_type='news' AND type(r) IN
           ['SUPPLIES_TO','ACQUIRES','OWNS_STAKE_IN','SUES','REGULATES',
            'DEPENDS_ON','IS_EXECUTIVE_OF'] RETURN count(*) AS n""",
        """MATCH ()-[r]->() WHERE r.source_type='news' AND type(r) IN
           ['SUPPLIES_TO','ACQUIRES','OWNS_STAKE_IN','SUES','REGULATES',
            'DEPENDS_ON','IS_EXECUTIVE_OF']
           AND r.direction_checked_at IS NOT NULL RETURN count(*) AS n""",
    ),
    (
        "대칭 병렬언급", "batch.audit.relations --scope symmetric",
        """MATCH ()-[r]->() WHERE r.source_type='news'
           AND type(r) IN ['PARTNERS_WITH','COMPETES_WITH'] RETURN count(*) AS n""",
        """MATCH ()-[r]->() WHERE r.source_type='news'
           AND type(r) IN ['PARTNERS_WITH','COMPETES_WITH']
           AND r.parallel_checked_at IS NOT NULL RETURN count(*) AS n""",
    ),
    # ★출처별로 **다른 검사**를 센다. 합치면 어느 쪽이 비었는지 알 수 없고,
    #   애초에 두 출처에 같은 검사를 쓰면 안 된다(방법서 §15-2):
    #     뉴스  문장에서 **추론**한 것 → LLM이 근거를 읽고 판정해야 한다
    #     DART  API 필드를 **템플릿 문장**으로 조립한 것 → LLM에 물으면 자기가 쓴
    #           문장을 자기가 채점하는 꼴이다. 값의 범위와 원문 대조로 본다.
    (
        "근거정합성(뉴스)", "batch.audit.grounding --llm --apply "
                            "--all --source news",
        """MATCH ()-[r]->() WHERE r.source_type='news' AND (r.evidence_id IS NOT NULL
           OR r.evidence_ids IS NOT NULL) RETURN count(*) AS n""",
        """MATCH ()-[r]->() WHERE r.source_type='news' AND (r.evidence_id IS NOT NULL
           OR r.evidence_ids IS NOT NULL)
           AND r.grounding_checked_at IS NOT NULL RETURN count(*) AS n""",
    ),
    (
        "전문 재검증(뉴스 의심분)", "batch.audit.grounding_fulltext",
        """MATCH ()-[r]->() WHERE r.grounding_suspect AND r.source_type='news'
           RETURN count(*) AS n""",
        """MATCH ()-[r]->() WHERE r.grounding_suspect AND r.source_type='news'
           AND r.grounding_verdict IS NOT NULL RETURN count(*) AS n""",
    ),
    (
        "DART 본문파싱 원문대조", "batch.audit.dart --apply --only parsed",
        """MATCH ()-[r]->() WHERE r.source_type='dart'
           AND type(r) IN ['DEVELOPS','SUPPLIES_TO','PARTNERS_WITH','DEPENDS_ON']
           AND r.source_doc IS NOT NULL RETURN count(*) AS n""",
        """MATCH ()-[r]->() WHERE r.source_type='dart'
           AND type(r) IN ['DEVELOPS','SUPPLIES_TO','PARTNERS_WITH','DEPENDS_ON']
           AND r.source_doc IS NOT NULL
           AND (r.parsed_checked_at IS NOT NULL OR r.parsed_suspect)
           RETURN count(*) AS n""",
    ),
]

# 아무 검사도 받지 않는 구멍을 직접 센다
_BLIND: list[tuple[str, str, str]] = [
    ("방향 검사 대상이 아닌 뉴스 엣지",
     "DEVELOPS·HAS_EVENT·IMPACTS 등 — 방향을 확인하지 않는다",
     """MATCH ()-[r]->() WHERE r.source_type='news' AND NOT type(r) IN
        ['SUPPLIES_TO','ACQUIRES','OWNS_STAKE_IN','SUES','REGULATES',
         'DEPENDS_ON','IS_EXECUTIVE_OF','PARTNERS_WITH','COMPETES_WITH']
        RETURN count(*) AS n"""),
    ("근거가 아예 없는 엣지",
     "무엇을 보고 만든 관계인지 확인할 방법이 없다",
     """MATCH ()-[r]->() WHERE r.evidence_id IS NULL AND r.evidence_ids IS NULL
        RETURN count(*) AS n"""),
]

_SUSPECT = [
    ("근거 미뒷받침 표시", "MATCH ()-[r]->() WHERE r.grounding_suspect "
                          "RETURN count(*) AS n"),
    ("유형오류 표시(재분류 대기)", "MATCH ()-[r]->() WHERE r.retype_suspect "
                                  "RETURN count(*) AS n"),
    ("이름 의심 Product", "MATCH (p:Product) WHERE p.name_suspect "
                          "RETURN count(*) AS n"),
]


def _one(session, cypher: str) -> int:
    rec = session.run(cypher).single()
    return int(rec["n"]) if rec else 0


def main() -> int:
    with neo4j_session() as session:
        total_edges = _one(session, "MATCH ()-[r]->() RETURN count(*) AS n")
        total_nodes = _one(session, "MATCH (n) RETURN count(*) AS n")

        print("=" * 70)
        print(f"  검사 커버리지   노드 {total_nodes:,} · 엣지 {total_edges:,}")
        print("=" * 70)

        print("\n■ 검사별 커버율 — 대상 중 실제로 본 비율")
        worst = 100.0
        for name, cmd, target_q, done_q in _CHECKS:
            target = _one(session, target_q)
            done = _one(session, done_q)
            pct = (done / target * 100) if target else 100.0
            worst = min(worst, pct)
            bar = "█" * int(pct / 5) + "·" * (20 - int(pct / 5))
            flag = "  ⚠ 미검사분 있음" if pct < 99.5 else ""
            print(f"  {name:16} {bar} {pct:5.1f}%  ({done:,}/{target:,}){flag}")
            if pct < 99.5:
                print(f"      → {cmd}")

        print("\n■ 사각지대 — 어떤 검사도 받지 않는 것")
        for name, why, q in _BLIND:
            n = _one(session, q)
            share = f"{n/total_edges*100:.0f}%" if total_edges else ""
            print(f"  · {name:38} {n:>5}건 {share}")
            print(f"      {why}")

        # ── 파이썬으로만 셀 수 있는 사각지대 ─────────────────────
        # 규칙이 Cypher가 아니라 코드에 있어(유형어 목록·별칭 사전) 여기서 센다.
        # ★쿼리로 전체를 세고 사각지대라 부르면 리포트가 거짓말을 한다.
        events = [dict(r) for r in session.run(
            "MATCH (e:Event) RETURN e.name AS name")]
        no_type = [e for e in events if not event_type_of(e["name"])]
        print(f"  · {'사건 ER 제외 Event (유형어 없음)':38} "
              f"{len(no_type):>5}건 {len(no_type)/max(len(events),1)*100:.0f}%")
        print(f"      이름이 달라도 병합 후보에 오르지 못한다 "
              f"(전체 Event {len(events)})")

        products = [dict(r) for r in session.run(
            "MATCH (p:Product) RETURN p.name AS name")]
        unknown = [p for p in products
                   if canonical_product(p["name"]) == p["name"]
                   and _key(p["name"]) not in FULL_ALIASES]
        print(f"  · {'정규화 사전에 없는 Product':38} "
              f"{len(unknown):>5}건 {len(unknown)/max(len(products),1)*100:.0f}%")
        print(f"      표기가 갈리면 통일되지 않는다 "
              f"(전체 Product {len(products)})")

        print("\n■ 표시만 하고 사람 판단을 기다리는 것")
        for name, q in _SUSPECT:
            print(f"  · {name:38} {_one(session, q):>5}건")

        print("\n" + "=" * 70)
        if worst < 99.5:
            print("  ⚠ 커버율 100%가 아닌 검사가 있습니다 — 위 명령을 돌리세요.")
        else:
            print("  ✅ 정의된 모든 검사가 대상을 전부 봤습니다.")
        print("  ※ 이것은 「검사가 완전하다」는 뜻이 아니라")
        print("     「정의해 둔 검사를 빠짐없이 돌렸다」는 뜻입니다.")
        print("     사각지대에 있는 것은 여전히 표본 심층검사로만 발견됩니다.")
        print("=" * 70)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
