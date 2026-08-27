"""재료 집합 대조를 **pytest 에서도** 부를 수 있게 한다.

★기본 실행에서는 빠진다(`needs_db` 마커). Neo4j·PostgreSQL·ChromaDB 가 떠
  있어야 하고 OpenAI 임베딩을 부른다 — 없는 환경에서 실패하면 「내 변경이
  깼나」를 매번 다시 가려야 한다.

    pytest -m needs_db tests/graph/test_parity.py

★여기서 재는 것은 **재료 집합**이지 프롬프트 바이트가 아니다. 1.5차는 표기를
  붙이므로 프롬프트가 달라지는 것이 정상이다 —
  `batch/audit/ask_graph_parity.py` 의 `--materials` 와 같은 기준을 쓴다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_db

# 시간이 오래 걸려 전수는 스크립트로 돌린다. 여기서는 성격이 다른 셋만 —
# RELATIONSHIP · workspace 앵커 · 재료 없음.
_SAMPLE = [
    "삼성전자에 납품하는 기업",
    "메모리 가격 담합",
    "storminmvpsdjfk 이 뭐야",
]


@pytest.mark.parametrize("question", _SAMPLE)
def test_material_set_matches_the_phase1_baseline(question):
    from batch.audit.ask_graph_parity import compare_materials

    row = compare_materials(question)

    assert row["cache_misses"] == 0, "임베딩 캐시 미스 — 대조 결과를 믿을 수 없다"
    assert not row["sims_empty"], "유사도 정렬이 빠진 실행이 있다 — 결과 무효"
    assert row["materials_same"], row["diff_summary"]
