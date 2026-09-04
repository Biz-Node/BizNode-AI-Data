"""관계의 **종료**를 찾아 `is_current=false`로 표시한다 — 두 경로로.

관계는 맺어지기만 하는 게 아니라 끝난다. 끝난 줄 모르면 2년 전 해지된 계약이
오늘의 리스크로 화면에 뜬다. 끝났다는 신호가 두 군데서 온다:

  ① 뉴스 문장이 종료를 말한다
       "KEB하나은행이 SK하이닉스 보유주식을 **전량 매각**한 것으로 확인됐다"
         → 「KEB하나은행 -OWNS_STAKE_IN-> SK하이닉스」가 만들어져 있다.
           근거가 관계의 **끝**을 말하는데 관계를 **만든** 것이다.
       문장 뜻을 봐야 하므로 LLM이 필요하다(키워드로 좁힌 뒤 판정).

  ② DART 재적재에서 관계가 사라진다
       작년 사업보고서에 있던 지분이 올해 보고서에 없으면 그 지분은 처분된 것이다.
           2025 보고서:  삼성전자 → A(5.2%) · B(3.1%) · C(2.0%)
           2026 보고서:  삼성전자 → A(5.4%) · B(3.1%)          ← C가 사라짐
       **구조화 데이터라서 할 수 있는 일**이다. 뉴스는 「없어졌다」를 말해 주지 않는다.
       LLM이 필요 없다(비용 0).

★어느 쪽도 **지우지 않는다.** `is_current=false`만 남기면 신선도 판정이
  expired(가중 0.3)로 낮추고, 조회 기본값이 답에서 뺀다. 이력은 보존된다.

    python -m batch.audit.freshness --dry-run
    python -m batch.audit.freshness              # 둘 다
    python -m batch.audit.freshness --only dart  # 무료 검사만
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline.llm import ask_json
from pipeline.importer.evidence import fetch_texts

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
#  ① 뉴스 근거가 종료를 말하는 엣지 (LLM)
# ══════════════════════════════════════════════════════════════


_WORKERS = 8

# 「지금도 그런가」를 물을 수 있는 엣지만 대상. 순수 사건(HAS_EVENT·IMPACTS·
# ACQUIRES)은 일어난 일이라 종료 개념이 없다 — 2024년 화재를 지금 "끝났다"고
# 표시할 일이 아니다.
#
# ★`SUES`·`REGULATES`를 뒤늦게 넣었다(표본 심층검사 2026-08-02). 처음엔 「소송은
#   사건이니 종료가 없다」고 뺐는데, 화면에서 이 엣지는 **「분쟁 중」이라는 상태**로
#   읽힌다. 실측: 합의·취하·기각으로 이미 끝난 분쟁 10건이 현재 리스크로 남아
#   있었다. 「제소했다」는 사건이지만 「분쟁 관계」는 상태다.
STATE_EDGES = ["SUPPLIES_TO", "OWNS_STAKE_IN", "PARTNERS_WITH",
               "DEPENDS_ON", "IS_EXECUTIVE_OF", "COMPETES_WITH", "DEVELOPS",
               "SUES", "REGULATES"]

# 후보를 좁히는 낱말 (정밀도는 낮아도 됨 — LLM이 가른다)
#
# ★분쟁 종료 낱말이 **하나도 없었다**(표본 심층검사 2026-08-02에서 발견).
#   지분 매각·계약 해지만 보고 있어서, 끝난 소송 10건이 현재 리스크로 남아 있었다:
#       「티씨케이는 와이엠씨·와이컴과 합의에 도달, 소송을 **취하**했다」
#       「칼텍과 분쟁 **마무리**」  「소송 **각하**」  「연방대법원에서 **기각**」
#   소송은 상태형이 아니라 사건형이지만, `SUES` 엣지는 「분쟁 중」이라는 **상태**로
#   화면에 뜬다. 끝난 줄 모르면 3년 전 합의한 사건이 오늘의 리스크가 된다.
END_HINTS = (
    # 지분·거래 관계의 종료
    "전량 매각", "지분 매각", "보유주식", "처분", "철수", "해지", "해제",
    "종료", "결별", "중단", "취소", "무산", "철회", "정리했", "매각했",
    "매각하기로", "제외됐", "빠졌", "끊겼", "손 뗐", "청산", "폐업",
    # 임원 관계의 종료
    "사임", "퇴임", "물러", "사퇴",
    # 분쟁(SUES·REGULATES)의 종료 — 결과가 나오면 관계가 끝난다
    "취하", "합의", "기각", "각하", "마무리", "종결", "화해",
    "승소", "패소", "확정 판결", "선고", "무효 확정", "분쟁 종료",
)

_FIND = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $types AND coalesce(r.is_current, true)
      AND ($full OR r.ended_checked_at IS NULL)
RETURN elementId(r) AS eid, type(r) AS edge,
       coalesce(a.name,'') AS a_name, coalesce(b.name,'') AS b_name,
       coalesce(r.subtype,'') AS subtype,
       coalesce([r.evidence_id],[]) + coalesce(r.evidence_ids,[]) AS ids
"""

_MARK_ENDED = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.is_current = false, r.ended_reason = $why,
    r.ended_checked_at = datetime()
"""

_MARK_OK = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
            "SET r.ended_checked_at = datetime()")

_SYSTEM = """근거 문장이 이 관계의 **성립**을 말하는지 **소멸**을 말하는지 판정하세요.

지식그래프에 「A -관계-> B」가 있고, 그 근거 문장이 주어집니다.
문장에 매각·중단·해지 같은 낱말이 있어서 후보로 뽑힌 것인데,
**실제로 이 관계가 끝났다는 뜻인지**를 봐야 합니다.

【ended=true — 이 관계가 끝났다】
· A가 B에 대해 갖던 것을 **A가** 처분·중단·해지했다
    "SK스퀘어가 11번가 지분을 매각했다"        → SK스퀘어-11번가 지분관계 종료
    "칸토덴카가 육불화텅스텐 생산을 영구 중단"   → 공급관계 종료
    "김OO 대표가 사임했다"                    → 임원관계 종료

【★소송(SUES)의 종료 — 결과가 나오면 끝난 것】
소송은 **결말이 나면 관계가 끝난다.** 3년 전 합의한 사건이 오늘의 리스크로
남아 있으면 안 된다.

  ended=true   "합의에 도달, 소송을 **취하**했다"
               "특허 분쟁 **종결**"  "분쟁 **마무리**"
               "연방대법원에서 **기각**되면서 손해배상액을 지급해야 된다"  ← 확정됐다
               "법원이 **각하**했다"  "**승소** 판결이 확정됐다"

  ended=false  "1심에서 기각됐으나 **항소**했다"          ← 아직 진행 중
               "**상고**했다"  "**항소심**이 진행 중"
               "합의를 **추진** 중"  "취하를 **검토**"     ← 아직 안 끝났다
               "**맞소송**을 제기했다"                   ← 오히려 확대됐다

  판단 기준: **더 다툴 절차가 남았는가.** 남았으면 false.

【★★REGULATES는 특히 조심하세요 — 정반대로 읽기 쉽습니다】
규제 기관이 **무언가를 중단·금지시키는 것은 규제의 실행**이지 규제 관계의 끝이
아닙니다. 실제로 이렇게 틀렸습니다:

  ✗ 잘못된 판정 (전부 ended=false여야 합니다)
      "네덜란드 정부가 EUV 장비의 대중 수출을 **중단**했다"
        → 「중단」은 수출을 막은 것. **규제를 실행한 것**이다.
      "광주노동청이 부분 작업 **중지 명령**을 내렸다"
        → 명령을 내린 것은 규제의 **시작**이다.
      "법원이 가처분을 인용해 분할 추진에 **제동**이 걸렸다"
        → 규제가 **강화**됐다.

  ✓ REGULATES가 정말 끝나는 경우
      "공정위가 **무혐의**로 결론 내렸다"   "조사를 **종결**했다"
      "제재를 **해제**했다"  "수출 규제 대상에서 **제외**됐다"
      "과징금 처분이 법원에서 **취소**됐다"

  판단 기준: **규제 기관이 손을 뗐는가.** 규제 대상의 행위가 멈춘 것이 아니라.

【★OWNS_STAKE_IN에서 흔한 오판】
  "A가 B의 지분 66.6%를 **인수**했다"
    → A가 **가지게 된** 것이다. A→B 지분 관계는 **생긴** 것이지 끝난 게 아니다.
  종료는 **판 쪽**에서만 성립한다: "A가 보유하던 B 지분을 전량 매각했다"

【★DEVELOPS는 웬만하면 ended=false】
「개발 **완료**」「양산 **개시**」「분쟁 **종결**」은 그 관계가 **성립했다**는 뜻이지
끝났다는 뜻이 아닙니다. 만든 제품은 계속 그 회사 것입니다. 실제로 이렇게 틀렸습니다:

  ✗ "테크윙이 HBM용 큐브프로버를 개발 **완료**했다"      → 성립이다. ended=false
  ✗ "특허 분쟁이 **종결**돼 티씨케이가 SiC링 독점 지위 유지" → 유지다. ended=false

  ✓ DEVELOPS가 정말 끝나는 경우
      "해당 제품 라인을 **단종**했다"  "그 사업에서 **철수**했다"
      "생산을 **영구 중단**했다"

★사유를 먼저 적고 판정하세요. **사유에 「성립」·「유지」라고 써 놓고 ended=true를
  고르면 안 됩니다.** 둘이 어긋나면 사유 쪽을 믿고 다시 정하세요.

【ended=false — 끝나지 않았다】
· **제3자**가 처분한 것이고 A와 B의 관계는 그대로거나 오히려 강화됨
    "대주주 지분 매각으로 SK하이닉스가 2대 주주로 올라섰다"
      → 판 것은 대주주. SK하이닉스-키옥시아 관계는 **강화**됐다
· 낱말이 **다른 대상**에 걸린 경우
    "한솔테크닉스는 삼성전자에 납품 중이다. …다른 사업은 정리했다"
      → 「정리했다」는 다른 사업 얘기
· 종료 **예정·검토·가능성**만 언급 (아직 끝나지 않음)
    "매각을 검토 중", "철수할 수도", "해지 가능성"
· 관계와 무관한 문장

【★근거 형식 — 제목도 근거입니다】
근거는 「본문 문장 + 「기사 제목」 + URL」 형태로 주어집니다.
**제목이 종료를 말하면 본문 시제보다 제목을 따르세요.** 본문은 관계가 있었던
과거 상태를 서술하고, 제목이 그 관계의 끝을 알리는 경우가 흔합니다.

실제로 이것 때문에 같은 기사에서 판정이 갈린 적이 있습니다:
    제목: 「삼성전자 올해 공급망 리스트서 한솔테크닉스·미래나노텍 빠졌다」
      본문 "미래나노텍은 …납품해왔다"   → 종료로 판정 ✓
      본문 "한솔테크닉스는 …납품 중이다" → 유지로 판정 ✗ (제목을 무시)
    두 관계는 **같은 기사에서 같이 끝난 것**이므로 판정도 같아야 합니다.

【중요】
· **애매하면 ended=false.** 잘못 종료 표시하면 실재하는 관계가 답변에서 빠집니다.
· 누가 무엇을 처분했는지 **주체를 반드시 확인**하세요. 가장 흔한 오판입니다.
· 다만 제목이 명시적으로 종료·탈락·해지를 말하면 그것은 「애매함」이 아닙니다."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "ended": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["ended", "reason"],
    "additionalProperties": False,
}


def _judge(item: tuple[dict, str]) -> tuple[dict, dict]:
    row, ev = item
    user = (f"관계: 「{row['a_name']}」 -[{row['edge']}"
            f"{'/' + row['subtype'] if row['subtype'] else ''}]-> "
            f"「{row['b_name']}」\n\n근거:\n{ev[:1200]}")
    # 실패 시 fallback은 **안전한 쪽** — 종료로 표시하지 않는다(실재 관계 보호)
    return row, ask_json(_SYSTEM, user, schema=_SCHEMA, name="ended",
                         fallback={"ended": False, "reason": ""})


def run_news(*, dry_run: bool, full: bool) -> int:
    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND, types=STATE_EDGES,
                                             full=full)]
    print(f"상태형 엣지 {len(rows):,}건 (미검사분)")
    if not rows:
        return 0

    # ── 1차: 낱말로 후보 좁히기 (무료) ──────────────────────
    texts = fetch_texts([i for r in rows for i in r["ids"] if i])

    cands: list[tuple[dict, str]] = []
    for r in rows:
        ev = "\n".join(texts.get(i, "") for i in r["ids"] if texts.get(i))
        if ev and any(k in ev for k in END_HINTS):
            cands.append((r, ev))
    print(f"[1차 · 무료] 종료 표현이 있는 후보 {len(cands)}건 "
          f"({len(cands)/max(len(rows),1)*100:.1f}%)")
    if not cands:
        print("종료 후보가 없습니다.")
        return 0

    # ── 2차: LLM 판정 ────────────────────────────────────
    print(f"[2차 · LLM] {len(cands)}건 판정 (약 {len(cands)*0.3:.0f}원)")
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(_judge, cands))

    ended = [(r, v) for r, v in results if v.get("ended") and not v.get("failed")]
    failed = [(r, v) for r, v in results if v.get("failed")]
    print(f"\n  → 종료 확인 {len(ended)}건 · 유지 "
          f"{len(results)-len(ended)-len(failed)}건"
          + (f" · 판정실패 {len(failed)}건" if failed else ""))
    for r, v in ended:
        print(f"  ○ ({r['a_name'][:18]}) -[{r['edge']}]-> ({r['b_name'][:20]})")
        print(f"      {v.get('reason','')[:88]}")

    if not dry_run:
        with neo4j_session() as session:
            for r, v in ended:
                session.run(_MARK_ENDED, eid=r["eid"],
                            why=v.get("reason", "")[:200])
            ok = [r["eid"] for r, v in results if not v.get("failed")]
            if ok:
                session.run(_MARK_OK, eids=ok)
        print(f"\n✅ {len(ended)}건 is_current=false "
              f"(삭제하지 않음 — 신선도 판정이 expired·가중 0.3으로 낮춥니다)")
    else:
        print("\n[dry-run] 변경 없음")
    return 0


# ════════════════════════════════════════════════════════════
#  ② DART 재적재에서 사라진 관계 (LLM 없음)
# ════════════════════════════════════════════════════════════


# 같은 적재 배치로 볼 시간 여유. 배치가 길어지면 시작·끝 시각이 벌어질 수 있다.
_GRACE_HOURS = 6

_STATS = """
MATCH (a:Company)-[r]->(b)
WHERE r.source_type = 'dart' AND r.loaded_at IS NOT NULL
RETURN count(*) AS with_stamp
"""

# 기업별 최신 적재 시각을 구하고, 그보다 확실히 오래된 엣지를 찾는다.
_FIND_MISSING = """
MATCH (a:Company)-[r]->(b)
WHERE r.source_type = 'dart' AND r.loaded_at IS NOT NULL
WITH a, max(r.loaded_at) AS newest
MATCH (a)-[r2]->(c)
WHERE r2.source_type = 'dart' AND coalesce(r2.is_current, true)
  AND (r2.loaded_at IS NULL
       OR datetime(r2.loaded_at) < datetime(newest) - duration({hours: $grace}))
RETURN elementId(r2) AS eid, coalesce(a.name,'') AS a_name,
       type(r2) AS edge, coalesce(c.name,'') AS b_name,
       coalesce(r2.subtype,'') AS subtype,
       toString(r2.loaded_at) AS loaded, toString(newest) AS newest
ORDER BY a_name, edge
"""

_EXPIRE = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.is_current = false,
    r.ended_reason = $why,
    r.ended_checked_at = datetime()
"""


def run_dart(*, dry_run: bool, grace_hours: int) -> int:
    with neo4j_session() as session:
        stamped = session.run(_STATS).single()["with_stamp"]
        total = session.run("MATCH ()-[r]->() WHERE r.source_type='dart' "
                            "RETURN count(*) AS n").single()["n"]
        print(f"DART 엣지 {total:,}건 · 적재시각(loaded_at) 있는 것 {stamped:,}건")

        if stamped == 0:
            print("\n적재시각이 기록된 엣지가 없습니다.")
            print("`loaded_at`은 2026-07-31에 도입됐고 기존 엣지엔 없습니다.")
            print("→ **다음 DART 재적재 이후에** 이 도구를 쓰세요. 지금은 비교 대상이")
            print("   없어 판정할 수 없습니다(근거 없이 종료로 몰지 않습니다).")
            return 0

        rows = [dict(r) for r in session.run(_FIND_MISSING, grace=grace_hours)]
        if not rows:
            print("\n최신 적재에서 사라진 관계가 없습니다.")
            return 0

        print(f"\n[사라진 관계] {len(rows)}건 — 최신 적재분에 나타나지 않음")
        for r in rows[:25]:
            print(f"  ○ ({r['a_name'][:18]}) -[{r['edge']}"
                  f"{'/' + r['subtype'] if r['subtype'] else ''}]-> "
                  f"({r['b_name'][:20]})")
            print(f"      마지막 적재 {str(r['loaded'])[:19]} · "
                  f"최신 {str(r['newest'])[:19]}")
        if len(rows) > 25:
            print(f"  … 외 {len(rows)-25}건")

        if dry_run:
            print("\n[dry-run] 변경 없음")
            return 0

        for r in rows:
            session.run(_EXPIRE, eid=r["eid"],
                        why=f"DART 재적재에서 누락 (최신 적재 {str(r['newest'])[:10]})")
        print(f"\n✅ {len(rows)}건 is_current=false "
              f"(삭제하지 않음 — 신선도 판정이 expired·가중 0.3으로 낮춥니다)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full", action="store_true", help="이미 검사한 것도 다시 (뉴스 경로)")
    ap.add_argument("--only", choices=["news", "dart"],
                    help="하나만 실행 (기본은 둘 다)")
    ap.add_argument("--grace-hours", type=int, default=_GRACE_HOURS,
                    help="DART 재적재 판정 유예 시간")
    args = ap.parse_args()

    if args.only in (None, "news"):
        run_news(dry_run=args.dry_run, full=args.full)
    if args.only in (None, "dart"):
        run_dart(dry_run=args.dry_run, grace_hours=args.grace_hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
