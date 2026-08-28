"""`embedding_cache.model` → **`embedding_model`** 개명 — 이미 돌고 있는 DB 용.

왜 이름을 바꾸나 (2026-08-28)

  같은 뜻인데 이름이 두 개였다.

      vector_chunks.embedding_model   모델 교체 시 **재임베딩 대상**을 고른다
      embedding_cache.model           모델 교체 시 **버릴 캐시**를 가른다

  둘 다 `app.core.config.EMBEDDING_MODEL` 이 들어가는 같은 값이다. 그런데
  이름이 갈려 있으면 모델을 바꿀 때 **한쪽만 고치고 넘어가기 쉽다** — 재임베딩은
  돌렸는데 캐시가 옛 벡터를 계속 돌려주는 식이다. 이 저장소는 같은 사실이 두
  이름으로 갈리는 것을 반복해서 겪었다(`sector_label` · `contract_amount`→`amount`).

★**이 스크립트가 필요한 이유** — `infra/postgres/init/02_schema.sql` 은 컨테이너가
  **데이터 디렉터리가 비었을 때만** 돌린다. 이미 데이터가 있는 DB 는 그 파일을
  아예 읽지 않으므로, 새 클론과 기존 DB 의 스키마가 갈린다. 런타임 `_ensure()`
  도 `CREATE TABLE IF NOT EXISTS` 라서 **이미 있는 표의 컬럼은 못 고친다.**

★**데이터를 잃지 않는다.** `RENAME COLUMN` 은 값을 그대로 두고 이름만 바꾼다.
  기본키(`embedding_cache_pkey`)도 컬럼을 따라가므로 다시 만들 필요가 없다.
  = 캐시를 비우지 않는다 → 개명 뒤에도 **같은 텍스트는 같은 벡터**가 유지된다.

★**여러 번 돌려도 안전하다.** 이미 바뀐 DB 에서는 아무것도 하지 않는다.

    python -m batch.repair.embedding_cache_column --dry-run
    python -m batch.repair.embedding_cache_column
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import postgres_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_TABLE = "embedding_cache"
_OLD, _NEW = "model", "embedding_model"

_COLUMNS = """
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
"""

_RENAME = f'ALTER TABLE public.{_TABLE} RENAME COLUMN {_OLD} TO {_NEW}'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_COLUMNS, (_TABLE,))
            cols = {r[0] for r in cur.fetchall()}

        # 표가 없다 — 새 클론이거나 아직 한 번도 안 쓴 DB. 02_schema.sql 이나
        # 런타임 `_ensure()` 가 **처음부터 새 이름으로** 만든다. 할 일이 없다.
        if not cols:
            print(f"· `{_TABLE}` 표가 없습니다 — 할 일 없음"
                  " (새로 만들 때 이미 `embedding_model` 입니다)")
            return 0

        if _NEW in cols and _OLD in cols:
            # 같은 뜻의 컬럼이 둘 — 어느 쪽이 정본인지 이 스크립트가 정할 수 없다.
            print(f"✗ `{_OLD}` 와 `{_NEW}` 가 **둘 다** 있습니다. "
                  "값을 확인하고 손으로 정리하세요 — 임의로 고르지 않습니다.")
            return 1

        if _NEW in cols:
            print(f"· 이미 `{_NEW}` 입니다 — 할 일 없음")
            return 0

        if _OLD not in cols:
            print(f"✗ `{_OLD}` 도 `{_NEW}` 도 없습니다. 표 모양이 예상과 다릅니다: "
                  f"{sorted(cols)}")
            return 1

        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM public.{_TABLE}")
            rows = cur.fetchone()[0]
        print(f"■ `{_TABLE}` — {rows:,}행 · `{_OLD}` → `{_NEW}`")
        print("   ★값은 그대로 둡니다(RENAME). 캐시를 비우지 않으므로 "
              "개명 뒤에도 같은 텍스트는 같은 벡터입니다.")

        if args.dry_run:
            print(f"\n[dry-run] 실행할 문장:\n   {_RENAME}")
            return 0

        with conn.cursor() as cur:
            cur.execute(_RENAME)

        with conn.cursor() as cur:
            cur.execute(_COLUMNS, (_TABLE,))
            after = {r[0] for r in cur.fetchall()}
        if _NEW not in after or _OLD in after:
            print(f"✗ 개명 뒤 컬럼이 예상과 다릅니다: {sorted(after)}")
            return 1

    print(f"\n✅ `{_TABLE}.{_OLD}` → `{_NEW}` ({rows:,}행 보존)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
