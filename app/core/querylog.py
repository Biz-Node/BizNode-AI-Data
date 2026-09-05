"""실사용 질의를 한 줄씩 남긴다 — **Phase 6 평가의 재료 조달** (2026-09-02).

★왜 필요한가

  이 저장소는 「규칙 키워드가 실제 질의에서 얼마나 맞고 얼마나 틀리나」를
  **실사용 표본으로 재 본 적이 없다.** 지금까지의 근거는 전부 내가 만든 33건과
  사건명 1,074건이고, 전자는 event_type 이름 중심으로 지은 것이라 정답률이
  실제보다 높게 나온다. 그런데 그 비율이 뒤집히면 랭킹 배치의 답이 바뀐다
  (규칙 티어를 유사도 아래로 내릴 것인가).

  질의가 **파일로 안 남고 있었다** — `app/core/trace.py` 는 `basicConfig` 만
  세우고 `FileHandler` 가 없어, 프로세스가 끝나면 그대로 사라진다.

★무엇을 남기나 — **라벨 없이 셀 수 있는 것만**

      질문 · intent · matched(규칙이 켠 type) · 실제로 고른 사건의 type

  이 넷이면 라벨 없이 세 갈래가 갈린다:

      정답  matched 가 있고, 고른 사건에 그 type 이 있다
      누락  matched 가 비었다 — 규칙이 아무것도 못 켰다
      오탐  matched 는 있는데 고른 사건에 그 type 이 하나도 없다

  P@10 같은 **정확도**는 여전히 사람 라벨이 필요하다. 그건 6b 다.

★**배치와 실사용을 `trace_id` 로 가른다.** `new_trace_id()` 는 요청 경계에서만
  불리므로, 기준선 도구(`batch/audit/ranking_baseline.py`)나 단위 테스트가 같은
  함수를 부르면 `trace_id` 가 `"-"` 로 남는다. Phase 6 집계는 **`trace_id != "-"`
  만** 센다 — 파라미터로 끄게 만들면 그 스위치를 잘못 두는 날 표본이 조용히 빈다.

★남기지 않는 것

  · 근거 본문 · 기사 원문 — 저작권 경계를 로그로 넘기지 않는다
  · 워크스페이스 key — 질의 분포를 보는 데 필요 없고, 남기면 개인화 기록이 된다

★**절대 요청을 죽이지 않는다.** 디스크가 없든 권한이 없든 예외를 삼킨다 —
  관측 장치가 서비스를 멈추면 그건 관측이 아니라 결함이다. 같은 규약을
  `app/core/observe.py` 가 이미 쓴다.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app.core.config import PROJECT_ROOT
from app.core.trace import current_trace_id, trace_logger

log = trace_logger(__name__)

# ★기본은 **켜짐**이다. 꺼 두면 Phase 6 이 영원히 76건에 갇힌다 — 「나중에 켜자」로
#   미룬 것이 이 자리에서 이미 한 번 일어났다. 끄려면 `BIZNODE_QUERY_LOG=off`.
_PATH_ENV = os.getenv("BIZNODE_QUERY_LOG", "").strip()
_DISABLED = _PATH_ENV.lower() in {"off", "0", "false", "none"}
_PATH = Path(_PATH_ENV) if (_PATH_ENV and not _DISABLED) else PROJECT_ROOT / "logs" / "queries.jsonl"

# 한 줄이 통째로 써지도록만 막는다. 여러 프로세스가 붙으면 OS 의 append 원자성에
# 기댄다 — 한 줄이 4KB 를 넘지 않으므로 실용상 안전하고, 여기서 파일 락까지
# 걸 이유는 없다(로그가 서비스를 기다리게 하면 안 된다).
_LOCK = threading.Lock()
_warned = False


def record(*, question: str, intent: str, matched: Iterable[str],
           selected_types: Iterable[str], anchor_source: str,
           n_events: int, n_companies: int,
           risk_wanted: bool = False, recent_since: Optional[str] = None,
           path: str = "") -> None:
    """질의 한 건. **부르는 쪽은 실패를 신경 쓰지 않아도 된다.**

    `path` 는 어느 경로가 골랐나 — `"global"`(앵커 없음) 또는 `"per_company"`.
    같은 질문이 두 입구에서 각각 한 줄씩 남을 수 있으므로 집계할 때 `trace_id`
    로 묶는다.
    """
    if _DISABLED:
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trace_id": current_trace_id(),
        "question": question,
        "intent": intent,
        "matched": sorted(matched),
        "selected_types": sorted(set(selected_types)),
        "anchor_source": anchor_source,
        "n_events": n_events,
        "n_companies": n_companies,
        "risk_wanted": bool(risk_wanted),
        "recent_since": recent_since,
        "path": path,
    }
    _append(row)


def _append(row: dict) -> None:
    global _warned
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _LOCK, _PATH.open("a", encoding="utf-8") as fp:
            fp.write(line)
    except Exception as exc:  # noqa: BLE001 — 관측이 서비스를 멈추면 안 된다
        if not _warned:      # 한 번만 알린다. 매 요청 경고하면 그게 소음이다
            _warned = True
            log.warning("querylog 를 못 쓴다(%s: %s) — 이후 조용히 건너뛴다. "
                        "Phase 6 표본이 안 쌓인다", type(exc).__name__, exc)
