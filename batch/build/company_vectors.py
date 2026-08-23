"""기업 **소개 카드**를 임베딩해 「말로 회사 찾기」를 가능하게 한다.

왜 필요한가 (2026-08-02)

지금 회사를 찾는 방법은 **이름을 정확히 아는 것뿐**이다. `company_search`는
`norm_name LIKE`라서 「HBM 만드는 회사」·「삼성전자에 소재 대는 데」로는 아무것도
안 나온다. 검색은 벡터 컬렉션이 있어야 하는데, 지금 임베딩된 건 `evidence`
6,240건 — **문장 조각**뿐이라 회사 단위로 답할 수 없다.

이 도구는 회사 하나당 카드 한 장을 만들어 `company` 컬렉션에 넣는다:

    SK하이닉스 (000660, KOSPI)
    업종: 반도체 / 전자집적회로 제조업
    <사업보고서 개요 3~4문장>
    주요 제품·기술: HBM3E 12단 · 176단 낸드플래시 · CXL · …
    주요 고객: 엔비디아 · …   주요 공급사: 한미반도체 · …

★카드가 프로필 원문보다 나은 이유
  `company_profiles.text`는 평균 219자로 짧고 **제품명이 거의 없다**(사업의 개요는
  서술형이라 "박막형성재료를 증착하는 장비"라고 쓰지 "HBM"이라 쓰지 않는다).
  검색어는 반대로 제품명·거래처명으로 들어온다. 그래서 그래프에 이미 있는
  DEVELOPS 제품과 SUPPLIES_TO 거래처를 **카드에 합쳐** 검색어와 말이 닿게 한다.

비용: 60건 × 약 600자 ≈ 0.5원(text-embedding-3-small). 재실행해도 내용이 안 바뀌면
      건너뛴다(content_hash 비교) — 매번 돈이 나가지 않는다.

    python -m batch.build.company_vectors --dry-run
    python -m batch.build.company_vectors
    python -m batch.build.company_vectors --force     # 해시 무시하고 전건 재임베딩
"""

from __future__ import annotations

import argparse
import hashlib
import sys

from app.core.config import EMBEDDING_MODEL
from app.core.database import neo4j_session, postgres_connection
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COMPANY_COLLECTION = "company"

# 카드에 넣을 제품·거래처 상한. 전부 넣으면(최대 60개) 카드가 제품 나열로 뒤덮여
# 개요 문장의 의미가 묻힌다 — 임베딩은 긴 쪽 토큰에 끌려간다.
_MAX_PRODUCTS = 20
_MAX_PARTNERS = 8

# ★stub은 더 짧게. 사업보고서 개요가 없어 **한 줄 라벨이 유일한 설명**인데,
#   제품을 15개 붙이면 그 한 줄이 묽어진다. 실측(TSMC, 코사인거리 낮을수록 좋음):
#
#                       "대만 파운드리"  "3나노 공정"  "엔비디아에 칩 만들어주는 회사"
#     제품15·거래처8        0.766        0.856          0.708
#     제품4·거래처4         0.725        0.840          0.691   ← 3/4 우세
#
#   ※ 그래도 「엣지파운드리」(이름이 닮은 회사, 0.55)는 못 이긴다. 짧은 이름형
#     질의는 벡터만으로 안 된다 — 화면은 이름 검색과 **함께** 써야 한다.
_STUB_PRODUCTS = 4
_STUB_PARTNERS = 4

# 근거가 의심스러운 엣지는 카드에 올리지 않는다. 화면에서 숨기는 관계를
# (`graph_service.HIDE_VERDICTS`) 검색 색인에는 넣는다면 앞뒤가 안 맞는다.
_CARD_QUERY = """
MATCH (c:Company)
// ★엣지가 하나도 없는 회사는 넣지 않는다(2026-08-03). 검색으로 찾아 들어가면
//   **관계가 하나도 없는 빈 화면**이 열린다. 코인원·키움증권·두산건설이 그랬다.
//   전부 관계 검사가 「나란한 언급」으로 판정해 마지막 엣지를 지운 자리다.
//   `is_orphan` 표시는 `repair.orphan_nodes`가 붙이고 엣지가 돌아오면 뗀다.
WHERE NOT coalesce(c.is_orphan, false)
  AND ((NOT coalesce(c.is_stub, false) AND c.corp_code IS NOT NULL)
    OR ($stubs AND coalesce(c.is_stub, false)
        AND c.sector_label IS NOT NULL AND c.entity_kind <> '불명'))
OPTIONAL MATCH (c)-[d:DEVELOPS]->(p:Product)
  WHERE NOT coalesce(d.grounding_suspect, false)
OPTIONAL MATCH (c)-[s:SUPPLIES_TO]->(buyer:Company)
  WHERE NOT coalesce(s.grounding_suspect, false)
OPTIONAL MATCH (vendor:Company)-[s2:SUPPLIES_TO]->(c)
  WHERE NOT coalesce(s2.grounding_suspect, false)
RETURN c.corp_code AS corp_code, c.name AS name, c.stock_code AS stock_code,
       c.market AS market, c.sector AS sector, c.induty AS induty,
       coalesce(c.is_stub, false) AS is_stub, c.sector_label AS sector_label,
       c.entity_kind AS entity_kind, c.sector_confidence AS confidence,
       [x IN collect(DISTINCT p.name)      WHERE x IS NOT NULL] AS products,
       [x IN collect(DISTINCT buyer.name)  WHERE x IS NOT NULL] AS customers,
       [x IN collect(DISTINCT vendor.name) WHERE x IS NOT NULL] AS vendors
"""

_REGISTER = """
INSERT INTO vector_chunks (chunk_id, chunk_type, collection, owner_key, corp_code,
                           source_doc, embedding_model, content_hash, version, is_active)
-- owner_key는 TEXT, corp_code는 CHAR(8)이다. 같은 이름의 파라미터를 두 칸에
-- 쓰면 psycopg가 타입을 하나로 못 정해 AmbiguousParameter로 죽는다 → 따로 넘긴다.
VALUES (%(chunk_id)s, 'company', %(collection)s, %(owner_key)s, %(corp_code)s,
        NULL, %(model)s, %(hash)s, 1, true)
ON CONFLICT (chunk_id) DO UPDATE SET
    content_hash=EXCLUDED.content_hash, embedding_model=EXCLUDED.embedding_model,
    is_active=true, embedded_at=now()
"""


def _flat(v) -> str:
    """스칼라든 배열이든 한 줄 문자열로. `sector`는 **설계상 배열**이다 —
    한 회사가 반도체이면서 로봇일 수 있어서(`node_identity._LIST_BY_DESIGN`).
    """
    if v is None:
        return ""
    if isinstance(v, list):
        return " · ".join(str(x) for x in v if x)
    return str(v)


def build_card(row: dict, profile: str) -> str:
    """회사 한 곳 → 검색용 카드 한 장."""
    head = row["name"]
    if row.get("stock_code"):
        head += f" ({row['stock_code']}, {_flat(row.get('market')) or '비상장'})"
    lines = [head]

    # ★`induty`는 카드에 넣지 않는다. DART가 주는 건 표준산업분류 **코드**(예: "264")
    #   뿐이라 사람 말이 아니다. 검색어 "영상기기"와 "264"는 임베딩 공간에서 서로
    #   멀다 — 넣으면 잡음만 는다. 코드는 메타데이터로만 남겨 필터에 쓴다.
    if row.get("sector"):
        lines.append(f"업종: {_flat(row['sector'])}")
    # stub은 사업보고서가 없다. `stub_profiles`가 붙인 한 줄이 유일한 설명이다 —
    # "미국 GPU 및 AI 칩 제조사" 정도지만, 이게 있어야 엔비디아가 검색에 걸린다.
    if row.get("sector_label"):
        lines.append(f"{row.get('entity_kind') or '기업'} · {row['sector_label']}")
    if profile:
        lines.append(profile)

    stub = bool(row.get("is_stub"))
    n_prod = _STUB_PRODUCTS if stub else _MAX_PRODUCTS
    n_part = _STUB_PARTNERS if stub else _MAX_PARTNERS

    # 제품은 긴 것부터 — "CXL"보다 "EUV용 수소가스 재활용 기술"이 검색어와 더 닿는다
    if row["products"]:
        top = sorted(row["products"], key=len, reverse=True)[:n_prod]
        lines.append("주요 제품·기술: " + " · ".join(top))
    if row["customers"]:
        lines.append("주요 고객: " + " · ".join(row["customers"][:n_part]))
    if row["vendors"]:
        lines.append("주요 공급사: " + " · ".join(row["vendors"][:n_part]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="해시가 같아도 재임베딩")
    ap.add_argument("--no-stubs", action="store_true",
                    help="본사 64곳만 (기본은 stub도 넣는다)")
    args = ap.parse_args()

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_CARD_QUERY, stubs=not args.no_stubs)]

    with postgres_connection() as conn:
        profiles = dict(conn.execute(
            "SELECT corp_code, text FROM company_profiles").fetchall())
        seen = dict(conn.execute(
            "SELECT chunk_id, content_hash FROM vector_chunks WHERE chunk_type='company'"
        ).fetchall())

    cards = []
    for r in rows:
        text = build_card(r, (profiles.get(r["corp_code"]) or "").strip())
        # 해외 stub은 corp_code가 없다 — 이름 해시로 안정된 id를 만든다
        cid = (f"co_{r['corp_code']}" if r["corp_code"]
               else "co_n" + hashlib.sha1(r["name"].encode("utf-8")).hexdigest()[:12])
        cards.append({
            "id": cid,
            "corp_code": r["corp_code"],
            "name": r["name"],
            "is_stub": bool(r["is_stub"]),
            "text": text,
            "hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:16],
            "meta": {
                # ChromaDB 메타데이터는 스칼라만 받는다 — 배열은 여기서 접는다
                "corp_code": r["corp_code"] or "", "name": r["name"],
                "sector": _flat(r.get("sector")), "market": _flat(r.get("market")),
                "stock_code": _flat(r.get("stock_code")),
                "induty": _flat(r.get("induty")),
                "is_stub": bool(r["is_stub"]),
                "kind": r.get("entity_kind") or "기업",
                # ★검색 결과에 「확신 낮음」을 표시할 수 있게 남긴다. LLM이 지은
                #   라벨과 사업보고서에서 온 개요를 화면이 구별해야 한다.
                "confidence": r.get("confidence") or "high",
                "has_profile": bool(profiles.get(r["corp_code"])),
            },
        })

    todo = cards if args.force else [c for c in cards if seen.get(c["id"]) != c["hash"]]
    main_cards = [c for c in cards if not c["is_stub"]]
    no_profile = [c["name"] for c in main_cards if not c["meta"]["has_profile"]]

    print(f"기업 {len(cards)}곳 (본사 {len(main_cards)} · stub {len(cards)-len(main_cards)}) "
          f"· 카드 평균 {sum(len(c['text']) for c in cards)//max(len(cards),1)}자")
    print(f"  임베딩 대상 {len(todo)}건 (내용 그대로 {len(cards)-len(todo)}건 건너뜀)")
    if no_profile:
        print(f"  ⚠ 사업보고서 개요가 없는 곳 {len(no_profile)}: {' · '.join(no_profile)}")
        print("    → 업종·제품만으로 카드를 만듭니다(검색은 되지만 설명이 얇습니다)")

    if main_cards:
        sample = max(main_cards, key=lambda c: len(c["text"]))
        print(f"\n── 카드 예시 · 본사 ({sample['name']}) ──\n{sample['text']}")
    stubs = [c for c in cards if c["is_stub"]]
    if stubs:
        sample = max(stubs, key=lambda c: len(c["text"]))
        print(f"\n── 카드 예시 · stub ({sample['name']}) ──\n{sample['text']}\n")

    if args.dry_run:
        print("[dry-run] 임베딩하지 않았습니다.")
        return 0
    if not todo:
        print("변경 없음.")
        return 0

    get_store().upsert(
        COMPANY_COLLECTION,
        ids=[c["id"] for c in todo],
        documents=[c["text"] for c in todo],
        metadatas=[c["meta"] for c in todo],
    )
    with postgres_connection() as conn:
        for c in todo:
            # owner_key는 NOT NULL이다. 해외 stub은 corp_code가 없으므로
            # 이름을 쓴다 — 「이 청크가 무엇에 속하나」를 비워 둘 수 없다.
            conn.execute(_REGISTER, {"chunk_id": c["id"], "collection": COMPANY_COLLECTION,
                                     "owner_key": c["corp_code"] or c["name"],
                                     "corp_code": c["corp_code"],
                                     "model": EMBEDDING_MODEL, "hash": c["hash"]})
    print(f"✅ company 컬렉션에 {len(todo)}건 임베딩 · vector_chunks 등록")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
