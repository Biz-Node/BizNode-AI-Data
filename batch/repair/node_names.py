"""가드 도입 이전에 만들어진 불량 노드 복구 (1회성 유지보수).

가드(`generic_names.py`·`resolver.py`)는 **앞으로 들어올** 데이터를 막을 뿐,
이미 그래프에 있는 노드는 그대로다. 여기서 되돌린다.

하는 일:
  1. 이름이 `Event`·NULL인 Event 노드 → 기사 제목(news_articles) 또는 title로 복구
  2. 그룹명 stub(`SK그룹`) → 해소되는 법인 노드로 병합
  3. 설명형 stub(`글로벌 대형기업`) → 삭제

**삭제보다 복구를 우선**한다 — 불량 노드도 엣지를 달고 있어서, 지우면 관계까지
사라진다. 이름만 고칠 수 있으면 고친다.

실행:
  python -m batch.repair.node_names --dry-run
  python -m batch.repair.node_names
"""

from __future__ import annotations

import sys

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.generic_names import (
    CERTAIN_REASONS,
    generic_reason,
    is_market_noise_event,
)
from pipeline.normalizer.resolver import close as close_resolver
from pipeline.normalizer.resolver import resolve

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_BAD_EVENTS = """
MATCH (e:Event)
WHERE e.name IS NULL OR toLower(e.name) IN ['event', '사건', '이벤트']
RETURN e.event_id AS id, e.name AS name, e.title AS title, e.source_doc AS doc
"""
_FIX_EVENT = "MATCH (e:Event {event_id:$id}) SET e.name=$name, e.title=$title"

# 같은 이름의 Event가 여럿 = 기사마다 노드가 갈린 것(옛 id가 기사 URL을 포함했다)
_DUP_EVENTS = """
MATCH (e:Event) WHERE e.name IS NOT NULL
WITH e.name AS name, collect(e) AS nodes
WHERE size(nodes) > 1
RETURN name, [n IN nodes | n.event_id] AS ids
"""
_MERGE_EVENTS = """
MATCH (a:Event {event_id:$keep})
MATCH (b:Event {event_id:$drop})
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.event_id AS id
"""
# 시황·맨동사 Event — 사건이 아니라 시세 현상
_ALL_EVENTS = "MATCH (e:Event) RETURN e.event_id AS id, e.name AS name, " \
              "size([(e)-[]-() | 1]) AS deg"
_DELETE_EVENT = "MATCH (e:Event {event_id:$id}) DETACH DELETE e"

# 그룹명·설명형 후보 — stub만 대상(시드는 건드리지 않는다)
_STUB_NAMES = """
MATCH (c:Company) WHERE coalesce(c.is_stub, false)
RETURN c.norm_name AS norm, c.name AS name, size([(c)-[]-() | 1]) AS deg
"""

# ★설명형 이름은 `Company` stub 에만 생기는 게 아니다(2026-08-12).
#   실측으로 `Organization`·`Product` 에서도 나왔다:
#       [Orga] 미국 소비자 14명과 중소 PC조립·유통업체 3곳     deg=3
#       [Orga] 소비자와 오프라인 소매업체를 대표하는 원고들       deg=3
#       [Prod] 전공정 반도체 소자회로 제작에서 발생하는 패턴 결함을 검사하는 장비
#       [Prod] 락(위상) 고정 루프(PLL) 회로 및 이를 포함하는 디스플레이 구동기
#   앞의 둘은 소송 원고를 한 덩이로 부른 것이고, 뒤의 둘은 **특허 명칭**이
#   제품명이 됐다. `is_stub` 조건이 없어 4단계 stub 목록에 안 잡혔다.
#
#   ★`corp_code`가 있으면 건드리지 않는다 — DART에 실재하는 법인이다.
_DESC_NODES = """
MATCH (n) WHERE (n:Organization OR n:Product)
  AND coalesce(n.corp_code, '') = ''
RETURN elementId(n) AS id, labels(n)[0] AS lb, n.name AS name,
       size([(n)-[]-() | 1]) AS deg
"""
_DELETE_BY_ID = "MATCH (n) WHERE elementId(n) = $id DETACH DELETE n"
_MERGE_INTO_RESOLVED = """
MATCH (stub:Company {norm_name:$stub_norm})
MATCH (target:Company {corp_code:$corp_code})
CALL apoc.refactor.mergeNodes([target, stub], {properties:'discard', mergeRels:true})
YIELD node RETURN node.name AS name
"""
_DELETE_STUB = "MATCH (c:Company {norm_name:$norm}) DETACH DELETE c"

_GROUP_SUFFIXES = ("그룹", "계열")


def _article_titles(urls: list[str]) -> dict[str, str]:
    if not urls:
        return {}
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT url, title FROM news_articles WHERE url = ANY(%s)", (urls,))
        return {u: t for u, t in cur.fetchall()}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    fixed_events = merged_stubs = deleted_stubs = 0
    deleted_events = merged_events = 0

    with neo4j_session() as session:
        # ── ① Event 이름 복구 ───────────────────────────────
        bad = [dict(r) for r in session.run(_BAD_EVENTS)]
        urls = [b["doc"] for b in bad if b.get("doc", "").startswith("http")]
        titles = _article_titles(urls)

        print(f"[1/5] 이름 불량 Event {len(bad)}건")
        for b in bad:
            # title이 멀쩡하면 그걸 쓰고, 그것마저 'Event'면 기사 제목으로
            title = b.get("title") or ""
            if not title or title.lower() == "event":
                title = titles.get(b.get("doc") or "", "")
            title = (title or "").strip()[:120]
            if not title:
                print(f"  · 복구 불가 {b['id']} (기사 제목 없음)")
                continue
            print(f"  ✓ {b['id']}: {b['name']!r} → {title!r}")
            if not dry_run:
                session.run(_FIX_EVENT, id=b["id"], name=title, title=title)
            fixed_events += 1

        # ── ①-b 시황성 Event 삭제 ───────────────────────────
        print(f"\n[2/5] 시황·맨동사 Event 삭제")
        for e in [dict(r) for r in session.run(_ALL_EVENTS)]:
            if not is_market_noise_event(e["name"]):
                continue
            print(f"  ✗ {e['name']} (deg={e['deg']})")
            if not dry_run:
                session.run(_DELETE_EVENT, id=e["id"])
            deleted_events += 1

        # ── ①-c 동명 Event 병합 ────────────────────────────
        # 옛 event_id가 기사 URL을 포함해, 같은 사건을 두 매체가 보도하면 노드가 갈렸다.
        # 사건이 여러 기업에 미치는 영향(IMPACTS)이 한 노드에 모여야 값이 생긴다.
        print(f"\n[3/5] 동명 Event 병합 (기사별로 갈린 같은 사건)")
        for row in [dict(r) for r in session.run(_DUP_EVENTS)]:
            ids = row["ids"]
            print(f"  ✓ {row['name']}: {len(ids)}개 → 1개")
            if not dry_run:
                for drop in ids[1:]:
                    session.run(_MERGE_EVENTS, keep=ids[0], drop=drop)
            merged_events += len(ids) - 1

        # ── ② 그룹명 stub 병합 / ③ 설명형 stub 삭제 ────────
        stubs = [dict(r) for r in session.run(_STUB_NAMES)]
        print(f"\n[4/5] 그룹명 stub 병합")
        for s in stubs:
            name = s["name"] or ""
            if not any(name.endswith(sfx) for sfx in _GROUP_SUFFIXES):
                continue
            res = resolve(name)
            if res is None:
                print(f"  · 보류 {name} (해소 실패 — 대표 법인 미확인)")
                continue
            print(f"  ✓ {name}(deg={s['deg']}) → {res.corp_name} ({res.corp_code})")
            if not dry_run:
                session.run(_MERGE_INTO_RESOLVED,
                            stub_norm=s["norm"], corp_code=res.corp_code)
            merged_stubs += 1

        print(f"\n[5/5] 설명형·익명 stub 삭제")
        for s in stubs:
            name = s["name"] or ""
            if any(name.endswith(sfx) for sfx in _GROUP_SUFFIXES):
                continue          # ②에서 처리
            reason = generic_reason(name)
            if reason is None:
                continue
            # 길이 초과만으로는 지우지 않는다 — 긴 컨소시엄 실명이 섞여 있다
            if reason not in CERTAIN_REASONS:
                print(f"  · 보존 {name[:50]}… ({reason} — 실명 가능성)")
                continue
            print(f"  ✗ {name} (deg={s['deg']}, {reason})")
            if not dry_run:
                session.run(_DELETE_STUB, norm=s["norm"])
            deleted_stubs += 1

        # ── Organization·Product 도 같은 기준으로 본다 ──────
        for n in [dict(r) for r in session.run(_DESC_NODES)]:
            reason = generic_reason(n["name"] or "")
            if reason is None or reason not in CERTAIN_REASONS:
                continue
            print(f"  ✗ [{n['lb'][:4]}] {n['name'][:48]} "
                  f"(deg={n['deg']}, {reason})")
            if not dry_run:
                session.run(_DELETE_BY_ID, id=n["id"])
            deleted_stubs += 1

    close_resolver()
    verb = "예정" if dry_run else "완료"
    print(f"\n{'[dry-run] ' if dry_run else '✅ '}"
          f"Event 복구 {fixed_events} · Event 삭제 {deleted_events} · "
          f"Event 병합 {merged_events} · 그룹 병합 {merged_stubs} · "
          f"stub 삭제 {deleted_stubs} {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
