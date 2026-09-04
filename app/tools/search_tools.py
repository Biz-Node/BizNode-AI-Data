"""근거 검색 도구 — **뉴스와 공시를 갈라서 본다.**

    Agent(2차) → Tool(여기) → ChromaRepository → evidence 컬렉션

★**컬렉션은 하나다.** 뉴스 전용도 DART 전용도 없다. 0차 백필이 청크 메타에
  `source_type` 을 채워 둔 덕분에(§8-9) 필터로 갈린다.

      실측 2026-08-28 · 청크 10,510건
          news         7,668
          dart         2,561
          dart_filing    113
          (없음)         168      ← ★아래 참조

★**`source_type` 이 없는 168건은 두 도구 모두에 안 잡힌다.** 0차 백필이
  Neo4j 엣지의 스칼라 `evidence_id` 에서만 값을 가져왔고, 거기서 못 찾은 청크는
  **추측하지 않고 남겨 뒀다**(`batch/repair/evidence_source_type.py`:
  「`rcept_no` 로 추측하지도 않는다 — 없는 것과 모르는 것은 다르다」).
  여기서 뒤집지 않는다. 「모르는 출처」를 뉴스나 공시로 밀어 넣으면 그 문장이
  확정 사실인지 주장인지를 우리가 지어내는 것이 된다.

★**`source_doc`(기사 URL·접수번호)을 여기서 만들지 않는다.** 그건
  `relation_service.evidence_for_ids()` 가 조립하는 값이고, 그 경로는 마감
  단계가 **한 번에** 탄다(계약 2번). 도구는 `evidence_id` 까지만 준다.

도구 4원칙은 `graph_tools` 와 같다 — key 만 받고 · 표기가 끝난 DTO 를 주고 ·
`limit` 을 인자로 받지 않고 · 빈 결과와 실패를 구별한다.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.core.trace import trace_logger
from app.tools import keys as keys_module
from app.tools.dto import SOURCE_NOTE, EvidenceHitDTO
from app.tools.errors import ToolError

log = trace_logger(__name__)

# ★상한은 **내부 상수**다(원칙 ③). 값은 `ChromaRepository.search_evidence` 의
#   기본값을 그대로 쓴다 — 새 숫자를 지어내면 두 벌이 되어 조용히 갈린다.
#   ★실측 근거가 있는 값은 아니다. 평가셋으로 재고 나서 정한다.
_MAX_HITS = 10

NEWS_TYPES = ("news",)
# ★`dart` 는 정기공시, `dart_filing` 은 개별공시다. 둘 다 확정 사실이라 같은
#   도구가 본다 — `relation_service` 도 조회 뒤 `dart` 하나로 접는다.
DART_TYPES = ("dart", "dart_filing")

_REPO: Any = None


def _repo():
    """★한 번만 맺는다. `HttpClient()` 생성이 2.2초라 요청마다 새로 맺으면
    검색보다 접속이 오래 걸린다(`relation_service._chroma()` 와 같은 이유)."""
    global _REPO
    if _REPO is None:
        from search.repository.chroma_repository import ChromaRepository

        _REPO = ChromaRepository()
    return _REPO


def _key_forms(keys: Sequence[str]) -> list[str]:
    """범위 검사 + **메타가 쓰는 두 형태를 모두** 모은다.

    ★청크 메타의 `source_corp`·`target_corp` 는 **그래프 노드 키 그대로**라
      `corp_code`(`00161383`) 와 `norm_name`(`c.o.k`) 이 섞여 있다(실측
      2026-08-28). 한 형태로만 거르면 그 기업의 근거 절반이 조용히 사라진다.

    ★범위 검사와 해소는 `app/tools/keys.py` 가 한다 — 실패 문구를 두 벌 두지
      않는다(원칙 ④: 빈 결과와 실패를 구별한다).
    """
    wanted, found = keys_module.resolved(keys)

    forms: list[str] = []
    for key in wanted:
        for form in (key, found[key]):
            if form and form not in forms:
                forms.append(form)
    return forms


def _iso(occurred_at: Any) -> Optional[str]:
    """`20260608` → `2026-06-08`. ★`0` 은 **시점을 못 뽑은 것**이라 `None` 이다."""
    try:
        value = int(occurred_at or 0)
    except (TypeError, ValueError):
        return None
    if value <= 19000000:
        return None
    s = str(value)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _hit_dto(evidence_id: str, text: str, meta: dict) -> EvidenceHitDTO:
    source_type = str(meta.get("source_type") or "news")
    rcept_no = str(meta.get("rcept_no") or "")
    return EvidenceHitDTO(
        evidence_id=evidence_id,
        text=(text or "").strip(),
        source_type=source_type,                       # ② 표기가 끝난 DTO
        source_note=SOURCE_NOTE[source_type],
        edge_type=str(meta.get("edge_type") or "") or None,
        subtype=str(meta.get("subtype") or "") or None,
        occurred_at=_iso(meta.get("occurred_at")),
        # ★뉴스 근거의 `rcept_no` 는 빈 문자열이다. `""` 를 그대로 주면 LLM 이
        #   「접수번호가 있는데 비어 있다」로 읽는다 — 없는 것은 `None` 이다.
        rcept_no=rcept_no or None,
    )


def _search(query: str, keys: Sequence[str], source_types: Sequence[str],
            *, tool: str) -> list[EvidenceHitDTO]:
    if not (query or "").strip():
        # ④ 빈 결과가 아니라 **입력이 틀린 것**이다
        raise ToolError(f"{tool}: 검색어가 비어 있다")
    forms = _key_forms(keys)
    if not forms:
        return []

    where = {"$and": [
        {"source_type": {"$in": list(source_types)}},
        {"$or": [{"source_corp": {"$in": forms}},
                 {"target_corp": {"$in": forms}}]},
    ]}
    got = _repo().search_evidence(query, n_results=_MAX_HITS, where=where)

    ids = (got.get("ids") or [[]])[0]
    docs = (got.get("documents") or [[]])[0]
    metas = (got.get("metadatas") or [[]])[0]
    out = [_hit_dto(i, d, dict(m or {})) for i, d, m in zip(ids, docs, metas)]
    log.info("%s query=%r keys=%d forms=%d -> hits=%d",
             tool, query[:40], len(keys), len(forms), len(out))
    return out


def search_news(query: str, keys: Sequence[str]) -> list[EvidenceHitDTO]:
    """이 기업들에 관한 **보도 근거**를 의미검색으로 찾는다.

    ★**「뉴스 원문」은 어디에도 없다.** `news_articles` 는 `body_length` 만 남기고
      본문을 저장하지 않고(저작권), `evidence` 컬렉션도 **검증을 통과한 엣지로
      승격된 문장만** 담는다. 그래서 여기서 나오는 것은 기사 전문이 아니라
      「그 관계를 뒷받침한다고 추출기가 지목한 문장」이다.
    """
    return _search(query, keys, NEWS_TYPES, tool="search_news")


def search_dart(query: str, keys: Sequence[str]) -> list[EvidenceHitDTO]:
    """이 기업들에 관한 **공시 근거**를 의미검색으로 찾는다.

    ★정기공시(`dart`)와 개별공시(`dart_filing`)를 **함께** 본다. 둘 다 확정
      사실이고, 가르면 「사업보고서엔 있는데 수시공시엔 없다」 같은 질문을
      Agent 가 스스로 만들게 된다 — 그건 재료 범위를 LLM 이 정하는 것이다.
    """
    return _search(query, keys, DART_TYPES, tool="search_dart")
