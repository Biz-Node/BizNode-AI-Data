"""노드에 `last_seen` 을 올리고, 운영 흔적 속성을 지운다.

왜 `last_seen` 이 필요한가 (2026-08-15)

노드에는 `first_seen`(우리가 처음 본 날)만 있었다. 그런데 이 값은 **사업적
사실이 아니다** — 수집을 2026-07-26 에 시작해서 전 노드가 그 이후 값이다.
「이 회사가 언제 생겼나」가 아니라 「우리가 언제 긁었나」를 말할 뿐이다.

반면 엣지에는 `last_seen`(그 관계를 마지막으로 관측한 날)이 100% 있고, 그걸
노드로 올리면 **진짜 사실**이 나온다:

    3,453곳 계산 가능 · 범위 2014-12-14 ~ 2026-08-14
    가장 오래 조용한 대상 기업  티씨케이 2026-05-07 · SFA반도체 2026-05-18

리스크 관점에서 **「조용한 회사」는 신호**다. 최근 3개월 아무 관계도 갱신되지
않은 협력사는 거래가 끊겼을 수 있다.

★조회할 때 계산하지 않고 저장하는 이유: 노드 8천 개마다 연결 엣지를 전부
  훑어야 해서 화면 조회에 쓸 수 없다. 증분 배치가 채운다.

실행:
    python -m batch.repair.node_lastseen
"""

from __future__ import annotations

import sys

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 운영 흔적 — 화면에 안 쓰고 탐색에도 안 쓴다.
# `renamed_from` 85건 중 `timeline` 과 겹치는 건 3건뿐이라 중복은 아니지만,
# 나머지 82건도 「이름 정규화 이력」이라 성격이 같다.
DROP = {
    "Organization": ["relabeled_from"],
    "Event": ["renamed_from"],
}


def fill_last_seen() -> None:
    """연결된 엣지의 `last_seen` 최대값을 노드에 올린다."""
    with neo4j_session() as s:
        n = s.run("""MATCH (n)-[r]-() WHERE r.last_seen IS NOT NULL
            WITH n, max(r.last_seen) AS ls
            SET n.last_seen = ls RETURN count(*) AS n""").single()["n"]
        print(f"  last_seen 기록 {n}곳")
        for lb in ["Company", "Person", "Organization", "Product", "Event"]:
            r = s.run(f"""MATCH (n:{lb}) RETURN count(*) AS tot,
                sum(CASE WHEN n.last_seen IS NOT NULL THEN 1 ELSE 0 END) AS has""").single()
            miss = r["tot"] - r["has"]
            tail = f"  ← 엣지가 없는 노드 {miss}곳" if miss else ""
            print(f"    {lb:<14}{r['has']:>5}/{r['tot']}{tail}")


def drop_traces() -> None:
    with neo4j_session() as s:
        for lb, props in DROP.items():
            for p in props:
                n = s.run(f"MATCH (n:{lb}) WHERE n.`{p}` IS NOT NULL "
                          f"RETURN count(*) AS n").single()["n"]
                if n:
                    s.run(f"MATCH (n:{lb}) WHERE n.`{p}` IS NOT NULL REMOVE n.`{p}`")
                print(f"  {lb}.{p:<20}{n}곳 삭제")


def main() -> int:
    print("=" * 58)
    print("노드 last_seen 채우기")
    print("=" * 58)
    fill_last_seen()
    print()
    print("=" * 58)
    print("운영 흔적 삭제")
    print("=" * 58)
    drop_traces()
    print()
    with neo4j_session() as s:
        for lb in ["Company", "Person", "Organization", "Product", "Event"]:
            ks = sorted(r["k"] for r in
                        s.run(f"MATCH (n:{lb}) UNWIND keys(n) AS k RETURN DISTINCT k AS k"))
            print(f"{lb:<14}{len(ks):>2}가지  " + " · ".join(ks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
