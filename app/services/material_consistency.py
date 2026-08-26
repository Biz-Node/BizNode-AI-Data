"""⑥.5 Material Consistency — ③ 그래프 라벨과 ⑥ 근거 원문을 **답변 전에** 대조한다.

★왜 필요한가 (설계서 §10 · 현황서 §5-12)

  대조 지점이 ⑩ 하나뿐이었고 **⑩ 은 답변을 쓴 뒤**다.

      전   ③ 그래프 재료 ─┐
           ⑥ 근거 원문  ─┴─→ ⑦ 조립 → ⑧ 답변 → ⑩ 대조     ← 대조가 답변 뒤에만
      후   ③ 그래프 재료 ─┐
           ⑥ 근거 원문  ─┴─→ ⑥.5 대조 → ⑦ → ⑧ → ⑩ 대조   ← 앞에도 자리를 둔다

  실측 사례 — Event 라벨 「HBM3E 대량 양산 **차질**」의 **유일한** 근거가
  「양산을 세계 최초로 **시작**했다」였다. LLM 이 지어낸 것이 아니라 **라벨에
  이미 있었다.** 낱말 겹침은 0.60 이라 「의심」 임계 0.34 를 한참 넘어 못 잡는다.

★**flag 만 낸다 — 버리지 않는다.**

  설계서 §10 의 ⑥.5 금지사항이 「사건·관계·근거를 **버리지 않는다** · LLM 을
  호출하지 않는다 · 새 조회를 하지 않는다」다. 대조는 **검증이지 생성이 아니다.**
  격리는 ⑦ Context Builder 의 일이고, `RetrieveResponse` 는 손대지 않는다.

★**규칙은 실측으로 정했다** (2026-08-26 · `HAS_EVENT` 위험사건 327건 전수)

      후보 A  라벨의 부정 어휘가 근거에 없다               26/327 (8.0%)  precision ~15%
      후보 B  A 이면서 근거에 그 부정 어휘의 반의어가 있다   4/327 (1.2%)  precision 50%  ★채택

  후보 A 는 **과다 제외**가 확인됐다 — 26건을 전수 판독하니 대부분이 동의어
  오탐이었다(「지연」↔「늦어지고」·「파손」↔「깨지는」·「사망사고」↔「숨진채
  발견」·「상장폐지」↔「상장 폐지」). 후보 B 가 잡은 4건 중 2건이 진짜이고,
  그중 하나가 위 HBM3E 사례 그 자체다.

★**오탐 2건을 규칙으로 보정하지 않았다.** 원인은 알고 있다 — 「가동이 어려워」
  (반의어 뒤 부정 서술)와 「신성장동력」(복합어 안의 반의어)이다. 다만 n=4 에
  맞춰 규칙을 얹는 것은 과적합이다([규칙 5]).

★**§5-14 유형(배경절 오라벨)은 이 규칙으로 안 잡힌다.** 그건 시간 맥락 대조의
  몫이고 별개 신호(원문 연도 ↔ `occurred_at`)를 쓴다.

★**규칙 둘을 분리해 둔 이유** — 설계서 §10 이 「둘이 같은 원인이라고 단정하지
  않는다」고 명시한다. 신호도 정밀도도 범위도 다르다.

      check_polarity   라벨 부정어 ↔ 근거 반의어      위험사건만    4/327 (1.2%)
      check_temporal   원문 연도 ↔ occurred_at 연도   전체 사건    39/1,005 (3.9%)

  시간 대조는 **오류 확정이 아니라 후보**다 — 문서 실측(§5-14)이 층 A 37건 중
  확정 24 · 배경절 아님 13 으로 갈렸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Sequence


class _EventLike(Protocol):
    event_id: str
    name: str
    is_risk: bool
    occurred_at: Optional[str]
    evidence_ids: list[str]


class _EvidenceLike(Protocol):
    evidence_id: str
    text: str
    missing: bool


@dataclass(frozen=True)
class PolarityFlag:
    """왜 flag 됐는지를 **값으로** 들고 있는다 — 조용히 flag 하지 않는다([규칙 2])."""

    event_id: str
    label_words: tuple[str, ...]      # 라벨에 있는데 근거에 없는 부정 어휘
    evidence_words: tuple[str, ...]   # 근거에 있는 그 어휘의 반의어


@dataclass(frozen=True)
class TemporalFlag:
    """★**오류 확정이 아니라 후보다.** 문서 실측(§5-14)은 층 A 37건 중 확정 24 ·
    배경절 아님 13 이었다 — 규칙 자체가 확정을 낼 수 없다."""

    event_id: str
    occurred_year: int                # `occurred_at` 의 연도 (= 대개 보도일)
    evidence_years: tuple[int, ...]   # 근거 원문이 말한, 그보다 이른 절대 연도
    markers: tuple[str, ...]          # 배경절 표지어 — **요구 조건이 아니라 신호**


# ── 부정 어휘 ────────────────────────────────────────────────────────────
# ★짐작이 아니다. 위험사건 327건 라벨에서 실제로 관측된 622개 토큰 중
#   위험을 나타내는 것만 골랐다(사람의 읽기 — 현황서 §5-14 B2 와 같은 방법).
_RISK_WORDS: tuple[str, ...] = (
    # 법적·규제
    "소송", "피소", "제소", "패소", "패배", "파기환송", "항소", "판결", "공판",
    "가처분", "분쟁", "다툼", "담합", "혐의", "기소", "검찰", "수사", "압수수색",
    "세무조사", "과징금", "과태료", "제재", "시정명령", "위반", "위법", "불법",
    "적발", "고발", "배임", "횡령", "은폐", "편법", "부당", "침해", "탈취",
    "시세조종", "부당이득", "몰아주기", "위장계열사", "허위공시", "불공정거래",
    "자본시장법", "특수상해", "성희롱", "갑질", "강요", "방해", "저지", "제한",
    "금지", "취소", "처분", "청구", "손배", "구제신청", "내부고발", "투서",
    "자작극", "투자사기", "선행매매", "불완전판매", "불성실공시법인",
    "상장폐지", "상장적격성", "거래정지", "투자경고종목", "투자위험종목",
    "직무집행정지가처분", "주주대표소송", "집단소송", "근로자지위확인",
    # 사고·재해
    "사고", "재해", "중대재해", "산업재해", "화재", "폭발", "누출", "유출",
    "붕괴", "추락", "끼임사고", "사망사고", "공정사고", "폭발사고", "참사",
    "부상", "피해", "피폭", "노출", "유독가스", "방사선", "손상", "파손",
    "감염", "좌초", "봉쇄", "차단", "셧다운",
    # 노무
    "파업", "총파업", "부분파업", "쟁의", "농성", "집회", "결의대회", "투쟁",
    "갈등", "교착", "결렬", "난항", "반발", "부결", "해고", "부당해고",
    "희망퇴직", "노동쟁의", "불법파견", "미가입자", "퇴직금소송", "임금소송",
    # 실적·재무
    "적자", "적자전환", "영업적자", "영업손실", "순손실", "손실", "쇼크",
    "어닝쇼크", "급감", "감소", "하락", "부진", "환손실", "위기", "리스크",
    "사법리스크", "미납", "청산", "기업회생", "감사의견", "축소", "동결",
    # 공급·품질
    "차질", "중단", "지연", "감산", "대란", "결함", "불량", "오류", "에러",
    "리콜", "오작동", "무산", "보류", "철회", "연기", "탈락", "거부", "거절",
    "해지", "제외", "역효과", "논란", "의혹", "문제", "비판", "논쟁", "사태",
    "해킹", "랜섬웨어", "군사기밀", "기술탈취", "핵심기술",
)

# ── 반의어 ──────────────────────────────────────────────────────────────
# ★근거에 이것이 나타나면 **극성이 뒤집힌** 것이다. 위 부정어 중 반대말이
#   분명한 것에만 짝을 붙였다 — 반대말이 애매한 것(「소송」·「혐의」…)에는
#   짝이 없고, 그래서 그 사건은 flag 되지 않는다.
_ANTONYMS: dict[str, tuple[str, ...]] = {
    "차질": ("정상", "순항", "본격", "시작", "돌입", "확대", "개선", "호조"),
    "중단": ("재개", "가동", "정상화", "본격", "시작", "착수", "확대"),
    "지연": ("앞당", "조기", "예정대로", "완공", "준공", "개시"),
    "감산": ("증산", "확대", "증설", "본격"),
    "적자": ("흑자", "영업이익", "순이익", "개선", "성장", "호실적"),
    "적자전환": ("흑자전환", "흑자", "개선"),
    "영업손실": ("영업이익", "흑자", "개선"),
    "영업적자": ("영업이익", "흑자", "개선"),
    "순손실": ("순이익", "흑자"),
    "손실": ("이익", "흑자", "수익"),
    "급감": ("급증", "증가", "성장", "확대", "호조"),
    "감소": ("증가", "성장", "확대", "늘", "호조"),
    "하락": ("상승", "반등", "급등", "회복"),
    "부진": ("호조", "선전", "회복", "개선", "성장"),
    "패소": ("승소", "인용", "기각"),
    "무산": ("성사", "체결", "확정", "승인"),
    "보류": ("승인", "확정", "재개", "결정"),
    "철회": ("확정", "추진", "강행"),
    "탈락": ("선정", "진입", "확보", "수주"),
    "거부": ("수용", "합의", "승인"),
    "거절": ("수용", "합의", "승인"),
    "결렬": ("타결", "합의", "체결"),
    "난항": ("타결", "순항", "합의"),
    "부결": ("가결", "통과", "승인"),
    "위기": ("호황", "회복", "개선", "성장"),
    "축소": ("확대", "증설", "확장"),
    "리스크": ("안정", "해소"),
}


def _own_texts(event: _EventLike,
               by_id: dict[str, _EvidenceLike]) -> list[str]:
    """★**이 사건에 달린 근거만** 읽는다. 남의 근거로 극성을 판정하면 안 된다 —
    기업별 evidence scope(설계서 §6-2)와 같은 원칙이다.

    `missing=true` 는 원문을 못 찾은 것이라 뺀다 — 없는 원문으로 판정하지 않는다.
    """
    out = []
    for eid in event.evidence_ids:
        evidence = by_id.get(eid)
        if evidence is not None and not evidence.missing and evidence.text:
            out.append(evidence.text)
    return out


def check_polarity(events: Sequence[_EventLike],
                   evidence: Iterable[_EvidenceLike]) -> dict[str, PolarityFlag]:
    """event_id → `PolarityFlag`. **재료를 건드리지 않고 flag 만 돌려준다.**

    후보 B — 라벨의 부정 어휘가 근거 원문에 **없고**, 그 어휘의 **반의어가
    근거에 있을 때만** flag 한다.
    """
    by_id = {e.evidence_id: e for e in evidence}
    flags: dict[str, PolarityFlag] = {}
    for event in events:
        # `is_risk=False` 는 애초에 부정 라벨이 아니다 — 대조 대상이 아니다.
        if not event.is_risk:
            continue
        risk_words = tuple(w for w in _RISK_WORDS if w in event.name)
        if not risk_words:
            continue
        texts = _own_texts(event, by_id)
        if not texts:
            continue
        blob = "\n".join(texts)
        # 부정 어휘가 근거에도 있으면 어긋난 것이 아니다 — 실측 327건 중 272건.
        absent = tuple(w for w in risk_words if w not in blob)
        if not absent:
            continue
        opposites = tuple(sorted({o for w in absent
                                  for o in _ANTONYMS.get(w, ()) if o in blob}))
        if not opposites:
            continue
        flags[event.event_id] = PolarityFlag(event_id=event.event_id,
                                             label_words=absent,
                                             evidence_words=opposites)
    return flags


# ── 시간 맥락 (§5-14) ────────────────────────────────────────────────────
# ★신호는 **연도 불일치**다. 배경절 표지어는 함께 기록하되 **요구하지 않는다** —
#   실측 층 A 39건 중 표지어가 같이 있는 것은 9건뿐이라, 곱하면 30건을 놓친다.
_YEAR = re.compile(r"(19|20)\d{2}\s*년")

# ★「지난해」·「앞서」류만 있고 절대 시점이 없는 것(문서 층 B · 92건)은 **후보로
#   삼지 않는다.** 발생일을 원문으로 확정할 수 없어 판독 불가가 많다.
_BACKGROUND_MARKERS: tuple[str, ...] = (
    "와 관련", "과 관련", "년 만에", "지난해", "바 있다", "앞서", "그동안",
    "이전에", "당시", "한 지",
)

# 연말 사건이 이듬해 보도되는 것은 정상이다 — 문서 실측도 `−1` 을 허용했다.
_REPORT_LAG_YEARS = 1


def check_temporal(events: Sequence[_EventLike],
                   evidence: Iterable[_EvidenceLike]) -> dict[str, TemporalFlag]:
    """event_id → `TemporalFlag`. **오류 확정이 아니라 flag 후보다.**

    ★`occurred_at` 은 대개 **보도일**이다(`news_loader.py:167,230` — `observed =
      published_at`. 실측 1,062건 중 1,059건이 `last_seen` 과 같다). 근거 원문이
      그보다 이른 절대 연도를 말하고 있으면, 라벨이 기사의 **배경절**을 가리킬
      수 있다.

    ★**극성 대조와 범위가 다르다** — `is_risk` 를 보지 않는다. 배경절 오라벨은
      위험사건에만 생기지 않는다(실측 층 A 에 사업확장·자본거래가 섞여 있다).
    """
    by_id = {e.evidence_id: e for e in evidence}
    flags: dict[str, TemporalFlag] = {}
    for event in events:
        occurred_year = _year_of(event.occurred_at)
        if occurred_year is None:
            continue
        texts = _own_texts(event, by_id)
        if not texts:
            continue
        blob = "\n".join(texts)
        earlier = tuple(sorted(
            y for y in _years_in(blob) if y < occurred_year - _REPORT_LAG_YEARS))
        if not earlier:
            continue
        markers = tuple(m for m in _BACKGROUND_MARKERS if m in blob)
        flags[event.event_id] = TemporalFlag(
            event_id=event.event_id, occurred_year=occurred_year,
            evidence_years=earlier, markers=markers)
    return flags


def _year_of(occurred_at: Optional[str]) -> Optional[int]:
    head = str(occurred_at or "")[:4]
    return int(head) if head.isdigit() else None


def _years_in(text: str) -> set[int]:
    return {int(m.group(0)[:4]) for m in _YEAR.finditer(text)}
