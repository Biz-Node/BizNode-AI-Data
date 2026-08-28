"""임베딩 영속 캐시 — **같은 텍스트는 언제나 같은 벡터.**

★왜 필요한가 (실측 2026-08-28)

  OpenAI 임베딩은 **같은 입력에 같은 벡터를 보장하지 않는다.** 편차는 한 번에
  몇 건을 보내느냐에 붙어 있다:

      배치   1건 · 10건        편차 0
      배치 100건               편차 1.2e-04
      배치 150건               편차 2.1e-03      (`_EMBED_BATCH` 가 100 이라 청크가 갈린다)

  `evidence_selector.similarities()` 는 `[intent, *labels]` 를 한 번에 임베딩하는데
  라벨 수가 사건 수만큼이라 배치가 쉽게 100 을 넘는다. 그 결과 코사인이
  중앙 1.1e-04 · 최대 4.4e-03 만큼 흔들리고, **가까이 붙은 두 사건의 순위가
  실행마다 뒤집힌다.** 실측: 20질문 × 4회에서 2질문이 갈렸다.

  그러면 평가셋 점수 차이를 **Agent 때문인지 이것 때문인지 귀속시킬 수 없다.**
  2차의 완료 기준이 점수라서 이건 기준선의 문제가 아니라 측정의 문제다.

★**반올림으로는 못 막는다.** 드리프트 최대 4.4e-03 가 `round(3)` 버킷 폭
  1e-03 보다 커서 경계 근처 값은 여전히 넘나든다. 실측으로 `round(2)` 는 이미
  한 질문에서 실패했다. 버킷을 아무리 넓혀도 경계는 남는다 — 통계적 완화이지
  보장이 아니다.

★그래서 **값을 고정한다.** 캐시가 (모델, 텍스트) → 벡터를 한 번만 정하고
  그 뒤로는 그 값을 돌려준다. 실측으로 이 방식이 20질문 × 5회 흔들림 0 이었다.

★**폴백은 용도에 따라 갈린다**(2026-08-28). 운영 `/ask` 는 캐시가 값을 못 주면
  직접 계산으로 넘어가고, 평가·대조 실행은 **그 자리에서 멈춘다**(`STRICT_ENV`).
  하나로 합쳐 두면 운영 폴백이 평가의 보호막까지 걷어낸다 — 흔들린 실행을
  점수의 근거로 쓰게 되고, 그러면 애초에 캐시를 만든 이유가 사라진다.

  ★부수 효과 하나가 더 있다 — **배치 구성 의존이 사라진다.** 같은 라벨이라도
    리스트 길이·위치에 따라 다른 청크에 실려 다른 벡터를 받았는데, 캐시는
    텍스트 하나를 키로 삼으므로 그 차이가 정규화된다.

★**모델 이름을 키에 넣는다.** 적재·질의가 지금은 둘 다
  `text-embedding-3-small` 하나지만(실측: `vector_chunks` 12,942행 전부 단일
  모델), 모델을 바꾸면 캐시가 통째로 무효가 되어야 한다. 키에 있으면 새 모델은
  자연히 새 항목이 되고 옛 벡터를 잘못 물려받지 않는다.

  ★컬럼 이름은 **`embedding_model`** 이다 — `vector_chunks.embedding_model` 과
    **같은 뜻이라 같은 이름을 쓴다**(2026-08-28 개명, 원래 `model`). 모델을
    교체할 때 재임베딩 대상을 고르는 쪽과 캐시를 버리는 쪽이 같은 값을 보는데,
    이름이 갈리면 한쪽만 고치고 넘어가게 된다. 파이썬 인자는 `model` 그대로다
    — 저장소 컬럼명이지 호출 규약이 아니다.

★**DDL 이 두 군데 있고 둘 다 필요하다.** 표의 정본은
  `infra/postgres/init/02_schema.sql` 이지만 그 파일은 컨테이너가 **데이터
  디렉터리가 비었을 때만** 돌린다. 이미 데이터가 들어 있는 DB 에는 아래
  `_ensure()` 로만 생긴다. 런타임 DDL 을 빼면 기존 DB 는 매 실행 폴백하며
  경고를 내고(= 그 실행은 고정이 아니다), 스키마 파일에서 빼면 새 클론이
  이 표의 출처를 추적할 수 없다.

  ★그래서 **컬럼을 바꾸려면 세 군데를 함께 고쳐야 한다** — 이 파일의 `_DDL`,
    `infra/postgres/init/02_schema.sql`, 그리고 이미 돌고 있는 DB 를 위한
    `batch/repair/` 마이그레이션. 하나라도 빠지면 새 클론과 기존 DB 의 스키마가
    갈린다.
"""

from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional, Sequence

from app.core.config import EMBEDDING_MODEL
from app.core.trace import trace_logger

log = trace_logger(__name__)

Embed = Callable[[list[str]], Sequence[Sequence[float]]]

# ★폴백은 **용도에 따라 갈린다**(2026-08-28). 하나로 합쳐 두면 운영 폴백이
#   평가의 보호막까지 걷어낸다.
#
#       운영 `/ask`    캐시가 값을 못 주면 **직접 계산으로 넘어간다.**
#                      저장소가 죽었다고 답변까지 막을 이유가 없다
#       평가·대조      **그 자리에서 멈춘다.** 직접 계산한 값은 드리프트를
#                      타므로(배치 150건에서 코사인 최대 4.4e-03) 그 실행은
#                      재현되지 않는다. 흔들린 실행을 점수의 근거로 쓰면
#                      「Agent 때문인지 임베딩 때문인지」를 못 가른다 —
#                      그러라고 캐시를 만든 것이다
#
# ★**환경변수로 가른다.** 인자로 두면 `retrieve_service._default_embed` 까지
#   내려가야 하는데 그 파일은 2차 동안 손대지 않는 자리다. 평가 스크립트가
#   프로세스 앞에서 켜면 아래 전 구간에 걸린다.
STRICT_ENV = "EMBED_CACHE_STRICT"
_TRUE = frozenset({"1", "true", "yes", "on"})


class EmbeddingCacheMiss(RuntimeError):
    """★평가·대조 실행에서 캐시가 값을 못 줬다 — **그 실행은 재현되지 않는다.**

    운영 경로에서는 절대 오르지 않는다(거기선 직접 계산으로 넘어간다).
    """


def _strict() -> bool:
    return os.getenv(STRICT_ENV, "").strip().lower() in _TRUE

_DDL = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    embedding_model text NOT NULL,
    text_hash       text NOT NULL,
    embedding       double precision[] NOT NULL,
    text_preview    text,
    cached_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (embedding_model, text_hash)
)
"""

# 테이블 확인은 프로세스당 한 번이면 된다.
_ready = False


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    with conn.cursor() as cur:
        cur.execute(_DDL)
    _ready = True


def embed_with_cache(texts: list[str], real: Embed,
                     *, model: Optional[str] = None) -> list[list[float]]:
    """캐시에 있으면 그 값을, 없으면 **한 번만** 계산해 넣고 그 값을 돌려준다.

    ★못 찾은 것만 모아 실제 임베더에 넘긴다. 그래서 캐시가 데워지면 왕복이
      아예 없고, 데워지는 중에도 중복 텍스트를 두 번 부르지 않는다.

    ★**저장소가 죽어도 답변은 나가야 한다.** 그때는 직접 계산으로 넘어가되
      **경고를 남긴다** — 조용히 넘어가면 「결정론적이다」라고 믿는 채로
      흔들리게 된다. 흔들림을 감춘 실행을 대조의 근거로 쓰면 안 된다.
    """
    if not texts:
        return []
    strict = _strict()
    model = model or EMBEDDING_MODEL
    wanted = list(dict.fromkeys(texts))          # 순서 보존 · 중복 제거

    try:
        from app.core.database import postgres_connection

        with postgres_connection() as conn:
            _ensure(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT text_hash, embedding FROM embedding_cache "
                    "WHERE embedding_model = %s AND text_hash = ANY(%s)",
                    (model, [_key(t) for t in wanted]))
                found = {row[0]: list(row[1]) for row in cur.fetchall()}

            missing = [t for t in wanted if _key(t) not in found]
            # ★평가·대조 — **계산하기 전에** 멈춘다. 계산해 버리면 그 값이
            #   이미 드리프트를 탄 뒤라 「멈췄다」가 의미를 잃는다.
            if missing and strict:
                raise EmbeddingCacheMiss(
                    f"캐시에 없는 텍스트 {len(missing)}건 / 요청 {len(wanted)}건 — "
                    f"{STRICT_ENV} 가 켜져 있어 멈춘다. 직접 계산한 값은 흔들리므로 "
                    f"이 실행을 대조의 근거로 쓸 수 없다. **캐시를 먼저 데운 뒤** "
                    f"다시 재라. 예: {missing[0][:60]!r}")
            if missing:
                fresh = list(real(missing))
                if len(fresh) != len(missing):
                    # 계약 위반 — 조용히 짝을 맞추지 않는다
                    raise RuntimeError(
                        f"임베더가 {len(missing)}건에 {len(fresh)}건을 돌려줬다")
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO embedding_cache "
                        "(embedding_model, text_hash, embedding, text_preview) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (embedding_model, text_hash) DO NOTHING",
                        [(model, _key(t), [float(x) for x in v], t[:200])
                         for t, v in zip(missing, fresh)])
                for t, v in zip(missing, fresh):
                    found[_key(t)] = [float(x) for x in v]
            log.info("embed.cache model=%s wanted=%d hit=%d miss=%d",
                     model, len(wanted), len(wanted) - len(missing), len(missing))
            return [found[_key(t)] for t in texts]

    except EmbeddingCacheMiss:
        raise                     # ★평가 경로의 정지 신호 — 삼키지 않는다
    except Exception as exc:  # noqa: BLE001 — 캐시가 죽어도 /ask 는 살아야 한다
        if strict:
            # 저장소가 죽은 것도 평가에서는 정지 사유다. 여기서 폴백하면
            # 「캐시를 물렸다」고 믿는 채로 흔들린 값을 재게 된다.
            raise EmbeddingCacheMiss(
                f"캐시를 쓸 수 없다 — {STRICT_ENV} 가 켜져 있어 멈춘다 ({exc!r})") from exc
        log.warning("embed.cache 사용 불가 — 직접 계산으로 넘어간다. "
                    "★이 실행의 임베딩은 **고정이 아니다** (%r)", exc)
        return [list(v) for v in real(texts)]
