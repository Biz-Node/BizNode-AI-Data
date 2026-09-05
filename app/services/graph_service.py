"""그래프 조회 — **신선도와 근거 검증 결과를 적용해서** 관계를 돌려준다.

서비스가 그래프를 읽는 **유일한 통로**다. 여기서 거르지 않으면 낡은 관계와
근거 없는 관계가 그대로 화면에 나간다(실측: 의심 표시된 431건이 그랬다).

이 모듈이 있어야 하는 이유

그래프의 엣지는 「지금 이렇다」가 아니라 **「그때 그렇게 보도됐다」**는 관측 사실이다.
2021년 기사로 만든 「A -SUPPLIES_TO-> B」가 2026년에도 유효한지는 **모른다**.
그런데 지금까지 질의는 모든 엣지를 동등하게 취급했다. 실측(2026-07-31):

    출처          current   stale
    dart            2,533      14     ← 정기 갱신되니 신선
    news              925   1,293     ← **58%가 갱신주기 초과**

`pipeline/freshness.py`에 판정 로직이 이미 있었지만 **어디에서도 호출되지 않았다.**
읽는 쪽과 쓰는 쪽이 둘 다 비어 있어, 만들어 두고 연결하지 않은 상태였다.

왜 오래된 엣지를 지우지 않는가

「2년간 언급이 없다」는 **관계가 끝났다는 증거가 아니라 정보가 없다는 뜻**이다.
뉴스는 관계의 시작은 보도해도 종료는 보도하지 않는다("A가 B 납품을 조용히 그만둠"은
기사가 되지 않는다). 그래서 뉴스만으로는 종료를 알 수 없다.

지우는 대신 **주장을 약화**한다:
    지금:  "삼성전자는 세메스로부터 공급받는다"        ← 근거 없는 현재형
    바꿔:  "2024-06에 그렇게 보도됨 (25개월 미갱신)"   ← 관측 사실

명시적으로 종료가 확인된 것만 `is_current=false`로 표시하고(→ expired, 가중 0.3),
나머지는 신선도 가중치를 곱해 순위를 낮춘다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

from app.core.database import neo4j_session
from pipeline.freshness import Freshness, assess

# 신선도별 표시 기호 — 사람이 답변에서 바로 알아보게
MARK = {"current": "●", "stale": "◐", "expired": "○", "unknown": "?"}

# 기본적으로 답변에서 제외할 상태. expired는 「끝난 관계」라 현재 질의엔 넣지 않는다.
DEFAULT_EXCLUDE = frozenset({"expired"})

# ── 근거 검증에서 걸린 관계 ──────────────────────────────────
#
# ★검사가 「삭제하지 않고 표시만」 하는 설계라, **조회 쪽에서 걸러 주지 않으면
#   표시한 의미가 없다.** 실측(2026-08-01): 재검증에서 821건이 걸렸는데
#   이 함수가 `grounding_suspect`를 안 봐서 화면에 그대로 나갔다.
#       SK하이닉스 -COMPETES_WITH-> 엔비디아   근거는 영업이익률 비교였다
#       국민성장펀드 -OWNS_STAKE_IN-> 심텍      근거는 「200억원 지원」이었다
#   둘 다 confidence 0.9·0.8이라 오히려 상위에 떴다.
#
# 판정마다 다루는 법이 다르다:
#   unfounded     근거를 봐도 관계가 없다        → 숨긴다
#   insufficient  근거가 잘려 판단 불가          → 숨긴다 (있다고 말할 수 없다)
#   wrong_type    관계는 있고 유형·방향만 틀렸다  → **숨기지 않는다.** 관계 자체는
#                 실재하므로 지우면 참인 연결을 잃는다. 대신 점수를 깎아 뒤로 민다.
HIDE_VERDICTS = frozenset({"unfounded", "insufficient"})

# 유형만 틀린 관계에 곱하는 값. 0으로 만들지 않는 이유는 위와 같다.
WRONG_TYPE_PENALTY = 0.5


@dataclass
class Relation:
    """신선도가 매겨진 관계 하나."""
    source: str
    target: str
    edge_type: str
    subtype: str
    source_type: str
    confidence: float
    corroboration: int
    freshness: Freshness
    props: dict[str, Any]
    # source/target 노드의 안정 식별자·Neo4j 라벨(Task6). source_type(위 필드,
    # evidence 출처 dart/news)과 이름이 겹치지 않도록 source_entity_type으로 뒀다.
    # 우선순위는 graph_loader._company_ident()/batch/repair/first_seen.py의
    # coalesce(corp_code, person_key, norm_name, event_id, name)과 동일.
    source_id: str
    source_entity_type: str
    target_id: str
    target_entity_type: str
    # 엣지 자체의 유일한 id(Neo4j elementId). `RetrieveResponse.relations` 의
    # `Relation.edge_id` 가 필수라 여기서 실어 보낸다. ★`evidence_id` 로 대신할 수
    # 없다 — 한 근거가 여러 엣지를 뒷받침해 유일하지 않다(엣지 11,060 : 근거 9,228).
    # 기본값을 둔 이유는 이 dataclass 를 직접 만드는 테스트를 깨지 않기 위해서다.
    edge_id: str = ""

    @property
    def verdict(self) -> str:
        """근거 검증 판정. 안 걸렸으면 'supported'."""
        if not self.props.get("grounding_suspect"):
            return "supported"
        return self.props.get("grounding_stage1") or "unfounded"

    @property
    def score(self) -> float:
        """순위 점수 — 확신도 × 신선도 × 뒷받침 보정 × 근거 판정.

        뒷받침(corroboration)은 완만하게만 반영한다. 실측상 여러 기사가 같은 말을
        하면 정확도가 확연히 높았지만(근거의심 484건 중 93%가 근거 1건짜리였다),
        10개 출처가 2개 출처의 5배로 확실한 것은 아니다.

        ★유형이 틀린 관계는 지우지 않고 **점수만 깎는다.** 관계 자체는 실재하므로
          연결은 살려 두되 상위에 뜨지 않게 한다.
        """
        corr = 1.0 + min(max(self.corroboration - 1, 0), 4) * 0.05
        pen = WRONG_TYPE_PENALTY if self.verdict == "wrong_type" else 1.0
        return self.confidence * self.freshness.confidence_factor * corr * pen

    def describe(self) -> str:
        """사람이 읽을 한 줄. **신선도도 검증 결과도 숨기지 않는다.**"""
        mark = MARK.get(self.freshness.status, "?")
        sub = f"/{self.subtype}" if self.subtype else ""
        corr = f" · 출처 {self.corroboration}건" if self.corroboration > 1 else ""
        vd = " · ⚠유형 확인 필요" if self.verdict == "wrong_type" else ""
        return (f"{mark} {self.source} -[{self.edge_type}{sub}]-> {self.target}"
                f"  ({self.freshness.reason}{corr}{vd})")


# ★워크스페이스로 **거르지 않는다**(2026-08-20 정책 변경). 한때 양끝이 모두
#   워크스페이스 안이어야 통과시켰는데, 그러면 「삼성전자 → SK하이닉스」처럼
#   워크스페이스 밖 상대와의 관계가 후보에서 통째로 사라지고, corp_code 를 갖지
#   않는 Event·Person·Organization·Product 끝은 **하나도 남지 않는다**(실측: 삼성전자
#   관계 상위 10건 중 5건이 비-Company 끝이었다).
#   워크스페이스는 필터가 아니라 **랭킹 문맥**이다 — 순서를 정하는 데 쓰고,
#   후보를 지우는 데 쓰지 않는다. 계산은 ResultRanker 가 한다.
_QUERY = """
MATCH (a)-[r]->(b)
WHERE ($name IS NULL OR a.norm_name = $name OR b.norm_name = $name)
  AND ($types IS NULL OR type(r) IN $types)
RETURN coalesce(a.name, '?') AS source, coalesce(b.name, '?') AS target,
       type(r) AS edge_type, properties(r) AS props, elementId(r) AS edge_id,
       coalesce(a.corp_code, a.person_key, a.norm_name, a.event_id, a.name) AS source_id,
       labels(a)[0] AS source_entity_type,
       coalesce(b.corp_code, b.person_key, b.norm_name, b.event_id, b.name) AS target_id,
       labels(b)[0] AS target_entity_type
"""


def _to_relation(row: dict, today: Optional[date]) -> Relation:
    p = row["props"] or {}
    return Relation(
        source=row["source"], target=row["target"], edge_type=row["edge_type"],
        subtype=p.get("subtype") or "", source_type=p.get("source_type") or "",
        confidence=float(p.get("confidence") or 0.7),
        corroboration=int(p.get("corroboration") or 1),
        freshness=assess(p, today=today), props=p,
        source_id=row["source_id"], source_entity_type=row["source_entity_type"],
        target_id=row["target_id"], target_entity_type=row["target_entity_type"],
        edge_id=row["edge_id"])


def relations_of(
    norm_name: Optional[str] = None,
    *,
    edge_types: Optional[Iterable[str]] = None,
    exclude_status: Iterable[str] = DEFAULT_EXCLUDE,
    hide_verdicts: Iterable[str] = HIDE_VERDICTS,
    min_score: float = 0.0,
    today: Optional[date] = None,
    limit: Optional[int] = None,
) -> list[Relation]:
    """기업의 관계를 **신선도 순으로** 돌려준다.

    · `exclude_status` 기본값은 expired 제외 — 끝난 관계는 현재 질의의 답이 아니다.
      과거 이력까지 보려면 `exclude_status=()`로 부른다.
    · stale은 **버리지 않는다.** 가중치만 낮춰 뒤로 민다. 오래됐다고 거짓은 아니다.
    · `hide_verdicts` 기본값은 근거 검증에서 「근거없음·근거부족」이 난 것 제외.
      검사가 지우지 않고 표시만 하므로 **여기서 걸러야 화면에 안 나간다.**
      검토 화면에서 의심분까지 보려면 `hide_verdicts=()`로 부른다.
    · ★`limit`은 **점수순으로 자르는 파이썬 슬라이스**다. Cypher에는 LIMIT이 없어
      조회량은 `limit`과 무관하다 — 넉넉히 가져와도 DB 작업량은 같다. 워크스페이스
      랭킹처럼 점수 외의 기준으로 다시 줄 세울 계획이면 여기서 미리 자르면 안 된다
      (자르는 순간 랭커가 볼 수 없는 후보가 생긴다).
    """
    types = list(edge_types) if edge_types else None
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_QUERY, name=norm_name, types=types)]

    out = [_to_relation(r, today) for r in rows]
    drop, hide = set(exclude_status), set(hide_verdicts)
    out = [r for r in out
           if r.freshness.status not in drop
           and r.verdict not in hide
           and r.score >= min_score]
    out.sort(key=lambda r: -r.score)
    return out[:limit] if limit else out


def summarize_freshness(rels: list[Relation]) -> str:
    """결과의 신선도 구성을 한 줄로. 답변에 붙여 **불확실성을 드러낸다**."""
    if not rels:
        return "관계 없음"
    tally: dict[str, int] = {}
    for r in rels:
        tally[r.freshness.status] = tally.get(r.freshness.status, 0) + 1
    parts = [f"{MARK.get(k, '?')}{k} {v}" for k, v in
             sorted(tally.items(), key=lambda x: -x[1])]
    stale = tally.get("stale", 0)
    note = ""
    if stale and stale / len(rels) >= 0.3:
        note = f"  ⚠ {stale/len(rels)*100:.0f}%가 갱신주기를 넘겨 재확인 필요"
    return " · ".join(parts) + note


# ══════════════════════════════════════════════════════════════════
#  리스크 파급 — **저장하지 않고 질의 시 계산한다**
# ══════════════════════════════════════════════════════════════════
#
# 실측(2026-07-31): 사건 436건 중 81%가 IMPACTS 0~1개다. 즉 그래프에 저장된
# 파급 경로가 거의 없다. 그런데 이건 데이터를 더 모아도 안 고쳐진다 —
# **기사가 파급을 명시하지 않기 때문**이다.
#
#     "SK하이닉스 청주공장 화재"  기사는 화재를 보도한다.
#     "그래서 엔비디아 HBM 공급이 위험하다"까지 쓰는 기사는 드물다.
#
# 추출기는 「기사에 명시된 것만」이 원칙이므로 없는 파급을 만들 수 없다(만들면
# 환각이다). 대신 **그래프 구조가 아는 것**을 질의 시점에 결합한다:
#
#     [기사가 말한 것]  청주 화재 -IMPACTS-> SK하이닉스
#     [그래프가 아는 것] SK하이닉스 -SUPPLIES_TO-> 엔비디아
#     ─────────────────────────────────────────────
#     [계산한 것]       청주 화재 → 엔비디아 (2홉, 감쇠 적용)
#
# ★저장하지 않는 이유: 저장하면 「기사가 말한 파급」과 「우리가 계산한 파급」이
#   섞여 근거를 구분할 수 없다. 계산 결과에는 항상 경로를 함께 돌려준다.

# 홉마다 곱하는 감쇠. 2차 공급사까지 가면 영향이 희석되는 것을 반영한다.
#
# ★채널이 둘이고 감쇠가 다르다(2026-08-03 추가).
#
#   공급 차질  사건이 난 회사가 **우리에게 납품**한다 → 생산이 즉시 막힌다
#   매출 상실  사건이 난 회사가 **우리 고객**이다     → 매출이 서서히 준다
#
#   전에는 공급 차질만 계산했다. 방향을 유지한 건 옳았지만(방향 무시 147개 →
#   유지 33개로 잡음이 걸렸다) **유효한 채널 하나가 같이 빠졌다.** 실측:
#
#       심텍          직접 2건 · 공급사 경유 0건 · 고객 경유 113건
#       레인보우로보틱스   직접 3건 · 공급사 경유 0건 · 고객 경유  57건
#
#   중소형 공급사는 고객 경유가 **유일한 파급 채널**이다. 시드 64곳 대부분이
#   그런 회사라, 이걸 빼면 「리스크 파급」이 사실상 빈 화면이 된다.
#
#   같은 감쇠를 쓰면 안 된다 — 공급이 끊기는 것과 고객이 흔들리는 것은
#   급성/만성의 차이다. 매출 상실을 더 세게 깎는다.
HOP_DECAY = 0.55            # 공급 차질 — 사건사 → 그 회사의 고객
HOP_DECAY_DEMAND = 0.35     # 매출 상실 — 사건사 → 그 회사의 공급사

# 허브 페널티 — **흔한 노드를 지나는 경로는 정보량이 적다**(검색의 IDF와 같은 원리).
# 「삼성전자를 안다」는 거의 모두가 그러하므로 변별력이 없다.
# 실측: 공급 엣지만 봐도 삼성전자는 120개 상대와 이어져 있다.
HUB_HALF = 40.0        # 이 차수에서 가중치가 절반이 된다


def _hub_penalty(degree: int) -> float:
    """차수가 클수록 그 노드를 지나는 경로를 약하게 본다."""
    return HUB_HALF / (HUB_HALF + max(degree - 1, 0))


@dataclass
class Propagation:
    """계산된 파급 하나 — **경로를 반드시 함께 돌려준다**."""
    target: str
    hops: int
    score: float
    path: list[str]          # 사람이 읽을 경로 설명
    stated: bool             # 기사가 직접 말한 파급인가(1홉), 계산한 것인가
    channel: str = ""        # supply(공급 차질) | demand(매출 상실) — 2홉만
    # ★경로를 이루는 **실제 엣지**. 화면이 이걸로 그래프의 그 선을 연다.
    #   `path` 는 사람이 읽는 설명이고, 이쪽은 되짚을 수 있는 열쇠다.
    edge_ids: list[str] = field(default_factory=list)

    def describe(self) -> str:
        tag = "보도됨" if self.stated else f"계산 {self.hops}홉"
        ch = {"supply": " · 공급 차질", "demand": " · 매출 상실"}.get(self.channel, "")
        return (f"[{tag}{ch}] {self.target}  (영향도 {self.score:.2f})\n"
                f"     {' → '.join(self.path)}")


# 1홉: 기사가 말한 파급. 2홉: 거기서 공급망을 **방향 유지**로 타고 내려간다.
# 방향을 유지하지 않으면 「같은 회사에 납품하는 경쟁사」까지 딸려와 무의미해진다
# (실측: 방향 무시 147개 → 방향 유지 33개).
#
# ★근거 검증에서 걸린 엣지는 **경로에서 뺀다.** 파급은 「이 관계가 있다」를
#   전제로 계산하므로, 근거 없는 엣지 하나가 없는 파급을 만들어 낸다.
#   조회(`relations_of`)만 막고 여기를 안 막으면 같은 오류가 파급 결과로 샌다.
_HIDE = "(NOT coalesce({r}.grounding_suspect, false) OR {r}.grounding_stage1 = 'wrong_type')"

_PROPAGATE = f"""
MATCH (e:Event) WHERE e.name = $event
MATCH (e)-[i:IMPACTS]->(hit:Company)
WHERE {_HIDE.format(r='i')}
// ★사건이 **공장·사업부**에 붙었으면 모회사로 올린다(2026-08-03).
//   「삼성전자 인도 공장 무기한 파업」이 공장 노드에만 붙어 있어서, 삼성전자의
//   고객사 어디로도 안 퍼지고 있었다. 법적으로 그 공장은 삼성전자 그 자체다.
//   엣지 타입은 12개로 고정이라 `PART_OF`를 못 만들어, 노드 속성을 조회 때 따라간다.
//   감쇠는 없다 — 홉이 아니라 **같은 법인**이다.
OPTIONAL MATCH (parent:Company)
  WHERE hit.part_of_corp_code IS NOT NULL
    AND parent.corp_code = hit.part_of_corp_code
WITH e, i, coalesce(parent, hit) AS c, hit,
     CASE WHEN parent IS NULL THEN null ELSE hit.part_of_unit END AS unit
// ★방향을 유지하되 **양쪽 다** 탄다. `c`가 파는 쪽이면 `d`는 고객(공급 차질),
//   `c`가 사는 쪽이면 `d`는 공급사(매출 상실). 둘을 `flow`로 구분해 감쇠를 달리한다.
OPTIONAL MATCH (c)-[s:SUPPLIES_TO]-(d:Company)
WHERE coalesce(s.is_current, true) AND {_HIDE.format(r='s')}
// ★`only` 를 주면 **그 회사에 닿는 갈래만** 계산한다. 선을 클릭했을 때는 반대쪽
//   끝 하나만 알면 되는데, 전부 계산하면 삼성전자 사건 하나가 129곳을 낸다.
//   필터를 Cypher 로 내려야 한다 — 파이썬에서 걸러도 계산은 이미 끝나 있다.
WITH e, i, c, hit, unit, s, d
WHERE $only IS NULL OR c.name = $only OR d.name = $only
RETURN e.name AS event, c.name AS direct, unit,
       CASE WHEN startNode(s) = c THEN 'supply' ELSE 'demand' END AS flow,
       coalesce(i.sign,'') AS sign, properties(i) AS i_props,
       // ★경로를 **문자열로만** 주면 화면이 「그래프에서 보기」를 못 만든다.
       //   경로는 실제 엣지로 이뤄져 있으니 그 id 를 함께 실어 보낸다.
       elementId(i) AS i_eid, elementId(s) AS s_eid,
       d.name AS downstream, properties(s) AS s_props,
       // 허브 감점은 **그 방향의 부채꼴 크기**로 센다. 파는 쪽으로 100곳에
       // 뿌리는 회사와, 사오는 곳이 3곳뿐인 회사는 희석 정도가 다르다.
       CASE WHEN startNode(s) = c
            THEN size([(c)-[s2:SUPPLIES_TO]->() WHERE {_HIDE.format(r='s2')} | 1])
            ELSE size([(c)<-[s3:SUPPLIES_TO]-() WHERE {_HIDE.format(r='s3')} | 1])
       END AS c_out_degree
"""


def propagate_risk(event_name: str, *, today: Optional[date] = None,
                   min_score: float = 0.05,
                   only: Optional[str] = None) -> list[Propagation]:
    """사건 → 영향받는 기업들. 1홉은 보도된 것, 2홉은 공급망으로 **계산한 것**.

    돌려주는 각 항목에 경로가 붙어 있어, 어디까지가 기사이고 어디부터가
    계산인지 사용자가 구분할 수 있다.

    `only` 를 주면 **그 회사에 닿는 갈래만** 계산한다(선을 클릭했을 때).
    점수 계산은 그대로라 전체를 돌린 뒤 걸러낸 것과 값이 같다.
    """
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_PROPAGATE, event=event_name, only=only)]
    if not rows:
        return []

    out: dict[str, Propagation] = {}
    for r in rows:
        # ── 1홉: 기사가 직접 말한 것 ──────────────────────
        f1 = assess(r["i_props"] or {}, today=today)
        base = float((r["i_props"] or {}).get("confidence") or 0.7)
        s1 = base * f1.confidence_factor
        neg = (r["sign"] or "") == "negative"
        # ★모회사로 올린 것이면 **어느 공장인지**를 경로에 남긴다. 「삼성전자가
        #   위험」이 아니라 「삼성전자 인도 공장이 멈춰서 삼성전자가 위험」이어야
        #   사용자가 근거를 되짚을 수 있다.
        hop1 = (f"{r['direct']}({r['unit']})" if r.get("unit") else r["direct"])
        if s1 >= min_score:
            prev = out.get(r["direct"])
            if not prev or prev.score < s1:
                out[r["direct"]] = Propagation(
                    r["direct"], 1, s1,
                    [r["event"], f"IMPACTS({r['sign'] or '?'})", hop1],
                    stated=True,
                    edge_ids=[x for x in (r["i_eid"],) if x])

        # ── 2홉: 공급망을 타고 계산 ───────────────────────
        if not r["downstream"]:
            continue
        f2 = assess(r["s_props"] or {}, today=today)
        supply = r["flow"] == "supply"
        decay = HOP_DECAY if supply else HOP_DECAY_DEMAND
        s2 = s1 * decay * f2.confidence_factor * _hub_penalty(r["c_out_degree"] or 1)
        if s2 < min_score:
            continue
        prev = out.get(r["downstream"])
        if prev and prev.score >= s2:
            continue
        # 경로 문구에 **어느 채널인지**를 적는다. 「왜 내가 영향을 받나」의
        # 답이 「공급이 끊겨서」와 「고객이 흔들려서」로 전혀 다르다.
        leg = "SUPPLIES_TO(공급 차질)" if supply else "SUPPLIES_TO←(매출 상실)"
        out[r["downstream"]] = Propagation(
            r["downstream"], 2, s2,
            [r["event"], f"IMPACTS({r['sign'] or '?'})", hop1,
             leg, r["downstream"]],
            stated=False, channel="supply" if supply else "demand",
            edge_ids=[x for x in (r["i_eid"], r["s_eid"]) if x])

    return sorted(out.values(), key=lambda p: -p.score)
