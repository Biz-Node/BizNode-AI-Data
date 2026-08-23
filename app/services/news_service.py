"""뉴스 피드 — **우리가 모은 기사로.**

외부 뉴스 API 를 안 쓰는 이유

  화면의 필터가 세 축인데, 그중 둘을 **우리만 할 수 있다.**

      축 1  주제 (공급망·지분·규제·사건)   우리 키워드 분류. 외부 API 는 못 한다
      축 2  내 워크스페이스               `matched_corps` 로 이미 붙어 있다
      축 3  최신순                      둘 다 가능

  외부 API 는 기업명 문자열 검색밖에 못 해서 「공급망 관련 기사만」을 못 거른다.
  게다가 국내는 뉴스 저작권 때문에 무료 API 가 사실상 없다 — 빅카인즈는
  전재·복제·배포를 금하고, 구글 뉴스 RSS 는 개인·비상업 용도만이다.

본문은 없다

  제목·언론사·발행일·링크까지다(방법서 §8). 원문은 언론사 링크로 보낸다.
  인용이 필요하면 관계의 근거 원문(`/relations/{edge_id}`)을 쓴다.

신선도는 배치가 정한다

  `batch/build/news_feed.py` 가 매일 아침 언론사 RSS 를 받아 채운다.
  PostgreSQL 만 쓰므로 **서비스 중에 돌려도 된다**(실측 8초).
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.database import neo4j_session, postgres_connection

# 화면의 축 1 ↔ DB 의 topics 값
_CATEGORY = {"공급망": "supply", "지분": "stake", "규제": "regulation", "사건": "incident",
             "supply": "supply", "stake": "stake",
             "regulation": "regulation", "incident": "incident"}
_LABEL = {"supply": "공급망", "stake": "지분", "regulation": "규제", "incident": "사건"}

# ★`incident` 만 위험으로 본다. 지분·공급망 기사는 나쁜 소식이 아니다 —
#   「담합 제재」처럼 규제가 걸린 것은 `regulation` 과 `incident` 에 함께 걸린다.
_RISK_TOPIC = "incident"


def _names(codes: list[str]) -> dict[str, str]:
    """corp_code → 이름. 화면이 기업명을 보여 줘야 한다."""
    if not codes:
        return {}
    with neo4j_session() as s:
        return {r["k"]: r["n"] for r in s.run(
            """UNWIND $ks AS k MATCH (c:Company) WHERE c.corp_code = k
               RETURN k AS k, c.name AS n""", ks=list(set(codes)))}


def news_feed(*, category: Optional[str] = None,
              workspace_keys: Optional[list[str]] = None,
              risk_only: bool = False, limit: int = 20,
              offset: int = 0) -> dict[str, Any]:
    """뉴스/이슈 화면. **세 축을 겹쳐 쓸 수 있다.**"""
    where = ["published_at IS NOT NULL", "matched_corps IS NOT NULL",
             "jsonb_array_length(matched_corps) > 0"]
    params: list[Any] = []

    topic = _CATEGORY.get(category or "")
    if category and topic is None:
        return {"total": 0, "items": []}          # 모르는 갈래 — 빈 결과가 정답
    if topic:
        where.append("topics @> %s::jsonb")
        params.append(f'["{topic}"]')
    if risk_only:
        where.append("topics @> %s::jsonb")
        params.append(f'["{_RISK_TOPIC}"]')
    if workspace_keys:
        # ★담은 기업 중 **하나라도** 걸린 기사. 전부 걸린 기사만 찾으면 거의 안 나온다.
        where.append("matched_corps ?| %s")
        params.append(list(workspace_keys))

    sql_where = " AND ".join(where)
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM news_articles WHERE {sql_where}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT url, title, press, published_at, matched_corps, topics
                FROM news_articles WHERE {sql_where}
                ORDER BY published_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset])
        rows = cur.fetchall()

    codes = [c for r in rows for c in (r[4] or [])]
    name_of = _names(codes)
    items = []
    for url, title, press, at, corps, topics in rows:
        tp = topics or []
        items.append({
            "url": url, "title": title, "press": press,
            "published_at": str(at)[:10],
            # ★그래프에 없는 corp_code 는 이름을 못 찾는다 — 그 기업은 뺀다.
            #   이름 없이 키만 보내면 화면이 corp_code 를 그대로 그린다.
            "companies": [{"key": c, "name": name_of[c], "label": "Company"}
                          for c in (corps or []) if c in name_of],
            "event": None,
            "categories": [_LABEL[t] for t in tp if t in _LABEL],
            "is_risk": _RISK_TOPIC in tp,
        })
    return {"total": total, "items": items}
