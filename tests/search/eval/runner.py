"""평가 케이스 1건을 실제 저장소 대상으로 끝까지 돌린다.

테스트(`test_search_eval.py`)와 보고서 생성기(`report.py`)가 같은 실행 경로를
쓰도록 여기 한 곳에만 둔다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from search.dto.search_query import SearchQuery
from search.dto.search_request import SearchRequest
from search.dto.search_result import SearchResult
from search.service.anchor_extractor import AnchorExtractor
from search.service.orchestrator import SearchOrchestrator
from tests.search.eval.cases import CASES, EvalCase


@dataclass(frozen=True)
class CaseRun:
    case: EvalCase
    anchor: Optional[str]
    query: SearchQuery
    result: SearchResult
    took_ms: int


def build_request(case: EvalCase) -> SearchRequest:
    return SearchRequest(
        query=case.query,
        edge_types=list(case.request_edge_types) if case.request_edge_types else None,
        workspace_keys=list(case.workspace_keys),
        top_k=case.top_k,
    )


def run_case(
    case: EvalCase, orchestrator: SearchOrchestrator, extractor: AnchorExtractor,
) -> CaseRun:
    anchor = extractor.extract(case.query)
    start = time.monotonic()
    query, result = orchestrator.search(build_request(case))
    took_ms = int((time.monotonic() - start) * 1000)
    return CaseRun(case=case, anchor=anchor, query=query, result=result, took_ms=took_ms)


def run_all(
    orchestrator: SearchOrchestrator, extractor: AnchorExtractor,
) -> dict[str, CaseRun]:
    return {case.id: run_case(case, orchestrator, extractor) for case in CASES}
