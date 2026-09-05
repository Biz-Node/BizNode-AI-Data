"""근거-주장 정합성 전수 검사 — 「평택 공장 화재」류를 일반적으로 잡는다.

**문제의 일반형**
    근거 문장은 기사에서 정확히 인용됐는데, 노드·엣지가 그 근거와 무관하다.

    근거: "한미반도체는 TC본더 가격을 인상했으며… 인력을 철수"
    엣지: 한미반도체 -HAS_EVENT-> 「평택 공장 화재」      ← 근거에 평택도 화재도 없다

    원인은 여러 가지다(프롬프트 예시 복사·환각·잘못된 병합). 원인을 몰라도
    **결과는 같은 방식으로 검출된다** — 주장에 쓰인 고유명사가 근거에 없다.

**2단 검사** (파이프라인의 비용 원칙 그대로)
    1차 무료  : 노드 이름·subtype의 토큰이 근거 텍스트에 실제로 있는가
    2차 저가  : 1차에서 걸린 것만 LLM이 "근거가 이 관계를 뒷받침하는가" 판정

1차만으로도 이번 사례는 전부 잡힌다(근거에 '평택'이 없다). 2차는 문자로는
못 잡는 것(동의어·의역·방향 오류)을 본다.

실행:
  python -m batch.audit.grounding                 # 1차만 (무료)
  python -m batch.audit.grounding --llm           # 1차 + 2차
  python -m batch.audit.grounding --llm --apply   # 표시까지
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline import token_overlap
from pipeline.llm import ask_json
from pipeline.importer.evidence import fetch_texts
from pipeline.ontology import (
    EDGE_DEFINITIONS, VERIFY_FAILURES, claim_sentence, verdict_rules,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_GROUND_THRESHOLD = 0.34    # 핵심 토큰의 1/3도 근거에 없으면 의심

_FIND = """
MATCH (a)-[r]->(b)
WHERE r.evidence_id IS NOT NULL OR r.evidence_ids IS NOT NULL
RETURN elementId(r) AS eid, type(r) AS edge, coalesce(r.subtype,'') AS subtype,
       labels(a)[0] AS a_label, coalesce(a.name,'') AS a_name,
       labels(b)[0] AS b_label, coalesce(b.name,'') AS b_name,
       coalesce(r.evidence_id, '') AS ev,
       coalesce(r.evidence_ids, []) AS evs,
       coalesce(r.source_type, '') AS src,
       r.grounding_checked_at IS NOT NULL AS checked
"""

# 1차(토큰 대조)는 공짜라 늘 전수로 돌린다 — 정규화·개명이 뒤늦게 관계를 망가뜨리는
# 것을 잡기 위함. 돈이 드는 2차(LLM)만 **미검사분으로 제한**한다.
_MARK_OK = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
            "SET r.grounding_checked_at = datetime()")

# 이번에 통과한 엣지의 **이전 의심 표시를 지운다.** `grounding_verdict`(기사 전문
# 재검증 결과)까지 지우는 이유 — 전문 판정도 낡은 프롬프트로 내려진 것이라
# 남겨 두면 새 판정과 어긋난 채 화면에 뜬다.
#
# ★`grounding_stage1`이 빠져 있었다(2026-08-02 발견). 통과한 엣지에 **옛 1차 판정이
#   그대로 남아** 1,116건이 「stage1=unfounded인데 의심 아님」이라는 앞뒤 안 맞는
#   상태가 됐다. 검사 결과를 읽는 쪽에서는 이게 「지금 근거가 없다」로 보인다.
#   낡은 표시를 남기는 건 이 저장소가 여러 번 데인 실패 방식이라 함께 지운다.
#
#   단, `grounding_verdict`가 있는 건은 여기 안 걸린다 — 그건 「1차는 걸렸는데
#   전문으로 풀렸다」는 **2단 검사의 기록**이라 stage1이 있어야 말이 된다.
_CLEAR = """
MATCH ()-[r]->() WHERE elementId(r) IN $eids AND (
    r.grounding_suspect IS NOT NULL OR r.grounding_verdict IS NOT NULL
    OR r.grounding_stage1 IS NOT NULL)
SET r.grounding_suspect = NULL, r.grounding_reason = NULL,
    r.grounding_stage1 = NULL,
    r.grounding_verdict = NULL, r.grounding_verdict_why = NULL,
    r.retype_suspect = NULL, r.retype_hint = NULL
RETURN count(r) AS n
"""

# 위 누락으로 이미 쌓인 것을 한 번에 정리한다(멱등). 「의심도 아니고 전문 판정도
# 없는데 1차 판정만 남은」 = 통과했는데 표시만 안 지워진 것.
_CLEAR_STALE = """
MATCH ()-[r]->()
WHERE r.grounding_stage1 IS NOT NULL
  AND r.grounding_suspect IS NULL AND r.grounding_verdict IS NULL
SET r.grounding_stage1 = NULL, r.retype_suspect = NULL, r.retype_hint = NULL
RETURN count(r) AS n
"""

# ★1차 대조 자체는 `pipeline/token_overlap.py` 로 옮겼다 — `app/services/
#   claim_check.py` 가 같은 것을 쓰는데 `app` 이 `batch` 를 임포트한 전례가
#   없어서다(확인: 0곳). 여기 이름은 그대로 둔다 — 이 모듈의 나머지와
#   `batch/audit/selftest.py` 가 계속 쓴다.
_STOP = token_overlap.STOP
_tokens = token_overlap.tokens
grounding = token_overlap.overlap


def _fetch_evidence(rows: list[dict]) -> dict[str, str]:
    """evidence_id → 본문. 배치 분할·중복 제거는 `fetch_texts`가 한다."""
    ids = [r["ev"] for r in rows if r["ev"]]
    ids += [e for r in rows for e in r["evs"] if e]
    return fetch_texts(ids)


# ★프롬프트가 얇으면 검증기가 **양쪽으로** 틀린다. 유형 정의 없이 supported
#   예/아니오만 물었더니 「고객사」를 공급으로 못 읽고(거짓 음성), 수익률 비교를
#   경쟁으로 읽었다(거짓 양성). 정의와 실패 사례는 `pipeline/ontology.py`에
#   모아 두고 추출기·재검증기와 같은 문장을 쓴다.
_SYSTEM = f"""근거 문장이 이 관계를 **뒷받침하는지** 판정하세요.

지식그래프의 엣지 하나와 그 근거를 받습니다.
근거만 읽고, 그 엣지가 근거에서 실제로 도출되는지 보세요.

【★엣지 유형의 뜻 — 이 정의로만 판단하세요】
{EDGE_DEFINITIONS}

{verdict_rules()}

{VERIFY_FAILURES}"""

# ★`finding`을 **verdict보다 먼저** 채우게 한다. 근거가 말하는 역할을 자기 말로
#   적고 나면 방향을 훨씬 덜 틀린다. 순서를 바꾸면(verdict가 앞) 효과가 사라진다 —
#   JSON을 앞에서부터 생성하므로 판정을 먼저 뱉고 사유를 끼워 맞추기 때문이다.
_SCHEMA = {
    "type": "object",
    "properties": {
        "finding": {"type": "string",
                    "description": "근거가 말하는 관계를 한 문장으로. "
                                   "누가 무엇을 누구에게 하는지 역할을 밝혀 쓰세요. "
                                   "예: 'ASML이 SK하이닉스에 EUV 장비를 판다'"},
        "verdict": {"type": "string",
                    "enum": ["supported", "wrong_type", "unfounded", "insufficient"]},
        "actual": {"type": "string",
                   "description": "verdict가 wrong_type일 때 맞는 엣지 유형. 아니면 빈 문자열"},
        "reason": {"type": "string"},
    },
    "required": ["finding", "verdict", "actual", "reason"],
    "additionalProperties": False,
}


_DIR_WORDS = ("방향", "반대", "뒤바", "역방향", "주어와 목적어")


def _normalize(row: dict, v: dict) -> dict:
    """모순된 판정을 정리한다 — 프롬프트로는 못 막는 조합이 남는다.

    ★`wrong_type`인데 `actual`이 **원래 유형과 같은** 경우가 나온다. 두 가지다:
        · 방향 오류    유형은 맞고 방향만 반대 → 그대로 둔다(사유에 '방향')
        · 라벨 혼동    "경쟁이 아니다"라면서 actual=COMPETES_WITH를 쓴 것
                       → 고칠 유형이 없으니 실은 unfounded다.
      후자를 그냥 두면 `apply_retypes`가 **자기 자신으로 재분류**하려 들어
      영영 의심으로 남는다.
    """
    if v.get("failed") or v.get("verdict") != "wrong_type":
        return v
    actual = (v.get("actual") or "").strip()
    reason = v.get("reason") or ""
    if actual and actual != row["edge"]:
        return v                                    # 정상적인 유형 오류
    if any(w in reason for w in _DIR_WORDS):
        v["actual"] = row["edge"]                   # 방향 오류 — 유형은 유지
        return v
    v["verdict"] = "unfounded"                      # 고칠 유형이 없다
    v["actual"] = ""
    return v


def _verify(item: tuple[dict, str]) -> tuple[dict, dict]:
    row, ev = item
    # 화살표 표기 대신 **한국어 주장 문장**으로 묻는다 — 방향을 못 읽던 문제.
    claim = claim_sentence(row["edge"], row["a_name"], row["b_name"],
                           row.get("subtype", ""))
    user = (f"주장: {claim}\n"
            f"(엣지 유형: {row['edge']})\n\n"
            f"근거:\n{ev[:1200]}")
    # 실패 fallback은 supported(안전한 쪽)지만 `ask_json`이 **반드시** failed를
    # 붙이므로 통과와 구별된다 — 그래야 검사 완료로 기록되지 않는다.
    v = ask_json(_SYSTEM, user, schema=_SCHEMA, name="grounding",
                 fallback={"verdict": "supported", "actual": "", "reason": ""})
    return row, (v if v.get("failed") else _normalize(row, v))


# 판정별로 남기는 표시가 다르다. **셋을 뭉뚱그리면 후속 처리를 못 한다** —
# 유형만 틀린 것은 고치면 되고, 근거가 잘린 것은 기사 전문을 다시 받으면 되며,
# 근거 없는 것만 사람이 봐야 한다.
#
# ★재판정할 때 **옛 전문 판정을 함께 지운다**(2026-08-02 발견). 안 지웠더니
#   「1차=근거없음(방금) · 전문=confirmed(예전)」인 엣지가 43건 생겼고, 그 43건은
#   **전문에서 확인된 참인 관계인데 화면에서 숨겨지고** 있었다:
#       두산밥캣 -ACQUIRES/사업적 결합-> 두산로보틱스   전문 confirmed인데 숨김
#       두산에너빌리티 -OWNS_STAKE_IN/지분 46.1%-> 두산밥캣   〃
#   `_CLEAR`가 통과분에 대해 같은 이유로 verdict를 지우는데(위 주석), 실패분만
#   빠져 있었다. 지우면 `grounding_fulltext`가 **다시 집어 판정한다** — 낡은
#   판정으로 조용히 숨기는 것보다 한 번 더 확인하는 편이 맞다.
_MARK = """
MATCH ()-[r]->() WHERE elementId(r) = $eid
SET r.grounding_suspect = true,
    r.grounding_reason  = $reason,
    r.grounding_stage1  = $verdict,
    r.grounding_verdict = NULL, r.grounding_verdict_why = NULL,
    r.retype_suspect    = CASE WHEN $verdict = 'wrong_type' THEN true
                               ELSE r.retype_suspect END,
    r.retype_hint       = CASE WHEN $verdict = 'wrong_type' AND $actual <> ''
                               THEN $actual ELSE r.retype_hint END
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", action="store_true", help="2차 LLM 검증까지")
    ap.add_argument("--apply", action="store_true", help="의심 엣지에 표시 남기기")
    # ★상한을 「한 번에 볼 양」이 아니라 **한 회차 크기**로 바꿨다(2026-08-03).
    #
    #   전에는 400건에서 잘리고 나머지는 다음 실행으로 밀렸다. 그런데 다음 실행이
    #   언제일지 정해져 있지 않아 **미검증분이 쌓인다.** 실측: 재수집 2개사만 했는데
    #   신규 엣지 592건 중 146건(25%)이 검증 없이 남았다. 확장할수록 더 벌어진다.
    #
    #   검증은 엣지당 0.3원이라 다 봐도 몇백 원이다. 잘라서 아끼는 돈보다
    #   **「검증했다」와 「안 했다」가 섞이는 손해**가 훨씬 크다.
    #   이제 상한은 회차 크기이고, 남은 게 있으면 회차를 반복한다.
    ap.add_argument("--limit", type=int, default=400,
                    help="한 회차에 검증할 건수 (남으면 회차를 반복한다)")
    ap.add_argument("--max-rounds", type=int, default=12,
                    help="회차 상한 — 폭주 방지. 넘으면 남은 건수를 알리고 멈춘다")
    ap.add_argument("--one-round", action="store_true",
                    help="한 회차만 (예산을 딱 끊고 싶을 때)")
    ap.add_argument("--full", action="store_true",
                    help="이미 LLM 검증한 엣지까지 다시 (프롬프트 수정 후에만)")
    ap.add_argument("--all", action="store_true",
                    # ★`%`는 `%%`로. argparse가 help를 포매팅해서 `--help`가 죽는다
                    help="1차에서 걸린 것만이 아니라 **근거를 가진 엣지 전부**를 "
                         "LLM 검증 (기본은 의심분만 — 커버율이 1%%대에 머문다)")
    ap.add_argument("--source", choices=["news", "dart", "any"], default="any",
                    help="출처 제한. news는 문장에서 추론한 것이라 우선순위가 높다")
    args = ap.parse_args()

    with neo4j_session() as session:
        # 낡은 1차 판정부터 걷어낸다 — 아래 통계가 옛 표시에 오염되지 않게.
        stale = session.run(_CLEAR_STALE).single()["n"]
        if stale:
            print(f"↺ 통과했는데 남아 있던 옛 1차 판정 {stale}건 정리\n")
        rows = [dict(r) for r in session.run(_FIND)]
    print(f"근거를 가진 엣지 {len(rows)}건\n")

    texts = _fetch_evidence(rows)

    # ── 1차: 무료 문자 검사 ────────────────────────────────
    suspects: list[tuple[dict, str, float, list[str]]] = []
    by_edge = Counter()
    for r in rows:
        ev = texts.get(r["ev"], "")
        if not ev:
            for e in r["evs"]:
                ev = texts.get(e, "")
                if ev:
                    break
        if not ev:
            continue
        # Company/Person 이름은 약칭·표기 차이가 많아 제외하고,
        # **Event·Product 이름과 subtype**을 본다(오염이 여기서 난다).
        check_names = []
        if r["a_label"] in ("Event", "Product"):
            check_names.append(r["a_name"])
        if r["b_label"] in ("Event", "Product"):
            check_names.append(r["b_name"])
        if not check_names:
            continue
        score, missing = grounding(ev, *check_names)
        if score < _GROUND_THRESHOLD:
            suspects.append((r, ev, score, missing))
            by_edge[r["edge"]] += 1

    print(f"[1차 · 무료] Event·Product 이름이 근거에 없는 엣지: "
          f"{len(suspects)}건")
    for edge, n in by_edge.most_common():
        print(f"    {edge:16} {n:>4}")
    for r, ev, score, missing in suspects[:12]:
        node = r["b_name"] if r["b_label"] in ("Event", "Product") else r["a_name"]
        print(f"  ✗ [{r['edge']}] 「{node[:30]}」 근거에 없음: {missing[:3]}")
        print(f"      근거: {ev.split(chr(10))[0][:66]}")
    if len(suspects) > 12:
        print(f"  … 외 {len(suspects)-12}건")

    if not args.llm:
        print(f"\n1차만 실행했습니다. --llm 으로 2차 검증(의역·방향)까지 가능합니다.")
        return 0

    # ── 2차: 저가 LLM 검증 ──────────────────────────────────
    # 기본은 1차 의심분만 본다. 그런데 1차는 **Event·Product 이름**만 대조하므로
    # 회사-회사 엣지는 애초에 후보에 오르지 않는다 → 커버율이 1%대에 머문다.
    # `--all`은 근거를 가진 엣지를 전부 본다(느리고 돈이 들지만 구멍이 없다).
    if args.all:
        base = [(r, None, None, None) for r in rows]
    else:
        base = suspects
    if args.source != "any":
        base = [s for s in base if s[0]["src"] == args.source]
    pool_rows = [s for s in base if args.full or not s[0]["checked"]]
    skipped = len(base) - len(pool_rows)
    all_targets = [(r, texts.get(r["ev"], "") or next(
        (texts.get(e, "") for e in r["evs"] if texts.get(e)), ""))
        for r, _, _, _ in pool_rows]
    all_targets = [t for t in all_targets if t[1]]

    # ★남은 게 없을 때까지 **회차를 돈다**(2026-08-03). 전에는 상한에서 잘라
    #   나머지를 다음 실행으로 미뤘는데, 다음 실행이 언제일지 정해져 있지 않아
    #   **미검증분이 쌓였다.** 실측: 재수집 2개사만 했는데 신규 엣지 592건 중
    #   146건(25%)이 검증 없이 남았다. 확장할수록 더 벌어진다.
    #
    #   검증은 엣지당 0.3원이라 다 봐도 몇백 원이다. 잘라서 아끼는 돈보다
    #   「검증했다」와 「안 했다」가 섞여 **커버율이 거짓이 되는 손해**가 훨씬 크다.
    rounds = 1 if args.one_round else max(
        1, min(args.max_rounds, -(-len(all_targets) // max(args.limit, 1))))
    print(f"\n[2차 · LLM] 대상 {len(all_targets)}건 "
          f"(약 {len(all_targets) * 0.0003 * 1380:.0f}원)"
          + (f" · 기검증 {skipped}건 건너뜀" if skipped else "")
          + (f" · {rounds}회차로 나눠 처리" if rounds > 1 else ""))
    left = len(all_targets) - rounds * args.limit
    if left > 0:
        print(f"  ⚠ 회차 상한(--max-rounds {args.max_rounds})에 걸려 {left}건이 남습니다. "
              f"다시 실행하면 이어서 봅니다.")

    results = []
    for rd in range(rounds):
        chunk = all_targets[rd * args.limit:(rd + 1) * args.limit]
        if not chunk:
            break
        if rounds > 1:
            print(f"  · {rd + 1}/{rounds}회차 — {len(chunk)}건")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results.extend(pool.map(_verify, chunk))

    failed = [(r, v) for r, v in results if v.get("failed")]
    ok_results = [(r, v) for r, v in results if not v.get("failed")]
    bad = [(r, v) for r, v in ok_results if v.get("verdict") != "supported"]
    tally = Counter(v.get("verdict", "?") for _, v in ok_results)
    rate = len(bad) / len(ok_results) * 100 if ok_results else 0
    print(f"  → 뒷받침 안 됨 {len(bad)}/{len(ok_results)}건 ({rate:.1f}%)")
    for k, label in (("supported", "뒷받침됨"), ("wrong_type", "유형오류"),
                     ("unfounded", "근거없음"), ("insufficient", "근거부족")):
        if tally[k]:
            print(f"      {label:8}{tally[k]:>5}")
    if failed:
        # 실패를 통과로 세면 커버율이 거짓이 된다 — 소리내어 알리고 기록하지 않는다
        print(f"  ⚠ LLM 호출 실패 {len(failed)}건 — 검증완료로 기록하지 않습니다. "
              f"다시 실행하면 재시도합니다.")
    for r, v in bad[:15]:
        hint = f" → {v['actual']}" if v.get("actual") else ""
        print(f"  ✗ [{v.get('verdict','?'):12}] ({r['a_name'][:16]}) -[{r['edge']}]-> "
              f"({r['b_name'][:22]}){hint}")
        print(f"      {v.get('reason','')[:70]}")
    if len(bad) > 15:
        print(f"  … 외 {len(bad)-15}건")

    if args.apply:
        with neo4j_session() as session:
            for r, v in bad:
                session.run(_MARK, eid=r["eid"], reason=v.get("reason", "")[:200],
                            verdict=v.get("verdict", ""), actual=v.get("actual", ""))
            # ★이번에 통과한 것은 **이전 의심 표시를 지운다.** 프롬프트를 고쳐
            #   재검증하는 목적이 「전에 잘못 의심한 것을 풀어주는 것」인데,
            #   표시를 안 지우면 고쳐도 의심인 채로 남는다(실측: 칸토덴카 건).
            good = [r["eid"] for r, v in ok_results if v.get("verdict") == "supported"]
            if good:
                cleared = session.run(_CLEAR, eids=good).single()["n"]
                if cleared:
                    print(f"  ↺ 이전에 의심으로 표시됐다가 이번에 통과한 엣지 {cleared}건 "
                          f"— 의심 해제")
            # 검증한 것 전부에 시점을 남긴다 — 통과분도 다시 볼 필요 없다.
            # ★실패분은 뺀다. 넣으면 다음 실행이 건너뛰어 영영 검사되지 않는다.
            if ok_results:
                session.run(_MARK_OK, eids=[r["eid"] for r, _ in ok_results])
        print(f"\n✅ {len(bad)}건에 grounding_suspect 표시 "
              f"(삭제하지 않음 — 검토 후 결정) · {len(ok_results)}건 검증완료 기록")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
