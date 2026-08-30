"""Event 개체해소(ER) — 한 사건이 이름만 달리해 갈린 것을 병합.

`event_id`를 이름+연월 해시로 바꿔 **같은 이름**은 이미 한 노드로 모인다. 그런데
LLM은 기사마다 다르게 이름 붙인다 — 같은 청주 화재를 두고:
    「청주 SK하이닉스 화재」 「청주 공장 화재」 「청주4캠퍼스 화재」
    「청주 SK하이닉스 화재·화학물질 누출」
이름이 다르므로 해시도 달라 4개 노드가 된다. 사건이 갈리면 그 사건이 어느 기업까지
번졌는지(IMPACTS)가 한곳에 모이지 않아 리스크 추론이 조각난다.

병합 규칙 — 셋을 **모두** 만족할 때만:
  R1. 연결된 기업이 겹친다        (같은 회사에서 일어난 일)
  R2. 발생 연월이 같다            (우시 2013 화재 ≠ 청주 2026 화재)
  R3. 사건 유형어가 같다          (화재 ≠ 누출 ≠ 파업)

R3에서 **첫 유형어**만 본다. 「화재·화학물질 누출」처럼 복합어가 다리를 놓아
화재 그룹과 누출 그룹이 통째로 붙는 것(연쇄 병합)을 막기 위함이다.

★2026-08-29 — R1의 「연결된 기업이 겹친다」가 **너무 넓었다.**

  `companies`에는 사건이 일어난 기업(HAS_EVENT·subject)과 **영향받은** 기업
  (IMPACTS)이 섞여 있다. 영향 기업만 겹쳐도 같은 기업으로 봤다. 더 나쁜 것은
  R0(이름+연월이 같으면 무조건 병합)에 **기업 조건이 아예 없었다**는 점이다.

      「자사주 소각」 2026-02  한미반도체 · NAVER · 삼성전자  → 한 노드
      (실측 2026-08-29: 주체가 둘 이상인 Event 66건)

  그래서 `subjects`를 따로 받아 `_subject_conflict()`로 **거부만 한다.** 후보를
  묶는 열쇠는 여전히 `companies`다 — 같은 화재를 두고 기사마다 주체를 달리
  적는 일이 있어 주체로 묶으면 합쳐야 할 것을 놓친다. 넓게 묶고 어긋날 때만
  뺀다.

  ※시점은 여기서 이미 좁다(고유형 ±2개월·반복형 같은 달). 해를 넘겨 되풀이되는
    사건이 붙는 문제는 이 파일이 아니라 시간 제한이 없는
    `batch/repair/event_merge.py` 쪽이라 거기서 막는다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from app.core.database import neo4j_session

# ── 유형어 두 등급 ────────────────────────────────────────────
#
# 처음엔 사고·법적 유형어만 넣었더니 **Event의 79%가 ER에서 제외**됐다(실측 159/201).
# 「HBM4 양산 체제」「HBM4 양산 출하」「HBM4 양산 조절」이 따로 노는 이유다.
#
# 그렇다고 유형어를 그냥 늘리면 안 된다. 두 부류의 성질이 다르다:
#
#   UNIQUE(고유형) — 한 기업에 같은 달 두 번 나기 어렵다.
#     청주 공장 화재가 6월에 두 번 나지 않는다. 이름이 달라도 같은 사건이다.
#     → 기업 + 연월 + 유형어가 같으면 **병합**.
#
#   RECURRING(반복형) — 같은 달에 **여러 건이 실제로 있을 수 있다**.
#     한미반도체가 6월에 공급계약을 세 곳과 맺을 수 있다. 상대가 다르면 다른 사건이다.
#     → 위 조건에 더해 **이름이 실제로 닮았는지**(토큰 겹침)까지 본다.
#
# 이 구분이 없으면 「SK하이닉스와의 97억 공급계약」과 「삼성전자와의 공급계약」이
# 한 사건으로 뭉개진다.
UNIQUE_TYPE_WORDS = (
    "화재", "폭발", "붕괴", "누출", "정전", "침수",
    "사망", "중대재해", "산업재해", "안전사고",
    "파업", "노동쟁의", "쟁의", "직장폐쇄",
    "리콜", "결함", "불량",
    "과징금", "제재", "압수수색", "기소", "고발", "담합", "세무조사", "행정처분",
    "소송", "가처분", "특허침해",
    "횡령", "배임", "분쟁",
    "가동중단", "생산중단", "생산차질", "감산", "셧다운",
    "적자전환", "어닝쇼크", "상장폐지", "신용등급",
    "인수", "합병", "매각", "분할", "상장",
    # ★2026-07-31 보강 — 리노공업 노사 분규가 **17개 노드로 갈린** 사례에서 나왔다.
    #   「성과급 갈등」과 「성과급 지급 갈등」은 이름 유사도가 **1.00**인데도
    #   유형어가 없어 병합 후보에조차 오르지 못했다. 노무·협상 어휘가 빠져 있었다.
    "갈등", "난항", "교섭", "임단협", "단체협약", "조정신청", "중재",
    "의혹", "논란", "제소", "고소", "수사",
)

RECURRING_TYPE_WORDS = (
    "공급계약", "장기계약", "본계약", "계약체결", "계약",
    "수주", "발주", "납품",
    "양산", "출하", "생산개시",
    "증설", "착공", "준공", "가동",
    "투자", "출자", "유치",
    "출시", "공개", "개발완료", "인증",
    "제휴", "협약", "합작", "MOU",
    "규제", "수출제한", "관세",
)

# 대표 유형어를 고를 때의 우선순위 (앞에 올수록 우선)
EVENT_TYPE_WORDS = UNIQUE_TYPE_WORDS + RECURRING_TYPE_WORDS

# 반복형은 이름이 이만큼 닮아야 같은 사건으로 본다.
# ★0.7에서는 같은 달의 「HBM4 양산 조절」과 「HBM4 양산 일정 연기」가 0.67로
#   아슬하게 갈렸다. 0.6으로 낮추되, 기간·기업 조건이 그대로 남아 있어
#   서로 다른 달의 사건은 여전히 섞이지 않는다.
_NAME_OVERLAP = 0.6

# 고유형이 걸쳐도 되는 개월 수.
# ★2026-07-31: 「같은 연월」만 허용했더니 리노공업 노조 파업이 갈렸다 —
#     「리노공업 노조 파업 가결」   2026-06
#     「리노공업 노조 전면 파업」   2026-07   ← 이름 유사도 0.75인데 월이 달라 실패
#   노사 분규·소송·화재 수습은 **여러 달에 걸치는 게 정상**이라 월 단위가
#   지나치게 엄격했다. 반복형(계약·수주)은 달마다 다른 건이 있을 수 있으므로
#   여기서 완화하지 않는다.
_UNIQUE_MONTH_SPAN = 2

# ★`subjects`(사건이 **일어난** 기업)를 따로 받는다(2026-08-29). `companies`는
#   영향받은 기업(IMPACTS)까지 섞여 있어 「같은 기업」의 근거로는 너무 넓다.
_FIND = """
MATCH (e:Event)
OPTIONAL MATCH (e)-[r]-(c:Company)
RETURN e.event_id AS id, e.name AS name,
       collect(DISTINCT c.corp_code) + collect(DISTINCT c.norm_name) AS companies,
       [x IN collect(DISTINCT CASE WHEN r.role = 'subject' THEN c.norm_name END)
          WHERE x IS NOT NULL] AS subjects,
       collect(DISTINCT r.occurred_at) + collect(DISTINCT r.valid_from) AS dates,
       count(r) AS links
"""

_MERGE = """
MATCH (a:Event {event_id:$keep}), (b:Event {event_id:$drop})
CALL apoc.refactor.mergeNodes([a, b], {properties:'discard', mergeRels:true})
YIELD node RETURN node.event_id AS id
"""


def event_type_of(name: Optional[str]) -> Optional[str]:
    """이름에서 대표 사건 유형어. 없으면 None(→ 병합 대상 제외)."""
    compact = re.sub(r"\s+", "", name or "")
    for word in EVENT_TYPE_WORDS:
        if word in compact:
            return word
    return None


def _period(dates: list) -> Optional[str]:
    """연결된 엣지들의 최초 발생 연월(YYYY-MM)."""
    valid = sorted(d for d in dates if d and len(str(d)) >= 7)
    return str(valid[0])[:7] if valid else None


# 이름 대조에서 빼는 낱말 — 어디에나 붙어 변별력이 없다
_NAME_STOP = frozenset({"반도체", "사업", "관련", "국내", "글로벌", "신규", "추가",
                        "본격", "확대", "강화", "계획", "추진", "체제", "규모"})


def _name_tokens(name: Optional[str]) -> set[str]:
    """이름 → 비교용 토큰. 알파벳은 아는 것만 한글 음차로 바꿔 맞춘다.

    ★영숫자는 **붙여서** 자른다. 음차 지원을 넣으면서 `[A-Za-z]+|[0-9]+`로
      쪼갰더니 `HBM4`가 `HBM`+`4`가 되어 **세대 구분이 사라졌다** —
      「HBM4」와 「HBM 생산능력 확대」가 같은 사건으로 병합될 뻔했다.
      음차는 순수 알파벳 토큰에만 적용하면 되므로 분리할 이유가 없다.
    """
    raw = re.findall(r"[가-힣]+|[A-Za-z0-9]+", name or "")
    out = set()
    for t in raw:
        if t.isascii() and t.isalpha():      # 숫자가 섞이면 음차하지 않는다
            t = _translit(t)
        t = t.upper()
        if len(t) >= 2 and t not in _NAME_STOP:
            out.add(t)
    return out


def _months_apart(p1: Optional[str], p2: Optional[str]) -> int:
    """두 연월(YYYY-MM)이 몇 개월 떨어져 있나. 하나라도 없으면 크게 본다."""
    if not p1 or not p2:
        return 99
    try:
        y1, m1 = int(p1[:4]), int(p1[5:7])
        y2, m2 = int(p2[:4]), int(p2[5:7])
    except (ValueError, IndexError):
        return 99
    return abs((y1 * 12 + m1) - (y2 * 12 + m2))


# ★영/한 표기가 갈리는 사건 — 음차만 다르고 같은 일이다.
#   실측: 두산로보틱스의 「Cosmos Cookoff 1위」와 「코스모스 쿡오프 1위」가
#   별개 노드로 남아 있었다. 이름 토큰이 하나도 안 겹쳐 유사도 0이 나온다.
#
#   ※문자 종류(한글/영문)가 다르다는 것만으로 짝지으면 안 된다. 그렇게 뽑아보니
#     12쌍 중 11쌍이 「메모리 반도체 생산 확대」~「CXMT 상장」처럼 무관했다.
#     실제 표기 변형은 1건뿐이었다. 그래서 **사전에 있는 것만** 바꾼다.
_TRANSLIT = {
    "cosmos": "코스모스", "cookoff": "쿡오프", "summit": "서밋",
    "forum": "포럼", "expo": "엑스포", "show": "쇼", "week": "위크",
    "day": "데이", "award": "어워드", "conference": "컨퍼런스",
    "ces": "씨이에스", "ifa": "이파", "computex": "컴퓨텍스",
    "semicon": "세미콘", "robot": "로봇", "world": "월드",
}


def _translit(token: str) -> str:
    """알파벳 토큰을 한글 음차로. 사전에 없으면 그대로."""
    return _TRANSLIT.get(token.lower(), token)


def _subject_conflict(a: dict, b: dict) -> bool:
    """사건이 **일어난 기업**이 서로 겹치지 않으면 다른 사건이다.

    ★2026-08-29에 넣었다. R0(이름+연월이 같으면 무조건 병합)에는 기업 조건이
      **아예 없었다.** 그래서 같은 달의 「자사주 소각」이 한미반도체·NAVER·
      삼성전자를 한 노드로 끌어모았다(실측: 주체가 둘 이상인 Event 66건).

    ★막는 데만 쓴다. 후보를 묶는 열쇠로는 여전히 `companies`를 쓴다 —
      같은 화재를 두고 기사마다 주체를 달리 적는 일이 있어, 주체로 묶으면
      합쳐야 할 것을 놓친다. 넓게 묶고 **주체가 어긋날 때만 거부**한다.
    """
    sa = {s for s in (a.get("subjects") or []) if s}
    sb = {s for s in (b.get("subjects") or []) if s}
    return bool(sa and sb and not (sa & sb))


def _similar(a: Optional[str], b: Optional[str]) -> bool:
    """반복형 사건이 **같은 건**인지 — 이름 토큰이 충분히 겹치는가.

    「TMAH 노출 사고」와 「인산 노출 사고」는 유형어가 같아도 다른 사건이다.
    반복형에서는 이 대조가 유일한 방어선이다.
    """
    x, y = _name_tokens(a), _name_tokens(b)
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= _NAME_OVERLAP


def resolve_events(dry_run: bool = False) -> dict[str, int]:
    """이름만 다른 동일 사건을 병합. 통계 반환."""
    stats = {"merged": 0, "groups": 0, "skipped_no_type": 0, "kept_apart": 0}

    with neo4j_session() as session:
        rows = [dict(r) for r in session.run(_FIND)]

        # ── R0: 이름과 발생월이 **모두 같으면** 무조건 병합 ──────────
        # event_id는 생성 시점의 이름으로 계산된다. 나중에 이름을 고치면
        # (예: 프롬프트 예시 유출 개명) 이름은 같아졌는데 id가 달라 노드가 갈린다.
        # 유형어가 없어 R3에서 걸러지는 경우(「HBM4 양산」)도 여기서 잡힌다.
        by_name_period: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            period = _period(row["dates"])
            if row["name"] and period:
                by_name_period[(row["name"], period)].append(row)

        merged_ids: set[str] = set()
        for (name, period), members in by_name_period.items():
            if len(members) < 2:
                continue
            ordered = sorted(members, key=lambda m: -m["links"])
            keep = ordered[0]
            # ★이름과 달이 같아도 **주체 기업이 다르면** 다른 사건이다.
            drops = [m for m in ordered[1:] if not _subject_conflict(keep, m)]
            stats["kept_apart"] += len(ordered) - 1 - len(drops)
            if not drops:
                continue
            stats["groups"] += 1
            print(f"  ✓ [동명·동월] 「{name}」 {period}: {len(drops)}건 병합")
            for drop in drops:
                if not dry_run:
                    session.run(_MERGE, keep=keep["id"], drop=drop["id"])
                merged_ids.add(drop["id"])
                stats["merged"] += 1
        rows = [r for r in rows if r["id"] not in merged_ids]

        # ── R0.5: 유형어가 없는 사건도 **이름이 거의 같으면** 병합 ──────
        #
        # 유형어 목록에 없는 사건이 259건이나 되어 후보에조차 못 올랐다. 그중
        # 「Cosmos Cookoff 1위」와 「코스모스 쿡오프 1위」처럼 명백히 같은 것이
        # 남았다(「1위」는 유형어가 아니다).
        #
        # 유형어라는 방어선이 없으니 이름 조건을 더 엄격히 한다 —
        # 기업·연월이 같고 이름이 **0.8 이상** 겹칠 때만(유형어 기반은 0.6).
        #
        # ★단순 포함 관계는 제외한다. 「HBM4」는 「HBM4 생산 확대」에 완전히
        #   포함돼 유사도 1.0이 나오지만, 짧은 쪽은 여러 사건의 **공통 상위어**일
        #   수 있다(HBM4 양산·연기·확대가 전부 「HBM4」로 빨려든다).
        #   토큰 수가 2배 넘게 차이 나면 병합하지 않는다.
        NO_TYPE_OVERLAP = 0.8
        by_cp: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            if event_type_of(row["name"]):
                continue
            period = _period(row["dates"])
            if not period:
                continue
            for corp in {c for c in row["companies"] if c}:
                by_cp[(corp, period)].append(row)

        for (corp, period), members in by_cp.items():
            uniq = {m["id"]: m for m in members if m["id"] not in merged_ids}
            if len(uniq) < 2:
                continue
            ordered = sorted(uniq.values(),
                             key=lambda m: (-m["links"], -len(m["name"] or "")))
            keep = ordered[0]
            kt = _name_tokens(keep["name"])
            for m in ordered[1:]:
                mt = _name_tokens(m["name"])
                if not kt or not mt:
                    continue
                ov = len(kt & mt) / min(len(kt), len(mt))
                ratio = max(len(kt), len(mt)) / max(min(len(kt), len(mt)), 1)
                if ov < NO_TYPE_OVERLAP or ratio > 2:
                    continue
                if _subject_conflict(keep, m):
                    stats["kept_apart"] += 1
                    continue
                stats["groups"] += 1
                stats["merged"] += 1
                print(f"  ✓ [유형어없음 {corp} {period}] '{keep['name']}' ← "
                      f"['{m['name']}']")
                if not dry_run:
                    session.run(_MERGE, keep=keep["id"], drop=m["id"])
                merged_ids.add(m["id"])
        rows = [r for r in rows if r["id"] not in merged_ids]

        # 후보군 묶기 — **고유형과 반복형의 기간 처리가 다르다**.
        #   고유형(사고·노무·법적): 기업+유형으로만 묶고, 기간은 나중에
        #       ±_UNIQUE_MONTH_SPAN 개월 이내인지로 판정한다.
        #       분규·소송은 여러 달에 걸치므로 월을 키에 넣으면 갈린다.
        #   반복형(계약·수주·양산): 기업+연월+유형으로 묶는다.
        #       같은 달에 여러 건이 실제로 있을 수 있으므로 월을 지킨다.
        recurring = set(RECURRING_TYPE_WORDS)
        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            etype = event_type_of(row["name"])
            if not etype:
                stats["skipped_no_type"] += 1
                continue
            period = _period(row["dates"])
            companies = {c for c in row["companies"] if c}
            if not companies or not period:
                continue
            row["_period"] = period
            key_period = period if etype in recurring else "*"
            # 기업이 여럿이면 각 기업 기준으로 후보에 넣는다(하나만 겹쳐도 같은 사건)
            for corp in companies:
                buckets[(corp, key_period, etype)].append(row)

        seen_merged: set[str] = set()
        for (corp, period, etype), members in buckets.items():
            uniq = {m["id"]: m for m in members if m["id"] not in seen_merged}
            if len(uniq) < 2:
                continue
            # 대표 = 링크 많은 것 → 이름 긴 것(더 구체적)
            ordered = sorted(uniq.values(),
                             key=lambda m: (-m["links"], -len(m["name"] or "")))
            keep = ordered[0]
            # ★주체 기업이 어긋나는 것은 유형·기간을 보기 전에 뺀다.
            rest = [m for m in ordered[1:] if not _subject_conflict(keep, m)]
            stats["kept_apart"] += len(ordered) - 1 - len(rest)

            if etype in recurring:
                # 반복형 — 같은 달에 여러 건이 실제로 있을 수 있다.
                # 이름이 대표와 충분히 닮은 것만 접는다.
                drops = [m for m in rest if _similar(keep["name"], m["name"])]
                kept_apart = len(rest) - len(drops)
                if kept_apart:
                    stats["kept_apart"] += kept_apart
            else:
                # 고유형 — 기간에 따라 요구 조건을 달리한다.
                #
                #   같은 달        : 이름 검사 없이 병합 (원래 동작. 안전했다)
                #   1~2개월 차이   : **이름 유사도까지** 요구
                #   3개월 이상     : 병합하지 않음
                #
                # ★기간만 풀고 이름 검사를 안 붙였더니 엉뚱한 것이 붙었다(실측):
                #     '블랙웰 결함' ← '엑시노스 보안 결함'      유사도 0.50
                #        엔비디아 GPU 결함과 삼성 AP 보안 결함은 다른 사건
                #     '첸나이 공장 노동자 파업' ← '광복절 연휴 파업'  유사도 0.33
                #   유형어(결함·파업)만 같으면 다 붙어버린 것이다.
                #   기간이 벌어질수록 이름 근거를 더 요구하는 게 맞다.
                drops, far = [], 0
                for m in rest:
                    gap = _months_apart(keep["_period"], m["_period"])
                    if gap > _UNIQUE_MONTH_SPAN:
                        far += 1
                    elif gap == 0 or _similar(keep["name"], m["name"]):
                        drops.append(m)
                    else:
                        far += 1
                if far:
                    stats["kept_apart"] += far

            if not drops:
                continue
            stats["groups"] += 1
            print(f"  ✓ [{corp} {period} {etype}] '{keep['name']}' ← "
                  f"{[d['name'] for d in drops]}")
            for drop in drops:
                if not dry_run:
                    session.run(_MERGE, keep=keep["id"], drop=drop["id"])
                seen_merged.add(drop["id"])
                stats["merged"] += 1

    return stats


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = resolve_events(dry_run="--dry-run" in sys.argv)
    print(f"\n{'[dry-run] ' if '--dry-run' in sys.argv else '✅ '}"
          f"{result['groups']}개 사건군에서 {result['merged']}건 병합 "
          f"(유형어 없어 제외 {result['skipped_no_type']} · "
          f"반복형이라 이름이 달라 보존 {result['kept_apart']})")
