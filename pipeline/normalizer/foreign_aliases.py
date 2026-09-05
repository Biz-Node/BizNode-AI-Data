"""표기 통일 — 별칭 사전을 `company_aliases` 표에서 읽는다.

해외 기업은 DART에 없어 **norm_name이 곧 노드 식별자**다. 표기가 갈리면 같은
회사가 별개 노드가 되고 그 회사를 지나는 경로가 끊긴다.
실측(2026-07-28): `Netlist`(연결 1)와 `넷리스트`(연결 9)가 별개 노드였다.

★2026-08-14 — 손 목록 75개를 표로 옮기고 코드에서 지웠다.
  설계와 출처 우선순위는 `normalizer/company_registry.py` 참고.

  대표형 선택 원칙은 표의 `note`에 남아 있다:
    · 한국 언론이 한글로 쓰는 것 → 한글 (엔비디아·마이크론·인텔·퀄컴)
    · 약어로만 통용되는 것       → 영문 대문자 (TSMC·ASML·CXMT)
    · 국내 기업의 영문 표기      → 한글 정식명 (SK Hynix → SK하이닉스)

★DB를 못 읽어도 죽지 않는다. `normalize_company_name`은 파이프라인 어디서나
  불리므로 여기서 예외가 나면 전부 멈춘다. 별칭이 없으면 표기가 갈릴 뿐이고
  그건 나중에 고칠 수 있다.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    try:
        from app.core.database import postgres_connection
        from pipeline.normalizer.company_registry import load_aliases
        with postgres_connection() as conn:
            return load_aliases(conn)
    except Exception:
        return {}


def apply_alias(norm_key: str) -> str:
    """정규화키를 대표형으로. 표에 없으면 그대로 둔다."""
    return _aliases().get(norm_key, norm_key)

