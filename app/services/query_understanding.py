"""`anchor_source` 판정 — 「이 질문이 **무엇을 대상으로** 하는가」를 한 값으로.

설계서 §14-3 이 두 축으로 가른다.

    ①a 질의가 대상을 **명시**했나   ×   ①b 그 대상이 **해소**됐나

        O  ×  O   → query        그것을 대상으로 답한다
        O  ×  ✗   → unresolved   ★못 찾았다고 말하고 끝낸다. 워크스페이스로 안 갈아탄다
        ✗  ×  —   → workspace    워크스페이스 기업을 대상 문맥으로 해석한다

★**왜 이 모듈이 따로 있나** — 판정 조각이 세 곳에 흩어져 있었다
  (`AnchorExtractor`·`EntityResolver`·`GraphSearcher._primary_resolution`). 「하나의
  값」으로 못 박는 자리가 없어서, 해소에 실패해도 그대로 anchorless/SEMANTIC 으로
  **조용히 통과**했다(현황서 §5-3).

★**①a 를 판정할 신호가 코드에 없었다.** `AnchorExtractor.extract()` 가 「기업명이
  아예 없다」와 「기업명이 있는데 못 뽑았다」에 **똑같이 `None`** 을 준다. 질의
  41건으로 재서 정했다 — 39/41(95.1%), 현황서 §8-5.

      ①a 1차   워크스페이스 기업명을 질문 문자열과 직접 대조
      ①a 2차   Kiwi 고유명사 토큰(NNP·SL)
               └ 단, **Company 아닌 노드 이름과 정확히 일치**하는 토큰은 뺀다
      ①b       corp_code(PostgreSQL) → 실패하면 norm_name(Neo4j)

★**재료를 모으지 않는다.** 여기는 flow ①b 이고 재료는 ③ 이다(설계서 §10). 새
  검색도 하지 않는다 — ② Search 가 낸 `resolved_entities` 를 **읽기만** 한다.

★**못 잡는 것 둘을 알고 있다**(현황서 §8-5) — 사명이 형태소로 분해되는 경우
  (「존재하지않는기업」)와 §4-5 동음이의(「대상」). **규칙을 더 얹지 않았다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.api.schemas import Anchor, AnchorSource
from app.core.trace import trace_logger
from app.services import company_service
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.resolver import Resolution
from pipeline.token_overlap import kiwi

log = trace_logger(__name__)

# 고유명사 후보 태그. `SL`(외국어)을 넣는 이유는 실측이다 — Kiwi 는 `TSMC`·
# `NAVER` 를 `NNP` 가 아니라 `SL` 로 준다(2026-08-25).
_NAME_TAGS = frozenset({"NNP", "SL"})


@dataclass(frozen=True)
class AnchorDecision:
    """「무엇을 대상으로 답하는가」 — 서버가 아는 **결정론적** 값이다."""

    source: AnchorSource
    # `source=query` 면 질문이 지정한 기업, `workspace` 면 워크스페이스 기업.
    # `unresolved` 면 **비어 있다** — 재료를 만들지 않는다(설계서 §14-4).
    anchors: list[Anchor] = field(default_factory=list)
    # `unresolved` 일 때 사용자가 지목한 것으로 보이는 문자열. 「'TSMC' 에
    # 해당하는 기업을 찾지 못했습니다」 문구가 이걸 쓴다.
    named: Optional[str] = None
    # ★워크스페이스 key → 이름. **`source` 와 무관하게 항상 채운다** — 조회는
    #   경계에서 이미 한 번 끝났고(설계서 §16-3), 이걸 안 들고 다니면 문구를
    #   조립하는 쪽이 같은 조회를 또 한다. `unresolved` 의 대안 제안(§14-4)과
    #   워크스페이스 소속 표기(§12)가 이 값을 쓴다.
    workspace_names: dict[str, str] = field(default_factory=dict)


def _name_tokens(question: str) -> list[str]:
    """질문에서 고유명사로 읽히는 토큰. **순서를 지키고 중복만 제거한다.**"""
    return list(dict.fromkeys(
        t.form for t in kiwi().tokenize(question) if t.tag in _NAME_TAGS))


def _workspace_hit(question: str, workspace_names: dict[str, str]) -> Optional[str]:
    """①a 1차 — 질문에 워크스페이스 기업 이름이 있나.

    ★**새 조회가 아니다.** 최대 수십 개 문자열이 메모리에 있다(설계서 §14-7).
      표기가 달라도 걸리도록 정규화 형태로도 한 번 본다.
    """
    squashed = "".join(question.split()).lower()
    for name in workspace_names.values():
        if not name:
            continue
        if name in question or normalize_company_name(name).lower() in squashed:
            return name
    return None


def _primary(resolved_entities: list[Resolution]) -> Resolution:
    """★`GraphSearcher._primary_resolution()` 과 **같은 규칙**(점수 최대)이다 —
    실제로 재료를 모은 앵커와 응답에 싣는 앵커가 어긋나면 안 된다."""
    return max(resolved_entities, key=lambda r: r.score)


def decide_anchor(
    question: str,
    resolved_entities: list[Resolution],
    workspace_names: dict[str, str],
) -> AnchorDecision:
    """`anchor_source` 와 재료 앵커를 정한다. **호출은 ② Search 뒤**다."""
    tokens = _name_tokens(question)
    ws_hit = _workspace_hit(question, workspace_names)

    # ── ①b 1단 — corp_code (② Search 가 이미 낸 결과를 읽기만 한다) ─────
    if resolved_entities:
        best = _primary(resolved_entities)
        return _query(best.corp_code, best.corp_name, "corp_code", workspace_names)

    # ── ①b 2단 — norm_name fallback (설계서 §16-1 의 식별 우선순위) ──────
    candidates = ([ws_hit] if ws_hit else []) + tokens
    found = company_service.find_by_names(candidates)
    if found is not None:
        return _query(found["key"], found["name"], "norm_name", workspace_names)

    # ── ①a — 그래서, 대상을 명시하기는 했나 ─────────────────────────────
    named = tokens
    if named:
        # 기업이 아닌 것을 기업으로 오인하지 않는다(실측: `HBM` 은 Product).
        non_company = company_service.non_company_labels(named)
        named = [t for t in named if t not in non_company]
        if non_company:
            log.info("anchor.non_company_dropped %s", non_company)

    if ws_hit or named:
        target = ws_hit or named[0]
        log.info("anchor.source=unresolved named=%r tokens=%s", target, tokens)
        return AnchorDecision(source=AnchorSource.UNRESOLVED, named=target,
                              workspace_names=workspace_names)

    anchors = [Anchor(key=key, name=name, source=AnchorSource.WORKSPACE)
               for key, name in workspace_names.items()]
    log.info("anchor.source=workspace anchors=%d", len(anchors))
    return AnchorDecision(source=AnchorSource.WORKSPACE, anchors=anchors,
                          workspace_names=workspace_names)


def _query(key: str, name: str, via: str,
           workspace_names: dict[str, str]) -> AnchorDecision:
    log.info("anchor.source=query key=%s name=%r via=%s", key, name, via)
    return AnchorDecision(
        source=AnchorSource.QUERY,
        anchors=[Anchor(key=key, name=name, source=AnchorSource.QUERY)],
        workspace_names=workspace_names)
