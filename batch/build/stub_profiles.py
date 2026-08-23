"""stub 회사 2,267곳에 **정체**를 붙인다 — 「이게 무슨 회사인지」.

왜 필요한가 (2026-08-02)

그래프에 회사가 2,331곳 있는데 **속을 아는 건 64곳뿐**이다. 나머지 2,267곳은
관계를 만들다 이름만 보고 생긴 자리(stub)라 `sector`도 `induty`도 **전부 비어 있다**.
화면에서는 이렇게 보인다:

    엔비디아          ← 이게 어디 회사인지, 뭐 하는 곳인지 한 줄도 없다
    삼성자산운용      ← 회사인지 펀드인지 기관인지도 모른다
    한국전자기술연구원 ← 연구소인데 「기업」으로 그려진다

연결이 가장 많은 stub 상위 8곳이 마이크론·엔비디아·TSMC·CXMT·인텔·AMD·애플·YMTC —
**전부 해외 기업**이다. 즉 DART만으로는 제일 중요한 자리를 못 채운다.

그래서 두 단계로 채운다

  1단계 · DART 기업개황 (무료, 764곳)
      corp_code가 있는 국내 stub만. `induty_code`·`corp_cls`·`est_dt`·`ceo_nm`을
      **공식 출처**로 채운다. 부수 소득이 있다 — `corp_cls`가 Y/K/N이면 그 stub은
      **상장사**다. 즉 「나중에 본격 수집할 후보」가 자동으로 추려진다.

  2단계 · LLM 한 줄 라벨 (~80원, 전건)
      DART는 업종을 코드(`"29271"`)로만 준다. 사람이 읽을 수 없고 검색어와도
      안 닿는다. 그래서 **그래프에 이미 있는 관계를 근거로** 한 줄을 짓게 한다:

          마이크론 ← [SUPPLIES_TO→엔비디아] [심텍-SUPPLIES_TO→]
              ⇒ kind=기업, "미국 메모리 반도체 제조사"

      회사명만 주면 지어낸다. 관계를 함께 줘야 근거가 생긴다.

★지어낸 값과 확인된 값을 섞지 않는다
  `sector_source`를 반드시 남긴다 — `dart`(공식) / `llm`(추론). LLM이 확신 못 하면
  `sector_confidence='low'`가 붙고, 화면은 낮은 것을 회색으로 흘리면 된다.
  「삭제보다 표시」 — 모르는 걸 비워 두는 대신 모른다고 적는다.

    python -m batch.build.stub_profiles --dart-only          # 1단계만 (무료)
    python -m batch.build.stub_profiles --dry-run --limit 50 # 2단계 미리보기
    python -m batch.build.stub_profiles                      # 전체
"""

from __future__ import annotations

import argparse
import sys
import time

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer.ksic import KSIC_CHOICES, KSIC_DIVISIONS
from pipeline.extractors.dart.company_info import (
    CORP_CLS_MARKET,
    fetch_company_info,
    to_iso_date,
)
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_BATCH = 25          # 한 번에 분류할 회사 수
_DART_SLEEP = 0.05   # DART 초당 호출 제한 여유

# ─────────────────────────── 1단계 · DART ───────────────────────────

# ★「이미 채운 곳」 판정이 **노드가 아니라 PG** 를 본다(2026-08-15).
#   `induty` 를 PG 로 옮겼으므로 노드에서 그걸 찾으면 전건이 매번 대상이 된다.
_NEED_DART = """
MATCH (c:Company)
WHERE coalesce(c.is_stub, false) AND c.corp_code IS NOT NULL
  AND ($refresh OR NOT c.corp_code IN $done)
RETURN c.corp_code AS corp_code, c.name AS name
"""

_DONE_DART = ("SELECT corp_code FROM company_attributes "
              "WHERE corp_code IS NOT NULL AND induty IS NOT NULL")

# ★노드에는 **탐색·표시에 쓰는 것만** 남긴다(2026-08-15).
#   `induty`·`est_dt`·`ceo_nm`·`name_en` 은 경로를 따라갈 때 안 쓰고 상세 화면에서
#   한 건씩 볼 뿐이라 PostgreSQL `company_attributes` 로 간다. 여기서 노드에 다시
#   쓰면 정리한 게 되돌아온다 — 실제로 그런 적이 있다.
_SET_DART = """
MATCH (c:Company {corp_code: $corp_code})
SET c.market = coalesce($market, c.market)
"""

# 상세 속성은 PG 로. 노드 식별자(corp_code 또는 norm_name)를 키로 쓴다.
_SET_DART_PG = """
INSERT INTO company_attributes (node_key, corp_code, name, induty, est_dt, ceo_nm, name_en)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (node_key) DO UPDATE SET
    corp_code = EXCLUDED.corp_code,
    induty    = COALESCE(EXCLUDED.induty,  company_attributes.induty),
    est_dt    = COALESCE(EXCLUDED.est_dt,  company_attributes.est_dt),
    ceo_nm    = COALESCE(EXCLUDED.ceo_nm,  company_attributes.ceo_nm),
    name_en   = COALESCE(EXCLUDED.name_en, company_attributes.name_en),
    updated_at = now()
"""


def run_dart(refresh: bool, dry: bool) -> None:
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(_DONE_DART)
        done = [r[0].strip() for r in cur.fetchall()]
    with neo4j_session() as session:
        targets = [dict(r) for r in session.run(_NEED_DART, refresh=refresh, done=done)]
    print(f"■ 1단계 DART 기업개황 — 국내 stub {len(targets)}곳 (무료)")
    if dry or not targets:
        if targets:
            print("   [dry-run] 조회하지 않았습니다.")
        return

    ok = listed = 0
    with neo4j_session() as session, postgres_connection() as conn:
        for i, t in enumerate(targets, 1):
            try:
                info = fetch_company_info(t["corp_code"])
            except Exception:
                info = None
            time.sleep(_DART_SLEEP)
            if not info:
                continue
            market = CORP_CLS_MARKET.get(info.get("corp_cls", ""))
            # 노드에는 market 만 — 나머지는 상세 화면용이라 PG 로
            session.run(_SET_DART, corp_code=t["corp_code"], market=market)
            with conn.cursor() as cur:
                cur.execute(_SET_DART_PG,
                            (t["corp_code"], t["corp_code"], t["name"],
                             info.get("induty_code"), to_iso_date(info.get("est_dt")),
                             info.get("ceo_nm") or None,
                             info.get("corp_name_eng") or None))
            ok += 1
            if market in ("KOSPI", "KOSDAQ", "KONEX"):
                listed += 1
            if i % 100 == 0:
                print(f"   … {i}/{len(targets)}")
    print(f"   ✅ {ok}곳 채움 · 그중 **상장사 {listed}곳** — 본격 수집 후보입니다")


# ─────────────────────────── 2단계 · LLM ───────────────────────────

# ★대상 판정이 노드의 `sector_label` 이 아니라 **`entity_kind`·`ksic`** 을 본다
#   (2026-08-15). 설명 필드를 PG 로 옮겼으므로 노드에서 그걸 찾으면 전건이 매번
#   대상이 된다. 이 둘이 노드에 남은 「분류 결과」다.
#   `불명` 도 다시 본다 — 규칙으로 임시로 채운 값이라 LLM 이 고칠 수 있다.
_NEED_LABEL = """
MATCH (c:Company)
WHERE coalesce(c.is_stub, false)
  AND (c.entity_kind IS NULL OR c.entity_kind = '불명' OR c.ksic IS NULL OR $refresh)
OPTIONAL MATCH (c)-[r]->(o) WHERE NOT coalesce(r.grounding_suspect, false)
WITH c, collect(DISTINCT type(r) + '→' + coalesce(o.name, ''))[..5] AS out
OPTIONAL MATCH (i)-[r2]->(c) WHERE NOT coalesce(r2.grounding_suspect, false)
WITH c, out, collect(DISTINCT coalesce(i.name, '') + '-' + type(r2) + '→')[..5] AS inn
OPTIONAL MATCH (c)-[any]-()
RETURN c.name AS name, c.corp_code AS corp_code, c.norm_name AS norm_name,
       out, inn, count(any) AS deg
ORDER BY deg DESC
"""

# ★노드에는 `entity_kind`(노드를 가르는 값)와 `ksic`(탐색 필터)만 쓴다.
#   한 줄 설명(`sector_label`)과 신뢰도·출처는 PG 로 간다 — 화면 상세에서만 쓴다.
_SET_LABEL = """
UNWIND $rows AS row
MATCH (c:Company {name: row.name})
SET c.entity_kind = row.kind,
    c.ksic = coalesce(row.ksic, c.ksic)
"""

_SET_LABEL_PG = """
INSERT INTO company_attributes (node_key, corp_code, name, norm_name, sector_label)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (node_key) DO UPDATE SET
    sector_label = EXCLUDED.sector_label, updated_at = now()
"""

_SYSTEM = f"""당신은 한국 반도체·로봇 산업 그래프의 회사 정보를 정리한다.

각 이름에 대해 **정체 한 줄**을 붙인다. 근거는 함께 주는 「관계」다.
관계는 그래프에서 실제로 뽑힌 것이며, 예를 들어
  · `SUPPLIES_TO→엔비디아`  = 이 회사가 엔비디아에 **판다**
  · `심텍-SUPPLIES_TO→`     = 심텍이 이 회사에 **판다** (즉 이 회사는 고객)
  · `DEVELOPS→HBM4`         = 이 회사가 HBM4를 만든다/개발한다

kind(정체 종류)는 다음 중 하나:
  기업 · 금융기관 · 공공기관 · 대학·연구소 · 펀드·조합 · 사업부문 · 불명
  ※「사업부문」은 독립 법인이 아닌 조직이다(예: "삼성전자 MX 사업부", "DS부문").
  ※ 국민연금공단·공정거래위원회는 공공기관, 삼성자산운용은 금융기관이다.

label(한 줄)은 25자 이내. **국적·업종·역할** 순으로 간결하게.
  좋은 예: "미국 메모리 반도체 제조사" / "대만 파운드리" / "국내 반도체 후공정 장비"
  나쁜 예: "반도체 회사입니다" (설명이 없다) / "세계적인 혁신 기업" (내용이 없다)

confidence:
  high = 널리 알려진 회사이거나, 관계만으로 업종이 분명하다
  low  = 이름을 모르겠고 관계도 부족하다 → label은 관계에서 읽히는 것만 쓴다

ksic(업종 표준코드)는 **아래 목록에서 하나만** 고른다. label 은 사람이 읽는 한
줄이고, ksic 은 **묶어 세기 위한 표준축**이다 — 「IT 서비스 회사가 몇 곳이야」에
답하려면 코드가 같아야 한다.

★**label에 업종을 썼으면 ksic도 반드시 골라야 한다. 둘은 같은 정보다.**
  실제로 이렇게 어긋난 적이 있다(2026-08-12, 290곳):
      label "미국 AI 연구개발 기업"      → ksic "99"  ✗   ✓ 62 또는 70
      label "러시아 IT 기업"            → ksic "99"  ✗   ✓ 63
      label "국내 패션·라이프스타일 기업"  → ksic "99"  ✗   ✓ 47
  업종을 한 줄로 쓸 수 있다면 **모르는 게 아니다.** 가장 가까운 코드를 고른다.

  자주 헷갈리는 것:
    · AI·소프트웨어·SI            → 62 컴퓨터 프로그래밍·시스템통합
    · 포털·플랫폼·데이터 서비스     → 63 정보서비스업
    · 게임·콘텐츠 퍼블리싱         → 58 출판업
    · 펀드·투자조합·자산운용        → 64 금융업
    · 증권·보험 중개              → 66 금융·보험 관련 서비스업
    · 반도체·디스플레이·전자부품     → 26 전자부품·컴퓨터·영상·음향·통신장비
    · 로봇·공작기계·반도체 장비      → 29 기타 기계·장비
    · 순수 R&D 법인·연구소         → 70 연구개발업

**"99"는 「업종을 모르겠다」가 아니라 「이건 회사가 아니거나 정체 불명」일 때만**
쓴다(추출 오류로 생긴 문장 조각, 공장·건물 이름 등).

{KSIC_CHOICES}

**모르면 지어내지 말고 kind="불명", confidence="low", ksic="99"로 둔다.**
이름이 회사가 아니라 추출 오류로 보이면(문장 조각, 숫자 등) kind="불명"."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["기업", "금융기관", "공공기관", "대학·연구소",
                                      "펀드·조합", "사업부문", "불명"]},
                    "label": {"type": "string"},
                    "ksic": {"type": "string",
                             "enum": sorted(KSIC_DIVISIONS)},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["name", "kind", "label", "ksic", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _render(rows: list[dict]) -> str:
    out = []
    for r in rows:
        ctx = " ".join(r["out"] + r["inn"]) or "(관계 정보 없음)"
        code = ""      # `induty` 는 PG 로 갔다 — 관계만으로 판단한다
        out.append(f"- {r['name']}{code}\n    관계: {ctx}")
    return "\n".join(out)


def run_labels(refresh: bool, dry: bool, limit: int | None) -> None:
    with neo4j_session() as session:
        targets = [dict(r) for r in session.run(_NEED_LABEL, refresh=refresh)]
    if limit:
        targets = targets[:limit]
    print(f"\n■ 2단계 LLM 한 줄 라벨 — stub {len(targets)}곳 "
          f"(약 {len(targets) // _BATCH + 1}회 호출, 대략 {len(targets) * 0.035:.0f}원)")
    if not targets:
        return

    # ★dry-run은 **한 배치만** 부른다(2026-08-12).
    #   전에는 전건을 다 호출하고 기록만 안 했다 — 즉 **미리보기가 본실행과 같은
    #   값이 들었다.** 1,897곳을 미리 보려다 66원을 그냥 썼다. dry-run의 목적은
    #   「대상이 맞나 · 값이 그럴듯한가」를 보는 것이고, 그건 25곳이면 안다.
    if dry and limit is None:
        targets = targets[:_BATCH]
        print(f"   [dry-run] 앞 {len(targets)}곳만 호출합니다 "
              f"(전건을 보려면 --limit 을 주세요)")

    labelled: list[dict] = []
    failed = 0
    # ★한 배치가 끝날 때마다 **바로 기록한다.** 전건을 모았다 끝에 한 번 쓰면
    #   91번째 호출에서 죽었을 때 앞의 90번(≈80원, 10분)이 통째로 날아간다.
    #   `sector_label IS NULL` 조건이 이미 있으니 다시 돌리면 남은 것만 집는다.
    with neo4j_session() as session:
        for i in range(0, len(targets), _BATCH):
            chunk = targets[i:i + _BATCH]
            got = ask_json(_SYSTEM, _render(chunk), schema=_SCHEMA,
                           name="stub_profile", fallback={"items": []})
            if got.get("failed"):
                # ★실패는 표시하고 **기록하지 않는다** — 다음 실행이 다시 집는다
                failed += len(chunk)
                continue
            known = {c["name"]: c for c in chunk}
            done = [{"name": it["name"], "label": it["label"][:40],
                     "kind": it["kind"], "conf": it["confidence"],
                     "ksic": it.get("ksic") or "99"}
                    for it in got["items"] if it["name"] in known]
            labelled += done
            if done and not dry:
                session.run(_SET_LABEL, rows=done)
                # 한 줄 설명은 PG 로 — 상세 화면에서만 쓴다
                with postgres_connection() as conn, conn.cursor() as cur:
                    for d in done:
                        src = known[d["name"]]
                        key = src.get("corp_code") or src.get("norm_name") or d["name"]
                        cur.execute(_SET_LABEL_PG, (key, src.get("corp_code"),
                                                    d["name"], src.get("norm_name"),
                                                    d["label"]))
            if (i // _BATCH) % 10 == 0:
                print(f"   … {min(i + _BATCH, len(targets))}/{len(targets)}")

    tally: dict[str, int] = {}
    for r in labelled:
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
    lows = sum(1 for r in labelled if r["conf"] == "low")

    print(f"\n   분류 {len(labelled)}곳" + (f" · LLM 실패 {failed}곳(다음 실행에서 재시도)" if failed else ""))
    for k, n in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"     {k:12} {n}")
    print(f"   확신 낮음 {lows}곳 ({lows * 100 // max(len(labelled), 1)}%) — 화면에서 흐리게 표시하세요")

    top = [r for r in labelled if r["name"] in {t["name"] for t in targets[:14]}]
    print("\n   ── 연결이 많은 곳 ──")
    for r in top[:14]:
        mark = "" if r["conf"] == "high" else "  ⚠낮음"
        print(f"     {r['name'][:22]:24} {r['kind']:8} {r['label']}{mark}")

    if dry:
        print("\n   [dry-run] 기록하지 않았습니다.")
        return
    print(f"\n   ✅ sector_label · entity_kind · sector_confidence 기록 {len(labelled)}곳")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dart-only", action="store_true", help="무료 1단계만")
    ap.add_argument("--refresh", action="store_true", help="이미 채운 것도 다시")
    ap.add_argument("--limit", type=int, help="2단계 대상 수 제한(연결 많은 순)")
    args = ap.parse_args()

    run_dart(args.refresh, args.dry_run)
    if not args.dart_only:
        run_labels(args.refresh, args.dry_run, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
