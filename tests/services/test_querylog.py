"""실사용 질의 로깅 — **Phase 6 평가의 재료 조달** (2026-09-02).

★왜 이 파일이 있나

  「규칙 키워드가 실제 질의에서 얼마나 맞고 틀리나」를 실사용 표본으로 재 본 적이
  없다. 근거가 내가 만든 33건과 사건명 1,074건뿐이고, 전자는 event_type 이름
  중심으로 지은 것이라 정답률이 실제보다 높게 나온다. 질의가 **파일로 안 남고
  있었다** — `app/core/trace.py` 가 `basicConfig` 만 세우고 `FileHandler` 가 없다.

★이 그물이 지키는 것 둘

  ① 라벨 없이 정답/누락/오탐을 셀 수 있는 **네 값**이 실제로 남는가
  ② 로깅이 **요청을 죽이지 않는가** — 관측 장치가 서비스를 멈추면 결함이다
"""

from __future__ import annotations

import json

import pytest

from app.core import querylog


@pytest.fixture
def sink(tmp_path, monkeypatch):
    """로그 파일을 임시 경로로 돌린다. 저장소의 `logs/` 를 건드리지 않는다."""
    path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(querylog, "_PATH", path)
    monkeypatch.setattr(querylog, "_DISABLED", False)
    monkeypatch.setattr(querylog, "_warned", False)

    def _read():
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    return _read


def test_the_four_values_phase_six_needs_are_written(sink):
    """★질문 · intent · matched · 고른 사건의 type. 이 넷이면 라벨이 필요 없다."""
    querylog.record(question="최근 노조 이슈", intent="최근 노조 이슈",
                    matched=["노무"], selected_types=["노무", "노무", "실적"],
                    anchor_source="anchorless", n_events=3, n_companies=2)

    row = sink()[0]
    assert row["question"] == "최근 노조 이슈"
    assert row["intent"] == "최근 노조 이슈"
    assert row["matched"] == ["노무"]
    assert row["selected_types"] == ["노무", "실적"], "type 은 집합이면 충분하다"


def test_the_three_buckets_are_derivable_without_labels(sink):
    """★정답/누락/오탐이 **라벨 없이** 갈리는지 — 이 파일의 존재 이유다."""
    querylog.record(question="a", intent="a", matched=["노무"],
                    selected_types=["노무"], anchor_source="anchorless",
                    n_events=1, n_companies=1)          # 정답
    querylog.record(question="b", intent="b", matched=[],
                    selected_types=["실적"], anchor_source="anchorless",
                    n_events=1, n_companies=1)          # 누락
    querylog.record(question="c", intent="c", matched=["사고재해"],
                    selected_types=["사업확장"], anchor_source="anchorless",
                    n_events=1, n_companies=1)          # 오탐

    def bucket(row):
        if not row["matched"]:
            return "누락"
        return "정답" if set(row["matched"]) & set(row["selected_types"]) else "오탐"

    assert [bucket(r) for r in sink()] == ["정답", "누락", "오탐"]


def test_a_broken_sink_does_not_kill_the_request(tmp_path, monkeypatch):
    """★관측이 서비스를 멈추면 그건 관측이 아니라 결함이다."""
    monkeypatch.setattr(querylog, "_DISABLED", False)
    monkeypatch.setattr(querylog, "_warned", False)
    # 디렉터리로 못 만드는 경로 — 파일 위에 디렉터리를 얹으라고 시킨다
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(querylog, "_PATH", blocker / "sub" / "queries.jsonl")

    querylog.record(question="q", intent="q", matched=[], selected_types=[],
                    anchor_source="anchorless", n_events=0, n_companies=0)


def test_turning_it_off_writes_nothing(sink, monkeypatch):
    monkeypatch.setattr(querylog, "_DISABLED", True)

    querylog.record(question="q", intent="q", matched=[], selected_types=[],
                    anchor_source="anchorless", n_events=0, n_companies=0)

    assert sink() == []


def test_batch_runs_are_separable_from_real_traffic(sink):
    """★`trace_id` 로 가른다. `new_trace_id()` 는 **요청 경계에서만** 불리므로
    기준선 도구나 테스트가 같은 함수를 불러도 `"-"` 로 남는다 — Phase 6 집계는
    그걸 빼고 센다. 파라미터로 끄게 만들면 그 스위치를 잘못 두는 날 표본이 빈다.
    """
    from app.core.trace import new_trace_id, reset_trace_id

    reset_trace_id()
    querylog.record(question="배치", intent="배치", matched=[], selected_types=[],
                    anchor_source="anchorless", n_events=0, n_companies=0)
    new_trace_id()
    querylog.record(question="실사용", intent="실사용", matched=[], selected_types=[],
                    anchor_source="anchorless", n_events=0, n_companies=0)
    reset_trace_id()

    rows = sink()
    assert rows[0]["trace_id"] == "-"
    assert rows[1]["trace_id"] != "-"
    assert [r["question"] for r in rows if r["trace_id"] != "-"] == ["실사용"]
