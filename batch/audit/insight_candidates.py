"""인사이트 후보를 **전부 뽑아 본다** — 무엇이 쓸 만한지 사람이 고르기 위해.

왜 필요한가 (2026-09-03)

지금 인사이트는 8종이고 그중 6종이 **구조**다(`insight_service.py`). 구조는 잘
안 바뀌어서 홈에 두면 어제와 오늘이 같고, 「4곳이 전부 엔비디아에 공급합니다」
까지만 말해 **「그래서 뭐」가 없다.**

깊게 만드는 방법이 LLM 이라고 생각하기 쉬운데 아니다. 지금 얕은 이유는
**한 홉만 보기 때문**이다. 두 홉·경로를 보면 훨씬 구체적인 것이 나오고,
그건 전부 Cypher 다:

    지금       4곳이 전부 엔비디아에 공급한다
    병목       4곳이 전부 **A사 한 곳을 거쳐** 엔비디아에 닿는다
    대체부재   엔비디아 말고 다른 고객이 있는 곳은 4곳 중 1곳뿐이다
    겉보기분산 업종은 3개인데 실제 거래 상대는 2곳으로 수렴한다

★**이 파일은 판정하지 않는다.** 후보를 계산해 예시와 함께 찍을 뿐이다.
  「이게 사용자에게 쓸모 있나」는 사람이 출력을 보고 정한다 — 그래서 점수도
  임계값도 두지 않는다(`batch/audit/` 의 규칙, `discovered_cohesion.py` 와 같은
  규약). 쓸 만한 것이 정해지면 그때 `insight_service` 로 옮긴다.

★**LLM 을 부르지 않는다. 0원이다.**

    python -m batch.audit.insight_candidates --list        패턴 목록만
    python -m batch.audit.insight_candidates               자동 워크스페이스 3개
    python -m batch.audit.insight_candidates --auto 5      5개
    python -m batch.audit.insight_candidates --keys 00126380,00164779
    python -m batch.audit.insight_candidates --only B1,B2  일부만
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

from app.core.database import neo4j_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 관계 12종 중 **거래 흐름**만. 지분·임원·규제는 성격이 달라 따로 본다.
_TRADE = "SUPPLIES_TO|DEPENDS_ON|PARTNERS_WITH"


@dataclass(frozen=True)
class Pattern:
    """후보 하나. `axis` 는 §축, `cypher` 는 `$keys` 를 받는다."""

    code: str
    axis: str
    title: str
    why: str          # 이게 왜 사용자에게 쓸모 있을 수 있나 — 사람이 판단할 근거
    cypher: str
    fmt: Callable[[dict], str]


def _f(*fields: str) -> Callable[[dict], str]:
    """행에서 필드 몇 개를 골라 한 줄로."""
    def fn(r: dict) -> str:
        return " · ".join(f"{k}={r.get(k)!r}" for k in fields)
    return fn


PATTERNS: tuple[Pattern, ...] = (

    # ══ A. 구조 — 1홉 (지금 있는 것. 대조용으로 함께 뽑는다) ══════════
    Pattern(
        "A1", "구조1홉", "같은 곳에 판다 (shared_customer)",
        "지금 인사이트의 주력. 대조 기준선으로 함께 본다.",
        """
        MATCH (c:Company)-[:SUPPLIES_TO]->(t:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND NOT coalesce(t.corp_code, t.norm_name) IN $keys   // ★자기 자신 제외
        WITH t, collect(DISTINCT c.name) AS mine
        WHERE size(mine) >= 2
        MATCH (:Company)-[:SUPPLIES_TO]->(t)
        WITH t, mine, count(*) AS base
        RETURN t.name AS 대상, size(mine) AS 걸린곳, base AS 전국, mine AS 기업
        ORDER BY size(mine) DESC, base ASC LIMIT 5
        """,
        _f("대상", "걸린곳", "전국", "기업")),

    # ══ B. 구조 — 2홉·경로 (새로 보는 것) ═══════════════════════════
    Pattern(
        "B1", "구조2홉", "병목 — 여럿이 한 중간 노드를 거친다",
        "「4곳이 전부 A사를 거쳐 엔비디아에 닿는다」. A사가 흔들리면 동시에 끊긴다. "
        "1홉만 보면 안 보이는 단일 장애점.",
        f"""
        MATCH (c:Company)-[:{_TRADE}]->(mid:Company)-[:{_TRADE}]->(dst:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND NOT coalesce(mid.corp_code, mid.norm_name) IN $keys
        WITH mid, dst, collect(DISTINCT c.name) AS mine
        WHERE size(mine) >= 2
        RETURN mid.name AS 병목, dst.name AS 목적지,
               size(mine) AS 걸린곳, mine AS 기업
        ORDER BY size(mine) DESC LIMIT 5
        """,
        _f("병목", "목적지", "걸린곳", "기업")),

    Pattern(
        "B2", "구조2홉", "대체 부재 — 거래 상대가 하나뿐",
        "의존 자체보다 **대체 불가**가 리스크다. 「엔비디아 말고 다른 고객이 있는 "
        "곳은 4곳 중 1곳뿐」이 훨씬 무겁다.",
        f"""
        MATCH (c:Company) WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        OPTIONAL MATCH (c)-[:{_TRADE}]->(t:Company)
        WITH c, count(DISTINCT t) AS 상대수, collect(DISTINCT t.name)[0..3] AS 상대
        WHERE 상대수 <= 1
        RETURN c.name AS 기업, 상대수, 상대
        ORDER BY 상대수 ASC LIMIT 10
        """,
        _f("기업", "상대수", "상대")),

    Pattern(
        "B3", "구조2홉", "내부 연결 — 담은 기업끼리 거래한다",
        "한 사건이 워크스페이스 **안에서** 연쇄된다는 뜻. 분산했다고 믿는 사용자에게 "
        "가장 놀라운 정보일 수 있다.",
        f"""
        MATCH (a:Company)-[r:{_TRADE}]->(b:Company)
        WHERE coalesce(a.corp_code, a.norm_name) IN $keys
          AND coalesce(b.corp_code, b.norm_name) IN $keys
        RETURN a.name AS 출발, type(r) AS 관계, b.name AS 도착, r.subtype AS 유형
        LIMIT 10
        """,
        _f("출발", "관계", "도착", "유형")),

    Pattern(
        "B4", "구조2홉", "겉보기 분산 — 업종은 흩어졌는데 거래처가 겹친다",
        "「업종은 3개인데 거래처의 절반을 2곳 이상이 공유」. 분산한 줄 알았는데 안 된 "
        "경우를 잡는다. 사용자가 스스로 알아내기 가장 어려운 종류.",
        f"""
        MATCH (c:Company) WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH collect(DISTINCT c.ksic) AS ks
        WITH size([x IN ks WHERE x IS NOT NULL]) AS 업종수
        MATCH (c:Company)-[:{_TRADE}]->(t:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND NOT coalesce(t.corp_code, t.norm_name) IN $keys
        WITH 업종수, t, count(DISTINCT c) AS 몇곳이
        WITH 업종수, count(t) AS 상대수,
             sum(CASE WHEN 몇곳이 >= 2 THEN 1 ELSE 0 END) AS 공유상대수,
             collect(CASE WHEN 몇곳이 >= 2 THEN t.name END)[0..5] AS 공유예시
        RETURN 업종수, 상대수, 공유상대수,
               CASE WHEN 상대수 > 0
                    THEN round(100.0 * 공유상대수 / 상대수) ELSE 0 END AS 공유율pct,
               [x IN 공유예시 WHERE x IS NOT NULL] AS 공유예시
        """,
        _f("업종수", "상대수", "공유상대수", "공유율pct", "공유예시")),

    Pattern(
        "B5", "구조2홉", "간접 노출 — 직접 연결은 없는데 한 다리 건너 같은 곳",
        "1홉 카드가 못 잡는 노출. 「직접 거래는 없지만 둘 다 A사를 통해 엮인다」.",
        f"""
        MATCH (c:Company)-[:{_TRADE}]-(mid)-[:{_TRADE}]-(other:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND coalesce(other.corp_code, other.norm_name) IN $keys
          AND NOT coalesce(mid.corp_code, mid.norm_name) IN $keys   // ★다리가 워크스페이스 안이면 제외
          AND c <> other
          AND NOT (c)-[:SUPPLIES_TO|DEPENDS_ON|PARTNERS_WITH]-(other)
        RETURN mid.name AS 공통, collect(DISTINCT c.name)[0..4] AS 기업,
               count(DISTINCT c) AS 걸린곳
        ORDER BY 걸린곳 DESC LIMIT 5
        """,
        _f("공통", "걸린곳", "기업")),

    # ══ C. 사건 ═════════════════════════════════════════════════════
    Pattern(
        "C1", "사건", "공유 위험 사건 (지금의 shared_risk)",
        "대조 기준선.",
        """
        MATCH (c:Company)-[r:HAS_EVENT]->(e:Event)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys AND e.is_risk
        WITH e, collect(DISTINCT c.name) AS mine, max(r.occurred_at) AS 최근
        WHERE size(mine) >= 2
        RETURN e.name AS 사건, e.event_type AS 유형, 최근, size(mine) AS 걸린곳, mine AS 기업
        ORDER BY 최근 DESC LIMIT 5
        """,
        _f("사건", "유형", "최근", "걸린곳", "기업")),

    Pattern(
        "C2", "사건", "전파 — 담지 않은 곳의 사건이 담은 곳까지",
        "「A사 감산 → 담은 B사까지」. 워크스페이스 밖에서 오는 충격이라 사용자가 "
        "직접 못 본다.",
        """
        MATCH (src:Company)-[:HAS_EVENT {role:'subject'}]->(e:Event)-[i:IMPACTS]->(dst:Company)
        WHERE coalesce(dst.corp_code, dst.norm_name) IN $keys
          AND NOT coalesce(src.corp_code, src.norm_name) IN $keys
          AND e.is_risk
        RETURN src.name AS 발원, e.name AS 사건, e.event_type AS 유형,
               i.occurred_at AS 시점, collect(DISTINCT dst.name) AS 영향받은곳
        ORDER BY 시점 DESC LIMIT 5
        """,
        _f("발원", "사건", "유형", "시점", "영향받은곳")),

    Pattern(
        "C3", "사건", "최근 유형 쏠림 — 최근 12개월 어떤 유형이 몰렸나",
        "「이번 반년에 노무 사건만 5건」. 개별 사건보다 패턴이 먼저 눈에 들어온다. "
        "★반복 융합을 정리한 뒤라야 신뢰할 수 있는 축.",
        """
        MATCH (c:Company)-[r:HAS_EVENT]->(e:Event)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND r.occurred_at >= $since
        RETURN e.event_type AS 유형, count(DISTINCT e) AS 건수,
               sum(CASE WHEN e.is_risk THEN 1 ELSE 0 END) AS 위험,
               collect(DISTINCT e.name)[0..3] AS 예시
        ORDER BY 건수 DESC LIMIT 6
        """,
        _f("유형", "건수", "위험", "예시")),

    Pattern(
        "C4", "사건", "되풀이 — 같은 유형이 여러 해에 걸쳐 반복되는 기업",
        "「이 회사는 노무 사건이 3년 연속」. 일회성과 체질을 가른다.",
        """
        MATCH (c:Company)-[r:HAS_EVENT]->(e:Event)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND e.is_risk AND r.occurred_at IS NOT NULL
        WITH c, e.event_type AS 유형,
             collect(DISTINCT substring(r.occurred_at, 0, 4)) AS 연도들
        WHERE size(연도들) >= 2
        RETURN c.name AS 기업, 유형, size(연도들) AS 연수, 연도들
        ORDER BY 연수 DESC LIMIT 8
        """,
        _f("기업", "유형", "연수", "연도들")),

    # ══ D. 변화 ═════════════════════════════════════════════════════
    Pattern(
        "D1", "변화", "새로 생긴 관계 — first_seen 기준",
        "「이번 달 새 공급 관계 2건」. 홈 화면이 매일 달라지려면 이 축이 필요하다.",
        f"""
        MATCH (a:Company)-[r:{_TRADE}]->(b:Company)
        WHERE (coalesce(a.corp_code, a.norm_name) IN $keys
               OR coalesce(b.corp_code, b.norm_name) IN $keys)
          AND r.first_seen >= date($since)   // ★Date 형이다. 문자열 비교는 조용히 0건
        RETURN a.name AS 출발, type(r) AS 관계, b.name AS 도착,
               r.first_seen AS 처음본때, r.subtype AS 유형
        ORDER BY r.first_seen DESC LIMIT 8
        """,
        _f("출발", "관계", "도착", "처음본때")),

    Pattern(
        "D2", "변화", "오래 갱신 안 된 관계 — loaded_at 기준",
        "「관계가 끊겼나」의 후보. `freshness` 가 판정하는 것과 같은 신호를 미리 본다.",
        f"""
        MATCH (a:Company)-[r:{_TRADE}]->(b:Company)
        WHERE coalesce(a.corp_code, a.norm_name) IN $keys
          AND r.loaded_at IS NOT NULL AND r.loaded_at < datetime($since + 'T00:00:00Z')
        RETURN a.name AS 출발, b.name AS 도착, r.loaded_at AS 마지막적재,
               r.last_seen AS 마지막관측
        ORDER BY r.loaded_at ASC LIMIT 8
        """,
        _f("출발", "도착", "마지막적재", "마지막관측")),

    # ══ E. 예정·만료 ════════════════════════════════════════════════
    Pattern(
        "E1", "예정", "만료 임박 — valid_until 이 가까운 관계",
        "행동 가능성이 가장 높은 정보. 「3개월 뒤 계약 만료」는 지금 뭘 해야 할지가 "
        "분명하다. 지금 인사이트에 **미래 축이 통째로 없다.**",
        f"""
        MATCH (a:Company)-[r:{_TRADE}]->(b:Company)
        WHERE coalesce(a.corp_code, a.norm_name) IN $keys
          AND r.valid_until IS NOT NULL AND r.valid_until >= $today
        RETURN a.name AS 출발, b.name AS 도착, r.valid_until AS 만료,
               r.subtype AS 유형
        ORDER BY r.valid_until ASC LIMIT 8
        """,
        _f("출발", "도착", "만료", "유형")),

    Pattern(
        "E2", "예정", "진행 중인 사건 — 국면이 남아 있는 것",
        "`timeline` 이 있는 사건은 여러 국면으로 전개된 것이다. 마지막 국면이 최근이면 "
        "아직 끝나지 않았을 수 있다.",
        """
        MATCH (c:Company)-[r:HAS_EVENT]->(e:Event)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND e.timeline IS NOT NULL AND size(e.timeline) > 0
        RETURN e.name AS 사건, e.event_type AS 유형, size(e.timeline) AS 국면수,
               e.timeline[0..3] AS 국면, max(r.occurred_at) AS 최근
        ORDER BY 최근 DESC LIMIT 6
        """,
        _f("사건", "유형", "국면수", "최근", "국면")),

    # ══ F. 공백 — 「내가 모르는 것」 ═════════════════════════════════
    Pattern(
        "F1", "공백", "소식 없는 기업 — 최근 사건 0",
        "「담은 4곳 중 2곳은 최근 소식이 없습니다」. 없다는 사실 자체가 정보다 — "
        "사용자가 「조용해서 괜찮다」와 「우리가 못 본다」를 구별해야 한다.",
        """
        MATCH (c:Company) WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        OPTIONAL MATCH (c)-[r:HAS_EVENT]->(:Event) WHERE r.occurred_at >= $since
        WITH c, count(r) AS 최근사건
        WHERE 최근사건 = 0
        RETURN c.name AS 기업, c.is_stub AS stub여부, 최근사건
        LIMIT 10
        """,
        _f("기업", "stub여부")),

    Pattern(
        "F2", "공백", "근거가 얇은 사건 — 기사 1건짜리",
        "카드로 올렸는데 근거가 한 건뿐이면 사용자가 확인할 것이 없다. 신뢰도 표시 "
        "재료가 될 수 있다.",
        """
        MATCH (c:Company)-[:HAS_EVENT]->(e:Event)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
          AND coalesce(e.article_count, 0) <= 1
        RETURN e.name AS 사건, e.event_type AS 유형, e.is_risk AS 위험,
               e.article_count AS 기사수
        LIMIT 8
        """,
        _f("사건", "유형", "위험", "기사수")),

    # ══ G. 인물·기관 — 지금 인사이트가 전혀 안 쓰는 축 ═══════════════
    Pattern(
        "G1", "인물기관", "공통 임원 — 담은 곳들이 같은 사람을 임원으로",
        "지배구조·이해관계의 신호. 지금 8종 카드에 인물 축이 아예 없다.",
        """
        MATCH (p:Person)-[:IS_EXECUTIVE_OF]->(c:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH p, collect(DISTINCT c.name) AS 기업
        WHERE size(기업) >= 2
        RETURN p.name AS 인물, size(기업) AS 걸린곳, 기업 LIMIT 5
        """,
        _f("인물", "걸린곳", "기업")),

    Pattern(
        "G2", "인물기관", "공통 규제기관 — 같은 기관이 규제한다",
        "「담은 3곳이 전부 공정위 대상」. 규제 리스크가 동시에 온다는 뜻.",
        """
        MATCH (o)-[:REGULATES]->(c:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH o, collect(DISTINCT c.name) AS 기업
        WHERE size(기업) >= 2
        RETURN o.name AS 기관, size(기업) AS 걸린곳, 기업 LIMIT 5
        """,
        _f("기관", "걸린곳", "기업")),

    Pattern(
        "G3", "인물기관", "공통 주주",
        "지금 shared_owner 로 있는 것. 대조용.",
        """
        MATCH (o)-[:OWNS_STAKE_IN]->(c:Company)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH o, collect(DISTINCT c.name) AS 기업
        WHERE size(기업) >= 2
        RETURN o.name AS 주주, size(기업) AS 걸린곳, 기업 LIMIT 5
        """,
        _f("주주", "걸린곳", "기업")),

    # ══ H. 제품 ═════════════════════════════════════════════════════
    Pattern(
        "H1", "제품", "같은 제품을 만든다 — 실질 경쟁",
        "`COMPETES_WITH` 가 없어도 같은 제품을 개발하면 경쟁이다. 선언된 경쟁보다 "
        "실질에 가깝다.",
        """
        MATCH (c:Company)-[:DEVELOPS]->(p)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH p, collect(DISTINCT c.name) AS 기업
        WHERE size(기업) >= 2
        RETURN p.name AS 제품, size(기업) AS 걸린곳, 기업 LIMIT 5
        """,
        _f("제품", "걸린곳", "기업")),

    Pattern(
        "H2", "제품", "같은 것에 의존한다",
        "공급망 단일점의 제품 버전.",
        """
        MATCH (c:Company)-[:DEPENDS_ON]->(x)
        WHERE coalesce(c.corp_code, c.norm_name) IN $keys
        WITH x, labels(x)[0] AS 종류, collect(DISTINCT c.name) AS 기업
        WHERE size(기업) >= 2
        RETURN x.name AS 대상, 종류, size(기업) AS 걸린곳, 기업 LIMIT 5
        """,
        _f("대상", "종류", "걸린곳", "기업")),
)


_AUTO = """
MATCH (seed:Company)-[:SUPPLIES_TO|DEPENDS_ON|PARTNERS_WITH]-(n:Company)
WITH seed, collect(DISTINCT coalesce(n.corp_code, n.norm_name)) AS nb,
     collect(DISTINCT n.name) AS nbn
WHERE size(nb) >= $size
RETURN seed.name AS seed,
       [coalesce(seed.corp_code, seed.norm_name)] + nb[0..$size] AS keys,
       [seed.name] + nbn[0..$size] AS names
ORDER BY size(nb) DESC SKIP $skip LIMIT $n
"""


def _auto_workspaces(session, n: int, size: int = 3) -> list[tuple[str, list[str], list[str]]]:
    """실제 워크스페이스가 없으므로 **거래 이웃으로 그럴듯한 것**을 만든다.

    ★무작위가 아니라 **연결이 많은 기업의 이웃**으로 만든다. 사용자가 실제로
      담을 법한 모양(같은 공급망)이어야 후보의 쓸모를 판단할 수 있다.
    """
    rows = session.run(_AUTO, n=n, size=size, skip=0)
    return [(r["seed"], r["keys"], r["names"]) for r in rows]


def _run(session, p: Pattern, keys: list[str], since: str, today: str) -> tuple[list[dict], float, str]:
    t0 = time.perf_counter()
    try:
        rows = [dict(r) for r in session.run(p.cypher, keys=keys, since=since, today=today)]
        return rows, time.perf_counter() - t0, ""
    except Exception as exc:                      # 하나가 죽어도 나머지는 본다
        return [], time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="패턴 목록만 보고 끝낸다")
    ap.add_argument("--auto", type=int, default=3, help="자동 워크스페이스 개수")
    ap.add_argument("--size", type=int, default=3, help="워크스페이스 크기(시드 제외)")
    ap.add_argument("--keys", help="쉼표로 구분한 corp_code/norm_name")
    ap.add_argument("--only", help="패턴 코드 쉼표 구분 (예: B1,B2,C3)")
    ap.add_argument("--since", default="2025-09-01", help="「최근」의 기준일")
    ap.add_argument("--today", default="2026-09-03")
    ap.add_argument("--rows", type=int, default=3, help="패턴당 보여 줄 예시 수")
    args = ap.parse_args()

    picked = PATTERNS
    if args.only:
        want = {c.strip().upper() for c in args.only.split(",")}
        picked = tuple(p for p in PATTERNS if p.code in want)

    if args.list:
        print("=" * 78)
        print(f"  인사이트 후보 {len(PATTERNS)}종")
        print("=" * 78)
        axis = None
        for p in PATTERNS:
            if p.axis != axis:
                axis, = (p.axis,)
                print(f"\n[{axis}]")
            print(f"  {p.code:<4} {p.title}")
            print(f"       {p.why}")
        return 0

    with neo4j_session() as s:
        if args.keys:
            spaces = [("직접 지정", [k.strip() for k in args.keys.split(",") if k.strip()], [])]
        else:
            spaces = _auto_workspaces(s, args.auto, args.size)
            if not spaces:
                print("자동 워크스페이스를 못 만들었습니다 — --keys 로 직접 주세요.")
                return 1

        for seed, keys, names in spaces:
            print("\n" + "=" * 78)
            print(f"  워크스페이스: {seed}  ({len(keys)}곳)")
            if names:
                print(f"  {' · '.join(names[:6])}")
            print("=" * 78)

            for p in picked:
                rows, took, err = _run(s, p, keys, args.since, args.today)
                head = f"  [{p.code}] {p.title}"
                if err:
                    print(f"{head}\n       ✗ {err[:110]}")
                    continue
                if not rows:
                    print(f"{head}  — 없음  ({took*1000:.0f}ms)")
                    continue
                print(f"{head}  — {len(rows)}건  ({took*1000:.0f}ms)")
                for r in rows[:args.rows]:
                    print(f"       {p.fmt(r)[:150]}")

    print("\n" + "-" * 78)
    print("  ★판정하지 않았습니다. 어느 패턴이 쓸모 있는지는 출력을 보고 정합니다.")
    print("  쓸 만한 것이 정해지면 insight_service 로 옮깁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
