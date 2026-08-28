"""도구 결과 중 **무엇을 인용할 수 있나** — 한 곳에서 정한다.

★왜 목록으로 두나. 「인용 가능한가」는 도구마다 이유가 다르고, 그 이유가
  **코드 여러 곳에 흩어지면 반드시 갈린다.** 재료를 모으는 자리(마감 단계)와
  프롬프트에 문구를 넣는 자리와 현황서가 각자 판단하면, 하나만 바뀌었을 때
  아무도 못 본다. 그래서 규칙은 여기 하나로 두고 나머지는 읽기만 한다.

★**인용 가능 ≠ 신뢰도.** `search_dart` 의 공시 근거가 뉴스보다 확실한 사실인데도
  이 단계에서는 인용으로 안 올린다. 지금 재는 것은 「어느 출처가 더 믿을 만한가」가
  아니라 **「Agent 가 고른 근거가 인용 경로를 제대로 타는가」**이고, 그건 출처
  하나로 먼저 확인하는 편이 귀속이 분명하다.

인용 가능 — `search_news` **하나뿐**
──────────────────────────────────────────────────────────────────
`evidence` 컬렉션 청크라 `evidence_id` 가 이미 있고, 본문·`source_doc`(기사 URL)·
언론사·보도일은 `relation_service.evidence_for_ids()` 가 채운다. 그래서
**코드가 사실상 안 바뀐다** — 기존 화이트리스트(`app/llm/prompt.sources_from`)를
그대로 탄다.

참고 맥락뿐 — 이유가 **둘로 다르다.** 뭉뚱그리면 안 된다
──────────────────────────────────────────────────────────────────
① **이 단계의 범위라서**(나중에 올릴 수 있다)

       search_dart      청크도 `evidence_id` 도 이미 있다. 올리는 데 필요한 것은
                        코드가 아니라 **측정**이다 — 한 출처로 경로를 확인한 뒤

② **구조적으로 못 올려서**(적재가 선행돼야 한다)

       get_business_overview   PostgreSQL 에만 있고 ChromaDB 에 청크가 **없다**
                               (실측 2026-08-28: `vector_chunks` 는 `evidence`
                               10,510 · `company` 2,432 두 종뿐). 근거 본문은
                               `evidence_for_ids()` 한 경로로만 오므로, 억지로
                               id 를 발급해도 `missing:True` 로 나가
                               **「근거 없음」으로 표시된다.** 게다가
                               `overview_text` 는 절 전문이라(64행 · 평균
                               2,294자 · 최대 16,623자) 청킹이 선행돼야 한다

       get_market              **계산값**이다. 근거 id 를 발급하면 원본 갱신 때
                               어긋난다 — 되짚을 것은 계산 좌표다
       get_filings             공시 **목록**이다. 제목까지고 인용할 문장이 없다
"""

from __future__ import annotations

from typing import Any, Iterable

# ★인용 가능한 도구. **하나뿐이고, 늘리려면 이 줄을 고쳐야 한다.**
CITABLE_TOOLS = frozenset({"search_news"})

# 「이 단계의 범위라서」 — 청크가 이미 있어 나중에 올릴 수 있다
DEFERRED_TOOLS = frozenset({"search_dart"})

# 「구조적으로 못 올려서」 — 적재나 성질 자체가 막는다
CONTEXT_ONLY_TOOLS = frozenset({
    "get_business_overview", "get_market", "get_filings",
})


def is_citable(tool: str) -> bool:
    """이 도구의 결과를 답변이 **인용해도 되나.**"""
    return tool in CITABLE_TOOLS


def citable_evidence_ids(tool: str, hits: Iterable[Any]) -> list[str]:
    """인용 후보 `evidence_id` — 인용 불가 도구면 **빈 목록.**

    ★**순서를 지키며 중복을 없앤다.** 마감 단계가 이걸 그대로 합집합에 넣는데,
      중복이 남으면 상한을 세는 쪽이 같은 근거를 두 건으로 센다(2026-07-30 사고와
      같은 자리 — `fetch_texts` 는 스스로 접지만 그 위에서 세는 코드는 아니다).

    ★**인용 불가 도구에 예외를 내지 않는다.** 부르는 쪽이 잘못한 것이 아니라
      「그 도구는 인용에 안 쓴다」가 정상 상태다. 재료로는 여전히 쓰인다.
    """
    if not is_citable(tool):
        return []
    out: list[str] = []
    for hit in hits:
        evidence_id = getattr(hit, "evidence_id", None)
        if evidence_id and evidence_id not in out:
            out.append(str(evidence_id))
    return out
