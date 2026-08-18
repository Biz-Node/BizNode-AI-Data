"""동명 Person을 **근거를 보고** 합친다 — 사람이 확인한 것만.

★왜 자동으로 못 합치나

한국은 동명이인이 흔하다. `person_er`는 DART 생년월이 있을 때만 합치고,
뉴스에서 온 사람(`@news`)은 생년월이 없어 손대지 않는다. 그래서 같은 사람이
둘로 갈린다:

    구본준@news       LX그룹 회장       (뉴스)
    구본준|1951-12    LX세미콘 회장     (DART)

이름만으로는 못 가른다. **근거 문장을 읽어야** 알 수 있다.

★2026-08-03 실측 — 8쌍을 열어 보니 셋으로 갈렸다

  ① 생년월이 둘 다 있고 다르다 → **다른 사람.** DART가 이미 구분해 준 것
       김근태 1963-05(주성엔지니어링) vs 1965-01(티씨케이)
       김병수 1969-08(로보티즈)      vs 1975-09(덕산네오룩스)
       이승훈 1962-12(케이티)        vs 1965-05(제주반도체)

  ② 근거가 서로를 가리킨다 → **같은 사람**
       박세훈  뉴스 「박세훈 SK온 팩토리이노베이션 담당」이 유일로보틱스 이사로 선임
              DART 주요경력 「(前) SK온 설비제작 PL」        ← 두 근거가 맞물린다
       전영선  「SI의 대표이사는 전영선 심텍 대표로, 오너 전세호 회장의 아들이다」
       구본준  LX그룹 회장 = LX세미콘 회장 (지주·자회사 겸직)
       고광일  「창업주인 고광일 대표」가 고영홀딩스 93.77% 보유

  ③ 회사도 역할도 무관 → **다른 사람**
       이준호  에스피지 최대주주 19.84%·대표이사 vs 덕산네오룩스 5%주주인데 지분 0.0%

  ①을 검사가 계속 띄우면 안 된다 — 「봐도 할 게 없는 경고」는 사람을 무디게 한다.
  그건 `audit/graph`에서 뺐고, 여기는 ②만 처리한다.

★남기는 쪽은 **생년월이 있는 노드**다. DART에서 온 것이라 식별자가 안정적이고,
  `@news` 키는 같은 이름이 또 나오면 충돌한다.

    python -m batch.repair.person_merge --dry-run
    python -m batch.repair.person_merge
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 사람이 근거를 읽고 확인한 쌍만 적는다. **추측으로 늘리지 말 것** —
# 여기 한 줄 잘못 넣으면 서로 다른 두 사람의 이력이 한 노드에 섞인다.
CONFIRMED: list[tuple[str, str, str]] = [
    # (남길 키, 합칠 키, 왜 같은 사람인가)
    ("구본준|1951-12", "구본준@news",
     "LX그룹 회장 = LX세미콘 회장 — 지주·자회사 겸직"),
    ("고광일|1957-08", "고광일@news",
     "「창업주인 고광일 대표」가 고영홀딩스 93.77% 보유 · 고영테크놀러지는 옛 사명"),
    ("전영선|1982-06", "전영선@news",
     "「SI의 대표이사는 전영선 심텍 대표로, 오너 전세호 회장의 아들이다」"),
    ("박세훈|1978-11", "박세훈@news",
     "뉴스 「박세훈 SK온 팩토리이노베이션 담당」 ↔ DART 주요경력 「(前) SK온 설비제작 PL」"),
]

_LOOK = """
MATCH (p:Person {person_key: $key})
OPTIONAL MATCH (p)-[x]-()
RETURN elementId(p) AS id, p.name AS name, count(x) AS deg
"""

# ★`mergeNodes`는 **라벨도 속성도 합친다.** 'discard'로 남길 쪽 속성을 지키고,
#   합친 뒤 배열이 된 스칼라는 `node_identity.unlist_scalars`가 되돌린다.
_MERGE = """
MATCH (keep:Person) WHERE elementId(keep) = $keep_id
MATCH (drop:Person) WHERE elementId(drop) = $drop_id
CALL apoc.refactor.mergeNodes([keep, drop],
     {properties: 'discard', mergeRels: true}) YIELD node
SET node.merged_keys = coalesce(node.merged_keys, []) + $drop_key
RETURN elementId(node) AS id
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"근거를 읽고 확인한 동명 Person {len(CONFIRMED)}쌍\n")
    merged = missing = 0
    with neo4j_session() as session:
        for keep_key, drop_key, why in CONFIRMED:
            k = session.run(_LOOK, key=keep_key).single()
            d = session.run(_LOOK, key=drop_key).single()
            if not k or not d:
                gone = keep_key if not k else drop_key
                print(f"  · {gone} 없음 — 이미 합쳐졌거나 사라졌습니다")
                missing += 1
                continue
            print(f"  ✎ {d['name']}  「{drop_key}」(연결 {d['deg']}) → "
                  f"「{keep_key}」(연결 {k['deg']})")
            print(f"      {why}")
            if not args.dry_run:
                session.run(_MERGE, keep_id=k["id"], drop_id=d["id"],
                            drop_key=drop_key)
            merged += 1

        if merged and not args.dry_run:
            # 병합이 스칼라를 배열로 바꿔 놓은 것을 되돌린다(세 번 데인 그 문제)
            from batch.repair.node_identity import unlist_scalars
            n = unlist_scalars(session)
            if n:
                print(f"\n   ↺ 병합으로 배열이 된 스칼라 속성 {n}건 되돌림")

    print(f"\n{'[dry-run] ' if args.dry_run else '✅ '}"
          f"병합 {merged}쌍 · 대상 없음 {missing}쌍")
    if not args.dry_run and merged:
        print("   합친 키는 `merged_keys`에 남겼습니다 — 되짚을 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
