"""적합성 필터 — LLM 진입 최전선.

2단 게이트로 비용을 방어한다:
  1차 규칙(무료): 관계 키워드 無 or 시드 미언급 → 즉시 컷
  2차 제로샷 LLM(gpt-4o-mini): {"is_relevant": bool}만 판정
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

from pipeline.llm import get_client

from app.core.config import ETF_LIST_PATH

_FILTER_MODEL = "gpt-4o-mini"   # 대량 처리 — 저렴한 모델
_ROUTER_WORKERS = 8             # 라우터 동시 호출 수 (I/O 대기라 스레드로 충분)

# 유의미한 비즈니스 관계를 시사하는 동사·명사 (방법서 §12-1)
#
# 설계 원칙: **재현율(recall) 우선.** 1차에서 놓치면 영원히 복구 불가하지만,
# 오탐은 2차 LLM 라우터가 걷어낸다(비대칭 비용). 실측상 오탐 53건 vs 누락 1건이라
# 넓게 잡는 편이 옳다.
RELATION_KEYWORDS = (
    # 소유·지배
    "인수", "합병", "피인수", "매각", "양수", "양도", "인수합병", "M&A",
    "지분", "출자", "투자", "지주", "계열", "자회사", "최대주주", "주주",
    # 거래·공급
    "공급", "납품", "수주", "계약", "체결", "발주", "조달", "위탁", "외주",
    "거래", "구매", "판매", "공급망", "밸류체인", "협력사", "파트너사",
    # 협력·제휴 (누락 사례 반영: 입점·진출·출시 등)
    "제휴", "협력", "협약", "MOU", "합작", "JV", "공동개발", "공동", "기술이전",
    "라이선스", "동맹", "파트너십", "손잡", "맞손", "입점", "진출", "협업",
    # 분쟁·규제
    "소송", "제소", "피소", "고소", "분쟁", "특허침해", "침해", "갈등",
    "규제", "제재", "과징금", "조사", "시정명령", "위반", "담합", "공정위",
    # 경쟁·시장
    "경쟁", "점유율", "1위", "추격", "맞대결", "격차",
    # 사건·리스크
    "리콜", "결함", "화재", "사고", "중단", "차질", "부도", "회생", "구조조정",
)


@dataclass
class RuleResult:
    passed: bool
    matched_corps: list[str]      # 언급된 시드 corp_code
    matched_names: list[str]
    reason: str


@lru_cache(maxsize=1)
def _seed_index() -> list[tuple[str, str]]:
    """[(corp_code, 기업명)] — 이름 긴 순(부분일치 오탐 완화)."""
    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        companies = json.load(f)["companies"]
    pairs = [(c["corpCode"], c["companyName"]) for c in companies]
    return sorted(pairs, key=lambda p: -len(p[1]))


# 사건·리스크를 시사하는 키워드 (1차 규칙용 — 검색용보다 넓게).
# 관계 키워드와 **OR**로 묶는다: 관계 기사도, 사건 기사도 통과시켜야 하기 때문.
RISK_SIGNAL_KEYWORDS = (
    # 사고·중단
    "화재", "폭발", "정전", "누출", "붕괴", "가동중단", "생산차질", "공장중단", "셧다운",
    # 품질·안전
    "리콜", "결함", "불량", "안전사고", "중대재해", "품질문제",
    # 노무
    "파업", "총파업", "노조", "노동쟁의", "임단협", "쟁의", "태업",
    # 규제·수사·법
    "과징금", "제재", "압수수색", "기소", "고발", "담합", "공정위", "국세청",
    "세무조사", "검찰", "특허침해", "소송", "가처분", "행정처분", "영업정지",
    # 지배구조·주주
    "경영권", "분쟁", "소액주주", "행동주의", "주주제안", "횡령", "배임",
    # 실적·신용
    "어닝쇼크", "적자전환", "영업손실", "신용등급", "유동성", "감사의견", "상장폐지",
    # 공급망·통상
    "공급망", "수출규제", "관세", "수급차질", "병목", "재고조정", "감산",
    # ★2026-07-31 보강 — 목록에 없어 놓치던 표현들.
    #   필터를 아예 없애는 안은 주가 시황이 대거 유입돼 되돌렸다(위 주석 참고).
    #   대신 **위기 서술에 쓰이는 낱말**을 추가한다. 시황 기사에는 잘 안 쓰인다.
    "불안", "위기", "우려", "타격", "차질", "이중고", "비상", "경고",
    "논란", "의혹", "제동", "무산", "철회", "결렬", "반발", "갈등",
    "적자", "부진", "급감", "위축", "축소", "중단", "지연", "연기",
    "사고", "붕괴", "누락", "실패", "취소", "해지", "탈락", "제외",
)


# ── 주제 분류 ──────────────────────────────────────────────────
#
# ★뉴스 화면의 **축 1**(전체·공급망·지분·규제·사건)이 이걸 쓴다.
#   `rule_filter` 는 「통과했나」만 알려 주고 **무엇으로 통과했는지**는 안 알려
#   줘서, 화면이 주제로 거를 수가 없었다. 키워드를 갈래로 묶어 함께 돌려준다.
#
# ★한 기사가 여러 주제에 걸릴 수 있다(「공정위, 납품단가 담합 제재」 → 공급망 + 규제).
#   하나로 고르지 않고 **전부** 준다 — 화면이 「공급망」으로 걸러도 「규제」로
#   걸러도 나오는 게 맞다.
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "supply": ("공급", "납품", "수주", "계약", "체결", "발주", "조달", "위탁", "외주",
               "거래", "구매", "공급망", "밸류체인", "협력사", "파트너사",
               "생산차질", "공장중단", "셧다운", "가동중단"),
    "stake": ("인수", "합병", "피인수", "매각", "양수", "양도", "인수합병", "M&A",
              "지분", "출자", "투자", "지주", "계열", "자회사", "최대주주", "주주"),
    "regulation": ("과징금", "제재", "압수수색", "기소", "고발", "담합", "공정위",
                   "국세청", "규제", "조사", "행정처분", "시정명령", "과태료"),
    "incident": ("화재", "폭발", "정전", "누출", "붕괴", "리콜", "결함", "불량",
                 "안전사고", "중대재해", "품질문제", "파업", "총파업", "노조",
                 "노동쟁의", "임단협", "쟁의", "태업", "소송", "분쟁"),
}


def topics_of(title: str, body: str = "") -> list[str]:
    """이 기사가 어느 주제에 걸리나. **여러 개가 정상이다.**"""
    blob = f"{title} {body}"
    return [t for t, kws in TOPIC_KEYWORDS.items() if any(k in blob for k in kws)]


def rule_filter(title: str, body: str, *, track: str = "relation") -> RuleResult:
    """1차 규칙 필터 — 시드 언급 AND (트랙별 키워드).

    track="relation": 관계 키워드 필요 (두 기업 사이의 거래·지분·분쟁)
    track="risk":     리스크 키워드 필요 (사건·사고·규제 — 상대 기업이 없어도 됨)
    track="both":     둘 중 하나
    """
    blob = f"{title} {body}"

    matched = [(code, name) for code, name in _seed_index() if name in blob]
    if not matched:
        return RuleResult(False, [], [], "시드 기업 미언급")

    codes = [c for c, _ in matched]
    names = [n for _, n in matched]

    has_relation = any(kw in blob for kw in RELATION_KEYWORDS)
    has_risk = any(kw in blob for kw in RISK_SIGNAL_KEYWORDS)

    if track == "risk":
        ok, why = has_risk, "리스크 키워드 없음"
    elif track == "both":
        ok, why = (has_relation or has_risk), "관계·리스크 키워드 모두 없음"
    else:
        ok, why = has_relation, "관계 키워드 없음"

    return RuleResult(ok, codes, names, "통과" if ok else why)


_SYSTEM_RELATION = (
    "당신은 기업 관계 지식그래프 구축을 위한 뉴스 선별기입니다. "
    "기사가 **특정 기업 간의 구체적 비즈니스 관계**를 담고 있는지 판정하세요.\n"
    "관련 있음(true): 인수·합병, 공급·납품 계약, 소송·분쟁, 규제·제재, 제휴·협력·JV, "
    "지분 취득·매각, 경쟁 구도 등 **두 주체 사이의 관계**가 명시된 기사.\n"
    "관련 없음(false): 단순 실적 발표·주가 시황, 거시경제·정책 일반론, 인사·채용, "
    "부동산·생활 기사, 특정 기업만 다루고 상대가 없는 기사, 제품 리뷰.\n"
    "JSON만 출력하세요."
)

_SYSTEM_RISK = (
    "당신은 기업 리스크 지식그래프 구축을 위한 뉴스 선별기입니다. "
    "기사가 **기업에 실제로 일어난 사건**을 담고 있는지 판정하세요.\n"
    "관련 있음(true): 화재·폭발·가동중단 등 사고, 파업·노동쟁의, 과징금·제재·압수수색·"
    "기소, 리콜·품질결함, 경영권 분쟁·주주 갈등, 횡령·배임, 신용등급 변동·적자전환, "
    "공급망 차질·수출규제. **상대 기업이 없어도 됩니다** — 한 기업에 일어난 사건이면 통과.\n"
    "관련 없음(false): 주가 시황·목표주가·수급 분석, 단순 실적 숫자 나열, "
    "거시경제 일반론, 인사·채용, 제품 리뷰·광고, 사건이 **예상·우려 수준**이고 "
    "실제로 일어나지 않은 기사.\n"
    "핵심 기준: **이미 일어난 구체적 사건**인가, 아니면 전망·해설인가.\n"
    "JSON만 출력하세요."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_relevant", "reason"],
    "additionalProperties": False,
}


def llm_router(title: str, body: str, *, track: str = "relation") -> tuple[bool, str]:
    """2차 제로샷 라우터 — 트랙 기준에 맞는 기사만 통과. 실패 시 보수적으로 통과시킨다
    (추출 단계에서 다시 걸러지므로 누락보다 낫다)."""
    system = _SYSTEM_RISK if track == "risk" else _SYSTEM_RELATION
    try:
        resp = get_client().chat.completions.create(
            model=_FILTER_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"제목: {title}\n\n본문: {body[:1500]}"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "relevance", "schema": _SCHEMA, "strict": True},
            },
        )
        data = json.loads(resp.choices[0].message.content)
        return bool(data.get("is_relevant")), data.get("reason", "")
    except Exception as exc:
        return True, f"라우터 실패({exc!r}) — 보수적 통과"


def llm_router_batch(items: list[tuple[str, str]], *, track: str = "relation",
                     workers: int = _ROUTER_WORKERS) -> list[tuple[bool, str]]:
    """여러 기사를 동시에 판정한다. 입력 순서대로 결과를 돌려준다.

    라우터 호출은 서로 독립적인데 순차로 돌리면 시드 64개 확장에서 500건 × 1~2초
    = 10~15분이 그냥 대기 시간이 된다. OpenAI 요청은 I/O 대기가 대부분이라
    스레드로 충분하다(GIL 영향 없음).
    """
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda it: llm_router(it[0], it[1], track=track), items))
