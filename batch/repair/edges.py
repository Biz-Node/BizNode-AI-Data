"""기존 엣지 subtype 소급 정규화 + 중복 병합 (유지보수 유틸).

정규화기(`normalizer/relations.py`)는 적재 시점에 동작하므로, 그 이전에 들어온
엣지는 표기가 제각각인 채로 남는다(`""`·`"."`·`Joint Venture`). 그래프 전체를
한 번 훑어 대표형으로 맞춘다.

**2단계로 돈다:**
  1. subtype → 대표형
  2. 그 결과 같아진 엣지 병합

2단계가 필요한 이유: Neo4j의 엣지 식별자에 subtype이 들어가므로, 전에는
`""`와 `"협력"`으로 갈려 있던 두 엣지가 1단계 후 **같은 관계**가 된다. Neo4j는
이미 만들어진 엣지를 자동으로 합쳐 주지 않아 중복이 남는다.

**멱등**하다 — 이미 정규화·병합된 그래프는 건드리지 않으므로 몇 번 돌려도 안전하다.
Neo4j만 손대고 staged_edges(authority)는 두는데, 다음 적재 때 정규화기가 같은
값을 만들어 내므로 어긋나지 않는다.

실행:
  python -m batch.repair.edges --dry-run   # 바뀔 내용만 확인
  python -m batch.repair.edges
"""

from __future__ import annotations

import sys
from collections import Counter

from app.core.database import neo4j_session
from pipeline.normalizer.relations import (
    _DEFAULT_SUBTYPE, canonical_forms, canonical_subtype)

# 엣지 타입 → 등재 대표형 목록. Cypher에 통째로 넘겨 「대표형 우선」에 쓴다.
_CANON_BY_TYPE = {t: sorted(canonical_forms(t)) for t in _DEFAULT_SUBTYPE}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 엣지 타입 × 현재 subtype 조합만 조회 — 개별 엣지를 다 읽을 필요가 없다
_SCAN = """
MATCH ()-[r]->()
RETURN type(r) AS edge_type, r.subtype AS subtype, count(*) AS n
"""

# 조합 단위 일괄 수정 — 엣지 수만큼이 아니라 조합 수만큼만 질의한다
_UPDATE = """
MATCH ()-[r]->()
WHERE type(r) = $edge_type AND coalesce(r.subtype, '') = $old
SET r.subtype = $new
RETURN count(r) AS n
"""

# ── 정규화 후 중복 병합 ──────────────────────────────────────
# subtype을 대표형으로 접으면 **전에는 달랐던 엣지가 같아진다.**
# (정규화기 도입 전 `subtype=""` 엣지 + 도입 후 `"협력"` 엣지 → 백필하면 충돌)
# Neo4j는 이미 만들어진 엣지를 자동으로 합쳐 주지 않으므로 여기서 접는다.
#
# ★근거를 버리지 않는다. 중복은 대개 **서로 다른 기사가 같은 관계를 보도한 것**이라
#   오히려 신뢰의 근거다. 대표 엣지 하나만 남기되 evidence_id·source_doc은 전부
#   목록으로 보존하고, 몇 개 출처가 뒷받침하는지(corroboration)를 남긴다.
_DEDUP = """
MATCH (a)-[r]->(b)
WITH a, b, type(r) AS t, coalesce(r.subtype, '') AS s, collect(r) AS rels
WHERE size(rels) > 1
WITH rels,
     [x IN rels WHERE x.evidence_id IS NOT NULL | x.evidence_id] AS all_ev,
     [x IN rels WHERE x.source_doc IS NOT NULL | x.source_doc] AS all_docs,
     reduce(m = '', x IN rels |
        CASE WHEN coalesce(x.last_seen, '') > m THEN x.last_seen ELSE m END) AS newest,
     // 생존자 = 확신도 높은 것, 같으면 최근 관측
     reduce(best = head(rels), x IN tail(rels) |
        CASE WHEN coalesce(x.confidence, 0) > coalesce(best.confidence, 0)
                  OR (coalesce(x.confidence, 0) = coalesce(best.confidence, 0)
                      AND coalesce(x.last_seen, '') > coalesce(best.last_seen, ''))
             THEN x ELSE best END) AS keep
// toSet — 같은 기사에서 온 엣지끼리 병합되면 evidence_id가 겹친다.
// 중복이 남으면 corroboration(뒷받침 출처 수)이 부풀고, ChromaDB 조회도 깨진다.
SET keep.evidence_ids  = apoc.coll.toSet(all_ev),
    keep.source_docs   = apoc.coll.toSet(all_docs),
    keep.corroboration = size(apoc.coll.toSet(all_docs)),
    keep.last_seen     = CASE WHEN newest <> '' THEN newest ELSE keep.last_seen END
WITH keep, [x IN rels WHERE elementId(x) <> elementId(keep)] AS drops
FOREACH (d IN drops | DELETE d)
RETURN count(*) AS groups
"""

_COUNT_DUPS = """
MATCH (a)-[r]->(b)
WITH a, b, type(r) AS t, coalesce(r.subtype, '') AS s, collect(r) AS rels
WHERE size(rels) > 1
RETURN t + ' ' + coalesce(a.name, '?') + '→' + coalesce(b.name, '?')
       + ' [' + s + '] ×' + toString(size(rels)) AS v
"""

# ── 3단계: 엣지 클러스터링 (같은 두 노드 · 같은 엣지 타입) ────────
#
# subtype이 **개방형**이라 같은 관계가 표현만 달리해 여러 엣지가 된다:
#   삼성전자 -OWNS_STAKE_IN-> 레인보우로보틱스
#     [최대주주] [5%이상주주] [지분보유] [지분투자] [지분 인수] [출자]   ← 6개 엣지
#   = DART 3종 API + 뉴스 3종 표현이 **같은 사실**을 6번 말한 것
#
# 관계망에서 "삼성전자가 레인보우로보틱스 지분을 갖고 있다"는 **하나의 사실**이다.
# 엣지를 하나로 접고, 세부 표현은 `subtypes` 배열에 전부 남긴다.
#   · 대표 subtype = 가장 많이 관측된 것 (동수면 더 구체적인 = 긴 것)
#     ★단, **엣지 타입 이름은 대표가 될 수 없다** (아래 참조)
#   · occurred_at·valid_from = 가장 이른 것 (사건의 시작)
#   · last_seen = 가장 늦은 것 (신선도)
#   · evidence_ids·source_docs = 전부 보존 → 근거는 하나도 잃지 않는다
#
# ★타입 이름을 대표에서 빼는 이유 (2026-08-11)
#
#   최빈값 규칙이 **옛 데이터를 편든다.** 과거 프롬프트가 subtype을 타입 이름으로
#   채워서 「공급」이 528건 쌓여 있는데, 새 방식으로 뽑은 「취출 로봇」은 1건이다.
#   같은 두 노드에 둘이 겹치면 최빈값 규칙으로 **「공급」이 이긴다** — 새로 잘 뽑은
#   값이 조용히 덮인다.
#
#     한양로보틱스 → 삼성전자   [공급 ×3(옛) | 취출 로봇 ×1(새)]  → 「공급」  ✗
#
#   타입 이름은 애초에 정보가 없다(타입이 이미 하는 말). 대표 후보에서 빼면
#   백필 순서에 상관없이 안전해진다. 다만 **후보가 전부 타입 이름이면 손대지
#   않는다** — 그건 클러스터링이 아니라 백필이 할 일이다.
#
#   ※`subtypes` 배열에는 타입 이름도 그대로 남긴다. 무엇이 관측됐는지의 기록이다.
_CLUSTER = """
MATCH (a)-[r]->(b)
WITH a, b, type(r) AS t, collect(r) AS rels
WHERE size(rels) > 1
WITH t, rels,
     [x IN rels WHERE x.evidence_id IS NOT NULL | x.evidence_id]
       + reduce(acc = [], x IN rels | acc + coalesce(x.evidence_ids, [])) AS all_ev,
     [x IN rels WHERE x.source_doc IS NOT NULL | x.source_doc]
       + reduce(acc = [], x IN rels | acc + coalesce(x.source_docs, [])) AS all_docs,
     [x IN rels WHERE x.subtype IS NOT NULL AND x.subtype <> '' | x.subtype] AS all_subs
// ── 대표 후보를 3단계로 좁힌다 ──────────────────────────────
// 각 단계는 **비면 앞 단계로 되돌린다** — 걸러서 후보가 없어지면 안 되므로.
//
//   ① 타입 이름 제외   「공급」·「협력」은 타입이 이미 하는 말이다.
//                     최빈값 규칙이 옛 데이터를 편들던 것을 막는다.
//   ② 숫자 제외       「지분 12.42%」는 `ratio` 필드에 이미 있다. 중복이고,
//                     문자열이라 조회도 안 된다. (실측 2026-08-11)
//   ③ 한글 우선       「defamation」이 「명예훼손」을 이겼다 — 동수일 때
//                     「긴 것」 규칙이 영어를 편든다. 화면에 뜨는 값이라
//                     읽히는 쪽을 고른다.
WITH t, rels, all_ev, all_docs, all_subs,
     [s IN all_subs WHERE s <> coalesce($defaults[t], '\\u0000')] AS c0
WITH t, rels, all_ev, all_docs, all_subs, c0,
     [s IN c0 WHERE NOT s =~ '.*\\\\d+(\\\\.\\\\d+)?\\\\s*(%|퍼센트|억|조|만원|억원).*'] AS c1
WITH t, rels, all_ev, all_docs, all_subs,
     CASE WHEN size(c1) > 0 THEN c1 ELSE c0 END AS c2
WITH t, rels, all_ev, all_docs, all_subs, c2,
     [s IN c2 WHERE s =~ '.*[가-힣].*'] AS c3
WITH t, rels, all_ev, all_docs, all_subs,
     CASE WHEN size(c3) > 0 THEN c3 ELSE c2 END AS c4
WITH t, rels, all_ev, all_docs, all_subs, c4 AS cand
// 대표 subtype — 최빈값, 동수면 긴 것(더 구체적)
WITH rels, all_ev, all_docs, all_subs,
     reduce(best = '', s IN cand |
        CASE WHEN size([x IN cand WHERE x = s]) > size([x IN cand WHERE x = best])
                  OR (size([x IN cand WHERE x = s]) = size([x IN cand WHERE x = best])
                      AND size(s) > size(best))
             THEN s ELSE best END) AS top_sub
WITH rels, all_ev, all_docs, all_subs, top_sub,
     reduce(m = '9999-99-99', x IN rels |
        CASE WHEN coalesce(x.occurred_at, x.valid_from, '9999-99-99') < m
             THEN coalesce(x.occurred_at, x.valid_from, '9999-99-99') ELSE m END) AS first_seen,
     reduce(m = '', x IN rels |
        CASE WHEN coalesce(x.last_seen, '') > m THEN x.last_seen ELSE m END) AS newest,
     reduce(best = head(rels), x IN tail(rels) |
        CASE WHEN coalesce(x.confidence, 0) > coalesce(best.confidence, 0)
             THEN x ELSE best END) AS keep
// ★근거 검증 도장을 걷는다(2026-08-11).
//   접고 나면 `evidence_ids`가 **여러 근거의 합집합**이 되는데, 판정은 대표 엣지
//   하나를 보고 내린 것이라 낡는다. 대표가 `unfounded`였다면 합쳐진 다른 근거가
//   뒷받침해도 그대로 물리쳐진다.
//   1차(토큰 대조)는 매번 전수라 알아서 다시 보지만, 2차(LLM)는 도장으로
//   대상을 고르므로 여기서 걷어야 다시 본다. (`retypes`와 같은 이유)
SET keep.grounding_checked_at = NULL,
    keep.grounding_verdict    = NULL,
    keep.grounding_verdict_why = NULL,
    keep.grounding_reason     = NULL,
    keep.grounding_stage1     = NULL,
    keep.grounding_suspect    = NULL,
    keep.subtype        = CASE WHEN top_sub <> '' THEN top_sub ELSE keep.subtype END,
    keep.subtypes       = apoc.coll.toSet(all_subs),
    keep.evidence_ids   = apoc.coll.toSet(all_ev),
    keep.source_docs    = apoc.coll.toSet(all_docs),
    keep.corroboration  = size(apoc.coll.toSet(all_docs)),
    keep.last_seen      = CASE WHEN newest <> '' THEN newest ELSE keep.last_seen END,
    keep.occurred_at    = CASE WHEN first_seen <> '9999-99-99' AND keep.occurred_at IS NOT NULL
                               THEN first_seen ELSE keep.occurred_at END
WITH keep, [x IN rels WHERE elementId(x) <> elementId(keep)] AS drops
FOREACH (d IN drops | DELETE d)
RETURN count(*) AS clusters
"""

_COUNT_CLUSTERS = """
MATCH (a)-[r]->(b)
WITH a, b, type(r) AS t, collect(DISTINCT coalesce(r.subtype, '')) AS subs, count(*) AS c
WHERE c > 1
RETURN t + ' ' + coalesce(a.name, '?') + '→' + coalesce(b.name, '?')
       + ' ×' + toString(c) + '  [' + apoc.text.join(subs, ' | ') + ']' AS v
ORDER BY c DESC
"""


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    changes: list[tuple[str, str, str, int]] = []

    with neo4j_session() as session:
        combos = [dict(r) for r in session.run(_SCAN)]

        for c in combos:
            edge_type, old, n = c["edge_type"], c["subtype"] or "", c["n"]
            new = canonical_subtype(edge_type, old)
            if new != old:
                changes.append((edge_type, old, new, n))

        if changes:
            print(f"{'엣지':18} {'현재':22} → {'대표형':18} 건수")
            print("─" * 70)
            for edge_type, old, new, n in sorted(changes, key=lambda x: -x[3]):
                print(f"{edge_type:18} {old or '(빈값)':22} → {new:18} {n:>5}")
            total = sum(c[3] for c in changes)
            if dry_run:
                print(f"\n[dry-run] {len(changes)}개 조합 / 엣지 {total}건이 바뀝니다.")
            else:
                applied = Counter()
                for edge_type, old, new, _ in changes:
                    result = session.run(_UPDATE, edge_type=edge_type,
                                         old=old, new=new).single()
                    applied[edge_type] += result["n"]
                print(f"\n✅ 엣지 {sum(applied.values())}건 정규화")
                for edge_type, n in applied.most_common():
                    print(f"   {edge_type:18} {n:>5}건")
        else:
            print("정규화할 엣지가 없습니다 (이미 전부 대표형).")

        # ── 정규화로 같아진 엣지 병합 ──────────────────────────
        # ★2단계에서 조기 return 하면 안 된다. 3단계 클러스터링은 2단계의
        #   **상위 집합**이다(subtype이 달라도 접는다). 완전중복이 0건이어도
        #   subtype만 다른 중복은 남아 있을 수 있다.
        #   실제로 「삼성전자 -OWNS_STAKE_IN-> 레인보우로보틱스」가 [출자]·[최대주주]
        #   두 개로 남았는데, 2단계가 0건이라 3단계가 아예 실행되지 않았다.
        dups = [r["v"] for r in session.run(_COUNT_DUPS)]
        if not dups:
            print("\n중복 엣지 없음. (subtype이 다른 중복은 3단계에서 봅니다)")
        else:
            print(f"\n[중복 병합] 같은 (출발,도착,엣지,subtype) 조합 {len(dups)}건")
            for v in dups[:10]:
                print(f"   {v}")
            if len(dups) > 10:
                print(f"   … 외 {len(dups) - 10}건")

            if dry_run:
                print("\n[dry-run] 위 조합을 각각 1개로 병합합니다"
                      " (evidence_id·source_doc은 목록으로 보존).")
            else:
                merged = session.run(_DEDUP).single()["groups"]
                print(f"\n✅ {merged}개 조합 병합 완료 "
                      f"(근거는 evidence_ids·source_docs에 전부 보존)")

        # ── 3단계: 엣지 클러스터링 (subtype이 달라도 같은 관계면 하나로) ──
        clusters = [r["v"] for r in session.run(_COUNT_CLUSTERS)]
        if not clusters:
            print("\n클러스터링 대상 없음 (한 쌍당 엣지 1개).")
            return 0

        print(f"\n[엣지 클러스터링] 같은 두 노드·같은 엣지에 여러 개: {len(clusters)}쌍")
        for v in clusters[:10]:
            print(f"   {v[:100]}")
        if len(clusters) > 10:
            print(f"   … 외 {len(clusters) - 10}쌍")

        if dry_run:
            print("\n[dry-run] 각 쌍을 1개로 접습니다 "
                  "(대표는 **타입 이름이 아닌 것** 중 최빈값, 전체는 subtypes 배열에 보존).")
            return 0

        n = session.run(_CLUSTER,
                        defaults=_DEFAULT_SUBTYPE).single()["clusters"]
        print(f"\n✅ {n}쌍 클러스터링 완료 "
              f"(subtypes·evidence_ids·source_docs 전부 보존)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
