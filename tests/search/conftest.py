"""Search Layer 테스트 공용 fixture.

실제 Docker Compose PostgreSQL/ChromaDB를 대상으로 한다(mock 금지, Task 2 원칙).
"""

import pytest


@pytest.fixture(scope="session")
def postgres_repo():
    from search.repository.postgres_repository import PostgresRepository

    return PostgresRepository()


@pytest.fixture(scope="session")
def chroma_repo():
    from search.repository.chroma_repository import ChromaRepository

    return ChromaRepository()


@pytest.fixture(scope="session")
def entity_resolver(postgres_repo):
    from search.service.entity_resolver import EntityResolver

    return EntityResolver(postgres_repo)


@pytest.fixture(scope="session")
def query_router():
    from search.service.query_router import QueryRouter

    return QueryRouter()


@pytest.fixture(scope="session")
def graph_searcher():
    from search.service.graph_searcher import GraphSearcher

    return GraphSearcher()


@pytest.fixture(scope="session")
def vector_searcher(chroma_repo):
    from search.service.vector_searcher import VectorSearcher

    return VectorSearcher(chroma_repo)


@pytest.fixture(scope="session")
def result_ranker():
    from search.service.result_ranker import ResultRanker

    return ResultRanker()


@pytest.fixture(scope="session")
def orchestrator(entity_resolver, query_router, graph_searcher, vector_searcher, result_ranker):
    from search.service.orchestrator import SearchOrchestrator

    return SearchOrchestrator(
        entity_resolver, query_router, graph_searcher, vector_searcher, result_ranker)
