"""`anchor_source` 판정 — 「이 질문이 **무엇을 대상으로** 하는가」를 한 값으로.

설계서 §14-3 이 두 축으로 가른다.

    ①a 질의가 대상을 **명시**했나   ×   ①b 그 대상이 **해소**됐나

        O  ×  O   → query        그것을 대상으로 답한다
        O  ×  ✗   → unresolved   ★못 찾았다고 말하고 끝낸다. 워크스페이스로 안 갈아탄다
        ✗  ×  —   → context      **보고 있는 기업**이 있으면 그것 (★워크스페이스보다 먼저)
        ✗  ×  —   → workspace    없으면 워크스페이스 기업을 대상 문맥으로 해석한다

★**`context` 가 `workspace` 앞이다.** 상세 페이지에서 「이 회사 노조 리스크
  어때?」를 물으면 답은 그 회사이지 담아 둔 기업이 아니다. 화면이 보여주는 것을
  무시하고 담아 둔 것으로 답하면, 그것도 「물은 것과 다른 대상으로 답하기」다.

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
    # `source=query` 면 질문이 지정한 기업, `context` 면 보고 있는 기업,
    # `workspace` 면 워크스페이스 기업.
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
    # ★보고 있는 기업 key → 이름. `workspace_names` 와 **같은 이유로** 항상
    #   채운다 — 조회는 경계에서 끝났고, 문구를 조립하는 쪽이 같은 조회를
    #   또 하지 않게 한다. `halt_no_material` 이 「담긴 기업도 보고 있는 기업도
    #   없다」를 가를 때와 프롬프트 머리말이 이 값을 쓴다.
    context_names: dict[str, str] = field(default_factory=dict)


def _name_tokens(question: str) -> list[str]:
    """질문에서 고유명사로 읽히는 토큰. **순서를 지키고 중복만 제거한다.**"""
    return list(dict.fromkeys(
        t.form for t in kiwi().tokenize(question) if t.tag in _NAME_TAGS))


def _name_hit(question: str, names: dict[str, str]) -> Optional[str]:
    """①a 1차 — 질문에 **이 목록의 기업 이름**이 있나.

    ★**새 조회가 아니다.** 최대 수십 개 문자열이 메모리에 있다(설계서 §14-7).
      표기가 달라도 걸리도록 정규화 형태로도 한 번 본다.

    ★워크스페이스와 **보고 있는 기업 양쪽에** 쓴다. 하는 일이 「이 이름들 중
      하나가 질문에 있나」뿐이라 목록의 출처를 따질 이유가 없다 — 그래서
      이름이 `_workspace_hit` 이 아니다.
    """
    squashed = "".join(question.split()).lower()
    for name in names.values():
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
    context_names: Optional[dict[str, str]] = None,
) -> AnchorDecision:
    """`anchor_source` 와 재료 앵커를 정한다. **호출은 ② Search 뒤**다.

    ★`context_names` 는 **기본값이 있다.** 부르는 쪽 셋(`material.resolve_anchor`·
      `retrieve_service._search`·테스트)이 전부 3인자로 부르고 있어서, 없으면
      「보고 있는 기업이 없다」로 읽히는 것이 맞다 — 빠뜨린 호출부가 조용히
      다르게 동작하지 않는다.
    """
    tokens = _name_tokens(question)
    context_names = context_names or {}
    ws_hit = _name_hit(question, workspace_names)
    ctx_hit = _name_hit(question, context_names)

    # ── ①b 1단 — corp_code (② Search 가 이미 낸 결과를 읽기만 한다) ─────
    if resolved_entities:
        best = _primary(resolved_entities)
        return _query(best.corp_code, best.corp_name, "corp_code",
                      workspace_names, context_names)

    # ── ①b 2단 — norm_name fallback (설계서 §16-1 의 식별 우선순위) ──────
    # ★`ctx_hit` 을 `ws_hit` 과 **같은 자리에** 넣는다. 둘 다 「질문에 이름이
    #   있는데 ② Search 가 못 해소했다」는 같은 상황이고, 여기서 갈라야 할
    #   이유가 없다 — 찾으면 `query` 다. **질문이 지목한 것**이기 때문이다.
    candidates = ([ws_hit] if ws_hit else []) + \
                 ([ctx_hit] if ctx_hit else []) + tokens
    found = company_service.find_by_names(candidates)
    if found is not None:
        return _query(found["key"], found["name"], "norm_name",
                      workspace_names, context_names)

    # ── ①a — 그래서, 대상을 명시하기는 했나 ─────────────────────────────
    named = tokens
    if named:
        # 기업이 아닌 것을 기업으로 오인하지 않는다(실측: `HBM` 은 Product).
        non_company = company_service.non_company_labels(named)
        named = [t for t in named if t not in non_company]
        if non_company:
            log.info("anchor.non_company_dropped %s", non_company)

    if ws_hit or ctx_hit or named:
        target = ws_hit or ctx_hit or named[0]
        log.info("anchor.source=unresolved named=%r tokens=%s", target, tokens)
        return AnchorDecision(source=AnchorSource.UNRESOLVED, named=target,
                              workspace_names=workspace_names,
                              context_names=context_names)

    # ── ③ 보고 있는 기업 — ★**워크스페이스보다 먼저** ───────────────────
    #
    #   ★순서가 이 분기의 전부다. 상세 페이지에서 「이 회사 노조 리스크 어때?」를
    #     물으면 답은 **그 회사**이지 담아 둔 기업이 아니다. 워크스페이스를 먼저
    #     보면 화면이 무엇을 보여주고 있든 담아 둔 기업으로 답하게 된다 —
    #     §14-3 이 막으려는 「물은 것과 다른 대상으로 답하기」의 같은 종류다.
    #
    #   ★워크스페이스가 **비어 있어도** 여기가 성립한다. 그래서 `guard_workspace`
    #     의 게이트가 `workspace_keys or context_keys` 로 넓어진다.
    if context_names:
        anchors = [Anchor(key=key, name=name, source=AnchorSource.CONTEXT)
                   for key, name in context_names.items()]
        log.info("anchor.source=context anchors=%d", len(anchors))
        return AnchorDecision(source=AnchorSource.CONTEXT, anchors=anchors,
                              workspace_names=workspace_names,
                              context_names=context_names)

    anchors = [Anchor(key=key, name=name, source=AnchorSource.WORKSPACE)
               for key, name in workspace_names.items()]
    log.info("anchor.source=workspace anchors=%d", len(anchors))
    return AnchorDecision(source=AnchorSource.WORKSPACE, anchors=anchors,
                          workspace_names=workspace_names,
                          context_names=context_names)


def _query(key: str, name: str, via: str,
           workspace_names: dict[str, str],
           context_names: Optional[dict[str, str]] = None) -> AnchorDecision:
    log.info("anchor.source=query key=%s name=%r via=%s", key, name, via)
    return AnchorDecision(
        source=AnchorSource.QUERY,
        anchors=[Anchor(key=key, name=name, source=AnchorSource.QUERY)],
        workspace_names=workspace_names,
        context_names=context_names or {})
