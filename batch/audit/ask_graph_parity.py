"""`/ask` 출력 대조 — **그래프 경로 vs `AnswerService.ask()`**.

Phase 1 의 목표는 「LangGraph 가 실행을 담당한다」이지 「답이 좋아진다」가
아니다. 그래서 성공 기준이 **출력이 똑같은 것**이다. 이 스크립트가 그걸 잰다.

★**LLM 을 실제로 부르지 않는다.** 두 경로에 같은 고정 응답을 물려 놓고 돌린다.
  이유는 둘이다:

    ① `temperature=0` 이어도 같은 프롬프트가 같은 문자열을 준다는 보장이 없다.
       LLM 이 흔들리면 「그래프가 틀렸나 모델이 흔들렸나」를 못 가린다.
    ② 진짜 대조 대상은 **프롬프트와 후처리**다. 프롬프트가 바이트까지 같고
       후처리가 같으면 같은 모델에 같은 답이 나온다 — LLM 은 두 경로가
       공유하는 함수일 뿐이다.

  그래서 **프롬프트를 바이트 단위로** 비교하고(`user_prompt`), 그 위에
  `AskResponse` 전체를 비교한다.

★**임베딩도 두 경로가 같은 값을 보게 고정한다.** 이걸 안 하면 대조가 거짓
  양성을 낸다 — `evidence_selector.similarities()` 는 임베딩 호출이 실패하면
  조용히 `{}` 를 돌려주고(`evidence_selector.py:146`), 그러면 사건 정렬에서
  **유사도 단계가 통째로 빠져** 순서가 달라진다. 20개 질문 × 2경로 = 40번을
  연속으로 부르면 그중 한 번이 흔들릴 수 있고, 실제로 흔들렸다(실측
  2026-08-27: 3건이 다르게 나왔는데 같은 질문을 단독으로 다시 재니 동일했다).

  그래서 **텍스트별로 한 번만 진짜 임베딩을 부르고 캐시**해서 두 경로에 같은
  벡터를 물린다. 실패하면 조용히 넘어가지 않고 **세어서 보고**한다 — 정렬이
  degrade 된 채로 「같다」고 말하지 않기 위함이다.

★허용되는 차이는 **하나**다 — `check_claims` 가 쓰는 `intent`.
  예전 `answer_service` 는 `decision.anchors` 만으로 다시 계산했는데,
  `source=query` 면 그건 최고점 **1개**라 `resolved_entities`(복수 후보)로
  재료를 고른 것과 어긋날 수 있다. 그래프는 재료를 고른 쪽(retrieve)의 값을
  쓴다. 이 차이가 난 질문은 **양쪽 값을 함께** 찍는다.

  ★`intent` 는 `claim_check` 관측에만 쓰이고 `AskResponse` 에 실리지 않는다 —
    그래서 이 차이가 응답을 바꾸지는 않는다. 그래도 「달랐다」를 숨기지 않는다.

    python -m batch.audit.ask_graph_parity              # 20개 대표 질문
    python -m batch.audit.ask_graph_parity --limit 5
    python -m batch.audit.ask_graph_parity --question "삼성전자에 납품하는 기업"
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional
from unittest.mock import patch

from app.api.schemas import AskRequest
from app.services import answer_service, evidence_selector
from app.services.answer_service import AnswerService
from batch.audit.claim_grounding import QUESTIONS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 두 경로에 똑같이 물릴 고정 응답. **실패가 아니어야** 후처리(화이트리스트
# 검증·claim 관측)까지 전부 지나간다 — 실패 경로만 재면 절반을 안 본 것이다.
_CANNED = {
    "answer": "대조용 고정 답변입니다.",
    # 재료 안에 없는 id 라 화이트리스트가 **버려야** 한다. 그 동작까지 대조된다.
    "evidence_ids": ["ev_parity_probe"],
    "claims": [{"text": "대조용 고정 답변입니다.", "evidence_ids": ["ev_parity_probe"]}],
}

# 워크스페이스 — `/ask` 는 `workspace_keys` 가 필수다(설계서 §16-2). 대표 질문이
# 삼성전자·SK하이닉스 중심이라 둘을 담는다. 앵커 없는 질문이 `workspace` 로
# 떨어지는 경로도 이걸로 열린다.
_WORKSPACE = ["00126380", "00164779"]


class _CachedEmbed:
    """텍스트별로 **한 번만** 진짜 임베딩을 부르고 캐시한다.

    ★대조 실험의 통제 변수다 — 두 경로가 **같은 벡터**를 봐야 「코드 경로가
      달라서 순서가 달라졌다」를 말할 수 있다. 안 그러면 임베딩 호출 한 번이
      흔들린 것을 그래프 회귀로 오독한다.
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[float]] = {}
        self.failures = 0
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        from app.services.retrieve_service import _default_embed

        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            self.calls += 1
            try:
                for text, vector in zip(missing, _default_embed(missing)):
                    self._cache[text] = vector
            except Exception:
                # ★조용히 넘어가지 않는다. 캐시가 비면 `similarities()` 가
                #   `{}` 로 떨어져 정렬이 degrade 되는데, 그걸 「같다」의 근거로
                #   삼으면 안 된다. 세어서 보고한다.
                self.failures += 1
                raise
        return [self._cache[t] for t in texts]


class _Recorder:
    """어댑터 자리에 끼워 프롬프트를 받아 적고 고정 응답을 돌려준다."""

    def __init__(self) -> None:
        self.system: Optional[str] = None
        self.user: Optional[str] = None
        self.calls = 0

    def structured(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        self.system, self.user = system, user
        self.calls += 1
        return dict(_CANNED)


def _run_service(request: AskRequest, embed=None) -> tuple[dict, _Recorder, dict]:
    """기준선 — `AnswerService.ask()`. `ask_json` 자리에 녹음기를 끼운다."""
    rec = _Recorder()
    seen: dict[str, Any] = {}

    def _fake_ask_json(system, user, **kwargs):
        return rec.structured(system, user, **kwargs)

    # ★`intent` 를 훔쳐본다 — 어느 쪽 계산식을 썼는지 실측하기 위함이다.
    real_intent_of = evidence_selector.intent_of

    def _spy_intent_of(question, anchor_names):
        value = real_intent_of(question, anchor_names)
        # 마지막 호출이 `answer_service` 쪽이다(재료 조립이 먼저 끝난다).
        seen["anchor_names"], seen["intent"] = list(anchor_names), value
        return value

    from app.services.retrieve_service import RetrieveService

    with patch.object(answer_service, "ask_json", _fake_ask_json), \
         patch.object(evidence_selector, "intent_of", _spy_intent_of):
        response = AnswerService(RetrieveService(embed=embed)).ask(request)
    return response.model_dump(mode="json"), rec, seen


def _run_graph(request: AskRequest, embed=None) -> tuple[dict, _Recorder, dict]:
    """그래프 경로. `_llm` 자리에 같은 녹음기를 끼운다."""
    from app.graph.ask_graph import build_ask_graph
    from app.graph.nodes import material as material_nodes
    from app.graph.nodes import answer as answer_nodes
    from app.graph.state import final_response, initial_state
    from app.services.retrieve_service import RetrieveService

    rec = _Recorder()
    with patch.object(answer_nodes, "_llm", rec), \
         patch.object(material_nodes, "_service", RetrieveService(embed=embed)):
        state = build_ask_graph().invoke(initial_state(request))
    response = final_response(state)
    seen = {"anchor_names": state.get("anchor_names"), "intent": state.get("intent")}
    return response.model_dump(mode="json"), rec, seen


def _run_graph_entry(request: AskRequest, embed=None):
    """그래프를 **`/ask` 라우트와 같은 방식으로** 부른다 — `run_ask()` 경유."""
    from app.graph.ask_graph import run_ask
    from app.graph.nodes import answer as answer_nodes
    from app.graph.nodes import material as material_nodes
    from app.services.retrieve_service import RetrieveService

    rec = _Recorder()
    with patch.object(answer_nodes, "_llm", rec), \
         patch.object(material_nodes, "_service", RetrieveService(embed=embed)):
        return run_ask(request)


def compare(question: str, embed=None) -> dict:
    """질문 하나를 두 경로로 돌려 비교한다. `embed` 는 **두 경로가 공유한다.**"""
    request = AskRequest(question=question, workspace_keys=list(_WORKSPACE))
    base, base_rec, base_seen = _run_service(request, embed)
    graph, graph_rec, graph_seen = _run_graph(request, embed)

    return {
        "question": question,
        "response_same": base == graph,
        "prompt_same": base_rec.user == graph_rec.user,
        "system_same": base_rec.system == graph_rec.system,
        "llm_called": (base_rec.calls, graph_rec.calls),
        "base": base,
        "graph": graph,
        "base_prompt": base_rec.user,
        "graph_prompt": graph_rec.user,
        "intent_same": base_seen.get("intent") == graph_seen.get("intent"),
        "base_intent": base_seen.get("intent"),
        "graph_intent": graph_seen.get("intent"),
        "base_anchor_names": base_seen.get("anchor_names"),
        "graph_anchor_names": graph_seen.get("anchor_names"),
    }


# ══════════════════════════════════════════════════════════════════
#  로그 대조 — 완료 기준 ③
# ══════════════════════════════════════════════════════════════════

# trace id 접두사. 값은 요청마다 다르므로 **떼고** 내용을 비교한다.
_TRACE = __import__("re").compile(r"^\[([0-9a-f]{8}|-)\] ")
# ★`took_ms` 는 **벽시계**다. 두 번째 실행이 캐시가 데워져 빠른 것은 당연하고,
#   그걸 「내용이 다르다」로 세면 대조가 늘 실패한다. 값만 지운다.
_TOOK_MS = __import__("re").compile(r"took_ms=\d+")
# 우리 로그만 본다 — httpx·chromadb 는 요청 수가 캐시에 따라 달라진다.
_OURS = ("app.", "search.", "pipeline.")


class _LogCapture:
    """우리 로거의 줄만 순서대로 모은다."""

    def __init__(self) -> None:
        import logging

        self.rows: list[str] = []

        outer = self

        class _Handler(logging.Handler):
            def emit(self, record):
                if record.name.startswith(_OURS):
                    outer.rows.append(record.getMessage())

        self._handler = _Handler()

    def __enter__(self):
        import logging

        from app.core.trace import _NOISY_LIBRARIES, reset_trace_id

        for name in (*_NOISY_LIBRARIES, "httpx2"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._root = logging.getLogger()
        self._level = self._root.level
        self._root.addHandler(self._handler)
        self._root.setLevel(logging.INFO)
        reset_trace_id()
        return self

    def __exit__(self, *exc):
        self._root.removeHandler(self._handler)
        self._root.setLevel(self._level)
        return False

    def messages(self) -> list[str]:
        """trace id 와 `took_ms` 를 뗀 내용."""
        return [_TOOK_MS.sub("took_ms=*", _TRACE.sub("", m)) for m in self.rows]

    def trace_ids(self) -> set[str]:
        return {m.group(1) for m in (_TRACE.match(r) for r in self.rows) if m}


def compare_logs(question: str) -> dict:
    """완료 기준 ③ — 같은 순서·같은 내용 + trace id 가 노드 경계를 넘는가."""
    request = AskRequest(question=question, workspace_keys=list(_WORKSPACE))
    embed = _CachedEmbed()

    with _LogCapture() as base_log:
        _run_service(request, embed)
    with _LogCapture() as graph_log:
        # ★로그 대조는 **진짜 입구**(`run_ask`)를 탄다. `compare()` 는 State 를
        #   봐야 해서 `invoke()` 를 직접 부르는데, 그러면 요청 경계를 건너뛰어
        #   trace id 가 발급되지 않는다 — 그 상태로 「전파 안 됨」이라고 적으면
        #   측정 방법이 만든 가짜 결론이다.
        _run_graph_entry(request, embed)

    ids = graph_log.trace_ids()
    return {
        "question": question,
        "base": base_log.messages(),
        "graph": graph_log.messages(),
        "same": base_log.messages() == graph_log.messages(),
        "trace_ids": ids,
        # ★노드 경계를 넘었나 — 줄마다 id 가 붙고 **전부 같은 값**이어야 한다.
        #   `-` 가 섞이면 그 줄은 경계 밖에서 찍힌 것이다.
        "trace_propagated": len(ids) == 1 and "-" not in ids,
    }


def report_logs(row: dict) -> int:
    import difflib

    print("\n" + "═" * 66)
    print(f"■ 로그 대조 — {row['question']}")
    print(f"   줄 수 base={len(row['base'])} graph={len(row['graph'])}")
    print(f"   순서·내용 동일             {row['same']}")
    print(f"   trace id 가 노드를 관통     {row['trace_propagated']}  {sorted(row['trace_ids'])}")
    if not row["same"]:
        for line in difflib.unified_diff(row["base"], row["graph"],
                                         "base", "graph", lineterm="", n=0):
            print("     ", line[:120])
    print("═" * 66)
    return 0 if row["same"] and row["trace_propagated"] else 1


def _first_diff(a: Optional[str], b: Optional[str]) -> str:
    """프롬프트가 다르면 **어디서** 갈렸는지 보여준다. 「다름」만으론 못 고친다."""
    if a is None or b is None:
        return f"한쪽이 없음 (base={a is not None} graph={b is not None})"
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"{i}번째 문자부터 — base={a[i:i+60]!r} graph={b[i:i+60]!r}"
    return f"길이만 다름 base={len(a)} graph={len(b)}"


def report(rows: list[dict]) -> int:
    """실측 요약. 반환값은 종료 코드 — 회귀가 있으면 1."""
    same = [r for r in rows if r["response_same"] and r["prompt_same"]]
    diff = [r for r in rows if not (r["response_same"] and r["prompt_same"])]
    intent_diff = [r for r in rows if not r["intent_same"]]

    print("\n" + "═" * 66)
    print(f"■ 출력 대조 — 질문 {len(rows)}개")
    print(f"   응답·프롬프트가 **동일**   {len(same):>3}")
    print(f"   다름                      {len(diff):>3}")

    for r in diff:
        print(f"\n   ✗ {r['question']}")
        if not r["prompt_same"]:
            print(f"      프롬프트: {_first_diff(r['base_prompt'], r['graph_prompt'])}")
        if not r["response_same"]:
            for key in sorted(set(r["base"]) | set(r["graph"])):
                if r["base"].get(key) != r["graph"].get(key):
                    print(f"      {key}: base={r['base'].get(key)!r}")
                    print(f"      {' ' * len(key)}  graph={r['graph'].get(key)!r}")

    # ── 작업 B 가 만든 예상된 차이 ──────────────────────────
    #
    # ★두 축을 따로 센다. `anchor_names` 는 **계산식이 실제로 갈렸는가**이고,
    #   `intent` 는 **그 차이가 관측값까지 번졌는가**다. 앞은 갈렸는데 뒤는
    #   같을 수 있다 — 여분의 앵커 이름이 질문 문자열에 없으면 `intent_of()` 가
    #   떼어낼 것이 없어 같은 값이 나온다. 둘을 합쳐 세면 「고칠 게 없었다」로
    #   잘못 읽힌다.
    name_diff = [r for r in rows
                 if r["base_anchor_names"] is not None
                 and r["base_anchor_names"] != r["graph_anchor_names"]]

    print(f"\n■ 작업 B — anchor_names 계산식 통일")
    print(f"   두 계산식이 **갈린** 질문   {len(name_diff):>3}"
          "   (answer 쪽 `decision.anchors` 만  vs  retrieve 쪽 `resolved_entities` 우선)")
    print(f"   그 차이가 intent 까지 번진 것 {len(intent_diff):>3}"
          "   ★이것만이 허용된 출력 차이다")
    for r in name_diff:
        marker = "★intent 도 다름" if not r["intent_same"] else "intent 는 같음"
        print(f"\n   · {r['question']}   [{marker}]")
        print(f"       base (answer 쪽)   anchor_names={r['base_anchor_names']!r} "
              f"intent={r['base_intent']!r}")
        print(f"       graph(retrieve 쪽) anchor_names={r['graph_anchor_names']!r} "
              f"intent={r['graph_intent']!r}")
    if not name_diff:
        print("   → 이 질문 세트에서는 두 계산식이 같은 값을 냈다")

    calls = {r["llm_called"] for r in rows}
    print(f"\n■ LLM 호출 횟수 (base, graph) 관측: {sorted(calls)}")
    print("   ★(0, 0) 은 재료가 없어 LLM 을 안 부른 질문이다 — 양쪽 다 안 불러야 한다.")
    print("═" * 66)
    return 0 if not diff else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=len(QUESTIONS))
    ap.add_argument("--question", help="질문 하나만 돌린다")
    ap.add_argument("--logs", action="store_true",
                    help="로그 순서·내용과 trace id 전파를 대조한다(완료 기준 ③)")
    args = ap.parse_args()

    if args.logs:
        return report_logs(compare_logs(args.question or QUESTIONS[11]))

    questions = [args.question] if args.question else QUESTIONS[:args.limit]
    # ★질문 하나마다 새로 만든다 — 질문끼리 캐시를 나누면 「두 경로가 같은
    #   벡터를 봤다」는 통제는 유지되지만 메모리만 커진다. 통제는 질문 안에서
    #   성립하면 충분하다.
    rows, failures = [], 0
    for i, question in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {question}")
        embed = _CachedEmbed()
        try:
            rows.append(compare(question, embed))
        except Exception as exc:
            print(f"   ✗ 실패: {exc!r}")
            raise
        failures += embed.failures
    if failures:
        print(f"\n★임베딩 호출 실패 {failures}건 — 정렬이 degrade 됐을 수 있다")
    return report(rows)


if __name__ == "__main__":
    raise SystemExit(main())
