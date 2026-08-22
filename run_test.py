# SearchOrchestrator.search() -> GraphSearcher, VectorSearcher 동시 호출 -> ResultRanker로 결과를 RRF 병합한 후 출력.
# python .\run_test.py

from dotenv import load_dotenv

# 1. 환경변수 로드 (.env)
load_dotenv()

from search.dto.search_request import SearchRequest
from search.service.factory import build_orchestrator


def main():
    # 2. 오케스트레이터 — 조립은 search/service/factory.py 한 곳에만 있다
    orch = build_orchestrator()

    # 3. 검색 실행 — SearchOrchestrator.search()는 동기 함수라 await 없이 호출한다
    query, result = orch.search(SearchRequest(query="삼성전자가 납품하는 기업"))

    print(f"mode: {query.mode.value}")
    print(f"총 건수: {result.total}")
    for hit in result.hits:
        # ★`score`가 아니라 `source_score`다 — 점수 하나에 뜻이 셋 섞여 있어
        #   이름으로 갈랐다(D2). 최종 순위는 `rank`, RRF 값은 `rrf_score`.
        print(hit.rank, hit.name, hit.entity_id, round(hit.source_score, 4), hit.sources)


if __name__ == "__main__":
    main()