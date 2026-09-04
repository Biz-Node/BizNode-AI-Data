"""/ask 답변의 claim-근거 겹침 **분포**를 모은다 — 판정하지 않는다.

★**운영 경로(LangGraph)를 그대로 돌린다**(2026-09-04). 전에는 1차
  (`AnswerService`) 의 프롬프트로 돌았는데, 1.5차부터 표기가 붙어 두 프롬프트가
  갈렸다 — 그 상태로 재면 **나가지 않는 프롬프트의 분포**를 재는 셈이었다.

★왜 분포부터인가 (Step4a, 2026-08-23)

  `batch/audit/grounding.py` 는 노드 **이름**(토큰 2~3개)이 근거에 있는지 보려고
  `_GROUND_THRESHOLD = 0.34` 를 쓴다. 여기 대상은 답변 **문장**(토큰 10개 이상)
  이라 모수가 다르다. 같은 비율이 같은 뜻이 아니다.

  게다가 `pipeline/token_overlap.py` 는 **의미 판정기가 아니라 의심 탐지기**다.
  의역·동의어·한국어 조사에 그대로 걸린다(실측: 「SK하이닉스의」가
  「SK하이닉스」를 담은 근거에서 없는 토큰으로 잡혔다). 실측 없이 임계값을
  박으면 거짓 양성이 멀쩡한 답변을 훼손한다.

  그래서 이 도구는 **점수만 뽑아 늘어놓는다.** 임계값은 이 출력을 보고 정한다.

실행:
  python -m batch.audit.claim_grounding              # 20개 대표 질문
  python -m batch.audit.claim_grounding --limit 5    # 앞 5개만
"""

from __future__ import annotations

import argparse
import sys

from app.api.schemas import AskRequest
from app.graph.ask_graph import ask_graph
from app.graph.nodes import answer as answer_node
from app.graph.state import initial_state
from app.services import claim_check

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# 진단 출력에서 「낮다」고 눈에 띄게 할 선. **임계값이 아니다** — 아직 임계값은
# 없다. 어디를 자를지 정하려고 보는 것이라 넉넉히 잡아 둔다.
_SHOW_BELOW = 0.5


# 대표 질문 20개. 모드(NAME/RELATIONSHIP/SEMANTIC)·사건 유형·기업 수·
# 재료 없음까지 고르게 섞었다 — 한쪽만 보면 분포가 거짓말을 한다.
#
# ★**2026-08-28 — 현재 DB 로 다시 맞췄다.** 이 목록은 2026-08-23 에 쓰였고, 그
#   뒤로 사건·근거가 늘면서 **재료가 사라진 질문**이 생겼다. 분포를 모으는
#   도구라 재료 없는 질문이 섞이면 「claim 이 근거에 안 걸린다」가 모델 탓인지
#   질문 탓인지 못 가린다. 실측으로 셋을 갈았다:
#
#     「심텍 공급 리스크」          → 심텍의 사건은 사업확장·자본거래·품질뿐이고
#                                    **공급망은 0건**이다. 현대모비스로 옮겼다
#                                    (공급망 실재 · 사건 51 · 청크 328)
#     「SK하이닉스에 납품하는 기업」 → 바로 위 「삼성전자에 납품하는 기업」과 같은
#                                    분기(SUPPLIES_TO)라 축이 겹쳤다. 한 번도 안
#                                    덮이던 **품질**(7사 18건, 가장 얇은 유형)로
#                                    바꿨다 — LG전자가 품질 사건을 갖는다
#     「SK하이닉스가 겪은 안전사고」 → 사고재해는 아래 인과형 질문이 그대로 덮는다.
#                                    자리를 **자본거래**(47사 72건 · 미커버)에 줬다
#
#   ★도구 5종(2026-08-28)을 겨냥한 질문은 **여기 넣지 않는다.** 이 목록의 몫은
#     claim-근거 겹침 분포이고, 도구 커버리지는 `tests/agent/eval/` 이 맡는다.
#     한 목록이 두 가지를 재면 어느 쪽이 나빠졌는지 못 가린다.
QUESTIONS = [
    "SK하이닉스",                                    # NAME, 의도 없음
    "SK하이닉스 노조 관련 리스크 알려줘",              # 노무 (28사 94건)
    "두산로보틱스 자본거래 동향",                      # ★자본거래 (47사 72건) — 신규 축
    "SK하이닉스의 HBM 투자 상황은?",                   # 사업확장 (128사 452건)
    "SK하이닉스 소송 상황",                            # 분쟁소송 (36사 88건)
    "SK하이닉스 노조 설립이 회사에 어떤 영향을 주나?",   # 인과를 유도하는 질문
    "SK하이닉스 안전사고 때문에 생산에 차질이 생겼어?",  # 인과 + 사고재해 (22사 45건)
    "삼성전자 실적 어때?",                             # 실적 (30사 60건)
    "삼성전자 압수수색 건 알려줘",                      # 규제수사 (26사 50건)
    "삼성전자 기술 유출 사건",                          # 정보유출 (14사 19건)
    "삼성전자와 SK하이닉스의 담합 소송은 어떻게 됐어?",  # 다중 기업
    "삼성전자에 납품하는 기업",                         # RELATIONSHIP
    "LG전자 품질 문제 논란",                           # ★품질 (7사 18건, 가장 얇다)
    "한미반도체와 SK하이닉스 관계",                     # 다중 기업 관계
    "마이크론 HBM 양산",                               # 해외 기업 (corp_code 없음)
    "현대오토에버 노조",                               # Step1 회귀 대상 기업
    "현대모비스 공급망 리스크",                         # ★공급망 (17사 25건)
    "반도체 업계 파업 위험",                            # 앵커 없음(SEMANTIC 유도)
    "메모리 가격 담합",                                # 앵커 없음
    "storminmvpsdjfk 이 뭐야",                        # 재료 없음
]


def _run_one(question: str) -> dict:
    """`run_ask()` 대신 **그래프를 직접 돌린다** — `AskResponse` 에는 claims 가
    없기 때문이다(Step4a 의 외부 계약 무변경 원칙). 최종 State 에서 꺼내 본다.

    ★판정 인자는 `check_state_claims()` 한 곳에서 조립한다. 여기서 따로 부르면
      **운영과 다른 것을 재면서 같다고 보고**하게 된다.
    """
    state = ask_graph().invoke(initial_state(AskRequest(question=question)))
    result = state.get("llm_result")
    if result is None:
        # 앵커를 못 찾아 `halt_no_material` 로 빠진 질문 — LLM 을 안 불렀다.
        return {"question": question, "failed": False, "evidence": 0,
                "checked": [], "summary": claim_check.summarize([])}
    checked = answer_node.check_state_claims(state)
    return {"question": question, "failed": bool(result.get("failed")),
            "evidence": len(state.get("evidence") or []), "checked": checked,
            "summary": claim_check.summarize(checked)}


def _print_row(row: dict) -> None:
    s = row["summary"]
    scores = sorted(c.score for c in row["checked"] if c.score is not None)
    print(f"\n── {row['question']}")
    print(f"   재료 {row['evidence']:3}건 · claims {s['claims']:2} "
          f"(uncited {s['uncited']}, no_text {s['no_text']}, scored {s['scored']})"
          f"{'  ★LLM 실패' if row['failed'] else ''}")
    if scores:
        print(f"   점수 min={s['min']} mean={s['mean']} max={s['max']}")
        print(f"   전체: {[round(x, 2) for x in scores]}")
    for c in row["checked"]:
        if c.status == claim_check.STATUS_UNCITED:
            print(f"   [uncited] {c.text[:70]}")
        elif c.score is not None and c.score <= _SHOW_BELOW:
            print(f"   [{c.score:.2f}] {c.text[:60]}  없는토큰 {c.missing[:5]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(QUESTIONS))
    args = parser.parse_args()

    rows = []
    for question in QUESTIONS[:args.limit]:
        try:
            row = _run_one(question)
        except Exception as exc:  # noqa: BLE001 — 한 건이 죽어도 분포는 모은다
            print(f"\n── {question}\n   ✗ 실패: {exc!r}")
            continue
        rows.append(row)
        _print_row(row)

    every = [c.score for r in rows for c in r["checked"] if c.score is not None]
    total_claims = sum(r["summary"]["claims"] for r in rows)
    uncited = sum(r["summary"]["uncited"] for r in rows)
    no_text = sum(r["summary"]["no_text"] for r in rows)

    print("\n" + "=" * 70)
    print(f"질문 {len(rows)} · claim {total_claims} "
          f"(uncited {uncited}, no_text {no_text}, scored {len(every)})")
    if not every:
        return
    every.sort()
    def pct(p: float) -> float:
        return round(every[min(int(len(every) * p), len(every) - 1)], 3)
    print(f"점수 분포  min={every[0]:.3f}  p10={pct(0.10)}  p25={pct(0.25)}  "
          f"p50={pct(0.50)}  p75={pct(0.75)}  p90={pct(0.90)}  max={every[-1]:.3f}")
    print(f"평균 {sum(every)/len(every):.3f}")
    for cut in (0.0, 0.1, 0.2, 0.34, 0.5):
        n = sum(1 for x in every if x <= cut)
        print(f"  ≤{cut:<5} {n:4}건 ({n/len(every)*100:5.1f}%)")
    print("\n★이 표는 임계값을 **정하기 위한** 것이지 임계값이 아니다.")


if __name__ == "__main__":
    main()
