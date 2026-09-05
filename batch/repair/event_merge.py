"""같은 사건이 이름만 달리해 갈린 것을 **모델이 판정해** 합치고, 국면을 남긴다.

왜 필요한가 (2026-08-14)

Event 노드가 있는 이유는 **전파 구조**다.

    「청주 공장 화재」 ─IMPACTS→ SK하이닉스 · 한미반도체 · 원익IPS

한 사건이 어디까지 번졌는지가 한 노드에 모여야 「이 화재가 어디까지 영향을
주나」에 답할 수 있다. 그런데 LLM은 기사마다 이름을 다르게 붙인다:

    「청주 SK하이닉스 화재」 「청주 공장 화재」 「청주4캠퍼스 화재」
    → 노드 3개 · 영향 기업이 셋으로 나뉨 → 답이 틀린다

★기존 `importer/event_er.py`는 **유형어 목록**으로 이 일을 한다. 그런데
  실측(2026-08-14): Event 1,352개 중 **631개(46%)가 유형어 목록에 없어
  후보에도 못 오른다.**

      「메모리 반도체 생산 확대」 「D램 사후정산제 도입」 「테일러 팹」
      「첨단 패키징 공장 건설」 「HBM4 생산라인 전환 일부 연기」

  사건 어휘는 열려 있어서 목록으로는 못 따라간다. 파일 주석에 두 번 실패한
  기록이 남아 있다 — 목록을 늘리면 연쇄 병합, 줄이면 46% 누락.

그래서 **구조로 후보를 만들고 모델이 판정한다.**

    1단  R1 같은 기업에 붙음 + 이름 **어근** 1개 이상 공유          무료
    2단  모델이 「같은 사건인가」 판정                            0.25원/쌍
    3단  합치되 사라지는 이름을 `timeline`에 남김

  ★이름 겹침에서 **회사명 토큰을 뺀다**(2026-08-14). R1이 이미 같은 기업임을
    보장하므로 중복이고, 안 빼면 「삼성전자」 하나만 겹쳐도 후보가 되어
    서로 무관한 사건이 전부 올라온다(실측: 208 → 191쌍, 잡음이 크게 줄었다).

  ★★2026-08-15에 후보 규칙을 갈았다. 기존 `timeline` 59건을 정답지(99쌍)로
    놓고 겨뤄 보니 **옛 규칙이 62%를 놓치고 있었다**(`_roots` 주석 참고).
    「같은 연월」 조건도 뺐다 — 사건은 몇 달~몇 년에 걸쳐 전개된다.

`timeline` — 합치되 **국면을 잃지 않는다**

  「파업 예고」→「총파업」→「파업 유보」→「현업 복귀」는 한 사건의 국면이다.
  전에는 병합이 `properties:'discard'`라 사라지는 이름이 버려졌다.

      timeline: [{at:"2026-04", label:"파업 예고", event_id:"..."},
                 {at:"2026-07", label:"총파업 돌입", ...}]

  전파 구조는 한 노드에 모으고, 시점·국면은 배열에 남긴다. 엣지 12종을
  건드리지 않고도 「언제 시작해 언제 끝났나」에 답할 수 있다.

★확신 없으면 합치지 않는다. 서로 다른 사건을 합치면 영향 기업이 뒤섞이고
  `timeline`으로도 되돌릴 수 없다.

★★2026-08-29 — **모델에 이름밖에 안 넘기고 있었다.**

  `_FIND`가 `corps`·`dates`를 가져와 놓고도 프롬프트에 넣는 것은 이름 두 개뿐이라
  모델이 회사도 날짜도 못 봤다. 위의 「시점이 멀어도 같은 사건일 수 있다」는
  판단을 모델에 맡긴 것인데, 정작 판단할 재료를 안 줬다. 결과(실측):

      기업 혼재    「자사주 소각」이 한미반도체·NAVER·삼성전자에 걸렸다  66건
      반복 융합    2022년 파업과 2026년 파업이 한 노드              75건

  셋을 고쳤다.
      ① 주체 기업(`role='subject'`)을 따로 받아 **서로소면 후보에서 뺀다**.
         영향받은 기업(IMPACTS)만 겹치는 것은 같은 기업이 아니다.
      ② **되풀이형**(화재·사망·파업·리콜·제재·실적)이 12개월 넘게 벌어지면
         후보에서 뺀다. 장기전개형(착공→준공·투자→양산·제소→확정)은 그대로 둔다.
      ③ 프롬프트에 `이름 [주체기업 · 연월]`을 넣는다.

  ①②는 모델을 부르기 전에 거르므로 **비용도 줄어든다**. 그리고 이 날 이전의
  캐시 판정은 재료 없이 내린 것이라 기본적으로 **버리고 다시 묻는다**
  (`_CONTEXT_SINCE`).

  이미 섞여 버린 노드를 되찾는 것은 이 파일이 아니라
  `batch/audit/event_merge.py`(찾기) + `batch/repair/event_split.py`(가르기)다.

    python -m batch.repair.event_merge --dry-run
    python -m batch.repair.event_merge
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from app.core.database import neo4j_session, postgres_connection
from pipeline.importer.event_er import _name_tokens
from pipeline.llm import ask_json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_COST = 0.25

_CREATE = """
CREATE TABLE IF NOT EXISTS event_merge_verdicts (
    id_a       TEXT NOT NULL,
    id_b       TEXT NOT NULL,
    verdict    TEXT NOT NULL,      -- same | phase | different
    reason     TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id_a, id_b)
)
"""
# ★이 시각 **이전**의 판정은 이름 두 개만 보고 내린 것이다. 주체 기업도 연월도
#   모델에 안 보여 주던 때라 「자사주 소각」 셋을 same 으로 묶는 판정이 들어
#   있다. 기본적으로 **다시 묻는다**(2,208쌍 ≈ 550원). 굳이 재사용하려면
#   `--use-old-verdicts`.
_CONTEXT_SINCE = "2026-08-29"

_LOAD = """
SELECT id_a, id_b, verdict FROM event_merge_verdicts
WHERE %s OR decided_at >= %s::timestamptz
"""
_SAVE = """
INSERT INTO event_merge_verdicts (id_a, id_b, verdict, reason) VALUES (%s,%s,%s,%s)
ON CONFLICT (id_a, id_b) DO UPDATE SET verdict = EXCLUDED.verdict,
                                       reason = EXCLUDED.reason
"""

_SYSTEM = """당신은 기업 지식그래프에서 **같은 사건이 두 이름으로 갈린 것**을
가려내는 도구입니다.

두 사건은 「같은 기업에 붙어 있고 낱말이 하나라도 겹친다」는 이유로 후보에
올랐을 뿐, 실제로는 다른 사건인 경우가 많습니다.

각 줄은 다음 형식입니다 — **주체 기업과 연월을 반드시 보고 판단하십시오.**

    이름 [주체기업 · 연월]  |  이름 [주체기업 · 연월]

★**주체 기업이 다르면 다른 사건입니다.**
   "자사주 소각 [한미반도체 · 2026-02]" / "자사주 소각 [naver · 2025-09]"
   → 이름이 같아도 각자 자기 자사주를 소각한 별개 사건입니다 → different

★**시점이 멀 때는 사건 성격을 보십시오.** 두 갈래입니다.

   길게 이어지는 하나의 일 — 멀어도 같은 사건입니다 → phase
     "HBM4 생산 투자"(2025-09) / "HBM4 양산 일정 연기"(2026-06)
     "세종 신사옥 착공"(2022-03) / "세종 신사옥 준공"(2025-11)
     투자→양산 · 착공→준공 · 제소→확정 은 한 일의 국면입니다.

   해마다 되풀이되는 일 — 멀면 **다른 사건**입니다 → different
     "임단협 교섭 난항"(2023-07) / "임단협 교섭 난항"(2025-08)
     "중대재해 사망사고"(2024-03) / "중대재해 사망사고"(2026-05)
     파업·사고·리콜·제재·실적은 내년에 **또** 납니다. 1년 넘게 벌어져
     있으면 같은 사건이 이어진 게 아니라 다시 일어난 것입니다.

【same — 같은 사건을 다르게 부른 것】
   "삼성전자 본사 압수수색" / "삼성전자 압수수색"
   "평택캠퍼스 방문" / "평택 반도체 공장 방문"
   "22조8000억원 규모 파운드리 계약" / "22조7648억 원 규모 반도체 위탁생산 계약"
   "반도체 공정 등 제어감시시스템 입찰 담합 적발" / "반도체 공정 제어감시시스템 입찰 담합"

【phase — 한 사건의 다른 국면】 ★같은 사건으로 봅니다
   "파업 예고" / "총파업 돌입" / "파업 유보" / "현업 복귀"
   "세종 신사옥 착공" / "세종 신사옥 준공 지연" / "세종 신사옥 준공"
   → 하나의 일이 시간에 따라 전개된 것입니다. 시작·중간·끝을 나눠 부른 것.

【different — 다른 사건】 ★이쪽이 가장 많습니다
   "삼성전자 지분 매각" / "삼성전자 반도체 적자"
   "HBM4 생산 투자" / "NAND 생산 확대"
   "삼성 파운드리 포럼 2024" / "파운드리 웨이퍼 결함"
   ★같은 회사에서 낱말이 겹친다는 것만으로는 같은 사건이 아닙니다.
   ★유형이 같아도 대상이 다르면 다른 사건입니다
       "즉시연금 소송" / "특허 소송 배상 평결"   → different

【판단이 어려울 때】
   확신이 없으면 **different**로 하세요. 다른 사건을 합치면 영향받은 기업이
   한 노드에 뒤섞여 되돌릴 수 없습니다. 못 합친 건 나중에 다시 볼 수 있습니다.

각 줄 앞의 번호 `n`을 **그대로** 돌려주십시오. 한 줄도 빠뜨리지 마십시오.
reason은 5~20자로 짧게."""

# ★쌍을 **번호**로 주고받는다(2026-08-29). 전에는 모델이 두 이름을 그대로
#   돌려주면 그것으로 쌍을 되찾았는데, 프롬프트에 주체·연월을 넣어 줄이 길어지자
#   모델이 조금씩 다르게 적어 되찾기가 실패했다. 실측: 797쌍을 묻고 **227쌍만**
#   저장됐다 — 570쌍의 답이 조용히 버려졌다(돈은 다 쓰고).
#   번호는 모델이 바꿔 적을 여지가 없다.
_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "verdict": {"type": "string",
                                "enum": ["same", "phase", "different"]},
                    "reason": {"type": "string"},
                },
                "required": ["n", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

# ★`subs`(주체 기업)를 따로 받는다(2026-08-29). 전에는 `corps` 하나만 받아
#   「하나라도 겹치면 같은 기업」으로 봤는데, 영향받은 기업(IMPACTS)만 겹쳐도
#   후보가 됐다. 「자사주 소각」이 한미반도체·NAVER·삼성전자에 걸린 원인이다.
#   ※`observed_at`은 그래프에 없는 속성이라 뺐다(수집만 하고 늘 비어 있었다).
_FIND = """
MATCH (e:Event)
OPTIONAL MATCH (e)-[r]-(c:Company)
WITH e, collect(DISTINCT c.norm_name) AS corps,
     collect(DISTINCT CASE WHEN r.role = 'subject' THEN c.norm_name END) AS subs_raw,
     collect(DISTINCT r.occurred_at) AS dates
RETURN e.name AS name, e.event_id AS id, corps, dates,
       [x IN subs_raw WHERE x IS NOT NULL] AS subs,
       size([(e)-[]-() | 1]) AS deg
"""

# 합치면서 국면을 남긴다. `timeline`은 문자열 배열로 둔다 —
# Neo4j 속성은 중첩 map을 못 담아서 "연월|이름|event_id" 형태로 적는다.
_TIMELINE = """
MATCH (keep:Event {event_id:$keep})
SET keep.timeline = coalesce(keep.timeline, []) + $entries
"""
_MERGE = """
MATCH (a:Event {event_id:$keep}), (b:Event {event_id:$drop})
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.event_id AS id
"""


def _period(dates) -> str:
    v = sorted(str(d)[:7] for d in (dates or []) if d and len(str(d)) >= 7)
    return v[0] if v else ""


# ★**해를 넘겨 되풀이되는** 사건 유형(2026-08-29). `event_er.UNIQUE_TYPE_WORDS`와
#   축이 다르다 — 저쪽은 「같은 달에 두 번 나기 어렵다」이고, 이쪽은 「해마다
#   또 난다」다. 화재는 같은 달에 두 번 안 나지만 내년에는 또 난다.
#
#   실측(2026-08-29): 이 구분이 없어서 2024년 사망사고와 2026년 사망사고가,
#   2022년 파업과 2026년 파업이 한 노드가 됐다(「삼성전자노조 파업」 51개월,
#   「파업 리스크」 50개월). 「최근 리스크」 검색에서 4년 전 사고가 최근 것으로
#   딸려 나온다.
_RECURRENT_WORDS = (
    "화재", "폭발", "붕괴", "누출", "정전", "침수",
    "사망", "중대재해", "산업재해", "안전사고",
    "파업", "노동쟁의", "쟁의", "직장폐쇄", "임단협", "단체협약", "교섭",
    "리콜", "결함", "불량",
    "과징금", "제재", "압수수색", "세무조사", "행정처분", "담합",
    "실적", "적자", "어닝쇼크", "배당", "자사주",
)

# 되풀이형이 이보다 벌어지면 **다른 사건으로 본다**. 1년을 넘기면 「작년 그
# 사건」이 아니라 「올해 또 난 사건」이다.
_RECURRENT_MONTH_CAP = 12


def _is_recurrent(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "")
    return any(w in compact for w in _RECURRENT_WORDS)


def _mk(p: str):
    """YYYY-MM → 월 일련번호."""
    try:
        return int(p[:4]) * 12 + int(p[5:7])
    except (ValueError, IndexError, TypeError):
        return None


def _months_apart(p1: str, p2: str) -> int:
    """두 연월(YYYY-MM)이 몇 개월 떨어져 있나. 하나라도 없으면 크게 본다."""
    try:
        return abs((int(p1[:4]) * 12 + int(p1[5:7]))
                   - (int(p2[:4]) * 12 + int(p2[5:7])))
    except (ValueError, IndexError, TypeError):
        return 99


def _label(e: dict) -> str:
    """모델에 보여 줄 한 줄 — 이름만으로는 가를 수 없다."""
    who = ", ".join((e.get("subs") or e.get("corps") or [])[:3]) or "-"
    return f"{e['name']} [{who} · {e.get('period') or '?'}]"


def _blocked(a: dict, b: dict) -> str:
    """구조만 보고 **합치면 안 되는** 쌍을 걸러 낸다. 모델을 부르기 전에.

    ★2026-08-29에 넣었다. 그전에는 이 두 검사가 **어디에도 없었다** — 후보
      규칙은 시간을 안 봤고(일부러 뺐다), 모델에는 이름 두 개만 넘겼다.
      「날짜 판단은 모델이 하겠지」였는데 정작 날짜를 안 보여 줬다.

    걸러 낸 쌍은 모델에 묻지 않으므로 **비용도 줄어든다.**
    """
    sa, sb = set(a.get("subs") or []), set(b.get("subs") or [])
    if sa and sb and not (sa & sb):
        return "주체기업 서로소"

    # ★**합친 뒤의 폭**을 본다(2026-08-29). 처음엔 두 노드의 가장 이른 달끼리
    #   비교했는데, 이미 폭이 넓은 노드는 옛 사건과 gap이 0으로 보여 계속
    #   빨아들였다 — 「삼성전자 노조 파업」이 2022년 것을 흡수해 50개월이 됐다.
    #   판단해야 할 것은 「둘이 얼마나 떨어졌나」가 아니라 「합치면 얼마나
    #   벌어지나」다.
    months = (a.get("months") or []) + (b.get("months") or [])
    span = (max(months) - min(months)) if months else 0
    if span >= _RECURRENT_MONTH_CAP and (_is_recurrent(a["name"])
                                         or _is_recurrent(b["name"])):
        return f"되풀이형 {span}개월"
    return ""


def _roots(name: str) -> set[str]:
    """비교용 **어근**. 조사·접미가 붙어도 같은 낱말로 보이게 한다.

    ★왜 토큰 그대로 쓰면 안 되나(2026-08-15 실측). 기존 `timeline` 59건을
      정답지(99쌍)로 놓고 규칙을 겨뤄 봤다:

          토큰 겹침 ≥30% (구 규칙)    38/99   38%   ← 62%를 놓치고 있었다
          토큰 1개 공유               85/99   86%
          어근 1개 공유             97/99   98%

      놓친 쌍이 왜 안 걸렸는지가 원인을 그대로 보여 준다:
          「442억원 규모 HBM4용 TC 본더 수주」 ↔ 「HBM4」
            → 「HBM4용」과 「HBM4」가 다른 토큰이라 겹침이 0이었다
    """
    out: set[str] = set()
    for t in _name_tokens(name):
        out.add(t)
        m = re.match(r"^([0-9A-Za-z]+)", t)          # HBM4용 → HBM4
        if m and len(m.group(1)) > 1:
            out.add(m.group(1))
        if len(t) > 3 and re.match(r"^[가-힣]+$", t):  # 한글은 앞 3글자
            out.add(t[:3])
    return out


def _candidates(evs: list[dict]) -> list[tuple[dict, dict]]:
    """R1(같은 기업) + 어근 1개 이상 공유. 전부 구조 조건이라 무료.

    ★**시간 제한을 두지 않는다**(2026-08-15). 전에는 「같은 연월」을 요구했는데,
      사건은 몇 달~몇 년에 걸쳐 전개된다:
          「HBM4 생산 투자」(2025-09) → 「HBM4 양산 일정 연기」(2026-06)
      어근 규칙만으로 후보가 36,264쌍 → 2,433쌍(93% 감축)이라 시간으로 더 조일
      이유가 없다. 판정은 어차피 모델이 한다.

    ★회사명 낱말은 뺀다. R1이 이미 같은 기업임을 보장하므로 중복이고, 안 빼면
      「삼성전자」 하나만 겹쳐도 후보가 되어 무관한 사건이 전부 올라온다.
    """
    corp_tokens: set[str] = set()
    for e in evs:
        for c in e["corps"]:
            if c:
                corp_tokens |= _roots(c)

    by_corp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        e["period"] = _period(e["dates"])
        e["months"] = sorted({m for m in (_mk(str(d)[:7]) for d in (e["dates"] or []))
                              if m})
        e["toks"] = _roots(e["name"]) - corp_tokens
        for c in e["corps"]:
            if c:
                by_corp[c].append(e)

    seen: set[tuple[str, str]] = set()
    out: list[tuple[dict, dict]] = []
    blocked: dict[str, int] = defaultdict(int)
    for group in by_corp.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["id"] == b["id"]:
                    continue
                key = tuple(sorted((a["id"], b["id"])))
                if key in seen or not (a["toks"] & b["toks"]):
                    continue
                seen.add(key)
                why = _blocked(a, b)
                if why:
                    blocked[why.split()[0]] += 1
                    continue
                out.append((a, b) if a["deg"] >= b["deg"] else (b, a))
    if blocked:
        print("  구조로 걸러 낸 쌍: "
              + " · ".join(f"{k} {v}쌍" for k, v in sorted(blocked.items())))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="판정할 쌍 수 상한(비용 통제)")
    ap.add_argument("--use-old-verdicts", action="store_true",
                    help=f"{_CONTEXT_SINCE} 이전의 이름만 보고 내린 판정도 재사용")
    # ★`pipeline.llm`의 기본값은 gpt-4o-mini다. 그대로 썼더니 「테슬라와의
    #   대규모 공급 계약」과 「대규모 충당금 설정」을 same 으로, 「삼성전자의
    #   레인보우로보틱스 인수」와 「플랙트 인수」를 한 사건으로 판정했다.
    #   합치기는 되돌릴 수 없으므로(`mergeNodes`는 노드를 없앤다) 감사
    #   (`batch/audit/event_merge.py`)와 같은 등급을 쓴다.
    ap.add_argument("--model", default="gpt-4o")
    args = ap.parse_args()

    with neo4j_session() as s:
        evs = [dict(r) for r in s.run(_FIND)]
    pairs = _candidates(evs)

    # ★일부러 가른 쌍은 다시 합치지 않는다(2026-08-29). `event_split`이 근거를
    #   보고 갈라 놓은 것을 여기서 되붙이면 작업이 원점으로 돌아간다. 구조
    #   검사만으로는 8쌍이 다시 후보로 올라왔다 — 같은 기업의 1년 이내
    #   사건이라 `_blocked`가 잡을 수 없는 것들이다.
    with postgres_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('event_splits')")
        if cur.fetchone()[0]:
            cur.execute("SELECT orig_id, new_id FROM event_splits WHERE undone_at IS NULL")
            split = {tuple(sorted(r)) for r in cur.fetchall()}
            before = len(pairs)
            pairs = [p for p in pairs
                     if tuple(sorted((p[0]["id"], p[1]["id"]))) not in split]
            if before - len(pairs):
                print(f"  일부러 가른 쌍이라 뺀 것: {before - len(pairs)}쌍")

    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(_LOAD, (args.use_old_verdicts, _CONTEXT_SINCE))
            cached = {(a, b): v for a, b, v in cur.fetchall()}

        todo = [p for p in pairs if (p[0]["id"], p[1]["id"]) not in cached]
        if args.limit:
            todo = todo[:args.limit]

        print("=" * 72)
        print(f"  이름만 다른 같은 사건 찾기 — Event {len(evs):,}개에서 후보 {len(pairs)}쌍")
        print(f"  이미 판정 {len(cached)}쌍 · 이번에 물을 것 {len(todo)}쌍 "
              f"(약 {len(todo) * _COST:.0f}원)")
        print("=" * 72)

        if todo and not args.dry_run:
            for i in range(0, len(todo), 20):
                chunk = todo[i:i + 20]
                # ★이름만 넘기던 것을 고쳤다(2026-08-29). `corps`·`dates`를 이미
                #   조회해 놓고도 모델에는 이름 두 개만 주고 있었다 — 회사도
                #   날짜도 안 보여 주니 모델이 가를 방법이 없었다.
                body = "\n".join(f"{n}. {_label(a)} | {_label(b)}"
                                 for n, (a, b) in enumerate(chunk, 1))
                got = ask_json(_SYSTEM, body, schema=_SCHEMA, model=args.model,
                               name="event_merge", fallback={"items": []})
                answered = 0
                with conn.cursor() as cur:
                    for it in got.get("items", []):
                        n = it.get("n", 0)
                        if not 1 <= n <= len(chunk):
                            continue
                        a, b = chunk[n - 1]
                        ia, ib = a["id"], b["id"]
                        cached[(ia, ib)] = it["verdict"]
                        answered += 1
                        cur.execute(_SAVE, (ia, ib, it["verdict"],
                                            (it.get("reason") or "")[:60]))
                conn.commit()
                # ★답이 빈 쌍이 있으면 **말한다.** 전에는 조용히 버려서 570쌍이
                #   사라진 줄도 몰랐다(비용은 다 쓰고 다음 실행에 또 묻는다).
                lost = len(chunk) - answered
                print(f"     … {min(i + 20, len(todo))}/{len(todo)}"
                      + (f"  ⚠ 답이 없는 쌍 {lost}개" if lost else ""))

        merge = [(a, b, cached.get((a["id"], b["id"])))
                 for a, b in pairs
                 if cached.get((a["id"], b["id"])) in ("same", "phase")]
        diff = len(pairs) - len(merge)
        print(f"\n  판정: 합칠 것 {len(merge)}쌍 "
              f"(같은 사건 {sum(1 for *_, v in merge if v == 'same')} · "
              f"국면 {sum(1 for *_, v in merge if v == 'phase')}) "
              f"· 다른 사건 {diff}쌍")
        for a, b, v in merge[:15]:
            tag = "국면" if v == "phase" else "동일"
            print(f"     [{tag}] {a['name'][:26]:<28}(연결 {a['deg']:>2})  ←  "
                  f"{b['name'][:26]}")

        if args.dry_run:
            print("\n[dry-run] 합치지 않았습니다.")
            return 0
        if not merge:
            print("\n· 합칠 것이 없습니다.")
            return 0

        done = 0
        with neo4j_session() as s:
            for a, b, verdict in merge:
                entry = f"{b['period'] or '?'}|{b['name']}|{b['id']}"
                s.run(_TIMELINE, keep=a["id"], entries=[entry])
                s.run(_MERGE, keep=a["id"], drop=b["id"])
                done += 1
        print(f"\n✅ {done}쌍 병합 · 사라진 이름은 `timeline`에 "
              f"「연월|이름|id」로 보관")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
