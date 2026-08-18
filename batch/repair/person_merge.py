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
import re
import sys

from app.core.database import neo4j_session, postgres_connection
from pipeline.llm import ask_json

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

    # ── 2026-08-09 · 확장 12곳이 들어오며 새로 생긴 쌍 ──────────
    ("권봉석|1963-09", "권봉석@news",
     "「권봉석 ㈜LG 최고운영책임자(COO)와 조주완 LG전자 CEO … 참석」 — "
     "LG전자 부회장 → 지주사 ㈜LG COO. 구본준과 같은 지주·자회사 겸직"),
    ("조남성|1959-07", "조남성@news",
     "원익머트리얼즈 대량보유 보고에 「보고자 원익홀딩스 45.69%, **특별관계자 조남성** 0.04%」 — "
     "원익홀딩스 대표이사가 계열사 지분을 소량 보유한 것"),

]


# ★같이 걸렸지만 **합치지 않기로 한** 이름 — 주석이 아니라 **코드**로 둔다.
#
#   전에는 이 내용이 주석이라 `audit/graph`가 읽을 수 없었다. 그래서 이미
#   판단을 끝낸 3건이 감사 때마다 「합병 후보」로 떴다. **「봐도 할 게 없는 경고」는
#   사람을 무디게 해서 진짜 후보까지 흘려보내게 한다** — 이 파일 위쪽에서 이미
#   같은 이유로 「생년월이 둘 다 있는 쌍」을 뺐는데, 사람이 내린 판단은 못 뺐다.
#
#   여기 이름을 넣으면 감사가 건너뛴다. 판단이 바뀌면 빼면 다시 뜬다.
REVIEWED_NOT_MERGED: dict[str, str] = {
    "김준성": "DART 삼성전자 사외이사(1967-10) vs 뉴스 「하이프라자 상무(경남담당)」. "
              "하이프라자는 LG전자 판매법인 — 그룹·직급이 다르다. 다른 사람.",
    "우정호": "DART 가온칩스 부사장(1980-05) vs 뉴스 「퀄컴 출신·LG전자 상무를 지낸 "
              "우정호 대표」(비전넥스트). 경력은 나이와 맞지만 가온칩스 이력이 "
              "근거에 없다. 판단 불가로 보류.",
    "이준호": "에스피지 최대주주 19.84%·대표이사 vs 덕산네오룩스 5%주주인데 지분 0.0%. "
              "회사도 역할도 무관 — 다른 사람.",
}

_LOOK = """
MATCH (p:Person {person_key: $key})
OPTIONAL MATCH (p)-[x]-()
RETURN elementId(p) AS id, p.name AS name, count(x) AS deg
"""

# ─────────────────────── 모델 판정 (2026-08-15 신설) ───────────────────
#
# ★손 목록만으로는 못 따라간다. 확장할 때마다 새 쌍이 생기는데, 그때마다 사람이
#   근거를 읽어야 한다면 구조적으로 늘 뒤늦는다. 위 `CONFIRMED`는 6쌍인데
#   확장 두 번에 5쌍이 새로 생겼다.
#
# ★그렇다고 이름만으로 합치면 안 된다 — **이 데이터셋에서 동명이인이 8쌍 중
#   3쌍(37%)으로 실재**한다(김병수·이승훈·김근태, 전부 생년월이 다르다).
#
# 그래서 세 단계로 간다:
#     1단  생년월이 둘 다 있고 다르면        → 절대 안 합침 (사람도 못 뒤집음)
#     2단  손 목록에 있으면                → 그대로 따름 (사람이 이김)
#     3단  나머지를 **근거와 함께** 모델에 물음 → 캐시에 저장
#
# 근거는 소속 회사·직위·주요경력이다. 실제로 이걸 봐야 갈린다:
#     이준희|1969-03  삼성에스디에스/사장 · 경력「삼성전자 네트워크사업부 전략마케팅팀장」
#     이준희@news     삼성SDI/대표이사 사장
#     → SDS 와 SDI 는 다른 회사다. 이름과 직급만 보면 같아 보인다.

_VERDICT_TABLE = """
CREATE TABLE IF NOT EXISTS person_merge_verdicts (
    key_a      TEXT NOT NULL,
    key_b      TEXT NOT NULL,
    same       BOOLEAN NOT NULL,
    reason     TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key_a, key_b)
)
"""

_PAIRS = """
MATCH (p:Person) WITH p.name AS nm, collect(p) AS ps WHERE size(ps) > 1
UNWIND ps AS p
OPTIONAL MATCH (p)-[r:IS_EXECUTIVE_OF]->(c:Company)
OPTIONAL MATCH (p)-[o]-(x) WHERE type(o) <> 'IS_EXECUTIVE_OF'
OPTIONAL MATCH (p)-[e]-() WHERE e.evidence_id IS NOT NULL
RETURN nm AS name, p.person_key AS key, p.birth_year_month AS birth,
       collect(DISTINCT c.name + '/' + coalesce(r.subtype, '?'))[..4] AS roles,
       collect(DISTINCT left(coalesce(r.main_career, ''), 90))[..2] AS career,
       collect(DISTINCT coalesce(x.name, ''))[..4] AS others,
       collect(DISTINCT e.evidence_id)[..3] AS ev
"""


def _evidence(ids: list[str]) -> list[str]:
    """근거 문장을 ChromaDB 에서 꺼낸다.

    ★이게 없으면 모델이 못 가른다(2026-08-15 실측). 소속·경력만 줬을 때
      두 쌍 다 「소속과 경력이 다름」으로 거절했는데, 근거를 읽으면 둘 다
      같은 사람이었다:

          이재용@news  「국민연금은 … **이 회장**과 삼성물산 측에게 손해배상 소송」
                       → 「이 회장」이 삼성전자 회장을 가리킨다
          이준희@news  기사 제목 「삼성전자 부진에 깊어지는 **삼성SDS** 고민」
                       → 노드에 붙은 「삼성SDI」가 오추출이었다
    """
    ids = [i for i in dict.fromkeys(ids or []) if i]
    if not ids:
        return []
    try:
        import chromadb
        from app.core.config import CHROMA_HOST, CHROMA_PORT
        col = chromadb.HttpClient(host=CHROMA_HOST,
                                  port=CHROMA_PORT).get_collection("evidence")
        return [d for d in col.get(ids=ids, include=["documents"])["documents"] if d]
    except Exception:
        return []          # 근거를 못 읽어도 판정 자체는 진행한다

_SYSTEM = """당신은 기업 지식그래프에서 **이름이 같은 두 사람이 같은 인물인가**를
판정합니다.

한국은 동명이인이 흔합니다. 실제로 이 그래프에서 동명 쌍의 37%가 다른 사람이었습니다.

【같은 사람으로 볼 근거】
· 소속 회사가 같거나 **같은 기업집단**이다 (지주사↔자회사 겸직이 흔합니다)
     「LX그룹 회장」 / 「LX세미콘 회장」        → 같은 사람
· 한쪽의 주요경력이 다른 쪽의 소속을 가리킨다
     뉴스 「SK온 팩토리이노베이션 담당」 / DART 경력 「(前) SK온 설비제작 PL」

【다른 사람으로 볼 근거】
· 회사도 업종도 무관하다
     「삼성전자 사외이사」 / 「하이프라자 상무」   → 하이프라자는 LG 판매법인
· 직급·연배가 서로 안 맞는다
· ★**이름이 비슷한 다른 회사**를 같은 회사로 착각하지 마세요. 이게 가장 흔한 실수입니다
     삼성에스디에스(삼성SDS·IT서비스) ≠ 삼성SDI(배터리)
     현대차 ≠ 현대차증권 ≠ 현대건설

【★근거 문장을 반드시 읽으세요】
소속 목록보다 **근거 문장이 더 정확합니다.** 소속은 추출이 틀릴 수 있지만
근거는 원문입니다. 실제로 이렇게 갈렸습니다:

  · 「국민연금은 불법합병 문제로 **이 회장**과 삼성물산 측에 손해배상 소송을 걸었다」
      → 「이 회장」은 삼성전자 회장을 가리킵니다. 소속이 비어 있어도 같은 사람입니다.
  · 기사 제목 「삼성전자 부진에 깊어지는 **삼성SDS** 고민」인데 소속에 「삼성SDI」가
      붙어 있다면 **소속 쪽이 오추출**입니다. 근거를 믿으세요.

대명사·약칭(「이 회장」·「정 부회장」)도 근거 안에서 누구를 가리키는지 읽으세요.

【판단이 어려울 때】
확신이 없으면 **same=false**로 하세요. 다른 두 사람의 이력이 한 노드에 섞이면
되돌릴 수 없습니다. 못 합친 건 근거가 더 모이면 다시 볼 수 있습니다.

reason은 10~40자로 짧게."""

_PSCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "same": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "same", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

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


def _trim(ev: str, body: int = 200) -> str:
    """근거를 줄이되 **기사 제목은 지킨다.**

    근거는 「문장 — 「제목」 URL」 꼴이라 앞에서 자르면 제목이 통째로 날아간다.
    실측(2026-08-15): 이준희 판정이 그래서 뒤집혔다 — 문장에는 회사명이 없고
    제목 「삼성전자 부진에 깊어지는 **삼성SDS** 고민」에만 있었는데, 220자에서
    잘라 제목을 없앤 채 물으니 모델이 판단할 근거가 없었다.
    """
    m = re.search(r"「([^」]{4,80})」", ev or "")
    head = (ev or "").split("—")[0].strip()[:body]
    return f"{head}  — 기사제목「{m.group(1)}」" if m else head


def _describe(p: dict) -> str:
    bits = []
    if p["birth"]:
        bits.append(f"생년월 {p['birth']}")
    roles = [r for r in p["roles"] if r and not r.startswith("/")]
    bits.append("소속 " + (" · ".join(roles) if roles else "(임원 기록 없음)"))
    others = [o for o in p["others"] if o]
    if others:
        bits.append("관련 " + " · ".join(others[:3]))
    career = [c for c in p["career"] if c]
    if career:
        bits.append("경력 " + " | ".join(career))
    out = "  ".join(bits)
    for ev in _evidence(p.get("ev")):
        out += f"\n         근거: {_trim(ev)}"
    return out


def run_llm(dry: bool) -> int:
    """손 목록에 없는 동명 쌍을 근거와 함께 모델에 묻는다."""
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_PAIRS)]
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)

    pairs: list[tuple[dict, dict]] = []
    for name, ps in by_name.items():
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                # 1단 — 생년월이 둘 다 있고 다르면 확정된 다른 사람
                if a["birth"] and b["birth"] and a["birth"] != b["birth"]:
                    continue
                # 2단 — 사람이 이미 판단한 것은 건드리지 않는다
                if name in REVIEWED_NOT_MERGED:
                    continue
                if any(name in (k, d) or k.startswith(name) for k, d, _ in CONFIRMED):
                    continue
                pairs.append((a, b))

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_VERDICT_TABLE)
            cur.execute("SELECT key_a, key_b, same FROM person_merge_verdicts")
            cached = {(x, y): v for x, y, v in cur.fetchall()}
        todo = [p for p in pairs
                if tuple(sorted((p[0]["key"], p[1]["key"]))) not in cached]

        print(f"■ 모델 판정 — 동명 쌍 {len(pairs)}개 "
              f"(이미 판정 {len(pairs) - len(todo)} · 물을 것 {len(todo)})")
        for a, b in pairs:
            print(f"\n  {a['name']}")
            print(f"    A {a['key'][:22]:<24}{_describe(a)[:96]}")
            print(f"    B {b['key'][:22]:<24}{_describe(b)[:96]}")
        if dry or not todo:
            if dry:
                print("\n[dry-run] 묻지 않았습니다.")
            return 0

        lines = [f"- {a['name']}\n    A: {_describe(a)}\n    B: {_describe(b)}"
                 for a, b in todo]
        # ★여기만 gpt-4o 를 쓴다(2026-08-15). 대명사·약칭을 근거 안에서 풀어야 하는
        #   일이라 라우터·판정용 mini 로는 안 됐다 — 근거를 줘도 두 쌍 다
        #   「소속과 경력이 다름」이라는 같은 문장으로 거절했다.
        #   동명 쌍은 한 자리 수라 값이 문제되지 않는다(쌍당 약 15원).
        got = ask_json(_SYSTEM, "\n".join(lines), schema=_PSCHEMA,
                       name="person_same", fallback={"items": []},
                       model="gpt-4o")
        verdicts = {it["name"]: it for it in got.get("items", [])}

        merged = 0
        with neo4j_session() as s, conn.cursor() as cur:
            for a, b in todo:
                v = verdicts.get(a["name"])
                if not v:
                    continue
                ka, kb = sorted((a["key"], b["key"]))
                cur.execute("INSERT INTO person_merge_verdicts (key_a, key_b, same, reason) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT (key_a, key_b) DO UPDATE "
                            "SET same = EXCLUDED.same, reason = EXCLUDED.reason",
                            (ka, kb, v["same"], v["reason"]))
                mark = "✓ 같은 사람" if v["same"] else "· 다른 사람"
                print(f"  {mark}  {a['name']:<8}{v['reason'][:46]}")
                if not v["same"]:
                    continue
                # 생년월이 있는 쪽을 남긴다 — 키가 안정적이다
                keep, drop = (a, b) if a["birth"] else (b, a)
                k = s.run(_LOOK, key=keep["key"]).single()
                d = s.run(_LOOK, key=drop["key"]).single()
                if k and d:
                    s.run(_MERGE, keep_id=k["id"], drop_id=d["id"],
                          drop_key=drop["key"])
                    merged += 1
            if merged:
                from batch.repair.node_identity import unlist_scalars
                unlist_scalars(s)
        print(f"\n✅ 모델 판정으로 {merged}쌍 병합")
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--llm", action="store_true",
                    help="손 목록에 없는 동명 쌍을 근거와 함께 모델에 묻는다")
    args = ap.parse_args()

    if args.llm:
        return 0 if run_llm(args.dry_run) >= 0 else 1

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
