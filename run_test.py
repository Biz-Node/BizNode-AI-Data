# SearchOrchestrator.search() -> GraphSearcher, VectorSearcher 동시 호출 -> ResultRanker로 결과를 RRF 병합한 후 출력.
# python .\run_test.py

from dotenv import load_dotenv

# 1. 환경변수 로드 (.env)
load_dotenv()

from search.dto.search_request import SearchRequest
from search.repository.chroma_repository import ChromaRepository
from search.repository.postgres_repository import PostgresRepository
from search.service.anchor_extractor import AnchorExtractor
from search.service.entity_resolver import EntityResolver
from search.service.graph_searcher import GraphSearcher
from search.service.orchestrator import SearchOrchestrator
from search.service.query_router import QueryRouter
from search.service.result_ranker import ResultRanker
from search.service.vector_searcher import VectorSearcher


def main():
    # 2. 오케스트레이터 및 의존성 초기화
    orch = SearchOrchestrator(
        entity_resolver=EntityResolver(PostgresRepository()),
        query_router=QueryRouter(),
        graph_searcher=GraphSearcher(),
        vector_searcher=VectorSearcher(ChromaRepository()),
        result_ranker=ResultRanker(),
        anchor_extractor=AnchorExtractor(PostgresRepository()),
    )

    # 3. 검색 실행 — SearchOrchestrator.search()는 동기 함수라 await 없이 호출한다
    query, result = orch.search(SearchRequest(query="삼성전자에 납품하는 기업"))

    print(f"mode: {query.mode.value}")
    print(f"총 건수: {result.total}")
    for hit in result.hits:
        print(hit.name, hit.entity_id, round(hit.score, 4), hit.sources)


if __name__ == "__main__":
    main()