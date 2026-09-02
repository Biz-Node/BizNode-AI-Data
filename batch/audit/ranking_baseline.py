"""사건 랭킹의 **기준선을 고정한다** — 코드가 바꾼 것과 데이터가 바꾼 것을 가르려고. 비용 0.

★왜 필요한가 (2026-08-30)

  「최근 리스크」 질의가 2026년 위험사건을 하나도 못 뽑는다(현황서 §8-20). 고칠
  곳은 `evidence_selector` 인데, **같은 기간에 데이터 팀이 그래프를 고치고 있다**
  (`batch/repair/event_split.py` · `event_merge.py`). 둘이 겹치면 개선이 나와도
  **무엇이 그것을 냈는지 못 가른다.**

  이 저장소는 그 함정을 이미 **네 번** 밟았다 — 임베딩 값 드리프트(§4-5) · 링
  계측기(§4-9) · 옛 도구 호출 폭 `33~37` · 모델 교체. 매번 「코드가 바꾼 것」과
  「환경이 바꾼 것」이 섞여서, 숫자가 움직였는데 귀속을 못 했다.

  그래서 **코드를 고치기 전에** 지금 그래프에서의 순위를 통째로 찍어 둔다.

★**재구현하지 않는다 — 프로덕션 함수를 부른다.**

  `graph_tools.get_events()` 를 `scope.anchor_scope()` 안에서 그대로 부른다.
  순위 규칙을 여기 옮겨 적으면 그 사본이 낡는 순간 기준선이 거짓말을 한다.

  ★`/ask` 의 **도구 경로**를 잰다. `retrieve_service` 는 `eventness_suspect` 를
    **안 거르고** `graph_tools.get_events` 는 거른다(2026-08-30 실측). 원 질의가
    탄 것은 도구 경로이므로 그쪽을 기준선으로 삼는다.

★무엇을 재나 — 세 지표

      risk_kept        뽑힌 10건 중 위험사건 수      작업 1(risk 축)이 올려야 한다
      recent_kept      뽑힌 10건 중 최근 12개월 수   작업 3(최근 창)이 올려야 한다
      fallback_overlap 폴백이 뽑는 것과 몇 개 겹치나 작업 1~3 전체의 진척 지표

  ★`fallback_overlap` 이 왜 진척 지표인가 — `select(sims={})` 는 위험·최신순으로만
    고르는데, 「최근 리스크」에서는 **그것이 정답에 가깝다**(실측: 2026년 위험사건
    10건이 정확히 나온다). 유사도가 그 답을 덮어쓰고 있는 것이 결함이므로,
    겹침이 0 에서 올라가는 것이 곧 고쳐지는 것이다. **다른 질의에서는 폴백이
    정답이 아니다** — 그래서 지표일 뿐 목표가 아니다.

★**대조군을 같이 둔다.** 규칙 티어가 걸리는 질의(「안전사고」·「소송 상황」)와
  의도가 없는 질의(기업명만)는 작업 1~4 가 **안 건드려야 하는** 것들이다.
  이것들이 움직이면 회귀다.

    python -m batch.audit.ranking_baseline                    재고 저장한다
    python -m batch.audit.ranking_baseline --compare          저장된 것과 대조
    python -m batch.audit.ranking_baseline --save other.json  다른 이름으로
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date

from app.services import company_service, evidence_selector
from app.services.retrieve_service import (_MAX_EVENTS_PER_COMPANY,
                                           _MAX_GLOBAL_EVENTS, _default_embed,
                                           select_global_events)
from app.tools import graph_tools, scope

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_SNAPSHOT = "docs/ranking_baseline.json"

# 워크스페이스 2곳 — `run_test.py:_WORKSPACE` 와 같다. 기준선이 손으로 돌려 보는
# 것과 다른 기업을 재면 대조할 수가 없다.
_COMPANIES = [("00126380", "삼성전자"), ("00164779", "SK하이닉스")]

# ★질의를 **노리는 것별로** 고른다. `kind` 는 작업 1~4 중 무엇이 이 줄을 움직여야
#   하는지다 — `control` 은 **아무것도 움직이면 안 되는** 줄이다.
_QUESTIONS = [
    ("이 회사 최근 리스크 어때?", "risk+recent"),   # 원 질의. matched=∅
    ("최근 위험한 일 있었어?", "risk+recent"),      # 같은 뜻 다른 말
    ("노조 관련 리스크 알려줘", "risk+rule"),        # 규칙(노무)과 위험이 겹칠 때
    ("최근 실적 어때?", "recent+rule"),             # 규칙(실적)과 최근이 겹칠 때
    ("안전사고", "control-rule"),                   # 규칙만 — 움직이면 회귀
    ("소송 상황", "control-rule"),                  # 규칙만 — 움직이면 회귀
    ("", "control-nointent"),                       # 의도 없음(기업명만) — 폴백 경로
]


# ── 앵커 없는 질의 (2026-09-02) ──────────────────────────────────────────
# ★위 `_QUESTIONS` 는 **기업 key 가 필수**라(`scope.anchor_scope([key])`) 앵커 없는
#   경로를 못 잰다. 그쪽은 후보가 기업 5곳의 사건이 아니라 **전역 933행**이고,
#   기업은 고른 사건에서 역산된다 — 재는 대상 자체가 다르다.
#
# ★`(global)` 을 기업 자리에 쓴다. `_compare()` 가 `(company, question)` 으로
#   케이스를 잇는데, 앵커 없는 질의는 기업이 입력이 아니라 **출력**이라 고정된
#   이름이 필요하다.
_GLOBAL = "(global)"

_ANCHORLESS_QUESTIONS = [
    ("최근 주요 투자 이벤트가 뭐야?", "anchorless-rule"),   # 설계서 §5 시나리오 3 의 그 질의
    ("최근 소송 걸린 기업 알려줘", "anchorless-rule"),      # 규칙(분쟁소송)이 걸린다
    ("최근 리스크 뭐가 있어?", "anchorless-risk"),          # matched=∅ · 위험 축만
    ("이번 주 주요 사건 뭐야?", "anchorless-nointent"),     # 축이 하나도 안 걸린다
]


def _fingerprint(ids) -> str:
    """후보 집합의 지문. **그래프가 바뀌었는지 한 눈에** 보려고 둔다 —
    이게 같으면 순위 차이는 코드가 낸 것이다."""
    return hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:12]


def _recent_since(today: date) -> str:
    """최근 12개월의 시작 연월. **잠정치다** — 작업 3 이 창을 정하면 그 값을 쓴다."""
    y, m = today.year, today.month
    return f"{y - 1:04d}-{m:02d}"


def _candidates(key: str) -> list[dict]:
    """`get_events` 가 보는 것과 **같은 후보 집합**(suspect 제외)."""
    return [r for r in company_service.events_of(key)
            if not r.get("eventness_suspect")]


class _Row:
    """`evidence_selector` 가 요구하는 속성만 노출한다 — 폴백 대조용.
    ★`graph_tools._Row` 와 같은 이유로 raw dict 를 감싼다(도구 전용 필드 보존)."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.event_id = raw["event_id"]
        self.name = raw["name"]
        self.event_type = raw.get("event_type") or "기타"
        self.is_risk = bool(raw.get("is_risk"))
        self.occurred_at = raw.get("occurred_at")


def _measure(key: str, name: str, question: str, recent_since: str) -> dict:
    """한 (기업 × 질의). **프로덕션 함수를 그대로 부른다.**"""
    intent = evidence_selector.intent_of(question, [name]) if question else ""
    matched = evidence_selector.matched_event_types(intent)

    ws = [k for k, _ in _COMPANIES]
    with scope.anchor_scope([key], workspace_keys=ws, anchor_keys=[key],
                            anchor_names=[name], intent=intent):
        dtos = graph_tools.get_events([key], intent)

    rows = _candidates(key)
    wrapped = [_Row(r) for r in rows]
    sims = evidence_selector.similarities(
        wrapped, intent=intent, embed=_default_embed, anchor_names=[name])
    fallback, _ = evidence_selector.select(
        wrapped, matched=frozenset(), sims={}, limit=_MAX_EVENTS_PER_COMPANY)
    fallback_ids = [e.event_id for e in fallback]

    selected = [{
        "event_id": d.event_id, "name": d.name, "event_type": d.event_type,
        "is_risk": d.is_risk, "occurred_at": d.occurred_at,
        "sim": round(sims.get(d.event_id, 0.0), 4),
    } for d in dtos]

    return {
        "question": question, "intent": intent, "matched": sorted(matched),
        "selected": selected,
        "risk_kept": sum(1 for s in selected if s["is_risk"]),
        "recent_kept": sum(1 for s in selected
                           if (s["occurred_at"] or "") >= recent_since),
        "fallback_overlap": len({s["event_id"] for s in selected}
                                & set(fallback_ids)),
        "fallback": fallback_ids,
    }


def _measure_anchorless(question: str, recent_since: str) -> dict:
    """앵커 없는 질의 하나. **프로덕션 함수를 그대로 부른다** — 사본 금지.

    ★`/retrieve` 와 `/ask` 가 **같은 이 함수**를 부른다(`plan_material` 경유).
      그래서 한쪽만 재도 두 입구를 다 잰 것이다 — 갈릴 자리가 없는 구조다.
    """
    kept = select_global_events(question, embed=_default_embed)

    rows = company_service.global_events()
    wrapped = [_Row(r) for r in rows]
    sims = evidence_selector.similarities(
        wrapped, intent=evidence_selector.intent_of(question, []),
        embed=_default_embed, anchor_names=[])
    fallback, _ = evidence_selector.select(
        wrapped, matched=frozenset(), sims={}, limit=_MAX_GLOBAL_EVENTS)
    fallback_ids = [e.event_id for e in fallback]

    intent = evidence_selector.intent_of(question, [])
    selected = [{
        "event_id": e.event_id, "name": e.name, "event_type": e.event_type,
        "is_risk": e.is_risk, "occurred_at": e.occurred_at,
        "company": e.company.name if e.company else None,
        "sim": round(sims.get(e.event_id, 0.0), 4),
    } for e in kept]

    return {
        "question": question, "intent": intent,
        "matched": sorted(evidence_selector.matched_event_types(intent)),
        "selected": selected,
        "risk_kept": sum(1 for s in selected if s["is_risk"]),
        "recent_kept": sum(1 for s in selected
                           if (s["occurred_at"] or "") >= recent_since),
        "fallback_overlap": len({s["event_id"] for s in selected}
                                & set(fallback_ids)),
        "fallback": fallback_ids,
        # ★앵커 없는 경로에만 있는 지표 — 기업은 **입력이 아니라 출력**이다.
        "companies_derived": len({s["company"] for s in selected if s["company"]}),
    }


def _capture() -> dict:
    today = date.today()
    recent_since = _recent_since(today)
    out = {"captured_at": today.isoformat(), "recent_since": recent_since,
           "companies": {}, "cases": []}

    for key, name in _COMPANIES:
        rows = _candidates(key)
        out["companies"][key] = {
            "name": name, "candidates": len(rows),
            "risk": sum(1 for r in rows if r.get("is_risk")),
            "recent_risk": sum(1 for r in rows if r.get("is_risk")
                               and (r.get("occurred_at") or "") >= recent_since),
            "latest": max((r.get("occurred_at") or "") for r in rows) or None,
            "fingerprint": _fingerprint(r["event_id"] for r in rows),
        }

    # ★전역 후보도 지문을 찍는다 — 앵커 없는 경로의 순위 변화를 코드에 귀속하려면
    #   기업 5곳이 아니라 **이쪽**이 그대로여야 한다.
    global_rows = company_service.global_events()
    out["companies"][_GLOBAL] = {
        "name": "전역 사건 후보", "candidates": len(global_rows),
        "risk": sum(1 for r in global_rows if r.get("is_risk")),
        "recent_risk": sum(1 for r in global_rows if r.get("is_risk")
                           and (r.get("occurred_at") or "") >= recent_since),
        "latest": max((r.get("occurred_at") or "") for r in global_rows) or None,
        # 쌍 단위로 찍는다 — 같은 사건도 기업이 다르면 다른 행이다
        "fingerprint": _fingerprint(f"{r['event_id']}|{r['company']['key']}"
                                    for r in global_rows),
    }

    for question, kind in _QUESTIONS:
        for key, name in _COMPANIES:
            case = _measure(key, name, question, recent_since)
            case.update(company=key, kind=kind)
            out["cases"].append(case)

    for question, kind in _ANCHORLESS_QUESTIONS:
        case = _measure_anchorless(question, recent_since)
        case.update(company=_GLOBAL, kind=kind)
        out["cases"].append(case)
    return out


def _print(snap: dict) -> None:
    print("=" * 72)
    print(f"사건 랭킹 기준선  (기준일 {snap['captured_at']} · "
          f"최근 = {snap['recent_since']} 이후)")
    print("=" * 72)
    print("\n[후보 집합]  ★지문이 같으면 순위 차이는 **코드가 낸 것**이다")
    for key, c in snap["companies"].items():
        print(f"  {c['name']:<10} {key}  후보 {c['candidates']:>4} · 위험 {c['risk']:>3}"
              f" · 최근위험 {c['recent_risk']:>3} · 최신 {c['latest']}"
              f"  지문 {c['fingerprint']}")

    print("\n[질의별]  risk = 위험/10 · recent = 최근/10 · overlap = 폴백과 겹침/10")
    last = None
    for case in snap["cases"]:
        if case["kind"] != last:
            print(f"  ── {case['kind']} " + "─" * (56 - len(case['kind'])))
            last = case["kind"]
        name = snap["companies"][case["company"]]["name"]
        label = case["question"] or "(기업명만)"
        print(f"  {label:<22} {name:<10} risk {case['risk_kept']:>2}/10 · "
              f"recent {case['recent_kept']:>2}/10 · overlap {case['fallback_overlap']:>2}/10"
              f"   matched={case['matched'] or '∅'}")

    anchorless = [c for c in snap["cases"] if c["company"] == _GLOBAL]
    if anchorless:
        print("\n[앵커 없는 질의]  ★기업은 입력이 아니라 **고른 사건에서 역산된 것**이다")
        for case in anchorless:
            print(f"  {case['question']:<26} risk {case['risk_kept']:>2}/10 · "
                  f"recent {case['recent_kept']:>2}/10 · overlap {case['fallback_overlap']:>2}/10"
                  f" · 기업 {case['companies_derived']:>2}   matched={case['matched'] or '∅'}")
            for s in case["selected"][:3]:
                print(f"       {s['occurred_at'] or '?':<12} [{s['event_type']}] "
                      f"{s['name'][:30]} · {s['company']}")

    print("\n[원 질의가 실제로 뽑는 것 — 삼성전자]")
    for case in snap["cases"]:
        if case["company"] == "00126380" and case["question"].startswith("이 회사 최근"):
            for s in case["selected"]:
                print(f"   {s['occurred_at'] or '?':<12} risk={str(s['is_risk']):<5}"
                      f" sim={s['sim']:.4f} [{s['event_type']}] {s['name'][:34]}")


def _compare(old: dict, new: dict) -> int:
    """저장된 기준선과 지금을 대조한다. **그래프가 움직였는지 먼저 말한다** —
    그게 아니라야 순위 변화를 코드에 귀속할 수 있다."""
    print("=" * 72)
    print(f"기준선 대조   {old['captured_at']}  →  {new['captured_at']}")
    print("=" * 72)

    print("\n[후보 집합]")
    graph_moved = False
    for key, c in new["companies"].items():
        o = old["companies"].get(key)
        if not o:
            # ★**「새 항목」과 「지문 바뀜」은 다르다.** 도구에 후보 집합을 추가한
            #   것을 그래프 변동으로 세면, 다음 대조에서 「코드에 귀속할 수 없다」가
            #   떠서 정작 회귀를 볼 때 그 문장을 안 믿게 된다.
            print(f"  {c['name']:<10} ＋새 항목  후보 {c['candidates']}"
                  f" · 위험 {c['risk']} · 최근위험 {c['recent_risk']}"
                  f"  지문 {c['fingerprint']}")
            continue
        same = o["fingerprint"] == c["fingerprint"]
        graph_moved |= not same
        mark = "그대로" if same else "★바뀜"
        print(f"  {c['name']:<10} {mark}  후보 {o['candidates']}→{c['candidates']}"
              f" · 위험 {o['risk']}→{c['risk']} · 최근위험 {o['recent_risk']}→{c['recent_risk']}")

    print("\n★그래프가 " + ("**바뀌었다** — 순위 변화를 코드에만 귀속할 수 없다."
                          if graph_moved else
                          "그대로다 — 순위 변화는 **코드가 낸 것**이다."))

    if old["recent_since"] != new["recent_since"]:
        print(f"\n★「최근」 기준이 {old['recent_since']} → {new['recent_since']} 로 움직였다 "
              "— recent 열은 그만큼 기울어져 있다.")

    print("\n[질의별]  변한 것만")
    index = {(c["company"], c["question"]): c for c in old["cases"]}
    unchanged = 0
    for case in new["cases"]:
        o = index.get((case["company"], case["question"]))
        if not o:
            print(f"  + 새 케이스: {case['question']!r}"); continue
        d_risk = case["risk_kept"] - o["risk_kept"]
        d_recent = case["recent_kept"] - o["recent_kept"]
        d_lap = case["fallback_overlap"] - o["fallback_overlap"]
        moved = [a["event_id"] for a in case["selected"]] != \
                [a["event_id"] for a in o["selected"]]
        if not (d_risk or d_recent or d_lap or moved):
            unchanged += 1
            continue
        name = new["companies"][case["company"]]["name"]
        label = case["question"] or "(기업명만)"
        flag = "  ⚠대조군" if case["kind"].startswith("control") else ""
        d_firms = (case.get("companies_derived", 0) - o.get("companies_derived", 0)
                   if "companies_derived" in case else None)
        firms = f" · 기업 {d_firms:+d}" if d_firms else ""
        print(f"  {label:<22} {name:<10} risk {d_risk:+d} · recent {d_recent:+d}"
              f" · overlap {d_lap:+d}{firms}{flag}")
        gone = {a["event_id"] for a in o["selected"]} - {a["event_id"] for a in case["selected"]}
        came = {a["event_id"] for a in case["selected"]} - {a["event_id"] for a in o["selected"]}
        for s in case["selected"]:
            if s["event_id"] in came:
                print(f"       + {s['occurred_at'] or '?':<12} risk={str(s['is_risk']):<5} {s['name'][:34]}")
        for s in o["selected"]:
            if s["event_id"] in gone:
                print(f"       − {s['occurred_at'] or '?':<12} risk={str(s['is_risk']):<5} {s['name'][:34]}")
    print(f"\n  변화 없음 {unchanged} 케이스")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", default=_SNAPSHOT, help=f"스냅샷 경로 (기본 {_SNAPSHOT})")
    ap.add_argument("--compare", action="store_true",
                    help="저장된 스냅샷과 대조만 한다 (덮어쓰지 않는다)")
    args = ap.parse_args()

    snap = _capture()
    if args.compare:
        try:
            with open(args.save, encoding="utf-8") as f:
                old = json.load(f)
        except FileNotFoundError:
            print(f"기준선이 없다: {args.save}  — 먼저 --compare 없이 돌려 저장하라.")
            return 1
        return _compare(old, snap)

    _print(snap)
    with open(args.save, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"\n기준선을 저장했다 → {args.save}")
    print("★이 파일을 **커밋하라.** 커밋되지 않으면 대조할 것이 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
