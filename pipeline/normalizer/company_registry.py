"""기업 이름 레지스트리 — 별칭·묶음열쇠를 **한 표에서** 관리한다.

왜 만들었나 (2026-08-14)

별칭이 코드 상수와 JSON 파일 세 곳에 흩어져 있었다. 같은 종류의 데이터인데
출처만 달랐다. 한 표로 모으면

  · 출처가 남는다 — 사람이 정한 건지 모델이 판정한 건지
  · 사람이 뒤집을 자리가 생긴다 — 모델이 틀린 제안을 반복해도 `source='hand'`가 이긴다
  · 조회가 한 번이다

해외 기업에는 **명부가 없다** — 그래서 이 표가 명부 노릇을 한다.

  한국 기업은 `corp_code_master` 118,535건이 명부라 「이 이름이 어느 회사인가」를
  대조로 답한다(`resolver.py`). 해외 기업은 그런 명부가 없다. 세 곳을 재봤다:

      Wikidata  별칭이 검증 안 됨 — 엔비디아 항목(Q182477)에 「포스웨어」가
                한글 별칭으로 등록돼 있다. 커뮤니티 편집이라 그대로 쓰면 오염된다.
      GLEIF     공식이지만 본사를 못 집는다 — 「NVIDIA」 검색 1순위가 NVIDIA를
                기초자산으로 하는 ETF다. LEI는 펀드·자회사에도 발급되기 때문.
                중국 비상장(CXMT)·소형(Unitree)은 아예 없고 한글 검색도 안 된다.
      DART      정확하지만 한국 법인만 (→ `batch/build/dart_aliases.py`)

  그래서 **우리가 판정한 것을 쌓아 명부를 만든다.** 한 번 판정하면 그 뒤로는
  조회만 하면 되므로, 쓸수록 싸지고 정확해진다.

★열쇠(`block_key`)는 **헐겁게** 만든다.
  이건 노드 식별자가 아니라 「후보를 모으는 열쇠」다. 잘못 묶여도 쌍 판정이
  거르므로, 헐거워야 후보에 올라가 판정 기회가 생긴다.
      Kioxia Corporation / Kioxia Holdings Corporation → 둘 다 `kioxia`
      → 후보로 올라가고, 쌍 판정이 「지주사는 별개」로 가른다
"""

from __future__ import annotations

import re
from typing import Optional

# 열쇠에서 떼어낼 법인격. `legal_forms.EN_LEGAL_SUFFIXES`보다 공격적이다 —
# 저기는 노드 식별자용이라 `Holdings`를 보존해야 하지만 여기는 열쇠라 떼도 된다.
_LEGAL = ("corporation", "incorporated", "technologies", "technology",
          "holdings", "holding", "company", "limited", "group", "corp",
          "inc", "ltd", "llc", "plc", "gmbh", "sa", "ag", "nv", "bv",
          "spa", "srl", "kk", "pte", "co")

# 모델이 답 대신 프롬프트 문장을 뱉는 일이 있었다(실측: 「입력을 그대로
# 돌려주세요.」가 canonical 로 들어옴). 문장이면 버린다.
_LEAK = ("입력을", "그대로 돌려", "모르겠", "알 수 없", "unknown", "n/a", "없습니다")

_CREATE = """
CREATE TABLE IF NOT EXISTS company_aliases (
    alias_key      TEXT PRIMARY KEY,   -- 정규화된 표기 (조회 키)
    canonical_key  TEXT NOT NULL,      -- 어느 노드로 갈지 (대표 표기)
    canon_name     TEXT,               -- 모델이 답한 정식 법인명 (참고용)
    block_key      TEXT,               -- 후보를 모으는 열쇠
    source         TEXT NOT NULL,      -- hand | dart | llm | first_seen
    note           TEXT,
    decided_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_IDX = ("CREATE INDEX IF NOT EXISTS company_aliases_block "
        "ON company_aliases (block_key)")

_UPSERT = """
INSERT INTO company_aliases
    (alias_key, canonical_key, canon_name, block_key, source, note)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (alias_key) DO UPDATE SET
    canonical_key = CASE
        -- 사람이 정한 것은 모델·자동 생성이 못 덮는다
        WHEN company_aliases.source = 'hand' AND EXCLUDED.source <> 'hand'
        THEN company_aliases.canonical_key ELSE EXCLUDED.canonical_key END,
    canon_name = COALESCE(EXCLUDED.canon_name, company_aliases.canon_name),
    block_key  = COALESCE(EXCLUDED.block_key, company_aliases.block_key),
    source = CASE
        WHEN company_aliases.source = 'hand' THEN 'hand' ELSE EXCLUDED.source END,
    note = COALESCE(EXCLUDED.note, company_aliases.note)
"""


def block_key(text: Optional[str]) -> str:
    """후보를 모으는 열쇠. 구두점·공백·법인격을 전부 떼어 헐겁게 만든다."""
    s = re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())
    changed = True
    while changed:
        changed = False
        for suf in _LEGAL:
            if s.endswith(suf) and len(s) > len(suf) + 2:
                s = s[: -len(suf)]
                changed = True
                break
    return s


def is_leaked(canonical: Optional[str]) -> bool:
    """모델이 답 대신 지시문·회피 문구를 뱉었는가."""
    low = (canonical or "").lower()
    return (not low) or any(b in low for b in _LEAK) or len(low) > 80


def ensure(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE)
        cur.execute(_IDX)


def record(conn, alias_key: str, canonical_key: str, *, source: str,
           canon_name: Optional[str] = None, note: Optional[str] = None) -> None:
    """별칭 한 줄을 남긴다. 같은 키가 있으면 출처 우선순위에 따라 갱신."""
    if not alias_key or not canonical_key:
        return
    with conn.cursor() as cur:
        cur.execute(_UPSERT, (alias_key, canonical_key, canon_name,
                              block_key(canon_name) if canon_name else None,
                              source, note))


def load_aliases(conn) -> dict[str, str]:
    """`apply_alias`가 쓸 사전 — {별칭키: 대표키}. 자기 자신은 뺀다."""
    ensure(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT alias_key, canonical_key FROM company_aliases "
                    "WHERE alias_key <> canonical_key")
        return dict(cur.fetchall())


def by_block(conn, key: str) -> list[tuple[str, str]]:
    """같은 열쇠를 쓰는 것들 — [(별칭키, 대표키)]. 후보 찾기용."""
    if not key:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT alias_key, canonical_key FROM company_aliases "
                    "WHERE block_key = %s", (key,))
        return list(cur.fetchall())
