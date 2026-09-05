"""`/ask` 실행 그래프 (LangGraph).

★**이 패키지는 실행 담당이다.** 한 덩어리이던 `/ask` 를 노드로 갈랐을 뿐이고,
  판단 로직은 여전히 밖에 있다 — 검색·재료는 `RetrieveService`, 프롬프트는
  `app/llm`·`app/graph/prompt`, 검사는 `claim_check`·`material_consistency`.
  노드는 위임한다.
"""
