"""이미 쓰고 있는 제품명을 기억해 **추출기에게 보여준다**.

왜 「합치기」가 아니라 「보여주기」인가 (2026-08-13)

제품명이 갈리는 걸 사후에 합치려 했더니 안 됐다. 실측으로 유사도 0.75 이상인
제품 쌍이 622쌍인데, 뜯어보니 **다수가 합치면 안 되는 것**이었다:

    휴머노이드 로봇 ↔ 양팔형 휴머노이드 로봇 · 이족보행 · 산업용
        → 진짜 다른 제품이다. 합치면 「어떤 로봇인지」가 사라진다
    Wafer ↔ Wafer Carrier · AEGIS-II ↔ AEGIS-III
        → 유사도 1.00인데 다른 물건이다(짧은 쪽 트라이그램이 통째로 포함될 뿐)

    휴머노이드 로봇 ↔ AI 휴머노이드 로봇 · AI 기반 · AI-Native
        → 이건 같은 걸 세 가지로 쓴 것. 합쳐야 한다.

**문자로는 위 둘을 못 가른다.** 「양팔형」과 「AI 기반」은 문자상 똑같이 수식어다.
subtype에서도 같은 결론이었다(`subtype_registry.same_notation` 주석).

그래서 방향을 바꾼다 — **애초에 안 갈리게** 한다. 추출 시점에 「이 도메인에는
이미 휴머노이드 로봇이 있다」를 보여주면 모델이 새 표기를 덜 만든다.
이건 subtype 레지스트리의 ④번 역할(프롬프트 주입)과 같고, 실제로 subtype에서는
이 부분이 빠져 있었다.

★표기 변형(공백·구두점)은 이미 `product_names.norm_key`가 흡수한다 — 그 키가
  모든 문자를 지우므로 「TC본더」와 「TC 본더」는 애초에 같은 노드다.
  이 모듈은 **표기가 아니라 어휘**를 다룬다.
"""

from __future__ import annotations

from typing import Optional

_CREATE = """
CREATE TABLE IF NOT EXISTS product_names (
    norm_key    TEXT PRIMARY KEY,
    display     TEXT NOT NULL,
    seen_count  INT  NOT NULL DEFAULT 1,
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_TOP = ("SELECT display FROM product_names "
        "ORDER BY seen_count DESC, display LIMIT %s")
_UPSERT = """
INSERT INTO product_names (norm_key, display) VALUES (%s, %s)
ON CONFLICT (norm_key) DO UPDATE
    SET seen_count = product_names.seen_count + 1, last_seen = now()
"""

# 프롬프트에 넣을 개수. 너무 많으면 프롬프트가 길어지고 모델이 훑고 만다.
# 연결이 있는 제품은 실측 273개 — 상위 60이면 도메인 감을 주기에 충분하다.
_PROMPT_LIMIT = 60

_cache: Optional[list[str]] = None


def known_products(conn, *, limit: int = _PROMPT_LIMIT) -> list[str]:
    """자주 쓰인 제품명 — 추출 프롬프트에 주입한다. 프로세스당 한 번만 읽는다."""
    global _cache
    if _cache is None:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(_TOP, (limit,))
            _cache = [r[0] for r in cur.fetchall()]
    return _cache


def record(conn, names: list[str]) -> None:
    """적재한 제품명을 레지스트리에 남긴다(빈도 누적)."""
    from pipeline.normalizer.product_names import canonical_product, norm_key

    with conn.cursor() as cur:
        cur.execute(_CREATE)
        for raw in names:
            display = canonical_product(raw)
            key = norm_key(raw)
            if key:
                cur.execute(_UPSERT, (key, display))


def seed_from_graph(conn, session) -> int:
    """현재 그래프의 Product로 레지스트리를 초기화한다(1회).

    연결이 있는 것만 담는다 — 한 번 나오고 만 이름까지 프롬프트에 넣으면
    **오히려 꼬리를 굳힌다.** 우리가 재사용하길 바라는 건 통용되는 이름이다.
    """
    rows = session.run(
        "MATCH (p:Product) WITH p, size([(p)-[]-() | 1]) AS deg "
        "WHERE deg >= 2 AND p.name IS NOT NULL "
        "RETURN p.name AS name, p.norm_name AS key, deg ORDER BY deg DESC"
    )
    n = 0
    with conn.cursor() as cur:
        cur.execute(_CREATE)
        for row in rows:
            if not row["key"]:
                continue
            cur.execute(
                "INSERT INTO product_names (norm_key, display, seen_count) "
                "VALUES (%s, %s, %s) ON CONFLICT (norm_key) DO UPDATE "
                "SET seen_count = GREATEST(product_names.seen_count, EXCLUDED.seen_count)",
                (row["key"], row["name"], row["deg"]),
            )
            n += 1
    global _cache
    _cache = None
    return n


def prompt_block(conn) -> str:
    """추출 프롬프트에 붙일 블록. 비어 있으면 빈 문자열."""
    items = known_products(conn)
    if not items:
        return ""
    listed = " · ".join(items)
    return f"""
【이미 쓰고 있는 제품·기술 이름】
아래는 이 그래프에 **이미 있는** 이름입니다. 같은 것을 가리킨다면
**새로 짓지 말고 아래 표기를 그대로 쓰세요.**

{listed}

★단, **다른 물건이면 다른 이름을 쓰세요.** 억지로 위 목록에 맞추지 마세요:
   · 세대·버전이 다르면 다른 제품입니다        HBM3 ≠ HBM3E ≠ HBM4
   · 형태·용도가 다르면 다른 제품입니다        휴머노이드 로봇 ≠ 양팔형 휴머노이드 로봇
   · 「AI 기반 ○○」처럼 **기사가 붙인 수식어**는 빼고 ○○만 쓰세요.
     실제로 「휴머노이드 로봇」이 AI/AI 기반/AI-Native 세 갈래로 갈렸습니다."""
