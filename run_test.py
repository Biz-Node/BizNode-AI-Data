"""수동 시험 — 만든 것을 손으로 돌려 본다. **서버 실행도 여기 있다.**

╔══════════════════════════════════════════════════════════════════════╗
║  PY=.venv-wsl/bin/python        ← ★이것부터. 다른 파이썬으로는 안 된다  ║
╚══════════════════════════════════════════════════════════════════════╝

    $PY run_test.py check                    ★먼저 이것 — 돌 수 있는 상태인가   1초
    $PY run_test.py serve                    API 서버 (/docs · /preview)

    $PY run_test.py anchor   "질문"           앵커가 어느 갈래로 가나     무료  20초
    $PY run_test.py search   "질의"           검색 계층만                무료  20초
    $PY run_test.py global   "질문"           전역 사건 검색만            무료  15초
    $PY run_test.py retrieve "질문"           재료 조립 끝까지            무료  25초
    $PY run_test.py tools    삼성전자          도구 7종을 직접 부른다       무료  17초
    $PY run_test.py ask      "질문"           Agent 루프 끝까지        ★LLM  1~2분

  앞의 다섯이 **무료**인 것이 중요하다 — 도구가 무엇을 돌려주는지, 앵커가 어디로
  가는지, 앵커 없는 질문에 무슨 사건이 잡히는지는 LLM 없이 전부 확인된다.
  `ask` 는 「Agent 가 그 도구들을 **실제로 골라 부르는가**」를 볼 때만 쓴다.

──────────────────────────────────────────────────────────────────────
  ★빈 화면은 고장이 아니다 — 무엇이 얼마나 걸리나 (2026-09-05 실측)
──────────────────────────────────────────────────────────────────────

    모듈 적재   10초쯤   kiwipiepy 모델 105MB + chromadb   ← 첫 실행이 특히 느리다
    그 뒤       위 표    저장소 왕복과 LLM

  진행은 `… ` 줄과 INFO 로그로 **`stderr` 에** 나간다. 그래서

      $PY run_test.py retrieve "질문" > 결과.txt     ← 결과만 파일로, 진행은 화면에

  가 된다. **아무 표시도 없이 멈춰 있으면** 그때가 진짜 이상한 것이다.
  예외로 끝나면 `_diagnose()` 가 **어느 컨테이너가 죽었는지** 짚어 준다.

──────────────────────────────────────────────────────────────────────
  ★안 될 때 — 순서대로 (오늘 실제로 다 겪은 것들)
──────────────────────────────────────────────────────────────────────

  ① 명령 자체가 없다      `python` 은 이 환경에 없다. `python3` 는 의존성이 없다.
                         `.venv` 는 **PE32+ Windows 실행파일**이라 WSL 에서 안 돈다.
                         → `.venv-wsl` 하나만 된다. 없으면:

                             uv venv .venv-wsl --python 3.10
                             uv pip install --python .venv-wsl/bin/python \
                                 -r requirements.txt pytest ruff

  ② 저장소가 죽었다       `$PY run_test.py check` 가 1초에 알려 준다.
                         재시작 **직후**에도 몇 초는 거부한다 — `check` 로 확인.

                             docker compose up -d
                             docker ps --filter name=biznode

  ③ 한글이 깨진다        `stdout`·`stderr` 를 둘 다 utf-8 로 돌린다(아래 코드).
                         전에는 `stdout` 만 돌려서 **traceback 이 통째로** 안 보였다.

  ④ 로그가 안 보인다      `configure_logging()` 을 여기서 부른다(아래 코드).
                         전에는 `app/api/main.py` 만 불러서 수동 실행에는 안 나왔다.

──────────────────────────────────────────────────────────────────────
  ★앵커 없는 질문을 볼 때의 순서 (2026-09-02)
──────────────────────────────────────────────────────────────────────

    ① anchor    → `anchor_source` 가 `anchorless` 로 나오나
                  ★여기서 `query` 가 나오면 그 아래는 볼 것도 없다 —
                    「요즘」·「대상」·「미래」 같은 흔한 낱말이 실제 사명이라
                    엉뚱한 기업이 앵커로 잡힌다(F2). 이 모드가 경고를 찍는다
    ② global    → 전역 후보에서 무엇이 뽑히나 (규칙 티어·위험·최근창이 보인다)
    ③ retrieve  → 그 사건에서 기업이 역산되고 관계·근거까지 붙는가
    ④ ask       → Agent 가 그 재료를 실제로 집어 답하나 (LLM)

  ②와 ③이 **같은 사건 목록**을 내야 한다. 다르면 `scope.event_pairs` 배선이
  깨진 것이다.

★`ask` 는 **관측치를 함께 찍는다** — 도구 호출 · 링 분포 · 예산 · 임베딩 ·
  최종 인용. 답변만 보면 「왜 이 재료가 왔나」를 되짚을 수 없다.

★워크스페이스는 **검색 경계가 아니라 랭킹 문맥**이다(최종 설계 §6-1·§17-1).
  비어 있어도 Global Search 로 답한다. 기본값을 삼성전자·SK하이닉스로 둔 것은
  **순위가 문맥으로 어떻게 달라지나**를 보기 위해서다 — `--workspace` 로 바꾸고,
  `--workspace` 만 주면(빈 목록) 문맥 없는 경로를 볼 수 있다.

★**옵션은 모드 뒤에 쓴다** — 앞에 쓰면 모드 이름까지 옵션 값으로 먹힌다.

    # 보고 있는 기업이 있다 (워크스페이스는 기본값 그대로)
    $PY run_test.py ask "이 회사 최근 리스크 어때?" --context 00126380

    # 워크스페이스 없이 — 빈 `--workspace` 가 먼저, `--context` 가 맨 끝
    $PY run_test.py ask "이 회사 최근 리스크 어때?" --workspace --context 00126380

  ★위 두 줄은 **그대로 복사해 붙이면 된다.** 설명을 명령 뒤에 `#` 없이 달지
    않는다 — 맨 끝 옵션이 `nargs="*"` 라 그 설명까지 기업 키로 먹는다. 실제로
    「워크스페이스 없이」가 앵커로 들어가 도구 4종이 범위 밖으로 거부됐다
    (2026-08-29). 명령과 설명은 **줄을 갈라 둔다.**
"""

from __future__ import annotations

import argparse
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

# ★`stdout` 만 utf-8 로 돌리면 **예외 traceback 은 그대로 깨진다**(2026-09-05
#   실측) — Windows OS 에러 문자열(`WinError 10061` 등)이 콘솔 코드페이지로
#   나가 「?????」로 보인다. traceback 은 `stderr` 로 나가므로 그것도 돌린다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ★**여기서 로그를 켠다.** `app/api/main.py` 만 `configure_logging()` 을 불러서
#   서버로 돌릴 때는 Agent 턴·도구 호출·예산 소진·LLM 요청/응답이 다 찍히는데,
#   `run_test.py` 는 그걸 부른 적이 없어 root 로거가 기본 WARNING 인 채로
#   남는다 — `agent.turn`·`run_tools`·`llm.request` 같은, 도구 7종의 관측치와
#   정확히 같은 목적의 로그 줄이 **전부 조용히 버려졌다**(2026-09-05 실측).
#   `ask` 의 docstring 이 「관측치를 함께 찍는다」고 적어 둔 것이 바로 이 로그다.
from app.core.config import LOG_LEVEL  # noqa: E402
from app.core.trace import configure_logging  # noqa: E402

configure_logging(LOG_LEVEL)

# 기본 워크스페이스 — 대표 질문이 이 둘 중심이라 재료가 확실히 잡힌다.
_WORKSPACE = ["00126380", "00164779"]      # 삼성전자 · SK하이닉스

_LINE = "─" * 72


def _head(title: str) -> None:
    print(f"\n{_LINE}\n  {title}\n{_LINE}")


def _step(text: str) -> None:
    """진행 중임을 **즉시** 알린다.

    ★왜 필요한가 (2026-09-05 실측). 빈 화면이 24초 이어졌다:

        0.6s   schemas import
       11.8s   retrieve_service import   ← kiwipiepy 모델 105MB + chromadb
        0.8s   RetrieveService() 생성
       11.4s   retrieve() 본체

      앞의 12초는 **로그가 나올 수 없는 구간**이다 — 로거를 켜 두는 것으로는
      안 되고(`configure_logging` 은 이미 켜져 있다), 우리 코드가 아직 안 돌기
      때문이다. `python -u` 도 소용없다. 버퍼가 아니라 **찍을 게 없어서**다.

      그동안 사용자는 「도는 중」과 「죽었다」를 구별할 수 없었다. 실제로 그렇게
      막혔고, 그게 이 함수가 생긴 이유다.

    ★`stderr` 로 보낸다 — INFO 로그와 **같은 스트림**이라 시간 순서가 맞고,
      `> out.txt` 로 결과만 받을 때 진행 표시가 섞이지 않는다.
    """
    print(f"  … {text}", file=sys.stderr, flush=True)


# ══════════════════════════════════════════════════════════════════
#  check — ★**돌리기 전에 1초 만에** 되는 상태인지 본다 (무료)
# ══════════════════════════════════════════════════════════════════


_PROBES = [
    ("Neo4j",      "NEO4J_URI",     "bolt://127.0.0.1:7687", 7687),
    ("PostgreSQL", "POSTGRES_HOST", "127.0.0.1",             5432),
    ("Chroma",     "CHROMA_HOST",   "127.0.0.1",             8001),
    ("Redis",      "REDIS_HOST",    "127.0.0.1",             6379),
]


def cmd_check(args) -> None:
    """저장소 넷과 API 키가 있는지만 본다. ★**무거운 import 를 하지 않는다.**

    ★이 모드가 생긴 이유(2026-09-05). 컨테이너가 재시작하는 중에 `ask` 를 돌려
      24초를 기다렸다가 예외로 끝나는 일이 실제로 있었다. 그때 알고 싶은 것은
      「내 코드가 깨졌나」가 아니라 **「지금 돌릴 수 있는 상태인가」** 하나였다.

    ★`app.services` 를 부르지 않는다 — 그쪽을 import 하는 순간 kiwipiepy 모델
      105MB 를 읽느라 10초가 든다. 여기서는 **소켓만 두드린다.**
    """
    import os
    import socket
    from urllib.parse import urlparse

    _head("환경 점검  (무거운 import 없음 · 1초)")
    bad = []
    for label, env, default, port in _PROBES:
        raw = os.getenv(env, default)
        host = urlparse(raw).hostname if "://" in raw else raw
        if env == "NEO4J_URI":
            port = urlparse(raw).port or port
        else:
            port = int(os.getenv(env.replace("_HOST", "_PORT"), port))
        try:
            with socket.create_connection((host, port), timeout=1.5):
                print(f"  ✅ {label:12} {host}:{port}")
        except OSError as exc:
            bad.append(label)
            print(f"  ❌ {label:12} {host}:{port}  — {exc.__class__.__name__}")

    print()
    for label, env in (("OpenAI", "OPENAI_API_KEY"), ("DART", "DART_KEY")):
        got = os.getenv(env)
        print(f"  {'✅' if got else '❌'} {label:12} {env}"
              f"{' (…' + got[-4:] + ')' if got else ' — .env 에 없다'}")

    if bad:
        print(f"\n  ★{' · '.join(bad)} 가 응답하지 않는다. 컨테이너부터 보라:")
        print("      docker ps --filter name=biznode")
        print("      docker compose up -d")
        raise SystemExit(1)
    print("\n  ★전부 정상 — 아래 수동 시험이 돌 수 있는 상태다.")


# ══════════════════════════════════════════════════════════════════
#  serve — ★API 서버를 로그와 함께 띄운다
# ══════════════════════════════════════════════════════════════════


def cmd_serve(args) -> None:
    """`app.api.main:app` 을 띄운다. **저장소에 서버 실행법이 없어서** 여기 둔다
    (2026-09-05) — `app/api/main.py` 머리말의 `uvicorn …` 한 줄이 전부였다.

    ★`--reload` 를 기본으로 켜지 않는다. 켜면 uvicorn 이 프로세스를 하나 더
      띄우고, 그 자식이 모듈을 다시 import 하느라 **기동이 두 배**로 든다
      (kiwipiepy 10초 × 2). 코드를 고치며 볼 때만 `--reload` 로 켠다.
    """
    import uvicorn

    _head(f"API 서버 — http://127.0.0.1:{args.port}")
    print(f"  문서      http://127.0.0.1:{args.port}/docs")
    print(f"  미리보기   http://127.0.0.1:{args.port}/preview")
    print(f"  헬스체크   http://127.0.0.1:{args.port}/health")
    print("\n  ★기동에 10초쯤 걸린다 — kiwipiepy 모델(105MB)을 읽는다.")
    print("    `Application startup complete.` 가 뜨면 준비된 것이다.")
    print("    멈추려면 Ctrl+C.\n")
    uvicorn.run("app.api.main:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


# ══════════════════════════════════════════════════════════════════
#  anchor — 질문이 어느 갈래로 가나 (무료)
# ══════════════════════════════════════════════════════════════════


def cmd_anchor(args) -> None:
    """★`/ask` 의 척추는 `anchor_source` 네 갈래다. 이 모드가 그것만 본다.

        QUERY       질문이 대상을 지정했고 해소됐다     → Agent 호출됨
        CONTEXT     **보고 있는 기업**이 있다           → Agent 호출됨
        ANCHORLESS  질문이 대상을 **지정하지 않았다**    → Agent 호출됨 · 검색 히트가 재료
        UNRESOLVED  지정했는데 못 찾았다                → ★Agent 를 아예 안 부른다

    ★마지막이 「TSMC 를 물었는데 삼성전자로 답하는」 오답을 막는 장치다 —
      워크스페이스로 **갈아타지 않는다**(설계서 §14-3).

    ★`ANCHORLESS` 도 워크스페이스로 갈아타지 않는다(최종 설계 §17-3). 전에는
      이 자리가 `WORKSPACE` 였고 담아 둔 기업이 대상으로 승격됐다.
    """
    from app.api.schemas import AskRequest
    from app.graph.nodes import material
    from app.graph.state import initial_state

    state = initial_state(AskRequest(question=args.question,
                                     workspace_keys=args.workspace,
                                     context_keys=args.context))
    # ★출발점 게이트는 **없어졌다**(최종 설계 §17-1). `--workspace` 와
    #   `--context` 가 둘 다 비어도 검색은 그대로 돈다.
    state.update(material.search(state))
    state.update(material.resolve_anchor(state))

    decision, result = state["decision"], state["result"]
    _head(f"앵커 판정 — {args.question!r}")
    print(f"  anchor_source : {decision.source.value}")
    print(f"  Agent 호출    : {'안 함 (halt_no_material)' if decision.source.value == 'unresolved' else '함'}")
    if decision.anchors:
        print(f"  앵커          : {', '.join(f'{a.name}({a.key})' for a in decision.anchors)}")
    if decision.named:
        print(f"  못 찾은 이름  : {decision.named!r}")
    print(f"  검색 mode     : {result.mode.value} · 히트 {result.total}건")
    if result.hits:
        print(f"  상위          : {' · '.join(h.name for h in result.hits[:5])}")

    # ★재료가 **어디서 오나** — 2026-09-02 부터 갈래가 둘이다.
    if decision.source.value == "anchorless":
        print(f"  match_type    : SEMANTIC  (앵커가 없으면 「정확 일치」가 아니다)")
        print("\n  ★위 히트는 **재료가 아니다**(2026-09-02). 관계 신선도 순이라 기업이")
        print("    사실상 임의로 정해지던 자리다 — 이제 앵커가 없으면 히트를 안 쓰고")
        print("    전역 사건을 먼저 골라 **거기서 기업을 역산**한다.")
        print("\n  → 재료는 **전역 사건 검색**이 댄다. `global` 모드로 이어서 보라:")
        print(f"       python run_test.py global {args.question!r}")
    elif decision.source.value in ("query", "context"):
        # ★F2 — 흔한 한국어 낱말과 겹치는 실제 사명이 있다. 앵커가 그중 하나로
        #   잡히면 전역 경로로 갔어야 할 질문이 **통째로 막힌다**(사건 0건).
        trap = {"요즘", "오늘", "우리", "미래", "대상"}
        hit_trap = [a.name for a in decision.anchors if a.name in trap]
        if hit_trap:
            print(f"\n  ★★경고 — 앵커 {hit_trap} 은(는) 실제 사명이지만 **흔한 낱말**이다(F2).")
            print("    질문이 그 회사를 물은 게 아니라면 이건 오탐이고, 앵커가 잡히는 바람에")
            print("    전역 사건 검색으로 **안 간다** — 재료가 0건이 되기 쉽다.")
            print("    확인: 같은 질문에서 그 낱말만 빼고 다시 돌려 보라.")


# ══════════════════════════════════════════════════════════════════
#  search — 검색 계층만 (무료 · 기존 동작)
# ══════════════════════════════════════════════════════════════════


def cmd_search(args) -> None:
    """`SearchOrchestrator.search()` → GraphSearcher·VectorSearcher 를 부르고
    ResultRanker 가 RRF 로 병합한 결과."""
    from search.dto.search_request import SearchRequest
    from search.service.factory import build_orchestrator

    query, result = build_orchestrator().search(
        SearchRequest(query=args.question, workspace_keys=args.workspace))

    _head(f"검색 — {args.question!r}")
    _step("검색 계층 — 의미검색으로 빠지면 10초쯤")
    print(f"  mode {query.mode.value} · edge_types {query.edge_types} "
          f"· direction {query.direction} · 총 {result.total}건\n")
    for hit in result.hits:
        # ★`score` 가 아니라 `source_score` 다 — 점수 하나에 뜻이 셋 섞여 있어
        #   이름으로 갈랐다. 최종 순위는 `rank`, RRF 값은 `rrf_score`.
        rrf = f" rrf={hit.rrf_score:.5f}" if hit.rrf_score is not None else ""
        print(f"  {hit.rank:2}. {hit.name:24} {hit.entity_id:12} "
              f"score={hit.source_score:.4f}{rrf} {hit.sources}")


# ══════════════════════════════════════════════════════════════════
#  global — 전역 사건 검색만 (무료 · 임베딩만 부른다)
# ══════════════════════════════════════════════════════════════════


def cmd_global(args) -> None:
    """★**앵커 없는 질문의 재료가 어디서 오나** — 최종 설계 §5 시나리오 3 · §17-2.

    전에는 이 질문이 Event 노드를 **한 번도 안 건드렸다**(F1). 「투자」가 사건이
    아니라 지분관계로 읽혀 관계 신선도 순으로 기업 5곳을 채웠고, 그 결과가
    (주)DB Inc.·IMANTOAG·유진로봇이었다.

    ★**앵커 판정을 거치지 않는다.** 질문이 실제로 `anchorless` 로 가는지는
      `anchor` 모드가 본다 — 여기서는 「전역 후보에서 무엇이 뽑히나」만 본다.
      둘을 섞으면 「앵커가 잘못 잡혀서」와 「랭킹이 잘못돼서」를 못 가른다.

    ★세 축이 따로 보인다 — 규칙 티어(`matched`) · 위험(`risk`) · 최근창(`recent`).
      셋 다 hard filter 가 아니라 **정렬 키**다. 「최근 리스크 뭐가 있어?」는
      `matched=∅` 인데도 제대로 서는 것이 정상이다(위험·시간 축만으로).
    """
    import time

    from app.services import company_service, evidence_selector as es
    from app.services.retrieve_service import (companies_of_events,
                                               default_embed,
                                               select_global_events)

    _head(f"전역 사건 검색 — {args.question!r}   (LLM 없음 · 임베딩만)")

    intent = es.intent_of(args.question, [])
    matched = es.matched_event_types(intent)
    risk = es.risk_intent(intent)
    recent = es.recent_window() if es.recent_intent(intent) else None

    print(f"  intent  : {intent!r}")
    print(f"  규칙 티어: {', '.join(sorted(matched)) or '∅  (임베딩·위험·최근창이 순위를 만든다)'}")
    print(f"  위험 축  : {'켜짐' if risk else '꺼짐'}"
          f"   최근 축 : {recent or '꺼짐'}")

    t0 = time.perf_counter()
    rows = company_service.global_events()
    t_q = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    events = select_global_events(args.question, embed=default_embed,
                                  limit=args.limit)
    t_s = (time.perf_counter() - t0) * 1000

    firms = companies_of_events(events)
    print(f"\n  후보    : {len(rows)}행 · 고유 사건 {len({r['event_id'] for r in rows})}"
          f" · 기업 {len({r['company']['key'] for r in rows if r['company']})}"
          f"   (Cypher {t_q:.0f}ms)")
    print(f"  선택    : {len(events)}건 → 기업 {len(firms)}곳 역산   (선택 {t_s:.0f}ms)")

    print(f"\n  {'날짜':<12} {'type':<8} {'위험':<4} 사건 · 기업")
    print("  " + "─" * 68)
    for event in events:
        firm = event.company.name if event.company else "★기업 없음"
        mark = "★" if event.event_type in matched else " "
        print(f"  {event.occurred_at or '?':<12} {event.event_type:<8} "
              f"{'위험' if event.is_risk else '  ':<4} {mark}{event.name[:34]} · {firm}")

    print(f"\n  ★ 표시 = 규칙 티어가 켠 type. 없는 것은 임베딩·위험·최근창이 올린 것이다.")
    print(f"  역산 기업: {' · '.join(c.name for c in firms)}")
    print(f"\n  → 같은 사건이 `/retrieve` 에서도 나와야 한다:")
    print(f"       python run_test.py retrieve {args.question!r}")


# ══════════════════════════════════════════════════════════════════
#  retrieve — 재료 조립 끝까지 (무료 · 임베딩만)
# ══════════════════════════════════════════════════════════════════


def cmd_retrieve(args) -> None:
    """`/retrieve` 종단 — **LLM 없이 재료가 다 나온다.**

    ★`ask` 와 다른 것 — 여기는 Agent 가 없다. 그래서 「재료가 없다」가 나오면
      **Agent 문제가 아니라 조립 문제**임이 바로 갈린다. `ask` 만 보면 그 둘이
      섞인다.

    ★앵커 없는 질문에서는 `events[].company` 가 차 있어야 한다 — 사건마다 기업이
      다르므로, 비어 있으면 화면이 「누구에게 난 일인지 모르는 사건」을 그리게 된다.
    """
    import time

    from app.api.schemas import AskRequest
    from app.services.retrieve_service import RetrieveService

    _head(f"/retrieve — {args.question!r}   (LLM 없음)")

    _step("검색 → 재료 조립 — 10초쯤 · 아래 INFO 로그로 단계가 보인다")
    t0 = time.perf_counter()
    got = RetrieveService().retrieve(AskRequest(
        question=args.question, workspace_keys=args.workspace,
        context_keys=args.context))
    ms = (time.perf_counter() - t0) * 1000

    with_company = sum(1 for e in got.events if e.company)
    print(f"  match_type : {got.match_type.value}"
          f"{'   ← 앵커 없음' if got.match_type.value == 'SEMANTIC' else ''}")
    print(f"  앵커       : {', '.join(f'{a.name}({a.key})' for a in got.anchors) or '없음'}")
    print(f"  재료       : 기업 {len(got.companies)} · 사건 {len(got.events)} · "
          f"관계 {len(got.relations)} · 파급 {len(got.propagation)} · "
          f"근거 {len(got.evidence)}   ({ms:.0f}ms)")
    print(f"  사건의 기업 : {with_company}/{len(got.events)}건에 실렸다"
          f"{'   (앵커 경로는 0 이 정상)' if with_company == 0 else ''}")

    if not got.events:
        print("\n  ★사건이 0건이다. 앵커 판정부터 보라:")
        print(f"       python run_test.py anchor {args.question!r}")
        return

    print(f"\n  {'날짜':<12} {'type':<8} 사건 · 기업")
    print("  " + "─" * 68)
    for event in got.events[:args.limit]:
        firm = event.company.name if event.company else "—"
        print(f"  {event.occurred_at or '?':<12} {event.event_type:<8} "
              f"{event.name[:34]} · {firm}")
    if len(got.events) > args.limit:
        print(f"  … {len(got.events) - args.limit}건 더 (--limit 로 늘린다)")

    print(f"\n  기업: {' · '.join(f'{c.name}({c.key})' for c in got.companies)}")


# ══════════════════════════════════════════════════════════════════
#  tools — 도구 7종을 직접 부른다 (무료)
# ══════════════════════════════════════════════════════════════════


def cmd_tools(args) -> None:
    """★**Agent 없이 도구만 부른다.** LLM 이 무엇을 고를지와 무관하게 「도구가
    무엇을 돌려주는가」를 본다 — 재료가 비면 도구 문제인지 Agent 문제인지
    여기서 갈린다.

    ★도구는 범위를 **인자로 받지 않는다**(4원칙 ①). 그래서 `anchor_scope` 를
      먼저 열어야 한다 — 안 열면 `scope.context()` 가 `None` 이라 거부된다.
    """
    import json

    from app.tools import agent_tools, citation, scope

    # ★사명을 그대로 넘긴다. `keys.resolved()` 가 정본으로 되짚으므로
    #   `corp_code` 든 `norm_name` 이든 같은 기업이면 같은 재료가 나온다
    #   (현황서 §6-0 A-5 — 전에는 이름으로 부르면 거부됐다).
    key = args.key
    rejected: list[str] = []
    _head(f"도구 7종 — key={key!r}  (LLM 없음)")

    with scope.anchor_scope([key], workspace_keys=args.workspace,
                            anchor_keys=[key], anchor_names=[key],
                            intent=args.query):
        calls = [
            ("get_relations", lambda: agent_tools.get_relations([key])),
            ("get_events", lambda: agent_tools.get_events([key])),
            ("search_news", lambda: agent_tools.search_news(args.query, [key])),
            ("search_dart", lambda: agent_tools.search_dart(args.query, [key])),
            ("get_business_overview",
             lambda: agent_tools.get_business_overview(key)),
            ("get_market", lambda: agent_tools.get_market(key)),
            ("get_filings", lambda: agent_tools.get_filings(key)),
        ]
        for name, call in calls:
            raw = call()
            got = json.loads(raw)
            citable = "인용 가능" if name in citation.CITABLE_TOOLS else "인용 불가"
            if isinstance(got, dict) and "error" in got:
                print(f"\n  ★{name:22} 거부 — {got['error']}")
                rejected.append(name)
                continue
            print(f"\n  {name:22} {len(got):3}건  ({citable})")
            for item in got[:args.limit]:
                print(f"      {_one_line(item)}")

    # ★그래프 도구(Neo4j)와 기업 도구(PostgreSQL)는 **key 를 다르게 해소한다.**
    #   표시명을 넣으면 앞의 넷만 조용히 거부되는데, 그걸 「재료가 없다」로
    #   읽기 쉽다. 실제로 그렇게 헷갈렸다 — 그래서 원인을 짚어 준다.
    graph_tools_rejected = {"get_relations", "get_events",
                            "search_news", "search_dart"} & set(rejected)
    if graph_tools_rejected and len(rejected) < 7:
        print(f"\n  ★그래프 도구 {len(graph_tools_rejected)}종만 거부됐습니다 — "
              f"key 형태 문제입니다.")
        print("    저쪽은 Neo4j 에서 Company 를 찾으므로 **corp_code 또는 norm_name** "
              "이어야 하고,")
        print("    기업 도구(PostgreSQL)는 표시명도 받습니다. "
              "corp_code 로 다시 해 보세요 — 예: 00126380(삼성전자)")


def _one_line(item: dict) -> str:
    """DTO 한 건을 한 줄로.

    ★**DTO 를 판별 필드로 가른다.** 전에는 필드 목록을 차례로 훑어 「있는 것만」
      찍었는데, 근거(`EvidenceHitDTO`)가 `occurred_at` 하나만 걸려 `occurred_at=…`
      만 나왔다 — 정작 봐야 할 원문과 `evidence_id` 가 안 보였다. 판별 필드를
      정해 두면 그런 부분 일치가 안 생긴다.
    """
    # ★판별 순서가 중요하다 — `RelationDTO` 도 `evidence_id` 를 갖는다. 근거를
    #   먼저 보면 관계가 근거처럼 찍힌다(실제로 그렇게 나왔다).
    if "edge_id" in item:                            # RelationDTO
        sub = f"({item['subtype']})" if item.get("subtype") else ""
        return (f"{item.get('source')} --{item.get('edge_type')}{sub}--> "
                f"{item.get('target')}  {item.get('freshness_note') or ''}".rstrip())
    if "event_id" in item:                           # EventDTO
        # ★`company` 는 **앵커 없는 질문에서만** 찬다. 안 찍으면 「누구에게 난
        #   일인지 모르는 사건」이 재료로 나가는 것을 손으로 못 잡는다.
        firm = f" · {item['company']['name']}" if item.get("company") else ""
        return (f"{item.get('name')}  [{item.get('event_type')}]  "
                f"{item.get('occurred_at') or '시점 없음'}{firm}")
    if "evidence_id" in item:                        # EvidenceHitDTO
        text = (item.get("text") or "").replace("\n", " ")
        edge = f" [{item['edge_type']}]" if item.get("edge_type") else ""
        return (f"{item['evidence_id']}  {item.get('source_type', '?')}{edge}  "
                f"{text[:64]}{'…' if len(text) > 64 else ''}")
    if "rcept_no" in item and "title" in item:       # FilingDTO
        return f"{item.get('rcept_dt')}  {item.get('title')}"
    if "market_cap" in item or "per" in item:        # market — 계산값
        return " · ".join(f"{k}={v}" for k, v in item.items()
                          if k in ("corp_name", "close_price", "market_cap",
                                   "per", "pbr", "psr") and v is not None)
    if "overview_text" in item:                      # business_overview
        text = (item.get("overview_text") or "").replace("\n", " ")
        return (f"{item.get('bsns_year')}년 · {len(item.get('overview_text') or '')}자  "
                f"{text[:60]}…")
    text = str(item)
    return text[:110] + ("…" if len(text) > 110 else "")


# ══════════════════════════════════════════════════════════════════
#  ask — ★Agent 루프 끝까지 (LLM 호출 · 비용)
# ══════════════════════════════════════════════════════════════════


def cmd_ask(args) -> None:
    """질문 하나를 그래프로 끝까지 돌리고 **관측치를 함께** 찍는다.

    ★`run_ask()` 를 안 쓴다 — 저건 `AskResponse` 만 돌려주는데, 수동 시험에서
      보고 싶은 것은 그 안이다(어떤 도구를 불렀나 · 링이 어떻게 갈렸나 · 예산이
      찼나). `run_ask()` 가 하는 일을 여기서 그대로 하되 State 를 통째로 받는다.
    """
    from app.api.schemas import AskRequest
    from app.core import observe
    from app.core.trace import new_trace_id
    from app.graph import budget
    from app.graph.ask_graph import ask_graph
    from app.graph.state import initial_state

    _head(f"/ask — {args.question!r}   ★LLM 을 부른다")

    new_trace_id()
    with observe.observing() as seen:
        _step("Agent 루프 — 도구 호출과 LLM 이 붙어 1~2분 걸린다")
        state = ask_graph().invoke(
            # ★`context_keys` 를 **빠뜨리면 안 된다.** 전에 여기만 빠져 있어
            #   `--context` 가 조용히 버려졌다 — `anchor` 는 CONTEXT 로 가는데
            #   `ask` 는 같은 입력에서 재료 없이 멈췄다(2026-08-29).
            initial_state(AskRequest(question=args.question,
                                     workspace_keys=args.workspace,
                                     context_keys=args.context)))

    decision = state.get("decision")
    source = decision.source.value if decision else "—"
    agent_called = bool(state.get("messages"))

    print(f"\n  [앵커]  {source}"
          f"{' · ' + ', '.join(a.name for a in decision.anchors) if decision and decision.anchors else ''}")
    print(f"  [Agent] {'호출됨' if agent_called else '★미호출 — 재료를 만들지 않는다'}")

    if seen.tools_used:
        used = " · ".join(f"{k}×{v}" for k, v in sorted(seen.tools_used.items()))
        print(f"  [도구]  호출 {seen.tool_calls}회 — {used}")
    else:
        print(f"  [도구]  호출 {seen.tool_calls}회")
    if seen.tool_errors:
        print(f"          ★거부 {dict(seen.tool_errors)} — 범위 밖 key 다")

    print(f"  [재료]  관계 {len(state.get('relations') or [])} · "
          f"사건 {len(state.get('events') or [])} · "
          f"파급 {len(state.get('propagation') or [])} · "
          f"근거 {len(state.get('evidence') or [])}")

    # ★앵커 없는 경로는 **서버가 사건을 먼저 고르고** 그 쌍을 도구에 넘긴다.
    #   도구가 다시 고르면 기업당 10건 × 최대 10곳이 되어 `/retrieve` 와 갈린다.
    pairs = state.get("event_pairs") or []
    if pairs:
        events = state.get("events") or []
        with_company = sum(1 for e in events if getattr(e, "company", None))
        print(f"  [전역]  서버가 고른 사건 {len(pairs)}쌍 → 재료 사건 {len(events)}건 "
              f"· 기업 실린 사건 {with_company}건")
        if len(events) > len(pairs):
            print("          ★재료가 고른 것보다 많다 — 도구가 다시 고르고 있다."
                  " `scope.event_pairs` 배선을 볼 것")

    if seen.ring_seen or seen.ring_kept:
        print(f"  [링]    본 것 {dict(sorted(seen.ring_seen.items()))} → "
              f"남은 것 {dict(sorted(seen.ring_kept.items()))} "
              f"(kept {seen.relations_kept} · cut {seen.relations_cut})")
        print(f"          최종 인용 링 {dict(sorted(seen.cited_rings.items()))} · "
              f"관계 아닌 근거 인용 {seen.cited_without_ring}건 (정상)")
        if seen.cited_relation_without_ring:
            print(f"          ★관계인데 링을 못 찾은 인용 "
                  f"{seen.cited_relation_without_ring}건 — 결함 신호다")

    # ★예산 — 「루프가 잘렸나」와 「끝난 뒤 플래그가 켜졌나」는 다른 사건이다.
    caps = {"tool_calls_used": budget.MAX_TOOL_CALLS,
            "events_used": budget.MAX_EVENTS}
    spent = " · ".join(f"{n.replace('_used', '')} {state.get(n) or 0}/{c}"
                       for n, c in caps.items())
    print(f"  [예산]  {spent} · 파급 {state.get('propagations_used') or 0}"
          f"/{budget.MAX_PROPAGATIONS} (★세기만 — 소진 판정 아님)")
    if seen.agent_stopped_by_budget:
        print("          ★Agent 루프가 예산으로 잘렸다 — 재료가 적은 이유다")
    elif state.get("budget_exhausted"):
        print("          ★플래그는 켜졌는데 루프는 안 잘렸다 — 2026-08-29 이후로는 "
              "안 나와야 한다(파급이 소진 판정에서 빠졌다). 나오면 조사할 것")

    print(f"  [임베딩] {seen.embed_calls}회 · 캐시 적중 {seen.embed_cache_hits} · "
          f"빗나감 {seen.embed_cache_misses}")
    if seen.embed_cache_misses:
        print("          ★빗나간 만큼 직접 계산했다 — 그 값은 실행마다 흔들린다")

    response = state.get("response")
    if response is None:
        print("\n  ★응답이 없다 — 그래프 배선이 깨졌다")
        return

    _head("답변")
    print(f"  failed={response.failed} · anchor_source={response.anchor_source.value}\n")
    for line in response.answer.splitlines():
        print(f"  {line}")

    if response.sources:
        _head(f"근거 {len(response.sources)}건")
        for source_row in response.sources:
            note = " ★원문 없음" if getattr(source_row, "missing", False) else ""
            print(f"  {source_row.evidence_id}  "
                  f"[{getattr(source_row, 'source_type', '?')}]{note}")


# ══════════════════════════════════════════════════════════════════
#  실패 진단 — Docker 인프라(Neo4j·PostgreSQL·Chroma)가 죽어 있을 때
# ══════════════════════════════════════════════════════════════════

# ★2026-09-05 실측 — `ask` 실행 중 Chroma 컨테이너가 **재시작 도중**이라
#   `ConnectionRefusedError`(WinError 10061)가 났다. `docker ps` 는 "Up" 이라고
#   찍지만 그 순간 포트가 안 열려 있을 수 있다 — 코드 결함이 아니라 인프라
#   타이밍 문제인데, 원본 traceback 만 보면 그걸 한눈에 못 알아본다. 흔한 신호를
#   미리 걸러 **어느 컨테이너·어느 포트를 볼지** 바로 알려준다.
_INFRA_HINTS = [
    ("Chroma", "8001", ("chroma", "8001")),
    ("Neo4j", "7687", ("neo4j", "7687", "bolt")),
    ("PostgreSQL", "5432", ("postgres", "5432", "psycopg")),
    ("Redis", "6379", ("redis", "6379")),
]


def _diagnose(exc: BaseException) -> str | None:
    """예외 사슬에서 흔한 인프라 다운 신호를 찾는다. 못 찾으면 `None`."""
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in chain:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__

    text = " ".join(f"{type(e).__module__}.{type(e).__name__}: {e}" for e in chain).lower()
    connection_signal = any(s in text for s in (
        "connectionrefused", "connecterror", "10061", "serviceunavailable",
        "connection refused", "could not connect", "operationalerror",
    ))
    if not connection_signal:
        return None

    for label, port, needles in _INFRA_HINTS:
        if any(n in text for n in needles):
            return (f"★{label}(포트 {port}) 연결이 거부됐다 — 컨테이너가 죽었거나 "
                    f"막 재시작하는 중일 수 있다.\n"
                    f"    확인: docker ps --filter name=biznode\n"
                    f"    로그: docker logs biznode-{label.lower()} --tail 30")
    return ("★연결이 거부됐다 — Docker 컨테이너(Neo4j·PostgreSQL·Chroma·Redis) 상태를 "
            "확인해 보라.\n    docker ps --filter name=biznode")


# ══════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BizNode 수동 시험 — ★`ask` 만 LLM 을 부른다(비용).")
    sub = parser.add_subparsers(dest="mode")

    # ★두 옵션은 **모드 뒤에** 온다 — 앞에 두면 `nargs="*"` 가 모드 이름과
    #   질문까지 삼킨다. `--workspace 00126380 ask "질문"` 은 워크스페이스를
    #   `['00126380', 'ask', '질문']` 으로 읽고 모드를 `None` 으로 만들어,
    #   **도움말만 찍고 조용히 끝났다** — 「답변이 안 나온다」의 정체였다.
    #   부모 파서로 내리면 옵션이 명령줄 끝에 서므로 삼킬 것이 없고, 옛 순서는
    #   invalid choice 로 **소리 내며** 실패한다(2026-08-29).
    workspace_opt = argparse.ArgumentParser(add_help=False)
    workspace_opt.add_argument("--workspace", nargs="*", default=_WORKSPACE,
                               help=f"워크스페이스 corp_code (기본 {_WORKSPACE})")
    # ★「담은 것」이 아니라 「보고 있는 것」이다. `--workspace ` (빈 목록)과 함께
    #   주면 **워크스페이스 없이** 답하는 경로를 손으로 확인할 수 있다.
    context_opt = argparse.ArgumentParser(add_help=False)
    context_opt.add_argument("--context", nargs="*", default=[],
                             help="지금 보고 있는 기업 corp_code (기업 상세 화면이 넘기는 값)")

    p = sub.add_parser("check", help="★환경 점검 — 저장소 넷과 키 (무료 · 1초)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("serve", help="★API 서버를 띄운다 (/docs · /preview)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--reload", action="store_true",
                   help="코드를 고치며 볼 때. 기동이 두 배 든다")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("anchor", parents=[workspace_opt, context_opt],
                       help="앵커가 어느 갈래로 가나 (무료)")
    p.add_argument("question")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("search", parents=[workspace_opt],
                       help="검색 계층만 (무료)")
    p.add_argument("question")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("global", parents=[workspace_opt],
                       help="★전역 사건 검색만 — 앵커 없는 질문의 재료 (무료)")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=10, help="몇 건까지 고르나")
    p.set_defaults(func=cmd_global)

    p = sub.add_parser("retrieve", parents=[workspace_opt, context_opt],
                       help="★재료 조립 끝까지 — Agent 없이 (무료)")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=10, help="사건을 몇 건까지 찍나")
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("tools", parents=[workspace_opt],
                       help="도구 7종을 직접 부른다 (무료)")
    p.add_argument("key", help="corp_code 또는 norm_name (예: 00126380 · 삼성전자)")
    p.add_argument("--query", default="최근 리스크", help="의미검색 도구에 넘길 질의")
    p.add_argument("--limit", type=int, default=3, help="도구마다 몇 건까지 찍나")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("ask", parents=[workspace_opt, context_opt],
                       help="★Agent 루프 끝까지 (LLM 호출 · 비용)")
    p.add_argument("question")
    p.set_defaults(func=cmd_ask)

    args = parser.parse_args()
    if args.mode is None:
        parser.print_help()
        print(__doc__)
        return
    # ★여기서 알려야 한다 — 아래 `args.func()` 안의 첫 줄이 **12초짜리
    #   import** 라, 이 줄이 없으면 그동안 화면이 통째로 빈다.
    if args.mode not in ("check", "serve"):
        # ★`check` 는 소켓만 두드리고 `serve` 는 자기 안내를 따로 찍는다.
        _step("모듈 적재 — kiwipiepy 모델(105MB)과 chromadb 를 읽는다 · 10초쯤")
    try:
        args.func(args)
    except Exception as exc:
        hint = _diagnose(exc)
        print(f"\n{_LINE}\n  ★실행 중 예외로 끝났다 — {type(exc).__name__}: {exc}\n{_LINE}",
              file=sys.stderr)
        if hint:
            print(f"\n{hint}\n", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
