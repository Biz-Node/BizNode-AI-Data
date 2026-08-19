"""build_orchestrator() — 조립이 한 곳에 모였는가."""

from __future__ import annotations

from search.service.factory import build_orchestrator
from search.service.orchestrator import SearchOrchestrator


def test_builds_a_usable_orchestrator():
    assert isinstance(build_orchestrator(), SearchOrchestrator)


def test_same_instance_per_process():
    """요청마다 새로 만들면 커넥션·캐시를 매번 버리는 셈이다."""
    assert build_orchestrator() is build_orchestrator()


def test_shares_one_postgres_repository():
    """EntityResolver와 AnchorExtractor가 저장소를 나눠 쓴다 — 상태가 없어서
    둘로 나눌 이유가 없다."""
    orch = build_orchestrator()
    assert orch._entity_resolver._repo is orch._anchor_extractor._repo
