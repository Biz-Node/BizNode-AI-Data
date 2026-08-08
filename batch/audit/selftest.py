"""검사기 **회귀 테스트** — 추출기·검증기가 한쪽으로 쏠렸는지 잰다.

★왜 필요한가 (2026-08-01)

프롬프트를 고칠 때마다 전량(3,775건·1,600원·12분)을 돌려서야 결과를 알 수 있었다.
그래서 **한 방향으로 과교정된 것을 늦게 발견**했다:

    검증기  거짓 음성을 고치려고 실패 사례를 넣었더니 거절률이 8% → **49%**.
            「로보티즈는 자기주식을 매각했다」를 두고 "'사건'이라는 표현이
            없어서" HAS_EVENT를 부정했다.
    추출기  "DEVELOPS를 함부로 만들지 마세요"가 프롬프트에 이미 있었는데도
            뉴스 DEVELOPS 885건 중 419건(47%)이 근거 검증에서 걸렸다.
            **경고가 먹는지 재는 수단이 없으면 안 먹는 줄 모른다.**

두 검사 다 **양쪽을 잰다** — 받아들여야 할 것과 물리쳐야 할 것. 한쪽 정확도만
좋으면 쏠린 것이라 경고한다. 합쳐서 150원·30초라 몇 번이고 돌릴 수 있다.

    python -m batch.audit.selftest                # 둘 다
    python -m batch.audit.selftest --only verifier
    python -m batch.audit.selftest -v             # 사유까지

★사례는 전부 **실제 DB의 근거 문장·기사**다. 지어낸 것이 있으면 실전에서
  어떻게 움직일지 알 수 없다.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from batch.audit.grounding import _verify
from pipeline.news.extractor import extract_relations

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
#  ① 추출기 — 금지 규칙이 실제로 먹는가
# ══════════════════════════════════════════════════════════════
# (제목, 본문, 힌트기업, [반드시 나와야 할 (source, edge, target)],
#  [나오면 안 되는 (source, edge, target)], 메모)
# ★ target이 ""이면 **엣지 타입만** 본다(제품 이름이 매번 조금씩 달라서).
EXTRACTOR_CASES: list[tuple] = [
    (
        "한화세미텍, SK하이닉스에 HBM용 TC 본더 공급",
        "한화세미텍이 SK하이닉스에 고대역폭메모리(HBM) 제조용 TC 본더 장비를 "
        "공급하기로 계약을 체결했다. 계약 규모는 420억원이다.",
        ["한화세미텍", "SK하이닉스"],
        [("한화세미텍", "SUPPLIES_TO", "SK하이닉스")],
        [("한화세미텍", "DEVELOPS", ""), ("SK하이닉스", "DEVELOPS", "")],
        "공급 계약 문장에서 DEVELOPS를 만들면 안 된다 (실측 오추출 1위)",
    ),
    (
        "한미반도체, 그리핀 공급계약 수주",
        "한미반도체가 HBM 제조용 듀얼 TC 본더 '그리핀'의 공급 계약을 수주했다고 "
        "공시했다. 계약금액은 442억원이다.",
        ["한미반도체"],
        [],
        [("한미반도체", "DEVELOPS", "")],
        "수주 얘기지 개발 얘기가 아니다",
    ),
    (
        "삼성전자·SK하이닉스, 정부 실무협의체 참여",
        "정부가 호남권 반도체 산업단지 전력공급 방안 마련에 속도를 낸다. "
        "삼성전자·SK하이닉스 등이 참여하는 실무협의체를 꾸려 공급선로 구축과 "
        "인허가 절차를 논의한다.",
        ["삼성전자", "SK하이닉스"],
        [],
        [("삼성전자", "PARTNERS_WITH", ""), ("SK하이닉스", "PARTNERS_WITH", "")],
        "정부 협의체 참여는 협력이 아니다 (실측 오추출)",
    ),
    (
        "SK하이닉스, 사상 최대 분기 실적",
        "SK하이닉스가 AI 반도체 수요 급증에 힘입어 1분기 매출 52조5762억원을 "
        "기록했다. 특히 영업이익률은 71.5%로, 지난해 4분기 엔비디아(67.7%)와 "
        "올해 1분기 TSMC(58.1%)의 기록을 앞질렀다.",
        ["SK하이닉스"],
        [],
        [("SK하이닉스", "COMPETES_WITH", "엔비디아"),
         ("SK하이닉스", "COMPETES_WITH", "TSMC")],
        "수익률 비교는 경쟁 근거가 아니다 (실측 오추출)",
    ),
    (
        "삼성 갤럭시, 러시아 판매 중단 지속",
        "삼성전자는 러시아 시장에서 스마트폰 공식 판매를 중단한 상태다. "
        "갤럭시S23 시리즈는 물론 Z 폴드5, Z 플립5도 러시아에서 공식 판매하지 않았다.",
        ["삼성전자"],
        [],
        [("삼성전자", "DEVELOPS", "")],
        "판매 얘기에서 개발을 만들면 안 된다 (배경지식으로 메우기)",
    ),
    (
        "SK하이닉스, 테스와 장비 공급계약",
        "SK하이닉스는 반도체 장비업체 테스와 반도체 제조 장비 공급 계약을 체결했다.",
        ["SK하이닉스", "테스"],
        [("테스", "SUPPLIES_TO", "SK하이닉스")],
        [("SK하이닉스", "SUPPLIES_TO", "테스")],
        "문장 주어가 아니라 실제 공급자가 source (장비업체 = 테스)",
    ),
    (
        "심텍, HBM용 패키지 기판 양산 확대",
        "심텍은 HBM용 패키지 기판을 양산하며 고부가 제품 비중을 늘리고 있다. "
        "현재 삼성전자, SK하이닉스 등 글로벌 메모리 칩 메이커를 주요 고객사로 "
        "확보하고 있다.",
        ["심텍"],
        [("심텍", "DEVELOPS", ""), ("심텍", "SUPPLIES_TO", "SK하이닉스")],
        [],
        "★양산한다고 명시하면 DEVELOPS를 만들어야 한다 (과교정 확인)",
    ),
    (
        "넷리스트, SK하이닉스 특허침해 제소",
        "넷리스트는 작년 3월 메모리 모듈 특허 침해 혐의로 SK하이닉스를 "
        "텍사스 서부지법에 제소했다.",
        ["SK하이닉스"],
        [("넷리스트", "SUES", "SK하이닉스")],
        [("SK하이닉스", "SUES", "넷리스트")],
        "제소한 쪽이 source",
    ),
    (
        "공정위, 반도체 3사 담합 조사 착수",
        "공정거래위원회가 D램 가격 담합 의혹과 관련해 삼성전자와 SK하이닉스를 "
        "상대로 현장 조사에 착수했다.",
        ["삼성전자", "SK하이닉스"],
        [("공정거래위원회", "REGULATES", "삼성전자")],
        [("공정거래위원회", "SUES", "삼성전자")],
        "규제기관의 조사는 REGULATES이지 SUES가 아니다",
    ),
]


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def _has(rels, src: str, edge: str, tgt: str) -> bool:
    """tgt가 빈 문자열이면 (source, edge) 조합만 본다."""
    for r in rels:
        if _norm(r.source) != _norm(src) or r.edge_type != edge:
            continue
        if not tgt or _norm(tgt) in _norm(r.target) or _norm(r.target) in _norm(tgt):
            return True
    return False


def run_extractor(verbose: bool) -> tuple[int, int, int, int]:
    """(나와야 할 것 통과, 전체, 물리쳐야 할 것 통과, 전체)"""
    def one(case):
        title, body, hints, must, must_not, note = case
        try:
            return case, extract_relations(title, body, hints), None
        except Exception as exc:
            return case, [], repr(exc)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(one, EXTRACTOR_CASES))

    n_must = ok_must = n_not = ok_not = 0
    for (title, body, hints, must, must_not, note), rels, err in results:
        if err:
            print(f"✗ {title[:34]:36} 추출 실패: {err[:60]}")
            continue
        miss = [m for m in must if not _has(rels, *m)]
        leak = [m for m in must_not if _has(rels, *m)]
        n_must += len(must); ok_must += len(must) - len(miss)
        n_not += len(must_not); ok_not += len(must_not) - len(leak)
        mark = "✓" if not miss and not leak else "✗"
        print(f"{mark} {title[:32]:34} 엣지 {len(rels):>2}개  {note}")
        for s, e, t in leak:
            print(f"    ✗ 지어냄  {s} -{e}-> {t or '(아무 대상)'}")
            for r in [r for r in rels
                      if _norm(r.source) == _norm(s) and r.edge_type == e][:2]:
                print(f"        실제: {r.source} -{r.edge_type}-> {r.target}"
                      f"  근거: {r.evidence[:48]}")
        for s, e, t in miss:
            print(f"    ✗ 빠뜨림  {s} -{e}-> {t or '(아무 대상)'}")
        if verbose:
            for r in rels:
                print(f"      · {r.source} -{r.edge_type}/{r.subtype}-> {r.target}"
                      f"  ({r.confidence})")
    return ok_must, n_must, ok_not, n_not


# ══════════════════════════════════════════════════════════════
#  ② 검증기 — 받아들임/물리침 양쪽을 재는가
# ══════════════════════════════════════════════════════════════
# (엣지유형, subtype, source, target, 근거, 기대판정, 메모)
VERIFIER_CASES: list[tuple[str, str, str, str, str, str, str]] = [
    # ── 받아들여야 하는 것 ────────────────────────────────────
    ("SUPPLIES_TO", "공급", "칸토덴카", "SK하이닉스",
     "일본 ‘칸토덴카(Kanto Denka)’와 ‘센트럴글래스(Central Glass)’는 삼성전자와 "
     "SK하이닉스, TSMC 등 주요 고객사에게 \"내달 1일부터 육불화텅스텐 생산을 영구 "
     "중단하기로 했다\"고 공식 통보했다.",
     "supported", "「고객사」는 공급 관계다"),
    ("SUPPLIES_TO", "공급", "심텍", "SK하이닉스",
     "현재 삼성전자, SK하이닉스 등 글로벌 '빅5' 메모리 칩 메이커를 비롯해 세계 유수의 "
     "반도체 패키징 전문 기업들을 주요 고객사로 확보하고 있다.",
     "supported", "주어가 생략돼도 고객사 서술이면 공급"),
    ("SUPPLIES_TO", "공급", "ASML", "SK하이닉스",
     "ASML은 최근 2023년 4분기 실적발표회를 통해 EUV 노광기 수주량이 메모리반도체 "
     "업체를 중심으로 대폭 증가했다고 밝혔다. 삼성전자와 SK하이닉스가 첨단 D램 공정 "
     "전환에 속도를 내면서 필수 장비인 EUV 노광기 구매에 나선 결과로 풀이된다.",
     "supported", "「A가 B의 장비를 구매」 = B가 A에 공급"),
    ("ACQUIRES", "인수", "메가폰", "스카르텔",
     "스카르텔은 4G/LTE 사업 역량을 강화하기 위해 메가폰이 지난 2013년 인수한 통신회사다.",
     "supported", "관형절 — 「메가폰이 인수한 회사」라 방향이 맞다"),
    ("HAS_EVENT", "사건", "로보티즈", "자사주 매각",
     "로보티즈는 최근 자기주식 17만4천161주를 매각했다.",
     "supported", "일이 벌어졌으면 사건이다"),
    ("IMPACTS", "영향", "현대자동차 부분파업", "현대자동차",
     "올해 임금협상에서 난항을 겪고 있는 현대자동차 노동조합이 이달 13일부터 15일까지 "
     "3일간 부분파업을 예고했다.",
     "supported", "당사자면 영향이 성립한다"),
    ("IMPACTS", "영향", "담합 혐의 피소", "SK하이닉스",
     "29일(현지시간) 미국 IT 매체 WCCFTECH에 따르면 미국 소비자 14명과 중소 PC조립·"
     "유통업체 3곳이 25일 캘리포니아 북부연방법원에 손해배상을 청구하는 집단소송을 제기했다. "
     "피고는 삼성전자, SK하이닉스, 마이크론이다.",
     "supported", "피소 자체가 영향이다"),
    ("OWNS_STAKE_IN", "최대주주", "SK스퀘어", "SK하이닉스",
     "SK스퀘어의 SK하이닉스 지분율은 20.5%이다.",
     "supported", "지분율 서술 = 지분 보유"),
    ("COMPETES_WITH", "DRAM", "삼성전자", "SK하이닉스",
     "삼성전자와 SK하이닉스는 HBM 시장을 사실상 양분하고 있다.",
     "supported", "같은 시장 양분 = 경쟁"),
    # ★표본검사에서 나온 거짓 음성 — 「점유율 비교」를 「수익률 비교」와 뭉뚱그려
    #   물리쳤었다. 같은 시장의 점유율은 경쟁의 정의 그 자체다.
    ("COMPETES_WITH", "NAND market", "삼성전자", "YMTC",
     "YMTC의 점유율이 1, 2위 업체인 삼성전자(약 29%)와 SK하이닉스(약 18%)에 여전히 "
     "크게 밀리지만 3, 4위권에서 각축을 벌이는 마이크론 또는 일본 키오시아와 유사한 "
     "수준에 이르게 됐다는 의미다. YMTC의 '치킨게임'이 공급과잉을 불러올 수 있다는 "
     "우려가 나온다.",
     "supported", "★같은 시장 점유율 비교 = 경쟁 (수익률 비교와 구분)"),
    # ★「A의 X」 소유격만으로는 의존이 아니다. 문장이 의존을 말하지 않으므로 unfounded.
    ("DEPENDS_ON", "의존", "삼성전자", "HBM3E",
     "삼성전자의 경우 아직 5세대 HBM인 HBM3E가 엔비디아의 품질 테스트를 통과조차 "
     "하지 못하고 있는 처지다.",
     "unfounded", "「A의 X」 소유격 — 가진 것이지 의존이 아니다"),
    # ★★위와 짝이다. **자기가 만드는 제품에 매출을 의존하는 것은 정상**이다.
    #   처음엔 「만들면서 의존은 모순」이라 보고 정의에서 배제하려 했는데, 실제
    #   12건을 읽어 보니 4건이 이 형태였다. 단일 제품 의존도는 리스크 지표라
    #   지우면 안 되는 정보다. 둘을 가르는 기준은 **매출의 대부분인가**이다.
    ("DEPENDS_ON", "매출의존", "LX세미콘", "DDI",
     "문제는 여전히 높은 DDI 의존도다. LX세미콘 매출의 약 90% 안팎이 DDI에서 "
     "발생하는 구조는 수년째 크게 달라지지 않고 있다.",
     "supported", "★자기 제품이어도 매출 90%면 의존이 맞다"),
    ("DEPENDS_ON", "의존", "ISC", "인터페이스 보드",
     "인터페이스 보드는 메모리반도체 테스트 장비에 들어가는 부품이다.",
     "unfounded", "제품이 무엇인지 설명하는 문장일 뿐"),
    ("ACQUIRES", "인수", "소프트뱅크 그룹", "엔비디아",
     "소프트뱅크 그룹은 2020년 9월 미국 반도체 기업 엔비디아에 ARM을 매각하려고 "
     "했지만 규제 당국의 반대로 무산됐다.",
     "unfounded", "★3자 관계 — 소프트뱅크가 엔비디아에 **ARM을** 팔려다 무산"),
    ("SUES", "특허침해", "넷리스트", "SK하이닉스",
     "넷리스트는 작년 3월 메모리 모듈 특허 침해 혐의로 SK하이닉스를 텍사스 서부지법에 제소했다.",
     "supported", "제소한 쪽 → 피소된 쪽"),
    ("PARTNERS_WITH", "협력", "심텍", "삼성전자",
     "심텍은 삼성전자와 공동으로 추진한 ‘Vision AI 기반 최종검사 자동화(ADC)’를 양산에 적용했다.",
     "supported", "공동 추진 = 협력"),
    ("REGULATES", "규제", "중국 정부", "CXMT",
     "중국 정부가 CXMT에 HBM 생산 확대를 계속 압박할 것으로 전망되는 만큼 2025년 기준 "
     "1%에 불과한 세계 공급 점유율이 2028년에는 12%까지 높아질 수 있다는 것이다.",
     "supported", "정부의 압박 = 규제·감독 행사"),
    ("SUPPLIES_TO", "공급", "제너셈", "SK하이닉스",
     "전날 제너셈은 공시를 통해 SK하이닉스와 반도체 후공정 장비 공급계약을 체결했다고 밝혔다.",
     "supported", "장비업체가 공급자"),

    # ── 물리쳐야 하는 것 ──────────────────────────────────────
    ("COMPETES_WITH", "경쟁", "SK하이닉스", "엔비디아",
     "SK하이닉스가 AI 반도체 수요 급증에 힘입어 1분기 매출 52조5762억원, 영업이익 "
     "37조6102억원을 각각 기록해 사상 최대 분기 실적을 올렸다. 특히 영업이익률은 71.5%로, "
     "지난해 4분기 엔비디아(67.7%)와 올해 1분기 TSMC(58.1%)의 기록을 앞질렀다.",
     "unfounded", "수익률 비교는 경쟁 근거가 아니다"),
    ("OWNS_STAKE_IN", "지분보유", "국민성장펀드", "심텍",
     "국민성장펀드는 충북 청주에 생산시설을 둔 반도체 패키지 기판 업체 심텍에 "
     "200억원을 지원하기로 했다.",
     "unfounded", "「지원」은 지분인지 대출인지 모른다"),
    ("SUES", "소송", "삼성전자", "심텍",
     "이 의혹과 관련, 지난 2월 초순 삼성전자와 SK하이닉스 등에선 심텍 등 여러 PCB "
     "협력사를 상대로 공장 검증(Audit)을 진행했다.",
     "unfounded", "감사는 소송이 아니다"),
    ("SUES", "소송", "한미반도체", "SK하이닉스",
     "한미반도체는 삼성을 대상으로 특허소송을 제기한 바 있다.",
     "unfounded", "피고가 다른 회사다"),
    ("DEVELOPS", "개발", "삼성전자", "Z 플립5",
     "갤럭시S23 시리즈는 물론 Z 폴드5, Z 플립5도 러시아에서 공식 판매하지 않았다.",
     "unfounded", "판매 이야기지 개발 이야기가 아니다"),
    ("SUPPLIES_TO", "협력", "SK하이닉스", "ASML",
     "ASML은 최근 2023년 4분기 실적발표회를 통해 EUV 노광기 수주량이 메모리반도체 "
     "업체를 중심으로 대폭 증가했다고 밝혔다. 삼성전자와 SK하이닉스가 첨단 D램 공정 "
     "전환에 속도를 내면서 필수 장비인 EUV 노광기 구매에 나선 결과로 풀이된다.",
     "wrong_type", "방향이 반대다 (사는 쪽이 source)"),
    ("SUPPLIES_TO", "공급", "하나머티리얼즈", "SK하이닉스",
     "하나머티리얼즈는 반도체용 실리콘 부품",
     "insufficient", "문장이 잘려 상대가 없다"),
]


def run_verifier(verbose: bool) -> tuple[int, int, int, int]:
    """(받아들여야 할 것 통과, 전체, 물리쳐야 할 것 통과, 전체)"""
    def one(case):
        edge, sub, a, b, ev, want, note = case
        row = {"a_name": a, "b_name": b, "edge": edge, "subtype": sub}
        return case, _verify((row, ev))[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, VERIFIER_CASES))

    n_acc = ok_acc = n_rej = ok_rej = 0
    for (edge, sub, a, b, ev, want, note), v in results:
        got = v.get("verdict")
        good = got == want
        accept = want == "supported"
        n_acc += accept; n_rej += not accept
        ok_acc += accept and good; ok_rej += (not accept) and good
        print(f"{'✓' if good else '✗'} {a[:12]:14}-[{edge:14}]-> {b[:14]:16}"
              f"{want:12} → {got or '?':12} {note}")
        if not good or verbose:
            print(f"      {v.get('reason','')[:110]}")
    return ok_acc, n_acc, ok_rej, n_rej


# ══════════════════════════════════════════════════════════════

# 한쪽 정확도가 이 아래로 떨어지면 **쏠린 것**으로 본다.
# 100%를 요구하지 않는 이유: gpt-4o-mini가 못 넘는 사례가 몇 개 있고(방향 판정),
# 그건 전용 검사(`audit.relations --scope direction`)가 따로 본다. 100%를 문턱으로
# 두면 매번 빨간불이라 아무도 안 보게 된다.
_SKEW_THRESHOLD = 0.8


def _report(label: str, ok_a: int, n_a: int, ok_r: int, n_r: int,
            pos: str, neg: str) -> tuple[bool, int]:
    """양쪽 정확도를 같이 보여준다. (쏠리지 않았나, 틀린 건수)"""
    print(f"\n{label}  {pos} {ok_a}/{n_a}  ·  {neg} {ok_r}/{n_r}")
    ok = True
    if n_a and ok_a / n_a < _SKEW_THRESHOLD:
        print(f"  ⚠ {pos}을 많이 놓칩니다 — 프롬프트가 **엄격한 쪽으로 쏠렸습니다.**")
        ok = False
    if n_r and ok_r / n_r < _SKEW_THRESHOLD:
        print(f"  ⚠ {neg}이 새고 있습니다 — 금지 규칙이 **안 먹습니다.**")
        ok = False
    return ok, (n_a - ok_a) + (n_r - ok_r)


def check_help() -> list[str]:
    """`--help`를 죽이는 도움말 문구를 **소스에서** 찾는다. 비용 0.

    ★2026-08-03. `run_companies --help`가 `ValueError`로 죽고 있었다. 원인은
      도움말 문구의 `%` 한 글자였다 — argparse가 help를 `% params`로 포매팅해서
      「생존율이 27%라」의 `%라`를 포맷 지정자로 읽는다. `grounding`의
      「커버율이 1%대」도 같았다. 도움말은 아무도 안 보는 자리라 몇 주가 지났다.

    ★모듈을 임포트해 파서를 찾는 방식은 **못 잡는다.** 이 저장소는 파서를
      전부 `main()` 안에서 만들기 때문에 모듈 전역에 파서가 없다. 그래서
      소스를 AST로 훑어 `add_argument(help=...)` 문자열만 본다 — 임포트가
      필요 없으니 DB도 API 키도 없이 돌아간다.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "add_argument"):
                continue
            for kw in node.keywords:
                if kw.arg != "help":
                    continue
                text = "".join(x.value for x in ast.walk(kw.value)
                               if isinstance(x, ast.Constant)
                               and isinstance(x.value, str))
                i = 0
                while (i := text.find("%", i)) >= 0:
                    # `%%`(이스케이프)와 `%(default)s`(argparse가 주는 값)는 정상
                    if text[i + 1:i + 2] == "%" or text[i:].startswith("%("):
                        i += 2
                        continue
                    rel = path.relative_to(root).as_posix()
                    bad.append(f"{rel}:{node.lineno}  …{text[max(0, i - 20):i + 4]}…"
                               f"  ← `%`는 `%%`로 쓰세요")
                    break
    return bad


def check_shadowed() -> list[str]:
    """같은 모듈에서 **상수 이름이 두 번 정의**되는 곳을 찾는다. 비용 0.

    ★2026-08-07. `node_identity.py`에 `_MERGE`가 두 번 있었다. 파이썬은 나중
      정의가 이기므로 앞쪽 호출부가 **덮인 쿼리**를 부르게 됐고, 파라미터 이름이
      달라 `finalize`가 5단계에서 통째로 멈췄다:

          ParameterMissing: Expected parameter(s): keep_id, drop_id

      두 쿼리는 병합 정책까지 달랐다(`discard` vs `combine`) — 조용히 다른
      동작을 했어도 이상했다. 로그만 봐서는 「이름 충돌」이 원인인 걸 못 본다.
    """
    import ast
    import pathlib
    from collections import Counter

    root = pathlib.Path(__file__).resolve().parents[2]
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        names: list[tuple[str, int]] = []
        for node in tree.body:                       # 모듈 최상위만 본다
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    # 상수 규약(대문자)만 — 재대입이 정상인 변수는 제외
                    if isinstance(t, ast.Name) and t.id.isupper():
                        names.append((t.id, node.lineno))
        for name, n in Counter(x[0] for x in names).items():
            if n > 1:
                lines = [ln for nm, ln in names if nm == name]
                bad.append(f"{path.relative_to(root).as_posix()}: "
                           f"{name} 이 {lines} 에 중복 정의 — 나중 것이 이깁니다")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["extractor", "verifier", "help"])
    ap.add_argument("-v", "--verbose", action="store_true", help="판정 사유까지")
    args = ap.parse_args()

    good, failed = True, 0
    if args.only in (None, "help"):
        bad = check_help()
        icon = "🔴" if bad else "✓"
        print(f"{icon} [도움말] 모든 배치의 --help 렌더: "
              f"{'실패 ' + str(len(bad)) + '건' if bad else '정상'} (0원)")
        for b in bad:
            print(f"     {b}")
        good &= not bad

        dup = check_shadowed()
        icon = "🔴" if dup else "✓"
        print(f"{icon} [이름충돌] 모듈 안에서 중복 정의된 상수: "
              f"{str(len(dup)) + '건' if dup else '없음'} (0원)")
        for d in dup:
            print(f"     {d}")
        good &= not dup

        if args.only == "help":
            return 0 if good else 1
        print()
    if args.only in (None, "extractor"):
        print("■ 추출기 — 금지 규칙이 먹는가\n")
        o, f = _report("추출기", *run_extractor(args.verbose),
                       pos="나와야 할 엣지", neg="★막아야 할 오추출")
        good &= o; failed += f
    if args.only in (None, "verifier"):
        print("\n\n■ 검증기 — 양쪽으로 쏠리지 않았는가\n")
        o, f = _report("검증기", *run_verifier(args.verbose),
                       pos="받아들여야 할 것", neg="물리쳐야 할 것")
        good &= o; failed += f

    # ★「전부 통과」라고 쓰면 안 된다 — 문턱만 넘었을 뿐 틀린 게 있을 수 있다.
    #   화면에 ✗가 보이는데 통과라고 하면 다음부터 이 줄을 안 믿는다.
    print("\n" + "─" * 84)
    if not good:
        print("⚠ 쏠림 발견 — 프롬프트를 다시 보세요")
    elif failed:
        print(f"쏠림 없음 · 다만 {failed}건은 여전히 틀립니다 "
              f"(문턱 {_SKEW_THRESHOLD:.0%} 이내라 통과로 봅니다 — 위 ✗ 확인)")
    else:
        print("전부 통과")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
