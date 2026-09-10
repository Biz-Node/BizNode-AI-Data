"""`anchor_source` 판정 — 「이 질문이 **무엇을 대상으로** 하는가」를 한 값으로.

설계서 §14-3 이 두 축으로 가른다.

    ①a 질의가 대상을 **명시**했나   ×   ①b 그 대상이 **해소**됐나

        O  ×  O   → query        그것을 대상으로 답한다
        O  ×  ✗   → unresolved   ★못 찾았다고 말하고 끝낸다. 워크스페이스로 안 갈아탄다
        ✗  ×  —   → context      **보고 있는 기업**이 있으면 그것
        ✗  ×  —   → anchorless   없으면 **앵커 없음**. ★워크스페이스로 갈아타지 않는다

★**`workspace` 갈래가 사라졌다** (최종 설계 §17-3, 이번 개정). 앵커가 없을 때
  워크스페이스 기업을 대상으로 승격시키던 자리다. 그러면 「최근 주요 투자
  이벤트가 뭐야?」가 「담아 둔 기업들의 투자 이벤트」로 조용히 바뀐다 —
  §14-3 이 막으려는 「물은 것과 다른 대상으로 답하기」와 **같은 종류**의 오답을,
  해소 실패가 아니라 **정상 질의**에서 저지르고 있었다.

  워크스페이스는 이제 **대상이 아니라 랭킹 문맥**이다. 앵커 없는 질의도 Global
  Search 를 그대로 타고, 워크스페이스는 그 결과의 순서에만 관여한다.

★**`context` 는 남는다.** 「보고 있는 기업」은 담아 둔 것과 달리 화면이 **대상을
  알고 있는** 상태다 — 상세 페이지에서 「이 회사 노조 리스크 어때?」를 물으면
  답은 그 회사다. 담아 둔 것과 한 값으로 묶으면 안 되는 이유가 이것이다.

★**왜 이 모듈이 따로 있나** — 판정 조각이 세 곳에 흩어져 있었다
  (`AnchorExtractor`·`EntityResolver`·`GraphSearcher._primary_resolution`). 「하나의
  값」으로 못 박는 자리가 없어서, 해소에 실패해도 그대로 anchorless/SEMANTIC 으로
  **조용히 통과**했다(현황서 §5-3).

★**①a 를 판정할 신호가 코드에 없었다.** `AnchorExtractor.extract()` 가 「기업명이
  아예 없다」와 「기업명이 있는데 못 뽑았다」에 **똑같이 `None`** 을 준다. 질의
  41건으로 재서 정했다 — 39/41(95.1%), 현황서 §8-5.

      ①a 1차   워크스페이스 기업명을 질문 문자열과 직접 대조
      ①a 2차   Kiwi 가 쪼갠 것을 도로 붙인 **덩어리** 중 고유명사(NNP·SL)를 품은 것
               └ 단, **Company 아닌 노드 이름과 정확히 일치**하는 것은 뺀다
               └ ★조각이 아니라 덩어리인 까닭은 `_name_tokens()` 에 (§5-15)
      ①b       corp_code(PostgreSQL) → 실패하면 norm_name(Neo4j)

★**재료를 모으지 않는다.** 여기는 flow ①b 이고 재료는 ③ 이다(설계서 §10). 새
  검색도 하지 않는다 — ② Search 가 낸 `resolved_entities` 를 **읽기만** 한다.

★**못 잡는 것 둘을 알고 있다**(현황서 §8-5) — 사명이 형태소로 분해되는 경우
  (「존재하지않는기업」)와 §4-5 동음이의(「대상」). **규칙을 더 얹지 않았다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.api.schemas import Anchor, AnchorSource, NodeLabel
from app.core.trace import trace_logger
from app.services import company_service
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.resolver import Resolution
from pipeline.token_overlap import kiwi, merged_spans

log = trace_logger(__name__)

# 고유명사 후보 태그. `SL`(외국어)을 넣는 이유는 실측이다 — Kiwi 는 `TSMC`·
# `NAVER` 를 `NNP` 가 아니라 `SL` 로 준다(2026-08-25).
_NAME_TAGS = frozenset({"NNP", "SL"})


@dataclass(frozen=True)
class AnchorDecision:
    """「무엇을 대상으로 답하는가」 — 서버가 아는 **결정론적** 값이다."""

    source: AnchorSource
    # `source=query` 면 질문이 지정한 기업, `context` 면 보고 있는 기업.
    # `unresolved`·`anchorless` 면 **비어 있다** — 앵커가 없는 상태다.
    #   · `unresolved` 는 재료를 만들지 않는다(설계서 §14-4)
    #   · `anchorless` 는 **재료를 만든다** — Global Search 히트가 재료다
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
    #   또 하지 않게 한다. 프롬프트 머리말이 이 값을 쓴다.
    context_names: dict[str, str] = field(default_factory=dict)


def _name_tokens(question: str) -> list[str]:
    """질문이 지목한 **사명 후보.** 순서를 지키고 중복만 제거한다.

    ★**토큰이 아니라 덩어리다**(2026-09-10 · 현황서 §5-15). 원문에서 사이가
      벌어져 있지 않았던 내용어를 도로 붙인 뒤, 그중 **고유명사를 품은 것**만
      남긴다. Kiwi 가 사명을 쪼개서 조각이 그대로 후보가 되면 **두 방향으로**
      틀린다:

          오탐  「TSMC반도체홀딩스코리아」의 조각 `TSMC` 가 실존 기업을 문다
          미탐  「SK하이닉스」가 조각 `SK`(SK주식회사 00144155)로 해소된다

      뒤엣것은 A-7 의 2차 앵커에서 **실제로 나가고 있었다** — 「삼성전자와
      SK하이닉스는 무슨 관계야?」의 2차 앵커가 SK주식회사였다.

    ★**고유명사를 품은 덩어리만** 남기는 것이 요점이다. 덩어리를 그냥 쓰면
      「최근」·「업계」·「가격」 같은 일반명사가 대상이 되어 **앵커리스가
      통째로 죽는다**(실측: 12/12 가 `unresolved` 로 뒤집혔다 — Global Event
      Search 가 막힌다). 반대로 이 조건을 걸면 **「고유명사가 없다」가 전과 같은
      뜻**이라 앵커리스 판정이 그대로다(질의 774건 전수에서 공집합 동치 무위반).

    ★**조각으로 되돌리는 폴백을 두지 않는다.** 「삼성전자주가」처럼 붙여 쓴 것은
      미탐으로 남는데, 폴백을 넣으면 합성 사명 오탐이 그대로 돌아온다 — 재 봤고
      121 로 되돌아갔다(2026-09-10 · 질의 539건). 그 형태는 ①b **1단**
      (`corp_code`)이 절반쯤 흡수한다(58건 중 26).

    ★실측 — ①b 2단으로 내려오는 539건에서 오탐 **121 → 12**, 정답 111 → 110.
    """
    return list(dict.fromkeys(
        text for text, tags in merged_spans(kiwi().tokenize(question))
        if tags & _NAME_TAGS))


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


def _second_anchor(tokens: list[str], primary_name: str,
                   primary_key: str) -> Optional[dict]:
    """질문이 **또 다른 기업**을 지목했나 (§6-0 A-7).

    ★**1차 앵커 이름의 부분 문자열인 토큰은 뺀다.** Kiwi 가 「SK하이닉스」를
      `SK`·`하이닉스` 로 쪼개는데 `SK` 는 **SK주식회사(00144155)로 실제로
      해소된다**(실측 2026-09-05). 조각이 별개 기업으로 앵커가 되면 묻지 않은
      대상이 재료에 들어온다 — §14-3 이 막으려는 그 오답이다.

      ★**2차 쪽이 쪼개지던 것은 여기서 못 막았다** — 이 함수는 **1차 앵커 이름의**
        조각만 본다. 「삼성전자와 SK하이닉스는 무슨 관계야?」의 2차 앵커가
        SK주식회사로 나가고 있었다. 후보가 덩어리가 된 뒤로(`_name_tokens` ·
        §5-15) 애초에 안 쪼개지므로, **이 줄이 남는 이유는 이제 비용이다.**

    ★**남는 토큰이 없으면 조회를 안 한다.** 단일 대상 질의(41건 중 33건 · §8-5)의
      그래프 조회가 하나로 유지된다 —
      `test_a_single_target_question_still_costs_one_graph_lookup` 이 묶어 둔다.

    ★**2차까지만이다.** 「A와 B」가 실측에서 잡은 형태이고, 셋 이상은 재 본 적이
      없다. 재료 기업 상한은 `retrieve_service.anchor_companies()` 의 기존
      `_MAX_COMPANIES` 가 그대로 맡는다 — 새 숫자를 만들지 않는다.
    """
    leftover = [t for t in tokens if t and t not in primary_name]
    if not leftover:
        return None
    found = company_service.find_by_names(leftover)
    if found is None or found["key"] == primary_key:
        return None
    log.info("anchor.second key=%s name=%r from=%s", found["key"], found["name"], leftover)
    return found


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
    #
    # ★**해소됐다 ≠ 그래프에 있다** (현황서 §6-0 A-2 · 2026-09-05). 해소는
    #   `corp_code_master` **118,535건**을 보는데 그래프 Company 는 **3,451곳**
    #   뿐이다. 없는 key 를 앵커로 세우면 재료가 통째로 0 이 되고 답이 죽는다:
    #
    #       「요즘 반도체 업계 어때?」 → 앵커 요즘(01719318) → 사건 0 → 「확인되지 않았습니다」
    #
    #   「요즘」·「대상」·「미래」·「오늘」·「우리」가 **실제 사명**이라 1.000 으로
    #   정확히 붙는다 — 점수로는 못 가른다. 그런데 **그래프엔 하나도 없다.**
    #
    #   ★**닫힌 낱말 목록으로 막지 않는다.** 이 저장소가 그 방법으로 두 번
    #     실패했다(`normalizer/generic_names.py` 머리말) — 놓치고, 실명을 친다.
    #     품사도 못 가른다(실측: 요즘·대상·미래가 삼성전자와 달리 NNG 이지만,
    #     NNP 를 요구하면 그래프 기업 3,451곳 중 **1,308곳(37.9%)** 이 죽는다.
    #     `3m`·`amd`·`arm`·`bmw` 처럼 Kiwi 가 고유명사로 안 읽는 이름들이다).
    #
    #   ★대신 **이미 있는 불변식**을 쓴다 — `names_by_keys()` 가 「존재 확인을
    #     겸한다」고 적어 둔 그것이다. 2단(`find_by_names`)은 이미 그래프를 보고
    #     있었다. **1단만 안 보고 있었던 것**이 이 결함의 전부다.
    #
    #   ★비용: 인덱스 조회 **한 번 6.5ms**(실측). 종단 15초의 0.04% 다.
    #     「해소에 성공하면 그래프를 건드리지 않는다」는 전 계약을 이만큼
    #     내주고 죽은 답 한 갈래를 없앤다.
    if resolved_entities:
        best = _primary(resolved_entities)
        if company_service.names_by_keys([best.corp_code]):
            return _query(best.corp_code, best.corp_name, "corp_code",
                          workspace_names, context_names,
                          extra=_second_anchor(tokens, best.corp_name, best.corp_code))
        # ★떨어뜨리지 않고 **아래로 흘린다.** 2단이 이름으로 다시 찾고, 그래도
        #   없으면 ①a·④ 가 「못 찾았다」와 「대상이 없다」를 가른다.
        log.info("anchor.not_in_graph key=%s name=%r — 해소는 됐으나 그래프에 없다",
                 best.corp_code, best.corp_name)

    # ── ①b 2단 — norm_name fallback (설계서 §16-1 의 식별 우선순위) ──────
    # ★`ctx_hit` 을 `ws_hit` 과 **같은 자리에** 넣는다. 둘 다 「질문에 이름이
    #   있는데 ② Search 가 못 해소했다」는 같은 상황이고, 여기서 갈라야 할
    #   이유가 없다 — 찾으면 `query` 다. **질문이 지목한 것**이기 때문이다.
    candidates = ([ws_hit] if ws_hit else []) + \
                 ([ctx_hit] if ctx_hit else []) + tokens
    found = company_service.find_by_names(candidates)
    if found is not None:
        return _query(found["key"], found["name"], "norm_name",
                      workspace_names, context_names,
                      extra=_second_anchor(tokens, found["name"], found["key"]))

    # ── ①b 3단 — ★비-Company 앵커 (§6-0 A-8 · 2026-09-10) ───────────────
    #
    # ★**Company 를 먼저 본 뒤에만 온다.** 순서가 계약이다 — 「국민연금」은
    #   Organization(엣지 3)이면서 Company 국민연금공단(엣지 34)이 따로 있어
    #   (A-10), 여기가 앞이면 재료가 얇은 쪽으로 붙는다.
    #
    # ★**전에는 ①a 가 이것을 떨어뜨렸다.** `non_company_labels()` 가 「기업이 아닌
    #   것을 기업으로 오인하지 않는다」며 Person·Product 토큰을 빼서, 「이재용 관련
    #   최근 이슈가 뭐야?」가 `anchorless` 로 가 **전역 사건 목록**으로 답했다.
    #   물은 것과 다른 대상으로 답하는데 응답이 그 사실을 말하지도 못했다(A-6 과
    #   같은 종류다). 이제 **Person 으로 제대로 부르므로** 그 오인이 성립하지 않는다.
    #
    # ★재료는 이 노드 자신이 아니라 **1홉 안의 Company** 다 — `material_companies()`
    #   의 Path B 갈래가 정한다. 실측(2026-09-06): 앵커→Company→Event 도달률이
    #   Person 76.0% · Organization 78.0% · Product 74.3% 로 Company 81.3% 와 거의 같다.
    #
    # ★`Event` 는 여기 안 온다(`_ANCHOR_LABELS`). 아래 ①a 의 제외는 **그대로 남는다** —
    #   Event 이름과 겹치는 토큰을 거르는 것이 여전히 그 함수의 일이다.
    non_company = company_service.find_non_company_by_names(tokens)
    if non_company is not None:
        return _query(non_company["key"], non_company["name"], "non_company",
                      workspace_names, context_names,
                      label=NodeLabel(non_company["label"]))

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

    # ── ③ 보고 있는 기업 — ★**UI 가 대상을 알고 있을 때만** ─────────────
    #
    #   ★상세 페이지에서 「이 회사 노조 리스크 어때?」를 물으면 답은 **그
    #     회사**다. 화면이 무엇을 보여주는지가 곧 질문의 대상이다.
    #
    #   ★**워크스페이스는 여기 오지 않는다.** 담아 둔 것은 「지금 보고 있는
    #     것」이 아니라 「관심 영역」이다 — 대상이 아니라 랭킹 문맥이다.
    if context_names:
        anchors = [Anchor(key=key, name=name, source=AnchorSource.CONTEXT)
                   for key, name in context_names.items()]
        log.info("anchor.source=context anchors=%d", len(anchors))
        return AnchorDecision(source=AnchorSource.CONTEXT, anchors=anchors,
                              workspace_names=workspace_names,
                              context_names=context_names)

    # ── ④ 앵커 없음 — ★**워크스페이스로 갈아타지 않는다** ────────────────
    #
    #   ★여기가 이번 개정의 핵심이다(최종 설계 §17-3·§19-4). 전에는 워크스페이스
    #     기업을 앵커로 **승격**시켜 `source=workspace` 를 냈다. 그러면
    #     「최근 주요 투자 이벤트가 뭐야?」가 「삼성전자·SK하이닉스의 투자
    #     이벤트」로 조용히 바뀐다 — **질문이 묻지 않은 것을 대상으로 답하기**다.
    #
    #   ★**실패가 아니다.** 앵커 없는 질의는 정상이고, 재료는 Global Search 의
    #     히트가 댄다(`retrieve_service.hits_reflect_the_anchor`). 워크스페이스는
    #     그 결과의 **순서**에만 관여한다(`result_ranker.workspace_priority`).
    #
    #   ★`workspace_names` 는 **그대로 싣는다.** 앵커가 아니라 **소속 표기**로
    #     쓰인다(설계서 §12, `llm/prompt.membership`) — 「이 기업이 담아 둔
    #     것인가」를 답변이 밝힐 수 있어야 한다.
    log.info("anchor.source=anchorless workspace=%d — 워크스페이스를 앵커로 쓰지 않는다",
             len(workspace_names))
    return AnchorDecision(source=AnchorSource.ANCHORLESS,
                          workspace_names=workspace_names,
                          context_names=context_names)


def _query(key: str, name: str, via: str,
           workspace_names: dict[str, str],
           context_names: Optional[dict[str, str]] = None,
           *, extra: Optional[dict] = None,
           label: NodeLabel = NodeLabel.Company) -> AnchorDecision:
    """★`extra` 는 질문이 함께 지목한 **둘째 기업**이다(§6-0 A-7). 1차와 같은
    `source=query` 다 — 둘 다 질문이 지목한 것이라 가를 이유가 없다.

    ★`label` 은 **1차 앵커의 것만** 받는다(§6-0 A-8). `extra` 는 `_second_anchor`
      가 Company 에서만 찾으므로 늘 Company 다 — 비-Company 2차 앵커는 재 본 적이
      없어 열지 않는다."""
    anchors = [Anchor(key=key, name=name, source=AnchorSource.QUERY, label=label)]
    if extra is not None:
        anchors.append(Anchor(key=extra["key"], name=extra["name"],
                              source=AnchorSource.QUERY))
    log.info("anchor.source=query key=%s name=%r via=%s anchors=%d",
             key, name, via, len(anchors))
    return AnchorDecision(
        source=AnchorSource.QUERY, anchors=anchors,
        workspace_names=workspace_names,
        context_names=context_names or {})
