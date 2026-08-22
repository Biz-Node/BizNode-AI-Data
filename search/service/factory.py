"""Search Layer 조립을 한 곳으로 모은다.

★왜 만들었나 (2026-08-20)

`SearchOrchestrator`는 협력 객체 6개를 전부 생성자로 받는다. 그 조립이 세 곳에
따로 적혀 있었다 — `search/api/search_controller.get_orchestrator()`(프로덕션),
`run_test.py`, `tests/search/conftest.py`. 컨트롤러를 지우면 프로덕션 조립처가
0곳이 되므로, 사라지기 전에 Search Layer 안으로 옮긴다.

★`app/`이 아니라 `search/`에 두는 이유 — 6개를 어떻게 엮는지는 Search Layer의
  지식이다. `RetrieveService`가 그걸 알아야 할 이유가 없다.

★프로세스당 하나만 만든다. 각 컴포넌트가 커넥션·캐시를 들고 있어(ChromaRepository는
  VectorStore를, PostgresRepository는 호출마다 커넥션을 연다) 요청마다 새로 만들면
  낭비다. 테스트는 이 함수를 부르지 않고 직접 조립하거나 `build_orchestrator.
  cache_clear()`로 비운다.
"""

from __future__ import annotations

from functools import lru_cache

from search.repository.chroma_repository import ChromaRepository
from search.repository.postgres_repository import PostgresRepository
from search.service.anchor_extractor import AnchorExtractor
from search.service.entity_resolver import EntityResolver
from search.service.graph_searcher import GraphSearcher
from search.service.orchestrator import SearchOrchestrator
from search.service.query_router import QueryRouter
from search.service.result_ranker import ResultRanker
from search.service.vector_searcher import VectorSearcher


@lru_cache(maxsize=1)
def build_orchestrator() -> SearchOrchestrator:
    """설정이 끝난 `SearchOrchestrator` 하나. 두 번째 호출부터는 같은 것을 준다.

    ★`PostgresRepository`는 하나를 둘이 나눠 쓴다 — 상태가 없고(호출마다
      `postgres_connection()`을 열고 닫는다) 둘로 나눌 이유가 없다.
    ★`GraphSearcher`는 `app.services.graph_service`를 함수 호출하므로 저장소를
      받지 않는다(설계 §4-4, graph_service 우회 금지).
    """
    postgres = PostgresRepository()
    return SearchOrchestrator(
        EntityResolver(postgres),
        QueryRouter(),
        GraphSearcher(),
        VectorSearcher(ChromaRepository()),
        ResultRanker(),
        AnchorExtractor(postgres),
    )
