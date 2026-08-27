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
from collections import Counter
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


def _real_embed():
    """진짜 임베더. **패치되기 전 값**을 그때그때 읽는다."""
    from app.services import retrieve_service as rs

    return rs._default_embed


class EmbedCacheMiss(RuntimeError):
    """대조 중 임베딩 캐시가 빗나갔다 — **결과를 믿을 수 없다.**"""


class _SimsWatch:
    """`similarities()` 가 **빈 dict** 를 돌려준 적이 있는지 지켜본다.

    ★비면 `select()` 의 4단 정렬에서 **유사도 단계가 통째로 빠진다**
      (`evidence_selector.py:146`). 그 실행의 사건 순서는 다른 규칙으로 정해진
      것이라, 그걸 「1차와 같다/다르다」의 근거로 쓰면 안 된다.
    """

    def __init__(self) -> None:
        self.empty = 0
        self.calls = 0

    def wrap(self, real):
        def _similarities(events, *, intent, embed, anchor_names):
            got = real(events, intent=intent, embed=embed, anchor_names=anchor_names)
            self.calls += 1
            # 사건이 없거나 의도가 없으면 애초에 안 부른다 — 그건 degrade 가 아니다.
            if not got and events and intent.strip() and embed is not None:
                self.empty += 1
            return got
        return _similarities


class _CachedEmbed:
    """텍스트별로 **한 번만** 진짜 임베딩을 부르고 캐시한다.

    ★대조 실험의 통제 변수다 — 두 경로가 **같은 벡터**를 봐야 「코드 경로가
      달라서 순서가 달라졌다」를 말할 수 있다. 안 그러면 임베딩 호출 한 번이
      흔들린 것을 그래프 회귀로 오독한다.
    """

    def __init__(self, *, strict: bool = False, real=None) -> None:
        # ★진짜 임베더를 **생성 시점에 붙잡는다.** 호출 시점에 다시 import 하면,
        #   이 캐시가 `retrieve_service._default_embed` 자리에 끼워져 있으므로
        #   **자기 자신을 부른다**(실측: 재귀로 300회 넘게 돌고 캐시는 0 이었다).
        self._real = real
        self._cache: dict[str, list[float]] = {}
        self.failures = 0
        self.calls = 0
        self.hits = 0
        self.misses = 0
        # ★`strict` 면 **캐시에 없는 텍스트를 만나는 순간 멈춘다.** 실제 호출로
        #   폴백하지 않는다 — 폴백하면 방어가 사라지고, 하필 그 한 번이 실패하면
        #   1차와 똑같이 「도구화 회귀」로 오독된다.
        self.strict = strict

    def __call__(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing and self.strict:
            raise EmbedCacheMiss(
                f"임베딩 캐시 미스 {len(missing)}건 — 두 경로가 다른 텍스트를 "
                f"임베딩하려 한다. 폴백하지 않고 멈춘다. 예: {missing[0][:60]!r}")
        if missing:
            self.calls += 1
            try:
                for text, vector in zip(missing, self._real(missing)):
                    self._cache[text] = vector
            except Exception:
                # ★조용히 넘어가지 않는다. 캐시가 비면 `similarities()` 가
                #   `{}` 로 떨어져 정렬이 degrade 되는데, 그걸 「같다」의 근거로
                #   삼으면 안 된다. 세어서 보고한다.
                self.failures += 1
                raise
        return [self._cache[t] for t in texts]

    def freeze(self) -> "_CachedEmbed":
        """지금까지 채운 캐시를 그대로 물려받되 **미스를 금지하는** 사본."""
        clone = _CachedEmbed(strict=True, real=self._real)
        clone._cache = self._cache
        return clone


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
    from app.services import retrieve_service as rs

    embed = _CachedEmbed(real=rs._default_embed)

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


# ══════════════════════════════════════════════════════════════════
#  재료 집합 대조 — ★완료 기준 ① (Phase 1.5)
# ══════════════════════════════════════════════════════════════════
#
# ★**프롬프트 바이트 비교를 끈다.** 1.5차는 표기를 붙이므로 프롬프트가 달라지는
#   것이 정상이다. 같아야 하는 것은 **무엇을 담았나**지 어떻게 썼나가 아니다.


def _materials_of_service(request: AskRequest) -> dict:
    """1차 기준선의 재료 — `RetrieveService.retrieve_for_ask()`."""
    from app.services.retrieve_service import RetrieveService

    from app.services.answer_service import _build_user_prompt

    decision, retrieved = RetrieveService().retrieve_for_ask(request)
    if retrieved is None:                      # unresolved — 재료를 안 만든다
        return {"companies": set(), "events": set(), "relations": set(),
                "evidence": set(), "prompt_chars": 0}
    return {
        "companies": {c.key for c in retrieved.companies},
        "events": {e.event_id for e in retrieved.events},
        "relations": {(r.source.name, r.type.value, r.target.name)
                      for r in retrieved.relations},
        "evidence": {e.evidence_id for e in retrieved.evidence},
        # ★1차 프롬프트 길이 — 표기가 얼마나 늘렸는지 재려면 기준선이 필요하다.
        "prompt_chars": len(_build_user_prompt(request.question, retrieved, decision)),
    }


def _materials_of_graph(request: AskRequest) -> tuple[dict, dict]:
    """도구 경로의 재료 + 관측값(프롬프트 길이·role 분포)."""
    from app.graph.ask_graph import build_ask_graph
    from app.graph.nodes import answer as answer_nodes
    from app.graph.state import initial_state

    rec = _Recorder()
    with patch.object(answer_nodes, "_llm", rec):
        state = build_ask_graph().invoke(initial_state(request))

    materials = {
        "companies": {c.key for c in state.get("companies", [])},
        "events": {e.event_id for e in state.get("events", [])},
        "relations": {(r.source, r.edge_type, r.target)
                      for r in state.get("relations", [])},
        "evidence": {e.evidence_id for e in state.get("evidence", [])},
    }
    observed = {
        "prompt_chars": len(rec.user or ""),
        "roles": Counter(e.role for e in state.get("events", [])),
    }
    return materials, observed


def _excluded_counts(request: AskRequest, companies) -> dict[str, int]:
    """의심 표시로 **몇 건이 빠졌나** — 도구가 안 뺐다면 몇 건이 더 있었을까."""
    from app.services import company_service
    from app.tools import graph_tools

    events_suspect = relations_suspect = 0
    for company in companies:
        norms = company_service.norm_names_by_keys([company])
        for norm in norms.values():
            events_suspect += sum(1 for r in company_service.events_of(norm)
                                  if r.get("eventness_suspect"))
            relations_suspect += sum(1 for r in company_service.relations_of(norm)
                                     if r.get("verdict") in graph_tools._HIDE)
    return {"eventness_suspect": events_suspect,
            "grounding_suspect": relations_suspect}


def compare_materials(question: str) -> dict:
    """질문 하나 — 1차 재료 집합과 도구 경로 재료 집합을 비교한다."""
    request = AskRequest(question=question, workspace_keys=list(_WORKSPACE))

    # ★**임베더를 한 자리에서 갈아끼운다.** `retrieve_service._default_embed` 는
    #   기준선(`_events_of`)과 도구(`graph_tools._embed()`)가 **둘 다** 늦게
    #   읽는 이름이라, 여기만 바꾸면 두 경로가 같은 벡터를 본다.
    #   `RetrieveService(embed=...)` 로는 도구 경로에 안 닿는다 — 도구는
    #   서비스 인스턴스를 거치지 않는다.
    #
    # ★기준선을 먼저 돌려 캐시를 채우고, 도구 경로는 **얼린 캐시**로 돈다.
    #   미스가 나면 폴백하지 않고 그 자리에서 멈춘다 — 그게 방어선이다.
    from app.services import retrieve_service as rs

    warm = _CachedEmbed(real=rs._default_embed)   # ★패치 **전에** 붙잡는다
    watch = _SimsWatch()
    real_similarities = evidence_selector.similarities
    with patch.object(evidence_selector, "similarities", watch.wrap(real_similarities)):
        with patch.object(rs, "_default_embed", warm):
            base = _materials_of_service(request)
        frozen = warm.freeze()
        with patch.object(rs, "_default_embed", frozen):
            graph, observed = _materials_of_graph(request)

    base_chars = base.pop("prompt_chars", 0)
    diffs = {}
    for key in ("companies", "events", "relations", "evidence"):
        only_base = base[key] - graph[key]
        only_graph = graph[key] - base[key]
        if only_base or only_graph:
            diffs[key] = {"only_base": sorted(only_base)[:8],
                          "only_graph": sorted(only_graph)[:8],
                          "n_only_base": len(only_base),
                          "n_only_graph": len(only_graph),
                          "_only_base_all": only_base, "_only_graph_all": only_graph}

    expected, why = _classify(diffs)
    return {
        "question": question,
        "base": base, "graph": graph, "diffs": diffs,
        # ★「같다」와 「예상된 차이다」를 **가른다.** 합쳐 세면 의심 표시 제외로
        #   줄어든 것과 도구화 회귀가 같아 보인다.
        "materials_same": not diffs,
        "expected_only": expected, "why": why,
        "diff_summary": "; ".join(
            f"{k}: 1차만 {v['n_only_base']} · 도구만 {v['n_only_graph']}"
            for k, v in diffs.items()) or "동일",
        "excluded": _excluded_counts(request, base["companies"]),
        "prompt_chars": observed["prompt_chars"], "base_prompt_chars": base_chars,
        "roles": observed["roles"],
        # ★대조의 전제 조건. 하나라도 어긋나면 이 행은 무효다.
        "cache_hits": frozen.hits, "cache_misses": frozen.misses,
        "sims_empty": watch.empty > 0, "sims_calls": watch.calls,
    }


def _classify(diffs: dict) -> tuple[bool, str]:
    """차이가 **예상된 것뿐인가.** `(예상됨, 사유)`.

    예상되는 것은 하나뿐이다 — `eventness_suspect` 제외와 **그로 인해 빈 자리로
    올라온 사건**(상한이 기업당 10건이라 하나 빠지면 하나 올라온다), 그리고 그
    사건들을 따라 달라지는 근거다.

    ★기업·관계가 달라지면 **예상 밖**이다. 의심 표시 제외는 그 둘을 건드리지 않는다.
    """
    from app.core.database import neo4j_session

    if not diffs:
        return True, "차이 없음"
    for key in ("companies", "relations"):
        if key in diffs:
            return False, f"{key} 가 달라졌다 — 의심 표시 제외로는 안 생기는 차이다"

    ev = diffs.get("events")
    if ev:
        ids = sorted(ev["_only_base_all"] | ev["_only_graph_all"])
        with neo4j_session() as s:
            suspect = {r["id"] for r in s.run(
                "MATCH (e:Event) WHERE e.event_id IN $ids AND "
                "coalesce(e.eventness_suspect,false) RETURN e.event_id AS id", ids=ids)}
        not_suspect = ev["_only_base_all"] - suspect
        if not_suspect:
            return False, (f"1차에만 있는 사건 중 의심 표시가 아닌 것 "
                           f"{len(not_suspect)}건: {sorted(not_suspect)[:4]}")
        promoted = ev["_only_graph_all"] & suspect
        if promoted:
            return False, f"의심 표시 사건이 도구 쪽에 올라왔다: {sorted(promoted)[:4]}"
    return True, ("eventness_suspect 제외와 그로 인한 빈 자리 승격, "
                  "그리고 그 사건들을 따라간 근거 차이뿐")


def report_materials(rows: list[dict]) -> int:
    """실측 요약. 반환값은 종료 코드 — 예상 밖 차이가 있으면 1."""
    invalid = [r for r in rows if r["cache_misses"] or r["sims_empty"]]
    same = [r for r in rows if r["materials_same"]]
    expected = [r for r in rows if not r["materials_same"] and r["expected_only"]]
    unexpected = [r for r in rows if not r["expected_only"]]

    print("\n" + "═" * 72)
    print(f"■ 재료 집합 대조 — 질문 {len(rows)}개  (프롬프트 바이트 비교는 끈다)")
    print(f"   1차와 **완전히 동일**        {len(same):>3}")
    print(f"   ★예상된 차이만 (의심 표시 제외) {len(expected):>3}")
    print(f"   ❌예상 밖 차이               {len(unexpected):>3}")

    for r in expected:
        ev = r["diffs"].get("evidence", {})
        e = r["diffs"].get("events", {})
        print(f"\n   · {r['question']}")
        print(f"       사건  1차에만 {e.get('n_only_base', 0)} {e.get('only_base', [])}")
        print(f"             도구에만 {e.get('n_only_graph', 0)} {e.get('only_graph', [])}"
              "   ← 빈 자리로 올라온 것")
        if ev:
            print(f"       근거  1차에만 {ev['n_only_base']} · 도구에만 "
                  f"{ev['n_only_graph']}   ← 위 사건을 따라간 것")

    for r in unexpected:
        print(f"\n   ✗ {r['question']}  — {r['why']}")
        for key, d in r["diffs"].items():
            print(f"      {key}: 1차에만 {d['n_only_base']}건 {d['only_base']}")
            print(f"      {' ' * len(key)}  도구에만 {d['n_only_graph']}건 {d['only_graph']}")

    # ── 의심 표시 제외 — 예상된 차이 ──────────────────────────
    print("\n■ 의심 표시로 뺀 건수 (★예상된 차이)")
    print(f"   {'질문':38}{'eventness':>11}{'grounding':>11}")
    tot_e = tot_g = 0
    for r in rows:
        e, g = r["excluded"]["eventness_suspect"], r["excluded"]["grounding_suspect"]
        tot_e += e
        tot_g += g
        if e or g:
            print(f"   {r['question'][:36]:38}{e:>11}{g:>11}")
    print(f"   {'합계':38}{tot_e:>11}{tot_g:>11}")
    print("   ★`grounding` 이 **0 인 것이 정상**이다 — `company_service._relation()`")
    print("     이 이미 같은 `_HIDE` 를 적용해 도구까지 오지 않는다(2026-08-28 실측:")
    print("     suspect 507건 중 449건이 Service 에서 빠지고 wrong_type 58건만 남는데,")
    print("     그 58건은 두 경로 모두 **의도적으로** 남긴다). 0 이 아니면 위쪽 규칙이")
    print("     바뀐 것이므로 그때 이 줄이 알려 준다.")

    # ── 프롬프트 길이 — 완료 기준 ② ──────────────────────────
    pairs = [(r["base_prompt_chars"], r["prompt_chars"]) for r in rows
             if r["prompt_chars"]]
    if pairs:
        deltas = [g - b for b, g in pairs if b]
        ratios = [g / b for b, g in pairs if b]
        base_max = max(b for b, _ in pairs)
        graph_max = max(g for _, g in pairs)
        print("\n■ 프롬프트 길이 — 표기가 붙어 늘어난다 (완료 기준 ②)")
        print(f"   {'':10}{'1차':>12}{'1.5차':>12}{'증가':>12}")
        print(f"   {'최대':10}{base_max:>12,}{graph_max:>12,}"
              f"{graph_max - base_max:>+12,}")
        print(f"   {'평균':10}{sum(b for b, _ in pairs)//len(pairs):>12,}"
              f"{sum(g for _, g in pairs)//len(pairs):>12,}"
              f"{sum(deltas)//len(deltas):>+12,}")
        print(f"   증가율  평균 {sum(ratios)/len(ratios):.2f}배 · "
              f"최대 {max(ratios):.2f}배")
        # ★34,430 은 과거 한 번 터졌던 지점이다. **표기가 넘긴 것이 아님**을
        #   가려서 적는다 — 1차에서 이미 넘고 있었는지가 핵심이다.
        over_graph = [r["question"] for r in rows if r["prompt_chars"] > 34430]
        over_base = [r["question"] for r in rows if r["base_prompt_chars"] > 34430]
        print(f"\n   ★과거 터진 지점 34,430자 — 1.5차 최대 {graph_max:,}자 "
              f"({34430 - graph_max:+,})")
        print(f"     넘는 질문   1차 {len(over_base)}개 → 1.5차 {len(over_graph)}개")
        if len(over_graph) == len(over_base):
            print("     ★**표기가 넘긴 것이 아니다.** 1차에서 이미 같은 질문들이 "
                  "넘고 있었고,")
            print(f"       표기가 더한 것은 최대 {graph_max - base_max:,}자"
                  f"({(graph_max / base_max - 1) * 100:.1f}%)뿐이다.")
        newly = sorted(set(over_graph) - set(over_base))
        if newly:
            print(f"     ★표기 때문에 새로 넘은 질문 {len(newly)}개: {newly}")

    # ── role 분포 — fetch_events 가 role=None 을 넘기는 근거 ──
    roles = Counter()
    for r in rows:
        roles.update(r["roles"])
    print(f"\n■ 사건 role 분포: {dict(roles)}")
    print("   ★`fetch_events` 는 `role=None`(전부)을 넘긴다. 도구 기본값인")
    print("     `subject` 로 거르면 위 subject 외 건수만큼 재료가 줄어든다.")

    # ── 전제 조건 ────────────────────────────────────────────
    hits = sum(r["cache_hits"] for r in rows)
    misses = sum(r["cache_misses"] for r in rows)
    print(f"\n■ 대조 전제 조건")
    print(f"   임베딩 캐시  히트 {hits:,} · 미스 {misses:,}   "
          f"{'✅ 미스 0' if misses == 0 else '❌ 미스가 있다 — 결과 무효'}")
    print(f"   유사도 정렬이 빠진 실행: {sum(1 for r in rows if r['sims_empty'])}건 / "
          f"similarities 호출 {sum(r['sims_calls'] for r in rows)}회   "
          f"{'✅ 없음' if not invalid else '❌ 있다 — 해당 행 무효'}")
    print("═" * 72)
    if invalid:
        print(f"★전제가 깨진 질문 {len(invalid)}개 — 이 대조 결과는 무효다: "
              f"{[r['question'] for r in invalid]}")
        return 1
    return 0 if not unexpected else 1


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
    ap.add_argument("--materials", action="store_true",
                    help="재료 집합을 1차와 비교한다(Phase 1.5 완료 기준 ①)")
    ap.add_argument("--logs", action="store_true",
                    help="로그 순서·내용과 trace id 전파를 대조한다(완료 기준 ③)")
    args = ap.parse_args()

    if args.materials:
        questions = [args.question] if args.question else QUESTIONS[:args.limit]
        rows = []
        for i, question in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] {question}")
            rows.append(compare_materials(question))
        return report_materials(rows)

    if args.logs:
        return report_logs(compare_logs(args.question or QUESTIONS[11]))

    questions = [args.question] if args.question else QUESTIONS[:args.limit]
    # ★질문 하나마다 새로 만든다 — 질문끼리 캐시를 나누면 「두 경로가 같은
    #   벡터를 봤다」는 통제는 유지되지만 메모리만 커진다. 통제는 질문 안에서
    #   성립하면 충분하다.
    rows, failures = [], 0
    for i, question in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {question}")
        embed = _CachedEmbed(real=_real_embed())
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
