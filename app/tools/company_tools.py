"""기업 사실 도구 — **PostgreSQL 에 저장된 것들.**

    Agent(2차) → Tool(여기) → company_service → PostgreSQL

`graph_tools` 와 같은 4원칙을 따른다 — key 만 받고 · 표기가 끝난 DTO 를 주고 ·
`limit` 을 인자로 받지 않고 · 빈 결과와 실패를 구별한다.

★**세 도구가 서로 다른 성격의 값을 준다. 섞으면 안 된다.**

    get_business_overview   사업보고서 **원문** — 우리가 요약하지 않은 문장
    get_filings             공시 **목록** — 제목까지다. 본문이 아니다
    get_market              **계산값** — 저장된 사실이 아니라 뷰가 나눈 값

★그래서 `get_market` 에는 `evidence_id` 를 두지 않는다. 시총·PER·PBR·PSR 은
  `market_metrics` 뷰가 「종가 × 상장주식수」를 순이익·자본·매출로 나눈 값이다.
  계산값에 근거 id 를 발급하면 원본이 갱신될 때 id 가 가리키는 값과 실제 값이
  어긋난다 — `ratio_change` 를 제거했던 것과 같은 실수다(1,306건 중 15건 불일치).
  대신 **계산 좌표**(`trade_date`·`fin_year`·`fs_div`)를 담아 되짚게 한다.
"""

from __future__ import annotations

from typing import Optional

from app.core.trace import trace_logger
from app.services import company_service
from app.tools import keys as keys_module
from app.tools.dto import (PER_NOTE_LOSS, PER_NOTE_NO_FINANCIALS,
                           BusinessOverviewDTO, FilingDTO, MarketDTO)
from app.tools.errors import KeyNotResolved

log = trace_logger(__name__)

# ★상한은 **내부 상수**다(원칙 ③). `company_service.filings_of` 기본값을 그대로 쓴다.
_MAX_FILINGS = 20


def _one(key: str) -> str:
    """key 하나를 범위 검사하고 **그래프에 있는지** 확인한다.

    ★`company_service` 의 조회들은 못 찾은 key 에 예외가 아니라 `None`·`[]` 를
      준다. 그러면 「그 기업은 자료가 없다」와 「key 가 틀렸다」가 구별되지
      않는다(원칙 ④). 여기서 한 번 가른다.
    """
    wanted, _ = keys_module.resolved([key])
    if not wanted:
        raise KeyNotResolved("key 가 비어 있다")
    return wanted[0]


# ══════════════════════════════════════════════════════════════════
#  사업의 내용 — ★**참고 맥락이지 인용 근거가 아니다**
# ══════════════════════════════════════════════════════════════════


def get_business_overview(key: str, year: Optional[int] = None
                          ) -> Optional[BusinessOverviewDTO]:
    """사업보고서 「사업의 내용」 원문. 없으면 `None`.

    ★**citation 대상이 아니다.** 이 텍스트는 PostgreSQL 에만 있고 ChromaDB
      `evidence` 컬렉션에 청크가 **없다**(실측 2026-08-28: `vector_chunks` 는
      `evidence` 10,510 · `company` 2,432 두 종뿐). 근거 본문은
      `relation_service.evidence_for_ids()` 한 경로로만 오므로, 여기에 억지로
      `evidence_id` 를 발급해도 그 경로가 `missing:True` 로 내보내
      **「근거 없음」으로 표시된다.**

      게다가 `overview_text` 는 목차 절 전문이다(실측: 64행 · 평균 2,294자 ·
      최대 16,623자). 인용하려면 청킹·적재가 선행돼야 하고 그건 별도
      파이프라인 작업이다 — 이 단계 범위가 아니다.

      → **참고 맥락으로만 쓴다.** claims 에 넣지 말라는 지시는 시스템
        프롬프트가 따로 한다(작업 B).

    Args:
        key: `corp_code` 이거나 `norm_name`.
        year: 사업연도. 주지 않으면 **가장 최근 연도**.
    """
    resolved = _one(key)
    row = company_service.business_overview_of(resolved, year=year)
    if row is None:
        return None                        # ④ 입력은 맞고 **정말로 없다**
    return BusinessOverviewDTO(**row)      # ② 표기가 끝난 DTO


# ══════════════════════════════════════════════════════════════════
#  공시 목록
# ══════════════════════════════════════════════════════════════════


def get_filings(key: str) -> list[FilingDTO]:
    """이 기업의 공시 목록 — **최근 것부터.** 없으면 `[]`.

    ★**제목까지다.** 공시 본문은 여기 없다. 인용할 문장이 필요하면
      `search_dart` 가 근거 청크를 준다.
    """
    resolved = _one(key)
    rows = company_service.filings_of(resolved, limit=_MAX_FILINGS)   # ③ 내부 상수
    log.info("get_filings key=%s -> %d", resolved, len(rows))
    return [FilingDTO(**row) for row in rows]


# ══════════════════════════════════════════════════════════════════
#  시장 — ★계산값이다. 근거 id 를 붙이지 않는다
# ══════════════════════════════════════════════════════════════════


def _per_note(per: Optional[float], fin_year: Optional[int]) -> Optional[str]:
    """★`per: null` 이 **왜** null 인지를 말한다.

    그냥 `null` 이면 LLM 이 「정보 없음」으로 읽는데, 이유가 둘이라 뭉뚱그리면
    안 된다 — 적자(순이익 ≤ 0)와 재무 미수집은 전혀 다른 사실이다.
    """
    if per is not None:
        return None
    return PER_NOTE_NO_FINANCIALS if fin_year is None else PER_NOTE_LOSS


def get_market(key: str) -> Optional[MarketDTO]:
    """시세와 지표. **상장사에만 있다.** 없으면 `None`.

    ★`None` 인 경우가 셋이다 — 비상장이거나, `corp_code` 가 없거나(해외),
      상장인데 지표를 못 만든 것(시세 미수집·주식수 불신)이다. 셋 다
      「이 기업의 시세를 줄 수 없다」로 같으므로 도구는 `None` 하나로 답한다.
      **왜 없는지는 `company_service.market_of()` 의 `unavailable_reason`** 에
      있고, 그건 화면이 읽는 값이다.

    ★**`market` 컬럼으로 국내/해외를 가르지 않는다.** DART 가 출처라 해외 시장
      값이 애초에 없다 — 비어 있다고 해외인 것이 아니다.
    """
    resolved = _one(key)
    corp = company_service.corp_codes_by_keys([resolved])
    if not corp:
        # 그래프에는 있는데 `corp_code` 가 없다 — 해외·비상장. 시세가 원리적으로 없다
        log.info("get_market key=%s -> corp_code 없음", resolved)
        return None

    got = company_service.market_of(resolved)
    latest = got.get("latest")
    if not latest:
        log.info("get_market key=%s -> 지표 없음 (%s)",
                 resolved, got.get("unavailable_reason"))
        return None

    fin_year = latest.get("fin_year")
    per = latest.get("per")
    return MarketDTO(                       # ★`evidence_id` 없음 — 계산 좌표만
        corp_code=next(iter(corp.values())),
        trade_date=latest["trade_date"],
        fin_year=fin_year,
        fs_div=latest.get("fs_div") or None,
        close_price=latest.get("close_price"),
        market_cap=latest.get("market_cap"),
        per=per, pbr=latest.get("pbr"), psr=latest.get("psr"),
        per_note=_per_note(per, fin_year),
    )
