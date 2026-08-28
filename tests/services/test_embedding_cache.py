"""임베딩 영속 캐시 — **같은 텍스트는 언제나 같은 벡터.**

★이 파일이 지키는 계약은 하나다: **한 번 정해진 벡터는 다시 계산되지 않는다.**
  OpenAI 임베딩이 같은 입력에 같은 벡터를 보장하지 않기 때문에 필요한 방어다
  (실측 2026-08-28: 배치 150건에서 편차 2.1e-03 · 코사인 최대 4.4e-03 · 그
  결과 20질문 중 2질문의 사건 순위가 실행마다 뒤집혔다).

★**DB 를 쓰지 않는다.** 저장소 자리에 대역을 세워 캐시의 규칙만 본다.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.services import embedding_cache as ec


class FakeCursor:
    """`embedding_cache` 가 쓰는 SQL 세 종류만 흉내 낸다."""

    def __init__(self, store: dict):
        self.store = store
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if "CREATE TABLE" in sql:
            return
        if sql.startswith("SELECT"):
            model, hashes = params
            self._rows = [(h, list(self.store[(model, h)]))
                          for h in hashes if (model, h) in self.store]

    def executemany(self, sql, rows):
        for model, text_hash, embedding, _preview in rows:
            # ON CONFLICT DO NOTHING — 이미 있으면 **덮지 않는다**
            self.store.setdefault((model, text_hash), list(embedding))

    def fetchall(self):
        return self._rows


@pytest.fixture
def store(monkeypatch):
    """가짜 저장소 + 그것을 쓰는 커넥션. `(store, calls)` 를 돌려준다."""
    data: dict = {}

    class FakeConn:
        def cursor(self):
            return FakeCursor(data)

    @contextmanager
    def fake_connection():
        yield FakeConn()

    import app.core.database as db

    monkeypatch.setattr(db, "postgres_connection", fake_connection)
    monkeypatch.setattr(ec, "_ready", False)
    return data


def counting_embed(log: list):
    """부른 텍스트를 기록하는 임베더. 값은 **호출마다 흔들리게** 만든다.

    ★텍스트마다 값이 **확실히 달라야** 하고(안 그러면 「같은 벡터」 단언이
      우연히 통과한다) 그 값이 **실행마다 재현**돼야 한다. `hash()` 는
      프로세스마다 달라지므로 쓰지 않는다 — 글자 코드로 직접 만든다.
    """
    state = {"n": 0}

    def _fingerprint(t: str) -> float:
        return float(sum((i + 1) * ord(c) for i, c in enumerate(t)))

    def _embed(texts):
        state["n"] += 1
        log.append(list(texts))
        # ★실제 임베더처럼 **실행마다 다른 값**을 준다 — 캐시가 없으면 흔들린다.
        return [[_fingerprint(t) + state["n"] * 1e-3, 0.5] for t in texts]

    return _embed


# ══════════════════════════════════════════════════════════════════
#  ★핵심 계약 — 값이 고정된다
# ══════════════════════════════════════════════════════════════════

def test_same_text_gets_the_same_vector_across_calls(store):
    """★임베더가 흔들려도 **캐시가 첫 값을 고정**한다."""
    log: list = []
    embed = counting_embed(log)

    first = ec.embed_with_cache(["가격 담합", "압수수색"], embed)
    second = ec.embed_with_cache(["가격 담합", "압수수색"], embed)

    assert first == second
    assert len(log) == 1, "두 번째 호출은 실제 임베더를 부르면 안 된다"


def test_vector_does_not_depend_on_batch_composition(store):
    """★**배치 의존이 사라진다.** 같은 라벨이 리스트 길이·위치에 따라 다른
    청크에 실려 다른 벡터를 받던 것이 캐시로 정규화된다(`_EMBED_BATCH`=100)."""
    log: list = []
    embed = counting_embed(log)

    alone = ec.embed_with_cache(["가격 담합"], embed)[0]
    in_batch = ec.embed_with_cache(["채움", "가격 담합", "채움2"], embed)[1]

    assert alone == in_batch


def test_only_the_missing_texts_are_embedded(store):
    """★못 찾은 것만 모아 넘긴다 — 데워진 뒤에는 왕복이 아예 없다."""
    log: list = []
    embed = counting_embed(log)

    ec.embed_with_cache(["a", "b"], embed)
    ec.embed_with_cache(["a", "b", "c"], embed)

    assert log == [["a", "b"], ["c"]]


def test_duplicate_texts_are_embedded_once(store):
    log: list = []
    embed = counting_embed(log)

    got = ec.embed_with_cache(["a", "a", "b", "a"], embed)

    assert log == [["a", "b"]]
    assert got[0] == got[1] == got[3], "같은 텍스트는 같은 벡터여야 한다"


def test_result_follows_the_input_order_including_duplicates(store):
    """★입력 순서 그대로 돌려준다 — 중복 제거는 **계산에만** 쓴다."""
    log: list = []
    embed = counting_embed(log)

    got = ec.embed_with_cache(["b", "a", "b"], embed)

    assert len(got) == 3
    assert got[0] == got[2] != got[1]


def test_model_is_part_of_the_key(store):
    """★모델을 바꾸면 옛 벡터를 물려받으면 안 된다."""
    log: list = []
    embed = counting_embed(log)

    ec.embed_with_cache(["a"], embed, model="model-1")
    ec.embed_with_cache(["a"], embed, model="model-2")

    assert log == [["a"], ["a"]], "다른 모델이면 다시 계산해야 한다"
    assert len(store) == 2


def test_empty_input_does_not_touch_the_store(store):
    log: list = []
    assert ec.embed_with_cache([], counting_embed(log)) == []
    assert log == []


# ══════════════════════════════════════════════════════════════════
#  실패 — ★조용히 넘어가지 않는다
# ══════════════════════════════════════════════════════════════════

def test_store_failure_falls_back_but_warns(monkeypatch, caplog):
    """★저장소가 죽어도 `/ask` 는 살아야 한다. 다만 **경고를 남긴다** —
    조용히 넘어가면 「결정론적이다」라고 믿는 채로 흔들리게 된다."""
    @contextmanager
    def broken():
        raise RuntimeError("postgres down")
        yield  # pragma: no cover

    import app.core.database as db

    monkeypatch.setattr(db, "postgres_connection", broken)
    monkeypatch.setattr(ec, "_ready", False)

    log: list = []
    with caplog.at_level("WARNING"):
        got = ec.embed_with_cache(["a"], counting_embed(log))

    assert len(got) == 1 and log == [["a"]]
    assert any("고정이 아니다" in r.message for r in caplog.records), \
        "폴백을 조용히 하면 안 된다"


def test_embedder_returning_the_wrong_count_is_an_error(store, caplog):
    """★짝이 안 맞으면 **조용히 맞추지 않는다.** 엉뚱한 텍스트에 엉뚱한 벡터가
    붙으면 순위가 통째로 틀리는데 아무도 모른다."""
    def short(texts):
        return [[0.1, 0.2]]          # 몇 개를 넘기든 1개만 준다

    with caplog.at_level("WARNING"):
        ec.embed_with_cache(["a", "b"], short)

    # 폴백 경로로 빠지되 경고가 남는다
    assert any("고정이 아니다" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════
#  ★폴백 정책 — **용도에 따라 갈린다**
#
#    운영 `/ask`   캐시가 값을 못 주면 직접 계산 (위 두 테스트가 지킨다)
#    평가·대조     그 자리에서 멈춘다 — 흔들린 실행을 점수의 근거로 쓰면
#                  「Agent 때문인지 임베딩 때문인지」를 못 가른다
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setenv(ec.STRICT_ENV, "1")


def test_strict_mode_stops_on_a_cache_miss(store, strict):
    """★미스면 **계산하기 전에** 멈춘다. 계산해 버리면 그 값이 이미 드리프트를
    탄 뒤라 「멈췄다」가 의미를 잃는다."""
    log: list = []

    with pytest.raises(ec.EmbeddingCacheMiss):
        ec.embed_with_cache(["처음 보는 텍스트"], counting_embed(log))

    assert log == [], "미스에서 멈춘다면 실제 임베더를 불러선 안 된다"


def test_strict_mode_reports_how_many_missed(store, monkeypatch):
    """★「미스 건수를 함께 보고해라」 — 몇 건이 비었는지 알아야 캐시를
    데울지 판단할 수 있다.

    ★캐시를 **먼저 데우고** strict 를 켠다. 순서가 반대면 데우는 호출부터
      멈춰서 미스 건수를 재지 못한다.
    """
    log: list = []
    ec.embed_with_cache(["a"], counting_embed(log))          # 한 건만 데운다

    monkeypatch.setenv(ec.STRICT_ENV, "1")
    with pytest.raises(ec.EmbeddingCacheMiss) as got:
        ec.embed_with_cache(["a", "b", "c"], counting_embed(log))

    msg = str(got.value)
    assert "2건" in msg and "3건" in msg, f"미스 2 / 요청 3 이 보여야 한다: {msg}"


def test_strict_mode_serves_a_warm_cache_without_calling_the_embedder(store, monkeypatch):
    """★데워진 캐시는 strict 에서도 그대로 나간다 — 멈추는 건 **미스**뿐이다."""
    log: list = []
    warm = ec.embed_with_cache(["가격 담합"], counting_embed(log))

    monkeypatch.setenv(ec.STRICT_ENV, "1")
    got = ec.embed_with_cache(["가격 담합"], counting_embed(log))

    assert got == warm
    assert len(log) == 1, "적중이면 실제 임베더를 부르지 않는다"


def test_strict_mode_does_not_silently_fall_back_when_the_store_is_down(monkeypatch):
    """★저장소가 죽은 것도 **평가에서는 정지 사유**다. 여기서 폴백하면
    「캐시를 물렸다」고 믿는 채로 흔들린 값을 재게 된다."""
    @contextmanager
    def broken():
        raise RuntimeError("postgres down")
        yield  # pragma: no cover

    import app.core.database as db

    monkeypatch.setattr(db, "postgres_connection", broken)
    monkeypatch.setattr(ec, "_ready", False)
    monkeypatch.setenv(ec.STRICT_ENV, "1")

    log: list = []
    with pytest.raises(ec.EmbeddingCacheMiss):
        ec.embed_with_cache(["a"], counting_embed(log))


def test_strict_is_off_by_default(monkeypatch, store):
    """★운영이 기본이다. 스위치를 안 켜면 **지금까지와 똑같이** 동작한다 —
    켜 두는 것을 잊어 평가가 조용히 통과하는 편이, 안 켰는데 운영이 멈추는
    것보다 낫다."""
    monkeypatch.delenv(ec.STRICT_ENV, raising=False)
    assert ec._strict() is False

    log: list = []
    got = ec.embed_with_cache(["처음 보는 텍스트"], counting_embed(log))
    assert len(got) == 1 and log == [["처음 보는 텍스트"]]
