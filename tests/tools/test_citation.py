"""인용 정책 — **규칙과 프롬프트가 같은 말을 하는가.**

★이 규칙의 실패 모드는 「틀리는 것」이 아니라 **「갈리는 것」**이다. 재료를
  모으는 자리(마감 단계)와 LLM 에게 말하는 자리(시스템 프롬프트)와 문서가
  각자 판단하면, 하나만 바뀌었을 때 아무도 못 본다. 그래서 여기서 **둘을
  마주 세운다.**
"""

from __future__ import annotations

import pytest

from app.llm.prompt import SYSTEM_PROMPT
from app.tools import citation


class _Hit:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id


# ══════════════════════════════════════════════════════════════════
#  ★인용 가능한 것은 하나뿐이다
# ══════════════════════════════════════════════════════════════════

def test_only_search_news_is_citable():
    assert citation.CITABLE_TOOLS == {"search_news"}


@pytest.mark.parametrize("tool", ["search_dart", "get_business_overview",
                                  "get_market", "get_filings"])
def test_every_other_tool_is_context_only(tool):
    assert not citation.is_citable(tool)


def test_the_three_buckets_never_overlap():
    """★한 도구가 두 통에 들면 어느 규칙이 이기는지 알 수 없다."""
    a, b, c = (citation.CITABLE_TOOLS, citation.DEFERRED_TOOLS,
               citation.CONTEXT_ONLY_TOOLS)
    assert not (a & b) and not (b & c) and not (a & c)


def test_deferred_and_structural_reasons_are_kept_apart():
    """★이유가 다르면 통도 달라야 한다.

    `search_dart` 는 **청크가 이미 있어** 측정만 끝나면 올릴 수 있고,
    `get_business_overview` 는 **Chroma 에 청크가 없어** 적재가 선행돼야 한다.
    한 통에 넣으면 「나중에 올리면 되지」로 뭉뚱그려진다.
    """
    assert "search_dart" in citation.DEFERRED_TOOLS
    assert "get_business_overview" in citation.CONTEXT_ONLY_TOOLS


def test_every_declared_tool_actually_exists():
    """★목록에 오타가 나면 그 도구는 조용히 「인용 불가」가 된다."""
    from app.tools import company_tools, search_tools

    declared = (citation.CITABLE_TOOLS | citation.DEFERRED_TOOLS
                | citation.CONTEXT_ONLY_TOOLS)
    for name in declared:
        assert hasattr(search_tools, name) or hasattr(company_tools, name), name


# ══════════════════════════════════════════════════════════════════
#  근거 id 수집
# ══════════════════════════════════════════════════════════════════

def test_citable_tool_yields_its_evidence_ids():
    got = citation.citable_evidence_ids("search_news", [_Hit("ev_a"), _Hit("ev_b")])
    assert got == ["ev_a", "ev_b"]


def test_non_citable_tool_yields_nothing_without_raising():
    """★부르는 쪽이 잘못한 것이 아니다 — 「그 도구는 인용에 안 쓴다」가 정상이다.
    재료로는 여전히 쓰인다."""
    assert citation.citable_evidence_ids("search_dart", [_Hit("ev_a")]) == []
    assert citation.citable_evidence_ids("get_business_overview", [_Hit("ev_a")]) == []


def test_duplicates_are_folded_in_input_order():
    """★마감 단계가 이걸 그대로 합집합에 넣는다. 중복이 남으면 상한을 세는
    쪽이 같은 근거를 두 건으로 센다."""
    got = citation.citable_evidence_ids(
        "search_news", [_Hit("ev_b"), _Hit("ev_a"), _Hit("ev_b")])
    assert got == ["ev_b", "ev_a"]


def test_hits_without_an_evidence_id_are_skipped():
    assert citation.citable_evidence_ids(
        "search_news", [_Hit(None), _Hit(""), _Hit("ev_a")]) == ["ev_a"]


# ══════════════════════════════════════════════════════════════════
#  ★프롬프트가 같은 말을 하는가
# ══════════════════════════════════════════════════════════════════

def test_prompt_says_the_article_full_text_is_not_available():
    """★「뉴스 원문」은 어디에도 없다 — `news_articles` 는 본문을 저장하지 않고
    (저작권) `evidence` 도 승격된 문장만 담는다. 이걸 안 적으면 LLM 이 기사
    전문을 아는 척한다."""
    assert "기사 원문은 제공되지 않습니다" in SYSTEM_PROMPT
    assert "기사 전문이 아니라" in SYSTEM_PROMPT


def test_prompt_forbids_padding_beyond_the_evidence_sentence():
    assert "[근거]에 없는 내용을 덧붙이지 않습니다" in SYSTEM_PROMPT


def test_prompt_says_business_overview_is_context_not_citation():
    """★이걸 안 적으면 LLM 이 사업개요를 claims 에 넣고, 인용할 id 가 없어
    `claim_check` 의 uncited 카운터가 오염된다 — 지표를 못 읽게 된다."""
    assert "사업개요는 참고 맥락입니다" in SYSTEM_PROMPT
    assert "claims에 넣지 않습니다" in SYSTEM_PROMPT


def test_prompt_and_policy_agree_on_business_overview():
    """★규칙과 프롬프트가 **같은 말을 하는지**를 마주 세운다."""
    assert not citation.is_citable("get_business_overview")
    assert "인용할 수 없습니다" in SYSTEM_PROMPT


def test_the_answer_node_uses_the_one_system_prompt():
    """★프롬프트 사본이 생기지 않았나. 사본이 생기면 한쪽만 고쳐지고 그 차이를
    아무도 못 본다(계약 5번과 같은 이유)."""
    from app.graph.nodes import answer as answer_node

    assert answer_node.SYSTEM_PROMPT is SYSTEM_PROMPT
