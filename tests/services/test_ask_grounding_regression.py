"""실측 실패 사례를 **회귀로 못박는다** — 2026-08-26 재발 확인분.

fixture `ask_sk_hynix_production_disruption.observed.json` 은 2026-08-23 에 실호출로
녹화한 것이고, **같은 실패가 2026-08-26 에 그대로 재현**됐다.

    질문  「SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?」

    답변  「SK하이닉스는 최근 HBM3E 대량 양산에 차질이 발생했다고 보도되었습니다.
           … 2024년 2월 16일에는 이천 공장에서 질소 누출 사고가 발생하여,
           이로 인해 생산에 영향을 미쳤을 가능성이 있습니다.」

세 가지가 한 답변에 겹쳐 있다.

    §5-12  Event 라벨 「HBM3E 대량 양산 **차질**」 ↔ 근거 「양산을 세계 최초로 **시작**」
    §5-14  「2024-02-16 에 **발생**」 ↔ 근거 원문은 **2015년** 사고 (보도일을 발생일로)
    §5-13  「이로 인해 생산에 영향을 미쳤을 가능성」 — 근거에 없는 인과

★이 파일은 fixture 를 **관측 기록에서 회귀 방지 케이스로 승격**시킨다. 셋 다
  프롬프트 규칙만으로 막으려다 실패한 이력이 있어(2026-08-23 에 규칙을 넣었는데
  2026-08-26 에 같은 문장이 재현됐다), **구조적 장치가 잡는지**를 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.schemas import AnchorSource, RetrieveResponse
from app.graph import prompt as graph_prompt
from app.tools.dto import ROLE_NOTE, SOURCE_NOTE, EventDTO
from app.services import claim_check, material_consistency

_FIXTURE = (Path(__file__).parent / "fixtures"
            / "ask_sk_hynix_production_disruption.observed.json")

# 재발한 답변에서 그대로 옮긴 주장 셋.
_OBSERVED_CLAIMS = [
    {"text": "SK하이닉스는 최근 HBM3E 대량 양산에 차질이 발생했다고 보도되었습니다",
     "evidence_ids": ["ev_71d469860eb2a66c"]},
    {"text": "이천 공장에서 질소 누출 사고가 발생한 적이 있으며 이 사건은 "
             "2024년 2월 16일에 보도되었습니다",
     "evidence_ids": ["ev_c4da6759d46b4f51"]},
    {"text": "이 사고로 인해 생산에 영향을 미쳤을 가능성이 있습니다",
     "evidence_ids": ["ev_c4da6759d46b4f51"]},
]

_POLARITY_EVENT = "evt_news_153cd0debaae"   # HBM3E 대량 양산 차질
_TEMPORAL_EVENT = "evt_news_77b13c5c182a"   # 이천 공장 질소 누출 사고


@pytest.fixture(scope="module")
def retrieved() -> RetrieveResponse:
    return RetrieveResponse(**json.loads(_FIXTURE.read_text(encoding="utf-8"))["retrieved"])


def _events(retrieved) -> list[EventDTO]:
    """녹화본의 API 스키마 `Event` → 운영 경로가 읽는 `EventDTO`.

    ★2026-09-04 — 1차(`AnswerService`) 폐기로 **운영 렌더러에 옮겨 붙였다.**
      녹화는 1차 시절 것이지만 이 파일이 보는 것은 격리 장치(⑥.5)이고, 그
      장치는 두 렌더러가 **같은 `material_consistency` 를** 부른다.
    """
    return [EventDTO(event_id=e.event_id, name=e.name, event_type=e.event_type,
                     is_risk=e.is_risk, occurred_at=e.occurred_at,
                     evidence_ids=list(e.evidence_ids), role="subject",
                     role_note=ROLE_NOTE["subject"])
            for e in retrieved.events]


def _facts(retrieved) -> str:
    """운영 렌더러의 `[사실]`. **관계·파급은 이 파일의 관심 밖**이라 비운다."""
    return graph_prompt.fact_lines(
        match_type=retrieved.match_type, companies=retrieved.companies,
        events=_events(retrieved), relations=[], propagation=[],
        evidence=retrieved.evidence, workspace_keys=set())


def _prompt(retrieved) -> str:
    return graph_prompt.build_user_prompt(
        "q", match_type=retrieved.match_type, companies=retrieved.companies,
        events=_events(retrieved), relations=[], propagation=[],
        evidence=retrieved.evidence, anchor_source=AnchorSource.QUERY,
        workspace_names={})


# ── §5-12 극성 반전 ──────────────────────────────────────────────────────

def test_the_polarity_reversed_event_is_flagged(retrieved):
    assert _POLARITY_EVENT in material_consistency.check_polarity(
        retrieved.events, retrieved.evidence)


def test_the_polarity_reversed_label_is_gone_from_the_confirmed_facts(retrieved):
    """★「HBM3E 대량 양산 차질」이 **사실로** 실리지 않는다."""
    facts = _facts(retrieved)
    event_lines = [l for l in facts.splitlines() if l.startswith("사건 ")]

    assert not any("HBM3E 대량 양산 차질" in l for l in event_lines)


# ── §5-14 시간 맥락 ──────────────────────────────────────────────────────

def test_the_background_clause_event_is_flagged(retrieved):
    flag = material_consistency.check_temporal(
        retrieved.events, retrieved.evidence)[_TEMPORAL_EVENT]

    assert flag.occurred_year == 2024
    assert 2015 in flag.evidence_years


def test_the_report_date_is_no_longer_presented_as_the_event_date(retrieved):
    """★「2024년 2월 16일에 질소 누출 사고가 **발생**」이 나온 자리다."""
    line = next(l for l in _facts(retrieved).splitlines()
                if "질소 누출" in l and l.startswith("사건 "))

    assert "보도 2024-02-16" not in line
    assert "2015" in line


def test_the_nitrogen_event_itself_survives(retrieved):
    """★사건 자체는 근거 원문에 실재한다 — 날짜만 격리하고 사건은 남긴다."""
    assert "이천 공장 질소 누출 사고" in _facts(retrieved)


# ── §5-13 근거에 없는 인과 ───────────────────────────────────────────────

def test_the_invented_causation_is_typed_as_a_free_combination(retrieved):
    """★claim 5종 어디에도 안 걸리던 문장이다 — 6번째 유형이 처음으로 잡는다."""
    checked = claim_check.check(
        _OBSERVED_CLAIMS, {e.evidence_id: e for e in retrieved.evidence},
        propagation_targets=[p.target for p in retrieved.propagation])

    invented = next(c for c in checked if "생산에 영향" in c.text)

    assert invented.claim_type == claim_check.TYPE_FREE_COMBINATION
    assert invented.effect_score == 0.0


def test_the_two_grounded_claims_are_not_typed_as_free_combination(retrieved):
    """★오탐 확인 — 인과를 주장하지 않은 문장까지 잡으면 안 된다."""
    checked = claim_check.check(
        _OBSERVED_CLAIMS, {e.evidence_id: e for e in retrieved.evidence},
        propagation_targets=[p.target for p in retrieved.propagation])

    assert [c.claim_type for c in checked[:2]] == [None, None]


# ── 불변식 — 격리는 [확인된 사실] 안에서만 ──────────────────────────────

def test_no_evidence_is_dropped_by_any_of_the_three_devices(retrieved):
    """★⑥.5 금지사항(설계서 §10) — 근거를 **버리지 않는다.** 화면이 인용할 수
    있는 것이 줄면 안 된다."""
    prompt = _prompt(retrieved)

    for evidence in retrieved.evidence:
        if not evidence.missing:
            assert f'<evidence id="{evidence.evidence_id}"' in prompt


def test_the_contradicting_source_text_is_still_quotable(retrieved):
    """★격리한 사건의 근거 원문도 [근거] 블록에 그대로 있다 — 원문이 실제로
    말하는 내용(양산 시작)은 답변에 쓸 수 있어야 한다."""
    prompt = _prompt(retrieved)

    assert "세계 최초로 시작했다" in prompt


def test_the_retrieve_response_is_untouched(retrieved):
    """★`RetrieveResponse` 는 변경하지 않는다 — `/retrieve` 계약도 `sources[]` 도
    그대로다. 격리는 프롬프트 조립(⑦) 안에서만 일어난다."""
    before = (len(retrieved.events), len(retrieved.evidence),
              len(retrieved.relations))

    _facts(retrieved)
    _prompt(retrieved)

    assert (len(retrieved.events), len(retrieved.evidence),
            len(retrieved.relations)) == before
