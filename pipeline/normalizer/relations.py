"""관계 정규화 — L3 subtype 통일 + OTHER 매핑 (방법서 §2, §12-4).

3층 온톨로지에서 L2(엣지 12종)는 고정이지만 **L3 subtype은 개방형**이다. 개방형이
곧 자유형은 아니다 — 같은 뜻이 표기만 달라지면(`특허 크로스 라이선스` /
`특허크로스라이선스` / `Joint Venture` / `합작투자`) 집계·필터가 쪼개진다.
여기서 대표형으로 접는다.

하는 일 3가지:
  1. **subtype 정규화** — 표기 변형을 대표형으로. 빈 값은 엣지별 기본값으로.
  2. **OTHER 매핑** — 12종에 못 넣은 관계를 원문 표현으로 재판정.
  3. **미매핑 기록** — 버린 표현을 남겨, 렉시콘을 실측으로 키운다.

★설계 원칙: **추출기를 이기려 들지 않는다.**
   추출 LLM은 이미 12종 매트릭스를 프롬프트로 받고도 OTHER를 골랐다. 렉시콘이
   그 판단을 뒤집으려면 근거가 확실해야 한다. 그래서 매핑은 **행위가 명시된
   표현만**(지분 취득·합작법인 설립·특허 침해 소송) 받고, 애매하면 버린다.
   버린 것은 unmapped_relations에 쌓여 다음 렉시콘 개정의 근거가 된다.

★방향을 바꾸는 재배정은 하지 않는다.
   PARTNERS_WITH(대칭)를 SUPPLIES_TO(방향)로 옮기려면 "누가 누구에게 공급하는지"를
   알아야 하는데 subtype 문자열엔 그 정보가 없다. 잘못된 방향의 엣지는 없는 엣지보다
   나쁘므로, 이런 오분류는 여기서 고치지 않고 **추출 프롬프트에서** 잡는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── L3 subtype 대표형 ────────────────────────────────────────────
# (대표형, 변형 패턴) — 패턴은 공백 제거·소문자화 후 부분일치로 본다.
_SUBTYPE_CANON: dict[str, tuple[tuple[str, ...], ...]] = {
    # ⚠ '출자'·'자회사'는 investment_normalizer가 타법인 출자현황에서 **지분율로
    #   구분해 붙인 의미 있는 값**이다. 여기서 접으면 그 구분이 사라진다 → 건드리지 않는다.
    # ★영문 표기를 함께 접는다(표본 심층검사 2026-08-02). LLM이 영문 기사에서
    #   뽑으면 subtype도 영문으로 온다. 「지분보유」와 `investment`가 갈려 있으면
    #   지분 관계 필터가 쪼개진다. 실측: investment 10 · minority 10 · acquisition 5.
    #   `majority`·`<50%` 같은 **지분율 표현은 subtype이 아니다** — 비율은 이미
    #   `ratio` 필드에 있으므로 기본형으로 접는다.
    "OWNS_STAKE_IN": (
        ("최대주주", ("최대주주", "largestshareholder", "controllingshareholder")),
        ("특수관계인", ("특수관계", "친인척", "relatedparty")),
        ("자회사", ("subsidiary",)),
        ("지분투자", ("지분투자", "주식취득", "지분취득", "주식매입",
                     "investment", "acquisition", "stakeacquisition", "equityinvestment")),
        ("자기주식", ("자기주식", "자사주", "treasurystock")),
        # 지분율만 말하는 표현 — 대표형(지분보유)으로 접는다
        ("지분보유", ("majority", "minority", "partial", "ownership", "joint",
                     "indirect", "100%", "<50%", ">50%")),
    ),
    # OEM·ODM은 일반 공급과 **분석상 의미가 다르다**(고객 설계·브랜드로 위탁생산).
    # 순서가 우선순위이므로 포괄어(공급계약)보다 앞에 둔다.
    # ★「고객사」「매출처」류를 넣은 이유 — 기사가 공급 관계를 **받는 쪽에서**
    #   서술하면 subtype이 「주요 매출처」「고객사」로 온다. 대표형으로 접지 않으면
    #   「공급」과 갈려 공급망 필터에서 빠진다. 영문 표기도 같이 접는다.
    "SUPPLIES_TO": (
        ("OEM공급", ("oem",)),
        ("ODM공급", ("odm",)),
        ("공급계약", ("공급계약", "납품계약", "구매계약", "물품거래", "거래기본",
                     "단일판매", "supplyagreement", "supplycontract")),
        ("수주", ("수주", "발주", "낙찰", "purchaseorder")),
        ("공급", ("납품", "공급", "고객사", "거래처", "매출처", "수요처", "납품처",
                 "벤더", "협력사", "supplier", "vendor", "customer", "client")),
    ),
    "PARTNERS_WITH": (
        ("합작투자", ("합작", "jointventure", "jv", "조인트벤처", "jointinvestment")),
        ("특허크로스라이선스", ("크로스라이선스", "crosslicense", "특허크로스")),
        ("특허라이선스", ("특허라이선스", "특허실시", "patentlicense")),
        ("기술이전", ("기술이전", "기술제공", "기술도입", "기술공여", "techtransfer")),
        ("공동개발", ("공동개발", "공동연구", "공동기술",
                     "jointdevelopment", "joint_development", "jointresearch",
                     "joint_research", "productdevelopment", "product_development",
                     "jointpatentapplication", "joint_patent_application")),
        ("업무협약", ("mou", "업무협약", "양해각서", "협약체결")),
        ("기술제휴", ("기술제휴", "제휴", "파트너십", "협력계약",
                     "strategicpartnership", "partnership")),
        ("협력", ("collaboration", "cooperation")),
    ),
    "ACQUIRES": (
        ("합병", ("합병", "merger")),
        ("주식취득", ("주식취득", "지분인수", "주식양수", "경영권인수", "지분매입",
                     "acquisition", "acquisitionprocess", "fullacquisition",
                     "partial", "subsidiary")),
        ("영업양수", ("영업양수", "사업양수", "자산양수", "assetacquisition")),
    ),
    # LLM이 영문 기사에서 뽑으면 subtype도 영문으로 온다(`patent infringement`).
    # 한글 대표형과 갈리면 집계가 쪼개지므로 함께 접는다.
    "SUES": (
        ("특허침해", ("특허침해", "특허소송", "지식재산권침해",
                     "patentinfringement", "ipinfringement")),
        ("손해배상", ("손해배상", "배상청구", "damages")),
        ("가처분", ("가처분", "금지청구", "injunction")),
        ("특허무효", ("특허무효", "무효심판", "patentinvalidation", "invalidation")),
        ("상표침해", ("상표침해", "trademarkinfringement")),
        ("집단소송", ("집단소송", "classaction")),
        ("소송", ("소송", "제소", "고소", "고발", "중재",
                 "lawsuit", "litigation", "complaint", "arbitration",
                 "intellectualproperty", "declaratoryjudgment", "noncompete")),
    ),
    # 구체형을 먼저 둔다 — '점유율경쟁'이 '경쟁'에 먼저 걸리면 대표형이 자기 자신으로
    # 안 돌아와 멱등성이 깨진다(import 시 고정점 검사가 잡아낸다).
    "COMPETES_WITH": (
        ("점유율경쟁", ("점유율", "marketshare")),
        ("경쟁", ("경쟁", "competition", "competitor", "rivalry")),
    ),
    "REGULATES": (
        ("제재", ("제재", "과징금", "시정명령", "처분", "sanction", "penalty", "fine")),
        ("조사", ("조사", "심사", "수사", "investigation", "probe")),
        ("판정", ("판정", "판결", "결정", "ruling", "determination", "verdict")),
        ("인허가", ("인허가", "승인", "허가", "인증", "approval", "clearance")),
        ("수출규제", ("수출규제", "수출통제", "exportcontrol", "exportrestriction")),
    ),
    # ★**영문 일반어만** 접는다. 한글 표현은 건드리지 않는다 — L3는 개방형이고,
    #   「양산 검증 완료」「로봇기술개발 선행 연구」는 그 자체가 정보다. 이걸
    #   「생산」·「개발」로 뭉개면 개방형을 둔 이유가 사라진다.
    #   (첫 시도에서 한글 패턴을 넣었다가 실측 dry-run이 잡아냈다)
    "DEVELOPS": (
        ("공동개발", ("jointdevelopment", "joint_development",
                     "collaboration", "cooperation")),
        ("개발", ("development", "technologydevelopment", "research",
                 "randd", "r&d")),
        ("생산", ("production", "manufacturing")),
        ("출시", ("launch", "release")),
    ),
    # 직위 — 영문만 한글 대표형으로. **한글 직위는 그대로 둔다.**
    #
    # ★부분 문자열 매칭이라 「사장」 패턴이 **「부사장」에도 걸린다.** 첫 시도에서
    #   `("사장", ("사장", "president"))`를 넣었더니 dry-run이 **부사장 99건 →
    #   사장**을 잡아냈다. 부사장과 사장은 다른 직위다. 한글을 빼서 막는다.
    #   (같은 이유로 「회장」을 넣으면 「부회장」이 걸린다)
    "IS_EXECUTIVE_OF": (
        ("대표이사", ("ceo", "chiefexecutive", "representativedirector")),
        ("사장", ("president",)),
        ("사내이사", ("internaldirector",)),
        ("사외이사", ("outsidedirector", "independentdirector")),
        ("최대주주", ("owner", "founder")),
    ),
    "DEPENDS_ON": (
        ("라이선스", ("license", "라이선스", "실시권")),
        ("기술의존", ("기술", "공정")),
        ("부품의존", ("부품", "소재", "원자재")),
    ),
}

# 빈 subtype일 때 엣지별 기본값 — 뉴스 추출은 subtype을 자주 비워 둔다.
# ""로 두면 L3 집계에서 통째로 빠지므로 엣지 뜻을 그대로 쓴다.
_DEFAULT_SUBTYPE = {
    "OWNS_STAKE_IN": "지분보유",
    "IS_EXECUTIVE_OF": "임원",
    "SUPPLIES_TO": "공급",
    "PARTNERS_WITH": "협력",
    "ACQUIRES": "인수",
    "SUES": "소송",
    "COMPETES_WITH": "경쟁",
    "REGULATES": "규제",
    "DEVELOPS": "개발",
    "DEPENDS_ON": "의존",
    "HAS_EVENT": "사건",
    "IMPACTS": "영향",
}

# ── OTHER → 12종 매핑 렉시콘 ─────────────────────────────────────
# **행위가 명시된 표현만** 받는다. "협력 기대", "실적 개선" 같은 상태·전망은
# 관계가 아니므로 여기 없고, 결과적으로 버려진다(의도된 동작).
# 순서가 곧 우선순위 — 위에서부터 먼저 맞는 것을 쓴다.
_OTHER_LEXICON: tuple[tuple[str, str, str], ...] = (
    # (정규식, 엣지, subtype)
    # 기사는 "지분 20% 취득"처럼 수량을 끼워 넣는다 → 숫자·단위만 사이에 허용
    (r"(?:지분|주식)\s*(?:[\d.,]+\s*(?:%|퍼센트|주|만주|억원)?\s*)?"
     r"(?:투자|취득|매입|인수|확보)|출자", "OWNS_STAKE_IN", "지분투자"),
    (r"합작\s*(?:법인|회사)?|조인트\s*벤처|joint\s*venture", "PARTNERS_WITH", "합작투자"),
    (r"양해\s*각서|업무\s*협약|\bMOU\b", "PARTNERS_WITH", "업무협약"),
    (r"공동\s*(?:개발|연구)|기술\s*(?:제휴|이전|공여)", "PARTNERS_WITH", "공동개발"),
    (r"(?:피)?인수\s*합병|경영권\s*(?:인수|양수)|흡수\s*합병", "ACQUIRES", "합병"),
    # 기사는 "특허 4개를 침해"처럼 목적어를 끼워 넣는다 → 사이에 짧은 구절을 허용.
    # (unmapped_relations 실측에서 발견 — 「콜리전 커뮤니케이션스의 특허 4개를 침해」)
    (r"(?:특허|지식\s*재산권?|상표권|저작권)\s*(?:\S{1,12}\s*){0,2}침해", "SUES", "특허침해"),
    (r"소송\s*(?:제기|을\s*제기)|제소|손해\s*배상\s*청구|가처분\s*신청", "SUES", "소송"),
    (r"납품\s*계약|공급\s*계약|수주\s*계약|양산\s*공급", "SUPPLIES_TO", "공급계약"),
    (r"과징금|시정\s*명령|영업\s*정지|제재\s*(?:조치|부과)", "REGULATES", "제재"),
)
_OTHER_PATTERNS = tuple((re.compile(p, re.IGNORECASE), e, s) for p, e, s in _OTHER_LEXICON)

# OTHER를 되살린 엣지는 추출기 판단을 뒤집은 것이라 확신도를 깎는다.
_REMAP_CONFIDENCE_FACTOR = 0.9


# ★규제·판정 기관 — 이들이 주체면 SUES가 아니라 REGULATES다.
# 실측: 「미국 국제무역위원회 -SUES-> SK하이닉스」가 나왔는데, ITC는 소송 당사자가
# 아니라 침해 여부를 **판정하는 기관**이다. 소송 상대로 두면 분쟁 구도가 뒤집힌다.
_REGULATOR_MARKERS = (
    "위원회", "공정위", "금감원", "금융감독원", "국세청", "관세청", "경찰",
    # 검찰 조직 표기 — 「서울남부지검」처럼 '검찰'이 안 들어가는 형태가 많다
    "검찰", "지검", "고검", "대검", "검찰청", "수사부", "수사대", "특수부",
    "법원", "지법", "고법", "대법원", "산업부", "환경부", "고용노동부",
    "식약처", "방통위", "특허심판원", "무역위원회",
    "commission", "itc", "ftc", "sec", "doj", "부처", "청장", "당국",
)
# 소송이 아닌데 SUES로 오는 표현 — 「요구」·「촉구」는 분쟁이지 소송이 아니다
_NOT_LITIGATION = ("요구", "촉구", "항의", "규탄", "성명", "호소", "청원")


def reclassify_sues(source_name: str, subtype: str) -> Optional[str]:
    """SUES로 온 관계를 다시 본다. 바꿔야 하면 새 엣지 타입, 아니면 None.

    · 주체가 규제·판정 기관   → REGULATES
    · 소송이 아닌 의사표시     → 폐기(None 반환 대신 호출측에서 판단하도록 "DROP")
    """
    name = (source_name or "").lower()
    if any(m in name for m in _REGULATOR_MARKERS):
        return "REGULATES"
    if any(m in (subtype or "") for m in _NOT_LITIGATION):
        return "DROP"
    return None


@dataclass
class NormalizedRelation:
    edge_type: str
    subtype: str
    confidence: float
    remapped: bool          # OTHER에서 되살렸는지


def _compact(text: str) -> str:
    """공백·구두점 제거 + 소문자 — 표기 변형 대조용."""
    return re.sub(r"[\s\-_·,.]+", "", (text or "")).lower()


def _check_fixed_points() -> list[str]:
    """대표형·기본값이 **자기 자신으로 되돌아오는지** 검사한다.

    정규화는 멱등해야 한다 — 두 번 돌렸을 때 값이 달라지면 소급 정규화가
    실행할 때마다 그래프를 흔든다. 실제로 이 검사가 없던 동안
    `SUPPLIES_TO` 기본값 `공급` → `납품`으로 접히는 버그가 있었다
    (기본값이 다른 대표형의 변형 목록에 걸려 있었다).
    """
    problems: list[str] = []
    for edge_type, groups in _SUBTYPE_CANON.items():
        for canon, _ in groups:
            again = canonical_subtype(edge_type, canon)
            if again != canon:
                problems.append(f"{edge_type}: 대표형 '{canon}' → '{again}'")
    for edge_type, default in _DEFAULT_SUBTYPE.items():
        again = canonical_subtype(edge_type, default)
        if again != default:
            problems.append(f"{edge_type}: 기본값 '{default}' → '{again}'")
    return problems


# 「분류하지 못했다」는 뜻의 값들. 빈 값과 **똑같이** 다뤄 기본 subtype으로 접는다.
# ★`OTHER`가 그대로 남아 화면에 뜨던 문제. 실측: 한 문장에서 나온 두 엣지가
#   「심텍→SK하이닉스 = 공급」인데 「심텍→삼성전자 = OTHER」로 갈렸다. 같은 근거인데
#   한쪽만 미분류로 남으면 필터·집계가 쪼개지고, 사용자에겐 뜻 없는 라벨이 보인다.
#
# ★표본 심층검사(2026-08-02)에서 영문 쓰레기값이 추가로 드러났다. `Event`는 타입
#   이름이 그대로 들어온 것이고, `/`는 파싱 부스러기다. `test`·`build`·`synergy`·
#   `potential`은 관계를 설명하지 못한다 — 화면에 「두산로보틱스 -협력/synergy->」가
#   뜨면 사용자는 아무것도 알 수 없다.
_UNKNOWN_SUBTYPE = {
    "other", "미상", "불명", "n/a", "na", "none", "null", "기타", "?",
    "event", "/", "test", "build", "synergy", "potential", "general",
    "unknown", "misc", "etc",
}


def canonical_subtype(edge_type: str, subtype: Optional[str]) -> str:
    """subtype을 대표형으로. 매칭 없으면 원본 유지(L3는 개방형이므로 버리지 않는다)."""
    raw = (subtype or "").strip().strip(".·-")
    if not raw or raw.lower() in _UNKNOWN_SUBTYPE:
        return _DEFAULT_SUBTYPE.get(edge_type, "")

    compact = _compact(raw)
    for canon, variants in _SUBTYPE_CANON.get(edge_type, ()):
        if any(v in compact for v in variants):
            return canon
    return raw          # 개방형 — 모르는 표현도 그대로 살린다


def canonical_forms(edge_type: str) -> frozenset[str]:
    """이 엣지 타입에 **명시 등재된 대표형** + 기본값.

    레지스트리 자동 정리가 이들을 서로 합치지 못하게 보호한다.
    「OEM공급」·「ODM공급」·「공급」처럼 **일부러 나눈 구분**을 빈도 기반 정리가
    덮어버리는 것을 막는다.
    """
    forms = {canon for canon, _ in _SUBTYPE_CANON.get(edge_type, ())}
    default = _DEFAULT_SUBTYPE.get(edge_type)
    if default:
        forms.add(default)
    return frozenset(forms)


def pick_representative(edge_type: str, subtypes: list[str]) -> str:
    """여러 subtype 중 **대표**를 고른다 (엣지 클러스터링용).

    같은 관계가 표현만 달리해 여러 엣지였던 것을 하나로 접을 때, 무엇을 대표로
    보여줄지 정한다. 길이나 빈도로 고르면 엉뚱한 게 뽑힌다 —
    실측: 「인수」가 26번 나왔는데 한 번뿐인 「acquisition process」가 대표가 됐다.

    우선순위:
      ① `_SUBTYPE_CANON`에 등재된 대표형 (사전 순서 = 구체성 순서)
      ② 한글 표기 (영문 잔재보다 읽기 좋다)
      ③ 짧은 것 (군더더기 없는 표현)
    """
    if not subtypes:
        return _DEFAULT_SUBTYPE.get(edge_type, "")
    order = {canon: i for i, (canon, _) in enumerate(_SUBTYPE_CANON.get(edge_type, ()))}

    def rank(s: str) -> tuple:
        return (
            order.get(s, len(order) + 1),                    # ① 사전 순서
            0 if re.search(r"[가-힣]", s) else 1,             # ② 한글 우선
            len(s),                                          # ③ 짧은 것
        )

    return min(subtypes, key=rank)


def map_other(raw_expression: Optional[str], evidence: str = "") -> Optional[tuple[str, str]]:
    """OTHER 관계를 12종 중 하나로. 확신이 없으면 None(→ 폐기 후 기록).

    raw_expression을 먼저 보고, 비어 있으면 근거 문장에서 찾는다.
    """
    for source in (raw_expression or "", evidence or ""):
        if not source:
            continue
        for pattern, edge_type, subtype in _OTHER_PATTERNS:
            if pattern.search(source):
                return edge_type, subtype
    return None


def normalize(
    edge_type: str, subtype: Optional[str], confidence: float,
    *, raw_expression: Optional[str] = None, evidence: str = "",
) -> Optional[NormalizedRelation]:
    """관계 하나를 정규화한다. 적재할 수 없으면 None."""
    if edge_type == "OTHER":
        mapped = map_other(raw_expression, evidence)
        if mapped is None:
            return None
        edge_type, subtype = mapped
        return NormalizedRelation(
            edge_type, subtype,
            round(confidence * _REMAP_CONFIDENCE_FACTOR, 3), remapped=True,
        )

    return NormalizedRelation(
        edge_type, canonical_subtype(edge_type, subtype), confidence, remapped=False,
    )


# ── 미매핑 기록 — 렉시콘을 실측으로 키우는 장치 ──────────────────
_RECORD_SQL = """
INSERT INTO unmapped_relations (expression, source_name, target_name, evidence,
                                source_doc, seen_count, last_seen_at)
VALUES (%(expression)s, %(source_name)s, %(target_name)s, %(evidence)s,
        %(source_doc)s, 1, now())
ON CONFLICT (expression) DO UPDATE SET
    seen_count = unmapped_relations.seen_count + 1,
    last_seen_at = now(),
    evidence = COALESCE(NULLIF(unmapped_relations.evidence, ''), EXCLUDED.evidence)
"""


def record_unmapped(conn, *, expression: str, source_name: str, target_name: str,
                    evidence: str, source_doc: str) -> None:
    """매핑 못 한 OTHER 표현을 누적한다. 자주 나오는 것부터 렉시콘에 추가하면 된다."""
    expr = (expression or "").strip()[:200]
    if not expr:
        return
    with conn.cursor() as cur:
        cur.execute(_RECORD_SQL, {
            "expression": expr, "source_name": source_name[:120],
            "target_name": target_name[:120], "evidence": (evidence or "")[:500],
            "source_doc": source_doc[:500],
        })


# 렉시콘을 고칠 때 실수로 멱등성을 깨뜨리지 않도록 import 시점에 자체 검사한다.
# (비용은 수십 회 문자열 비교라 무시할 수준)
_FIXED_POINT_PROBLEMS = _check_fixed_points()
if _FIXED_POINT_PROBLEMS:
    raise RuntimeError(
        "관계 정규화 렉시콘이 멱등하지 않습니다 — 대표형/기본값이 다시 접힙니다:\n  "
        + "\n  ".join(_FIXED_POINT_PROBLEMS)
    )
