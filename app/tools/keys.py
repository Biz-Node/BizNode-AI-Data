"""key → `norm_name` 해소 — ★**조용한 0건을 실패로 바꾼다.**

왜 한 곳인가 (2026-09-04)

`graph_tools._resolve()` 와 `search_tools._key_forms()` 가 앞부분 여섯 줄을
글자까지 같게 들고 있었다. 줄 수보다 나쁜 건 **예외 문구가 두 벌**이라는
점이다 — 그 문구는 Agent 가 읽고 다음 도구 호출을 정하는 값이라(원칙 ④),
한쪽만 고치면 같은 실패가 두 얼굴로 나간다.

★왜 해소가 필요한가 — `company_service` 의 조회는 `corp_code` 든 `norm_name`
  이든 받지만(`WHERE c.corp_code = $k OR c.norm_name = $k`), **틀린 값을 주면
  예외가 아니라 조용히 0건**이다. 그러면 「이 기업에 사건이 없다」와 구별이
  안 된다. 여기서 한 번 확인하고 넘긴다.

★`norm_name` 으로 바꿔 넘겨도 **재료가 안 바뀐다**(실측 2026-08-28):
  Company 3,432곳의 `norm_name` 은 **전부 유일**하고(겹치는 이름 0종),
  `corp_code` 와 같은 문자열인 `norm_name` 도 0건이다. 표본 400곳에서
  `corp_code` 로 부를 때와 `norm_name` 으로 부를 때 매칭 노드 수가 갈리는
  기업이 0곳이었다. 겹치는 이름이 생기면 이 전제가 깨지므로
  `tests/tools/test_graph_tools.py` 가 그 불변식을 묶어 둔다.
"""

from __future__ import annotations

from typing import Sequence

from app.services import company_service
from app.tools import scope
from app.tools.errors import KeyNotResolved


def resolved(keys: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """(범위를 통과한 key 를 **정본으로** 순서대로, 정본 key → `norm_name`).

    빈 입력이면 `([], {})`. 범위 밖이면 `OutOfScopeKey`, 그래프에 없으면
    `KeyNotResolved` — **둘 다 0건으로 넘어가지 않는다.**

    ★**정본으로 되짚는 이유** (2026-09-05 · 현황서 §6-0 A-5). 조회는 `corp_code`
      와 `norm_name` 을 **둘 다** 매치하지만 돌려주는 key 는 `corp_code` 를 우선한
      **정본 하나**다. 그래서 물어본 key 로 그대로 `in found` 를 보면, 그래프가
      **찾았는데도** 「못 찾은 key」가 나갔다:

          resolved(['00126380'])  → OK
          resolved(['삼성전자'])   → ★KeyNotResolved  (같은 기업인데)

      `corp_code` 가 없는 기업은 정본이 곧 `norm_name` 이라 안 걸렸고, **있는
      기업을 이름으로 부를 때만** 걸렸다. 그 문구는 Agent 가 읽고 다음 호출을
      정하는 값이라(원칙 ④) 틀린 원인을 말하면 안 된다.

      ★돌려주는 key 를 정본으로 맞춰야 **부르는 쪽의 `found[k]` 가 성립한다** —
        세 도구가 전부 `for k in wanted: found[k]` 로 짝지어 읽는다.
    """
    wanted = scope.check(keys)          # ① 범위 밖이면 여기서 `OutOfScopeKey`
    if not wanted:
        return [], {}
    found = company_service.norm_names_by_keys(wanted)
    by_norm_name = {norm: key for key, norm in found.items()}

    canonical: list[str] = []
    missing: list[str] = []
    for key in wanted:
        hit = key if key in found else by_norm_name.get(key)
        if hit is None:
            missing.append(key)
        elif hit not in canonical:      # 같은 기업을 두 형태로 불러도 한 번만
            canonical.append(hit)
    if missing:
        # ★0건으로 넘어가지 않는다. 「해소됐다 ≠ 그래프에 있다」다.
        raise KeyNotResolved(f"그래프에서 Company 를 못 찾은 key: {missing}")
    return canonical, found
