"""동명 때문에 못 좁힌 노드의 corp_code 를 **관계와 근거를 보고** 확정한다.

왜 (2026-08-15)

명부에 같은 이름의 법인이 여럿이라 어느 회사인지 못 정한 노드가 45곳이다.
지금은 **번호 하나가 붙어 있고 후보 목록도 함께** 들고 있다 — 「이 번호 믿지
마라」는 표시다. 그런데 그대로 두면 화면이 엉뚱한 회사의 재무를 보여 준다.

    「세종」   명부에 9곳   현재 01315732 (갱신 2018-06-04, 가장 오래된 것)

그런데 **관계 자체가 어느 회사인지 말해 준다**:

    세종      OWNS_STAKE_IN→세종텔레콤 · 김형진 · 현대차증권
              김형진은 세종텔레콤 회장 → 세종텔레콤 계열이다
    태성산업   OWNS_STAKE_IN→배해동 · 배성우 · 배진형
              일가 지분 구조가 특정 회사를 가리킨다
    리벨리온   케이티 · 사피온코리아 · 삼성전자 → AI 반도체 스타트업

  엣지가 1개뿐인 노드가 27곳이라, 근거 문장 하나만 읽으면 가려지는 것이 많다.

판정은 **세 갈래**다. 이게 핵심이다.

    ① 후보 중 하나다     → corp_code 확정 · 후보 목록 삭제
    ② 아무도 아니다      → corp_code 제거 ★해외·미등록 기업
    ③ 판단 불가         → 그대로 둔다

  ②를 안 가르면 **해외 기업이 한국 소기업으로 둔갑한다.** 실제로 그 상태였다:

      「스페이스」  OWNS_STAKE_IN→피터 틸 · 일론 머스크   ← SpaceX 다
                  DART 명부에 없는데 이름만 같은 한국 소기업 3곳이 후보로 잡혔다

★확신이 없으면 ③으로 둔다. 틀린 번호를 확정하면 그 회사의 재무·공시가
  통째로 잘못 붙고, 후보 목록을 지워 버리면 되돌릴 근거도 사라진다.

실행:
    python -m batch.repair.corp_code_resolve --dry-run
    python -m batch.repair.corp_code_resolve
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import neo4j_session, postgres_connection
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_BATCH = 8          # 근거가 길어 한 번에 적게

# ★후보를 **저장하지 않고 이름으로 다시 계산한다**(2026-08-15).
#   `candidate_corp_codes` 는 명부에서 언제든 똑같이 나오는 파생값이라 노드에서
#   뺐다(실측 37/37 일치). 그래서 여기서는 「번호가 없는 국내 후보 노드」를
#   집어 놓고 `resolver.candidates()` 로 후보를 만든다.
#
#   해외로 확정된 노드는 제외한다 — 명부에 있을 리가 없다.
_FIND = """
MATCH (c:Company)
WHERE c.corp_code IS NULL AND coalesce(c.entity_kind, '') <> '해외'
OPTIONAL MATCH (c)-[r]-(o)
WITH c, collect(DISTINCT type(r) + '→' + coalesce(o.name, ''))[..8] AS rels,
     collect(DISTINCT r.evidence_id)[..3] AS ev, count(r) AS deg
WHERE deg > 0
RETURN c.norm_name AS key, c.name AS name, rels, ev, deg
ORDER BY deg DESC
"""

_SET_RESOLVED = """
MATCH (c:Company {norm_name: $key})
SET c.corp_code = $code, c.corp_code_resolved_by = 'llm', c.corp_code_why = $why
REMOVE c.candidate_corp_codes, c.candidate_count, c.resolution_note
"""

# ★번호를 떼면 노드 식별자가 norm_name 으로 바뀐다. 해외·미등록이라는 뜻이므로
#   `entity_kind` 도 맞춘다 — 그래야 「국내 기업만」 필터에서 안 걸린다.
_SET_NONE = """
MATCH (c:Company {norm_name: $key})
SET c.corp_code_why = $why,
    c.entity_kind = CASE WHEN c.entity_kind IN ['기업','불명'] THEN '해외'
                         ELSE c.entity_kind END
REMOVE c.corp_code, c.candidate_corp_codes, c.candidate_count, c.resolution_note
"""

_SYSTEM = """당신은 기업 지식그래프에서 **이 노드가 명부의 어느 법인인가**를
가려내는 도구입니다.

같은 이름의 법인이 명부에 여럿이라 자동으로 못 정했습니다. 그 노드가 실제로
맺고 있는 **관계와 근거 문장**을 보고 판정하세요.

【판정 세 가지】
· matched      후보 중 하나가 맞다 → 그 corp_code 를 고르세요
· none         후보 중 아무도 아니다 → corp_code=null
· unsure       판단 불가 ★**대부분 여기입니다**

【★none 은 아주 좁게 쓰세요】
**근거가 그 회사를 명시적으로 해외 기업이라 말할 때만** none 입니다.

  ✓ none  근거「스페이스X는 일론 머스크가 설립한 세계 최대 민간 우주기업」
          → 근거가 해외 기업임을 직접 말합니다

  ✗ none  「이 관계는 한국 법인과 무관해 보인다」   ← **추측입니다**
  ✗ none  「일본 아데카의 자회사이므로」            ← 자회사는 한국 법인일 수 있습니다.
                                              「◯◯코리아」는 대개 한국 법인입니다
  ✗ none  「삼성전자가 자회사라는 관계는 비현실적」   ← 관계 방향을 오해한 것입니다.
                                              A -OWNS_STAKE_IN-> B 는 A가 B의 주주라는 뜻입니다
  ✗ none  후보가 몇 곳인지 · 상장 여부만 보고 판단   ← 명부에는 비상장 중소기업이
                                              대부분이라 정보가 없는 게 정상입니다

  ★한국 회사가 명부에 **여러 곳 있는 것은 지극히 정상**입니다. 「어느 곳인지
    모르겠다」는 unsure 이지 none 이 아닙니다. none 으로 하면 그 회사의
    DART 연결이 통째로 끊겨 재무·공시를 영영 못 붙입니다.

【matched 로 볼 근거】
· 관계 상대가 그 회사의 **알려진 계열사·주주·임원**이다
      「세종」 ← 김형진(세종텔레콤 회장)·세종텔레콤이 주주 → 세종텔레콤 계열
· 후보의 **업종·상장 여부**가 관계와 맞아떨어진다
      반도체 장비를 공급한다면 후보 중 제조업 법인이지 부동산 법인이 아니다
· 후보가 **한 곳뿐**이고 관계와 모순되지 않는다

【확신이 없으면 unsure】
틀린 번호를 확정하면 **그 회사의 재무·공시가 통째로 잘못 붙습니다.**
못 정한 것은 나중에 근거가 더 모이면 다시 볼 수 있습니다.

why 는 20~50자로 왜 그렇게 봤는지 적으세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "verdict": {"type": "string",
                                "enum": ["matched", "none", "unsure"]},
                    "corp_code": {"type": ["string", "null"]},
                    "why": {"type": "string"},
                },
                "required": ["name", "verdict", "corp_code", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _master(codes: list[str]) -> dict[str, tuple]:
    """후보 corp_code → (이름, 종목코드, 갱신일). 명부에서 읽는다."""
    codes = [str(c).strip() for c in codes if c]
    if not codes:
        return {}
    with postgres_connection() as conn, conn.cursor() as cur:
        ph = ",".join(["%s"] * len(codes))
        cur.execute(f"SELECT corp_code, corp_name, stock_code, market, modify_date "
                    f"FROM corp_code_master WHERE corp_code IN ({ph})", codes)
        return {r[0].strip(): r[1:] for r in cur.fetchall()}


def _evidence(ids) -> list[str]:
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
        return []


def _render(rows: list[dict]) -> str:
    out = []
    for r in rows:
        info = _master(list(r["cands"]))
        cands = []
        for c in [str(x).strip() for x in r["cands"]]:
            m = info.get(c)
            if not m:
                continue
            nm, sc, mk, md = m
            cands.append(f"      {c}  {nm}  상장={sc or '-'}  갱신={md}")
        block = [f"- {r['name']}  (명부 후보 {r['n']}곳)",
                 f"    관계: {' · '.join(r['rels']) or '(없음)'}"]
        for ev in _evidence(r["ev"])[:2]:
            block.append(f"    근거: {ev[:200]}")
        block.append("    후보:")
        block += cands
        out.append("\n".join(block))
    return "\n\n".join(out)


# ★판정을 캐시한다(2026-08-15). 안 그러면 배치를 다시 돌릴 때마다 같은 45곳을
#   다시 묻는다 — 근거가 늘지 않았으면 답도 같다.
#   `--recheck` 로 캐시를 무시할 수 있다(프롬프트를 고친 뒤에 쓴다).
_VERDICT_TABLE = """
CREATE TABLE IF NOT EXISTS corp_code_verdicts (
    node_key    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    verdict     TEXT NOT NULL,      -- matched | none | unsure
    corp_code   CHAR(8),
    why         TEXT,
    -- 판정 당시의 엣지 수. **근거가 늘었을 때만 다시 묻기 위한** 값이다.
    -- 없으면 `unsure` 를 매번 다시 물어 같은 답에 돈을 쓴다.
    deg         INT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_ADD_DEG = "ALTER TABLE corp_code_verdicts ADD COLUMN IF NOT EXISTS deg INT"
_SAVE_VERDICT = """
INSERT INTO corp_code_verdicts (node_key, name, verdict, corp_code, why, deg)
VALUES (%s,%s,%s,%s,%s,%s)
ON CONFLICT (node_key) DO UPDATE SET verdict = EXCLUDED.verdict,
    corp_code = EXCLUDED.corp_code, why = EXCLUDED.why,
    deg = EXCLUDED.deg, decided_at = now()
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--recheck", action="store_true",
                    help="이미 판정한 것도 다시 묻는다 (프롬프트를 고친 뒤)")
    args = ap.parse_args()

    from pipeline.normalizer.resolver import candidates as _cands
    with neo4j_session() as s:
        rows = [dict(r) for r in s.run(_FIND)]
    # 후보를 이름으로 만든다. 후보가 없으면 애초에 물을 게 없다(해외·미등록).
    for r in rows:
        cs = _cands(r["name"])
        r["cands"] = [c.corp_code for c in cs[:10]]
        r["n"] = len(cs)
    rows = [r for r in rows if r["cands"]]

    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_VERDICT_TABLE)
        cur.execute(_ADD_DEG)
        cur.execute("SELECT node_key, verdict, coalesce(deg, 0) FROM corp_code_verdicts")
        cached = {k: (v, d) for k, v, d in cur.fetchall()}
    if not args.recheck:
        before = len(rows)
        def _skip(r):
            hit = cached.get(r["key"])
            if not hit:
                return False
            verdict, deg = hit
            if verdict in ("matched", "none"):
                return True            # 확정 — 이미 노드에 반영됐다
            return r["deg"] <= deg     # 보류 — 근거가 안 늘었으면 답도 같다
        rows = [r for r in rows if not _skip(r)]
        if before != len(rows):
            print(f"  (이미 확정된 {before - len(rows)}곳은 건너뜁니다 — "
                  f"다시 묻으려면 --recheck)")

    if args.limit:
        rows = rows[:args.limit]
    print(f"■ 못 좁힌 노드 {len(rows)}곳 · 약 {len(rows) * 15:,.0f}원\n")
    if not rows:
        return 0
    if args.dry_run:
        print(_render(rows[:2]))
        print("\n[dry-run] 묻지 않았습니다.")
        return 0

    tally = {"matched": 0, "none": 0, "unsure": 0}
    with neo4j_session() as s, postgres_connection() as pg:
        for i in range(0, len(rows), _BATCH):
            chunk = rows[i:i + _BATCH]
            got = ask_json(_SYSTEM, _render(chunk), schema=_SCHEMA,
                           name="corp_code_resolve", fallback={"items": []},
                           model="gpt-4o")
            by_name = {r["name"]: r for r in chunk}
            for it in got.get("items", []):
                src = by_name.get(it["name"])
                if not src:
                    continue
                v, code, why = it["verdict"], (it.get("corp_code") or "").strip(), it["why"]
                tally[v] = tally.get(v, 0) + 1
                if v == "matched" and code in {str(c).strip() for c in src["cands"]}:
                    s.run(_SET_RESOLVED, key=src["key"], code=code, why=why)
                    mark = f"✓ 확정 {code}"
                elif v == "none":
                    s.run(_SET_NONE, key=src["key"], why=why)
                    mark = "✗ 후보 없음 (해외·미등록)"
                else:
                    # 후보에 없는 번호를 답한 것도 보류다 — 지어낸 값을 쓰면 안 된다
                    v, code, mark = "unsure", None, (
                        "· 보류 (후보에 없는 번호를 답함)" if v == "matched"
                        else "· 판단 보류")
                with pg.cursor() as pc:
                    pc.execute(_SAVE_VERDICT, (src["key"], it["name"], v,
                                               code or None, why, src["deg"]))
                print(f"  {mark:<26}{it['name'][:12]:<14}{why[:44]}")

    print(f"\n확정 {tally['matched']} · 후보없음 {tally['none']} · 보류 {tally['unsure']}")
    with neo4j_session() as s:
        left = s.run("""MATCH (c:Company) WHERE c.candidate_corp_codes IS NOT NULL
                        RETURN count(*) AS n""").single()["n"]
        both = s.run("""MATCH (c:Company)
            WHERE c.corp_code IS NOT NULL AND c.candidate_corp_codes IS NOT NULL
            RETURN count(*) AS n""").single()["n"]
    print(f"남은 미확정 {left}곳 · 그중 번호가 붙어 있는 것 {both}곳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
