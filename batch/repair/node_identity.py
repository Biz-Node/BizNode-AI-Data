"""**같은 것을 가리키는 노드를 하나로** — 정규화키 재계산 + 중복 병합.

노드가 갈리면 관계도 갈린다. 「도쿄일렉트론」이 노드 두 개면 그 회사의 관계가
둘로 나뉘어 2홉 파급이 끊긴다. 화면에서는 그냥 「연결이 적은 회사」로 보여서
눈에 띄지도 않는다. 갈리는 원인이 둘이다:

  ① 정규화 규칙이 바뀌었다
       stub Company는 **norm_name이 곧 노드 식별자**다(graph_loader `_company_ident`).
       `normalize_company_name`을 고치면 기존 노드의 키가 낡은 값으로 남고,
       새로 들어오는 같은 회사가 **별도 노드**로 생긴다.
       → 전 노드의 norm_name을 현재 규칙으로 다시 계산하고, 같아진 stub을 합친다.
         `staged_edges`(authority)의 키도 함께 갱신한다 — 안 하면 다음 적재 때
         옛 노드가 되살아난다.

  ② 적재 시 MERGE가 못 잡은 중복이 남았다
       ①을 돌린 뒤에도 norm_name이 같은 노드가 둘 남아 있을 수 있다.
       → 남길 쪽(corp_code 있음 → 시드 → 연결 많음)으로 합친다.

    python -m batch.repair.node_identity --dry-run
    python -m batch.repair.node_identity              # ① → ② 순서로 둘 다
    python -m batch.repair.node_identity --only merge

★Person은 **자동으로 합치지 않는다.** 한국은 동명이인이 흔하고, 소속이 다르면
  대개 다른 사람이다:
      김병수 @덕산네오룩스  ·  김병수 @로보티즈      → 다른 사람일 것
      구본준 @LX세미콘     ·  구본준 @LX그룹        → 같은 사람일 것
  이름만으로 가를 수 없어 보고만 한다(생년월 기반 병합은 `pipeline/importer/person_er`).

★corp_code가 둘 이상인 Company도 건드리지 않는다 — 서로 다른 법인일 수 있다
  (실측: 케이씨텍이 corp_code 두 개, 분할 전후로 보인다).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.base import normalize_company_name

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
#  ① 정규화키 재계산 + 같아진 stub 병합
# ══════════════════════════════════════════════════════════════

_SCAN = """
MATCH (c:Company)
RETURN elementId(c) AS eid, c.name AS name, c.norm_name AS norm,
       c.corp_code AS corp_code, coalesce(c.is_stub, false) AS is_stub
"""

_SET_NORM = "MATCH (c:Company) WHERE elementId(c)=$eid SET c.norm_name=$norm"

# 병합 — 시드/해소된 노드를 대표로 남긴다(정보가 더 많은 쪽)
#
# ★이름이 `_MERGE`였는데 **같은 모듈 180행에 같은 이름이 또 있어 덮였다**
#   (2026-08-07). 파이썬은 나중 정의가 이기므로, 여기서 `keep=`/`drop=`으로
#   부르면 덮인 쪽이 요구하는 `$keep_id`/`$drop_id`가 없어 죽는다:
#       ParameterMissing: Expected parameter(s): keep_id, drop_id
#   `finalize`가 5단계에서 통째로 멈췄고, 원인이 「이름 충돌」이라 로그만 봐서는
#   안 보였다. 두 쿼리는 병합 정책도 다르다(`discard` vs `combine`).
_MERGE_STUB = """
MATCH (a:Company) WHERE elementId(a)=$keep
MATCH (b:Company) WHERE elementId(b)=$drop
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.norm_name AS norm
"""


def renormalize(dry_run: bool) -> int:
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_SCAN)]

        changed = [r for r in rows
                   if r["norm"] != normalize_company_name(r["name"] or "")]
        print(f"Company {len(rows)}개 중 norm_name 변경 대상 {len(changed)}개")

        # 새 규칙 기준으로 stub을 묶는다 — 같은 값이면 같은 회사
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if r["is_stub"]:
                buckets[normalize_company_name(r["name"] or "")].append(r)
        collisions = {k: v for k, v in buckets.items() if len(v) > 1}

        print(f"병합 대상 stub 그룹 {len(collisions)}개")
        for norm, members in list(collisions.items())[:20]:
            print(f"   {norm}  ←  {[m['name'] for m in members]}")

        if dry_run:
            print(f"\n[dry-run] norm_name {len(changed)}건 갱신, "
                  f"{len(collisions)}개 그룹 병합 예정")
            return 0

        # ① norm_name 재계산 (병합보다 먼저 — 병합 후엔 elementId가 사라진다)
        for r in changed:
            session.run(_SET_NORM, eid=r["eid"],
                        norm=normalize_company_name(r["name"] or ""))
        print(f"\n✅ norm_name {len(changed)}건 갱신")

        # ② 중복 stub 병합 — 대표 선택 규칙
        #   1) 정규화키가 한글이면 **한글 표기**를 대표로 (norm_name과 화면 표시를 맞춘다)
        #      「Netlist」/「넷리스트」가 합쳐질 때 화면에 영문이 남으면 어색하다.
        #   2) 그다음 이름이 긴 쪽(정식명일 가능성)
        merged = 0
        for norm, members in collisions.items():
            wants_hangul = bool(re.search(r"[가-힣]", norm))
            ordered = sorted(
                members,
                key=lambda m: (
                    not (wants_hangul and re.search(r"[가-힣]", m["name"] or "")),
                    -len(m["name"] or ""),
                ),
            )
            keep = ordered[0]
            for drop in ordered[1:]:
                session.run(_MERGE_STUB, keep=keep["eid"], drop=drop["eid"])
                merged += 1
        print(f"✅ stub {merged}건 병합 ({len(collisions)}개 그룹)")

    # ③ staged_edges(authority)의 키도 맞춘다 — 안 하면 재적재 시 옛 노드가 되살아난다
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT src_key FROM staged_edges WHERE src_node_type='Company' "
                    "UNION SELECT DISTINCT tgt_key FROM staged_edges WHERE tgt_node_type='Company'")
        keys = [r[0] for r in cur.fetchall()]
        # corp_code(8자리 숫자)는 그대로 두고 norm_name 키만 재계산
        remap = {k: normalize_company_name(k) for k in keys
                 if not (k.isdigit() and len(k) == 8)}
        remap = {k: v for k, v in remap.items() if v and v != k}

        for old, new in remap.items():
            cur.execute("UPDATE staged_edges SET src_key=%s "
                        "WHERE src_node_type='Company' AND src_key=%s", (new, old))
            cur.execute("UPDATE staged_edges SET tgt_key=%s "
                        "WHERE tgt_node_type='Company' AND tgt_key=%s", (new, old))
        print(f"✅ staged_edges 키 {len(remap)}종 갱신")

    return 0


# ════════════════════════════════════════════════════════════
#  ② 남은 중복 노드 병합 (Company · Event)
# ════════════════════════════════════════════════════════════


# Company — norm_name이 같은 무리
_FIND_COMPANY = """
MATCH (c:Company)
WITH c.norm_name AS nn, collect(c) AS cs
WHERE size(cs) > 1
RETURN nn AS key,
       [x IN cs | {id: elementId(x), name: x.name, corp: x.corp_code,
                   seed: coalesce(x.is_seed,false), deg: COUNT{(x)--()}}] AS nodes
"""

# Event — 이름이 같은 무리 (사건은 이름이 곧 정체다)
_FIND_EVENT = """
MATCH (e:Event)
WITH e.name AS nm, collect(e) AS es
WHERE size(es) > 1
RETURN nm AS key,
       [x IN es | {id: elementId(x), name: x.name, etype: x.event_type,
                   seed: false, corp: null, deg: COUNT{(x)--()}}] AS nodes
"""

_FIND_PERSON = """
MATCH (p:Person)
WITH p.name AS nm, collect(p) AS ps
WHERE size(ps) > 1
RETURN nm AS key,
       [x IN ps | {name: x.name, deg: COUNT{(x)--()},
                   orgs: [(x)-[:IS_EXECUTIVE_OF]->(c) | c.name]}] AS nodes
"""

# APOC가 엣지를 옮기고 속성을 합친다. `combine`은 값이 다르면 배열로 모은다 —
# 어느 한쪽을 조용히 버리지 않는다.
#
# ★단, `mergeRels: true`가 **엣지도 합치면서** 스칼라 속성을 배열로 바꾼다.
#   실측(2026-08-01): 병합 4건 뒤 `source_doc`·`evidence_id`·`confidence`가
#   리스트가 된 엣지 10건이 생겼고, 이걸 스칼라로 읽던 `recheck_suspects`가
#   `TypeError: unhashable type: 'list'`로 죽었다.
#   → 병합 직후 `_UNLIST`로 되돌린다. 목록은 `*_variants`에 남긴다.
_MERGE = """
MATCH (keep) WHERE elementId(keep) = $keep_id
MATCH (drop) WHERE elementId(drop) = $drop_id
CALL apoc.refactor.mergeNodes([keep, drop],
     {properties: 'combine', mergeRels: true}) YIELD node
RETURN elementId(node) AS id
"""

# ★배열이 되면 안 되는 속성을 **목록으로 관리하지 않는다.** 실제로 세 번
#   샜다 — 엣지만 고쳤더니 `n.name`에서, 그걸 고쳤더니 `r.occurred_at`에서
#   `TypeError: '<' not supported between 'str' and 'list'`로 죽었다.
#   속성 이름을 다 적을 수 없으니 **원래 배열인 속성만 빼고 전부** 되돌린다.
#
#   원래 배열인 것 = 여러 값을 담으려고 우리가 복수형으로 지은 것들.
_LIST_BY_DESIGN = {
    "evidence_ids", "source_docs", "subtypes", "sector", "etf_list",
    "aliases", "tags",
}
_VARIANT_SUFFIX = "_variants"

# 배열이 된 스칼라 속성을 찾는다 — 이름을 미리 알 필요가 없다.
_FIND_LISTED = """
MATCH {pattern}
WITH x, [k IN keys(x) WHERE valueType(x[k]) STARTS WITH 'LIST'] AS listed
WHERE size(listed) > 0
UNWIND listed AS k
RETURN DISTINCT k AS prop
"""

_UNLIST = """
MATCH {pattern}
WHERE x[$k] IS NOT NULL AND valueType(x[$k]) STARTS WITH 'LIST'
SET x[$k + '{suffix}'] = x[$k], x[$k] = head(x[$k])
RETURN count(*) AS n
"""


def unlist_scalars(session, *, verbose: bool = False) -> int:
    """병합이 배열로 바꿔 놓은 스칼라 속성을 되돌린다. 되돌린 건수를 반환.

    대표값을 스칼라로 두고 원래 목록은 `<이름>_variants`에 남긴다 —
    어느 값도 조용히 버리지 않는다.
    """
    total = 0
    for pattern in ("(x)", "()-[x]->()"):
        props = [r["prop"] for r in
                 session.run(_FIND_LISTED.format(pattern=pattern))]
        for k in props:
            if k in _LIST_BY_DESIGN or k.endswith(_VARIANT_SUFFIX):
                continue
            n = session.run(_UNLIST.format(pattern=pattern,
                                           suffix=_VARIANT_SUFFIX),
                            k=k).single()["n"]
            if n:
                total += n
                if verbose:
                    print(f"      · {k:24}{n:>4}건")
    return total


def _pick_keep(nodes: list[dict]) -> tuple[dict, list[dict]] | None:
    """남길 노드와 버릴 노드들. 합치면 안 되는 무리는 None.

    남길 쪽 = corp_code가 있는 것 → 시드 → 연결이 많은 것.
    """
    withcorp = [n for n in nodes if n.get("corp")]
    # ★corp_code가 둘 이상이면 **서로 다른 법인**일 수 있다. 손대지 않는다.
    if len({n["corp"] for n in withcorp}) > 1:
        return None
    ranked = sorted(nodes, key=lambda n: (bool(n.get("corp")), bool(n.get("seed")),
                                          n["deg"]), reverse=True)
    return ranked[0], ranked[1:]


def merge_duplicates(dry_run: bool) -> int:
    merged = skipped = 0
    with neo4j_session() as session:
        for label, query in (("Company", _FIND_COMPANY), ("Event", _FIND_EVENT)):
            groups = [dict(r) for r in session.run(query)]
            print(f"\n■ {label} 중복 {len(groups)}쌍")
            for g in groups:
                pick = _pick_keep(g["nodes"])
                if pick is None:
                    corps = sorted({n["corp"] for n in g["nodes"] if n.get("corp")})
                    print(f"   ⏸ {g['key'][:26]:28} corp_code가 여럿 {corps} "
                          f"— 다른 법인일 수 있어 **건너뜁니다**(사람 확인 필요)")
                    skipped += 1
                    continue
                keep, drops = pick
                for d in drops:
                    print(f"   ✎ {g['key'][:26]:28} "
                          f"「{d['name'][:18]}」(연결 {d['deg']}) → "
                          f"「{keep['name'][:18]}」(연결 {keep['deg']}"
                          f"{', corp ' + keep['corp'] if keep.get('corp') else ''})")
                    merged += 1
                    if not dry_run:
                        session.run(_MERGE, keep_id=keep["id"], drop_id=d["id"])

        # 병합이 스칼라를 배열로 바꿔 놓은 것을 되돌린다 (위 _UNLIST 주석 참조)
        if merged and not dry_run:
            unlisted = unlist_scalars(session)
            if unlisted:
                print(f"\n   ↺ 병합으로 배열이 된 스칼라 속성 {unlisted}건 되돌림 "
                      f"(원래 목록은 `*{_VARIANT_SUFFIX}`에 보존)")

        # Person은 보고만 한다 — 동명이인과 구별할 수 없다
        people = [dict(r) for r in session.run(_FIND_PERSON)]
        print(f"\n■ Person 동명 {len(people)}쌍 — **합치지 않습니다**")
        print("   소속이 다르면 대개 다른 사람입니다. 같은 사람으로 보이는 것만 사람이 합치세요.")
        for p in people:
            orgs = [" / ".join(n["orgs"][:2]) or "(소속 없음)" for n in p["nodes"]]
            print(f"   · {p['key'][:12]:14}" + "   vs   ".join(o[:26] for o in orgs))

    print(f"\n{'[dry-run] ' if dry_run else '✅ '}"
          f"병합 {merged}건 · 건너뜀 {skipped}건 · Person {len(people)}쌍은 보고만")
    return 0


# ══════════════════════════════════════════════════════════════
#  ③ 라벨이 잘못 붙은 노드 — 법인인데 :Person
# ══════════════════════════════════════════════════════════════
#
# 최대주주 파서가 「최대주주 = 개인」을 전제해서, 법인 최대주주가 `:Person`으로
# 들어온다. 실측 2건인데 그중 하나가 **같은 회사를 둘로 쪼개 놓았다**:
#
#     (:Person {name:"TOKAI CARBON CO.,LTD."})  -OWNS_STAKE_IN/최대주주-> 티씨케이
#     (:Company{name:"TOKAI CARBON CO.,LTD."})  <-PARTNERS_WITH/기술이전- 티씨케이
#
# 같은 이름·같은 상대인데 라벨이 달라 「지분도 있고 기술도 준 회사」로 안 보인다.
#
# ★반대 방향(개인이 :Company)은 **고치지 않는다.** 조사해 보니 우리 파싱 오류가
#   아니라 DART가 개인 특수관계인에게 corp_code를 발급한 것이었다:
#       corp_code_master  01587542 → 「김동진」   01461271 → 「장남」
#   원천이 그렇게 주는 값을 우리가 임의로 사람으로 바꾸면 DART와 대조가 깨진다.

_CORP_MARKERS = ("㈜", "(주)", "(유)", "주식회사", "유한회사", "재단", "조합",
                 "홀딩스", "그룹")
_CORP_SUFFIXES = frozenset({
    "inc", "ltd", "llc", "corp", "co", "gmbh", "sa", "nv", "plc",
    "limited", "holdings", "company", "ag", "pte", "bhd", "sdn", "kk",
})

# ★단체(:Organization)로 보내야 하는 것들. 이 검사를 **법인 검사보다 먼저** 한다 —
#   「조합원」이 「조합」에 걸려 회사가 되는 일이 실제로 있었다:
#       삼성전자 DX부문 조합원 -SUES/가처분-> 초기업노조(:Organization)
#   상대는 이미 Organization인데 이쪽만 Company가 되면 노조 분쟁이 기업 소송으로
#   보인다. 매트릭스도 `SUES`에 Organization↔Organization을 허용한다.
_ORG_MARKERS = ("조합원", "노조", "노동조합", "위원회", "협회", "연맹", "학회",
                "지부", "근로자", "직원")


def _looks_organization(name: str) -> bool:
    return any(m in name for m in _ORG_MARKERS)


def _looks_corporate(name: str) -> bool:
    """이름만으로 **법인이 확실한가**. 애매하면 False — 사람을 회사로 만들지 않는다."""
    if _looks_organization(name):
        return False
    if any(m in name for m in _CORP_MARKERS):
        return True
    tokens = [t.strip(".,()").lower() for t in re.split(r"[\s,]+", name) if t.strip(".,()")]
    return len(tokens) > 1 and tokens[-1] in _CORP_SUFFIXES


_PERSON_NODES = """
MATCH (p:Person) OPTIONAL MATCH (p)-[x]-()
RETURN elementId(p) AS eid, p.name AS name, count(x) AS deg
"""

# 같은 이름의 Company가 이미 있으면 **그쪽으로 관계를 옮기고** Person을 지운다.
# 없으면 라벨만 바꿔 단다 — 어느 쪽이든 관계는 하나도 잃지 않는다.
# ★`mergeNodes`는 **라벨도 합친다** — 그대로 두면 `:Company:Person`이 남아서
#   Person 통계와 Company 통계에 같은 노드가 이중으로 잡힌다. 떼고 끝낸다.
_RELABEL_INTO = """
MATCH (p:Person) WHERE elementId(p) = $eid
MATCH (c:Company) WHERE elementId(c) = $keep_id
CALL apoc.refactor.mergeNodes([c, p], {properties: 'discard', mergeRels: true})
YIELD node
REMOVE node:Person
SET node.absorbed_person = true
RETURN elementId(node) AS id
"""
_RELABEL_ONLY = """
MATCH (p:Person) WHERE elementId(p) = $eid
REMOVE p:Person SET p:Company, p.is_stub = true,
       p.norm_name = $norm, p.relabeled_from = 'Person'
"""

# Organization은 `norm_name`을 식별자로 쓰지 않는다(stub Company만 그렇다).
_TO_ORG = """
MATCH (n) WHERE elementId(n) = $eid
REMOVE n:Person, n:Company SET n:Organization, n.relabeled_from = $was
"""


def fix_label_splits(dry_run: bool) -> int:
    """라벨이 잘못 붙은 노드를 되돌린다 — 법인은 Company, 단체는 Organization."""
    fixed = 0
    with neo4j_session() as session:
        people = [dict(r) for r in session.run(_PERSON_NODES)]
        corps = [p for p in people if p["name"] and _looks_corporate(p["name"])]
        orgs = [p for p in people if p["name"] and _looks_organization(p["name"])]
        # 예전 실행이 Company로 잘못 옮겨 놓은 단체를 되찾는다(멱등)
        orgs += [dict(r) for r in session.run(
            "MATCH (c:Company) WHERE c.relabeled_from = 'Person' "
            "OPTIONAL MATCH (c)-[x]-() "
            "RETURN elementId(c) AS eid, c.name AS name, count(x) AS deg")
            if _looks_organization(r["name"] or "")]

        print(f"\n■ 라벨이 잘못 붙은 노드 {len(corps) + len(orgs)}건 "
              f"(:Person 전체 {len(people)})")

        for t in corps:
            norm = normalize_company_name(t["name"])
            twin = session.run(
                "MATCH (c:Company {norm_name:$norm}) OPTIONAL MATCH (c)-[x]-() "
                "RETURN elementId(c) AS id, c.name AS name, count(x) AS deg "
                "ORDER BY deg DESC LIMIT 1", norm=norm).single()
            if twin:
                print(f"   ✎ 「{t['name'][:32]}」(연결 {t['deg']}) → 같은 이름 Company "
                      f"「{twin['name'][:24]}」(연결 {twin['deg']})로 합칩니다")
                if not dry_run:
                    session.run(_RELABEL_INTO, eid=t["eid"], keep_id=twin["id"])
            else:
                print(f"   ✎ 「{t['name'][:32]}」(연결 {t['deg']}) → :Company로 라벨 교체")
                if not dry_run:
                    session.run(_RELABEL_ONLY, eid=t["eid"], norm=norm)
            fixed += 1

        for t in orgs:
            print(f"   ✎ 「{t['name'][:32]}」(연결 {t['deg']}) → :Organization "
                  f"(노조·협회는 회사도 사람도 아닙니다)")
            if not dry_run:
                session.run(_TO_ORG, eid=t["eid"], was="Person")
            fixed += 1

        if fixed and not dry_run:
            unlist_scalars(session)
    return fixed


# ══════════════════════════════════════════════════════════════
#  ④ 이름이 겹치는 노드 — **보고만 한다**
# ══════════════════════════════════════════════════════════════
#
# ★왜 자동 병합하지 않나 (2026-08-02 실측)
#
# 「한쪽 이름이 다른 쪽에 통째로 들어감」으로 154쌍이 나왔다. 붙어 있는 이웃까지
# 봐도 대부분 **합치면 안 되는 것**이었다:
#
#     피에스케이(corp 01365825) ⟷ 피에스케이홀딩스(corp 00208444)   별개 법인
#     HD현대(01205709)         ⟷ HD현대중공업(01390344)            모/자회사
#     삼성물산                  ⟷ 삼성물산 건설부문                  사업부문
#     마이크론                  ⟷ 마이크론 메모리 말레이시아            해외 자회사
#
# 진짜 중복은 소수다(마이크론 ⟷ 마이크론 테크놀로지, 삼성화재 ⟷ 삼성화재해상보험).
# 규칙으로 이 둘을 가를 방법이 없다 — **판단은 사람 몫**이라 목록만 낸다.
_OVERLAP_SCAN = """
MATCH (c:Company) OPTIONAL MATCH (c)-[x]-(o)
RETURN c.name AS name, c.corp_code AS corp, coalesce(c.is_stub,false) AS stub,
       count(x) AS deg, collect(DISTINCT o.name)[..40] AS nb
"""


def _short(name: str) -> str:
    return re.sub(r"[\s()（）\-·,.㈜]|주식회사|\(주\)", "", name or "").lower()


def report_overlaps(limit: int = 20) -> None:
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_OVERLAP_SCAN)]
    idx = {_short(r["name"]): r for r in rows if _short(r["name"])}
    keys = sorted(idx, key=len)

    cands = []
    for i, a in enumerate(keys):
        if len(a) < 4:
            continue
        for b in keys[i + 1:]:
            if a in b and a != b:
                ra, rb = idx[a], idx[b]
                # corp_code가 둘 다 있고 다르면 **법적으로 다른 회사**다
                if ra["corp"] and rb["corp"] and ra["corp"] != rb["corp"]:
                    continue
                cands.append((len(set(ra["nb"]) & set(rb["nb"])), ra, rb))

    cands.sort(key=lambda x: (-x[0], -min(x[1]["deg"], x[2]["deg"])))
    print(f"\n■ 이름이 겹치는 Company {len(cands)}쌍 — **합치지 않습니다**")
    print("   (corp_code가 서로 다른 쌍은 별개 법인이라 목록에서 뺐습니다)")
    print("   자회사·사업부문일 수 있어 사람이 봐야 합니다. 공통 이웃이 많을수록 같을 확률이 높습니다.")
    for sh, a, b in cands[:limit]:
        print(f"   공통이웃{sh:>2}  {a['name'][:24]:26}(연결{a['deg']:>3})  ⟷  "
              f"{b['name'][:24]:26}(연결{b['deg']:>3})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["renorm", "merge", "label", "overlap"],
                    help="하나만 실행 (기본은 재계산 → 병합 → 라벨 → 겹침보고)")
    args = ap.parse_args()

    # ★순서가 있다 — 재계산이 노드를 같게 만든 뒤라야 병합이 그것을 본다.
    if args.only in (None, "renorm"):
        renormalize(args.dry_run)
    if args.only in (None, "merge"):
        merge_duplicates(args.dry_run)
    if args.only in (None, "label"):
        fix_label_splits(args.dry_run)
    if args.only in (None, "overlap"):
        report_overlaps()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
