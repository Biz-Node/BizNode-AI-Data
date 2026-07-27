"""P1 개체해소(ER) — 근거가 확실한 Person 분열만 병합 (ERD §2-6).

P1은 DART 단독이라 근거가 제한적이다. **확실한 것만** 병합하고 나머지는 P2로 남긴다.

병합 규칙(둘 다 만족해야 함):
  R1. 이름이 같다
  R2. 생년월이 서로 모순되지 않는다 (둘 다 있고 다르면 = 동명이인 → 절대 병합 금지)
그리고 아래 중 하나:
  R3-a. 같은 회사에 걸쳐 있다 (같은 회사 + 같은 이름 = 동일인)
  R3-b. 양쪽 다 '최대주주 본인' 신분이다 (동명이인이 둘 다 최대주주일 확률 희박)

병합하지 않는 것(P2 이관):
  - 삼성 총수 일가처럼 relate가 '특수관계인'인 크로스-회사 케이스
    → 기업집단 정보·뉴스 문맥이 필요 (§2-6)

병합은 apoc.refactor.mergeNodes로 **엣지를 보존**하며 수행한다.
"""

from __future__ import annotations

from typing import Any

from app.core.database import neo4j_session

# '최대주주 본인'을 나타내는 relate 표기 (공백·개행 제거 후 비교)
_SELF_OWNER_VALUES = {"최대주주본인", "본인", "최대주주"}

# 분열 후보 조회: 같은 이름, 서로 다른 person_key
_FIND_SPLITS = """
MATCH (p:Person)
WITH p.name AS name, collect(p) AS people
WHERE size(people) > 1
RETURN name,
       [x IN people | {
          key: x.person_key,
          birth: x.birth_year_month,
          companies: [(x)-[]->(c:Company) | c.corp_code],
          relates: [(x)-[r:OWNS_STAKE_IN]->(:Company) | r.shareholder_relation]
       }] AS members
"""

_MERGE_CYPHER = """
MATCH (a:Person {person_key: $keep}), (b:Person {person_key: $drop})
CALL apoc.refactor.mergeNodes([a, b], {properties: 'discard', mergeRels: true})
YIELD node
RETURN node.person_key AS key
"""


def _norm_relate(value: Any) -> str:
    return (value or "").replace(" ", "").replace("\n", "")


def _is_self_owner(relates: list) -> bool:
    return any(_norm_relate(r) in _SELF_OWNER_VALUES for r in relates or [])


def _can_merge(a: dict, b: dict) -> tuple[bool, str]:
    """(병합 가능 여부, 사유). 근거가 확실할 때만 True."""
    birth_a, birth_b = a.get("birth"), b.get("birth")
    # R2: 생년월이 둘 다 있고 다르면 동명이인 — 절대 병합 금지
    if birth_a and birth_b and birth_a != birth_b:
        return False, "동명이인(생년월 상이)"

    companies_a = set(a.get("companies") or [])
    companies_b = set(b.get("companies") or [])

    # R3-a: 같은 회사에 걸쳐 있음
    if companies_a & companies_b:
        return True, "같은 회사"

    # R3-b: 양쪽 다 최대주주 본인
    if _is_self_owner(a.get("relates")) and _is_self_owner(b.get("relates")):
        return True, "양쪽 최대주주 본인"

    return False, "근거 부족(P2 ER 대상)"


def _pick_keep(a: dict, b: dict) -> tuple[dict, dict]:
    """생년월 있는 쪽을 canonical로 유지(키가 안정적)."""
    if a.get("birth") and not b.get("birth"):
        return a, b
    if b.get("birth") and not a.get("birth"):
        return b, a
    return (a, b) if a["key"] <= b["key"] else (b, a)


def resolve_persons(dry_run: bool = False) -> dict[str, int]:
    """분열 Person을 규칙에 따라 병합. 통계 반환."""
    stats = {"merged": 0, "skipped": 0}
    with neo4j_session() as session:
        splits = [dict(r) for r in session.run(_FIND_SPLITS)]

        for row in splits:
            name = row["name"]
            members = row["members"]
            # 그리디 페어와이즈 병합
            pool = list(members)
            i = 0
            while i < len(pool):
                j = i + 1
                while j < len(pool):
                    ok, reason = _can_merge(pool[i], pool[j])
                    if ok:
                        keep, drop = _pick_keep(pool[i], pool[j])
                        print(f"  ✓ 병합 {name}: {drop['key']} → {keep['key']} ({reason})")
                        if not dry_run:
                            session.run(_MERGE_CYPHER, keep=keep["key"], drop=drop["key"])
                        # 병합된 쪽 정보를 keep에 흡수
                        keep["companies"] = list(
                            set(keep.get("companies") or []) | set(drop.get("companies") or [])
                        )
                        keep["relates"] = (keep.get("relates") or []) + (drop.get("relates") or [])
                        keep["birth"] = keep.get("birth") or drop.get("birth")
                        pool[i] = keep
                        pool.pop(j)
                        stats["merged"] += 1
                        continue
                    print(f"  · 보류 {name}: {pool[i]['key']} / {pool[j]['key']} ({reason})")
                    stats["skipped"] += 1
                    j += 1
                i += 1

    return stats


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = resolve_persons(dry_run="--dry-run" in sys.argv)
    print(f"\n병합 {result['merged']}건, 보류 {result['skipped']}건")
