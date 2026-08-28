"""`/ask` 회귀 케이스 뼈대를 **다시 만드는** 스크립트.

★지금은 **정답을 정하지 않는다.** 같은 요청을 다시 실행할 수 있게 하고, 그때
  나온 응답과 재료를 그대로 떠 두는 것까지가 목적이다. pass/fail 기준은
  claim validation·evaluation 단계에서 정한다(현황서 §6-2 ⑤·⑥).

★파일 이름에 `test_` 를 붙이지 않는다 — pytest 가 수집하면 매 실행마다 실제
  OpenAI 호출이 나간다.

실행:
    PYTHONPATH=. python tests/services/fixtures/record_ask_fixture.py

`observed.json` 은 **덮어쓰지 않는다.** 이미 있으면 `.YYYYMMDD-HHMMSS.json` 으로
옆에 쌓는다 — 관측 기록이라 이전 것을 지우면 비교할 대상이 사라진다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.api.schemas import AskRequest
from app.services.answer_service import AnswerService
from app.services.retrieve_service import RetrieveService

HERE = Path(__file__).parent
CASE = "ask_sk_hynix_production_disruption"


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    request = json.loads((HERE / f"{CASE}.request.json").read_text(encoding="utf-8"))
    body = AskRequest(**request["body"])

    retrieve = RetrieveService()
    answer = AnswerService(retrieve)

    retrieved = retrieve.retrieve(body)          # 재료 — §5-14 가 보는 층
    response = answer.ask(body)                  # 답변 — §5-12·§5-13 이 보는 층

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    observed = {
        "case": CASE,
        "recorded_at": stamp,
        "git_head": _git_head(),
        "note": "관측 기록이다. 정답이 아니다 — pass/fail 기준은 아직 없다.",
        "request": request["body"],
        "retrieved": json.loads(retrieved.model_dump_json()),
        "response": json.loads(response.model_dump_json()),
        "counts": {
            "companies": len(retrieved.companies),
            "events": len(retrieved.events),
            "relations": len(retrieved.relations),
            "propagation": len(retrieved.propagation),
            "evidence": len(retrieved.evidence),
            "evidence_missing": sum(1 for e in retrieved.evidence if e.missing),
            "sources": len(response.sources),
        },
        "match_type": retrieved.match_type.value,
        "failed": response.failed,
    }

    out = HERE / f"{CASE}.observed.json"
    if out.exists():
        out = HERE / f"{CASE}.observed.{stamp}.json"
    out.write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out.name}  ({out.stat().st_size:,} bytes)")
    print(f"  {observed['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
