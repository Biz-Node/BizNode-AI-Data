"""/ask 답변의 claim-근거 겹침 **분포**를 모은다 — 판정하지 않는다.

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
from app.services import claim_check
from app.services.answer_service import (_ANSWER_SCHEMA, _SAFE_FALLBACK,
                                         _SYSTEM_PROMPT, _build_user_prompt)
from app.services.retrieve_service import RetrieveService
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# 대표 질문 20개. 모드(NAME/RELATIONSHIP/SEMANTIC)·사건 유형·기업 수·
# 재료 없음까지 고르게 섞었다 — 한쪽만 보면 분포가 거짓말을 한다.
QUESTIONS = [
    "SK하이닉스",                                    # NAME, 의도 없음
    "SK하이닉스 노조 관련 리스크 알려줘",              # 노무
    "SK하이닉스가 겪은 안전사고",                      # 사고재해
    "SK하이닉스의 HBM 투자 상황은?",                   # 사업확장
    "SK하이닉스 소송 상황",                            # 분쟁소송
    "SK하이닉스 노조 설립이 회사에 어떤 영향을 주나?",   # 인과를 유도하는 질문
    "SK하이닉스 안전사고 때문에 생산에 차질이 생겼어?",  # 인과를 유도하는 질문
    "삼성전자 실적 어때?",                             # 실적
    "삼성전자 압수수색 건 알려줘",                      # 규제수사
    "삼성전자 기술 유출 사건",                          # 정보유출
    "삼성전자와 SK하이닉스의 담합 소송은 어떻게 됐어?",  # 다중 기업
    "삼성전자에 납품하는 기업",                         # RELATIONSHIP
    "SK하이닉스에 납품하는 기업",                       # RELATIONSHIP
    "한미반도체와 SK하이닉스 관계",                     # 다중 기업 관계
    "마이크론 HBM 양산",                               # 해외 기업
    "현대오토에버 노조",                               # Step1 회귀 대상 기업
    "심텍 공급 리스크",                                # 중소 기업
    "반도체 업계 파업 위험",                            # 앵커 없음(SEMANTIC 유도)
    "메모리 가격 담합",                                # 앵커 없음
    "storminmvpsdjfk 이 뭐야",                        # 재료 없음
]


def _run_one(question: str) -> dict:
    """`AnswerService.ask()` 를 그대로 쓰지 않는다 — 그쪽은 claims 를 응답에
    내보내지 않기 때문이다(Step4a 의 외부 계약 무변경 원칙). 같은 프롬프트·
    같은 스키마로 직접 부르고 claims 를 받아 본다."""
    request = AskRequest(question=question)
    retrieved = RetrieveService().retrieve(request)
    user = _build_user_prompt(question, retrieved)
    result = ask_json(_SYSTEM_PROMPT, user, schema=_ANSWER_SCHEMA,
                      name="ask_answer", fallback=_SAFE_FALLBACK)

    checked = claim_check.check(
        result.get("claims") or [],
        {e.evidence_id: e for e in retrieved.evidence})
    return {"question": question, "failed": bool(result.get("failed")),
            "evidence": len(retrieved.evidence), "checked": checked,
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
        elif c.score is not None and c.score <= 0.34:
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
