"""해외 기업 한글·영문 표기 통일.

한국 뉴스는 같은 회사를 매체·기사마다 다르게 쓴다:
    「Netlist」 / 「넷리스트」   「NVIDIA」 / 「엔비디아」   「Micron」 / 「마이크론」

해외 기업은 DART에 없어 **norm_name이 곧 노드 식별자**다. 표기가 갈리면 같은 회사가
별개 노드가 되고, 그 회사를 거쳐 가는 경로(브리지)가 끊긴다.
실측(2026-07-28): `Netlist`(연결 1) 와 `넷리스트`(연결 9)가 별개 노드였다.

**대표형 선택 원칙**
  · 한국 언론이 한글로 쓰는 것 → 한글 (엔비디아·마이크론·인텔·퀄컴)
  · 약어로만 통용되는 것       → 영문 대문자 (TSMC·ASML·CXMT·ASMPT)
  · 국내 기업의 영문 표기      → 한글 정식명 (SK Hynix → SK하이닉스)

★키는 `normalize_company_name`이 만드는 **정규화 결과**(소문자·공백/구두점 제거,
  법인격 제거 후)와 같은 형태여야 한다. 값은 대표형의 정규화 결과다.
"""

from __future__ import annotations

# 정규화키 → 대표 정규화키
FOREIGN_ALIASES: dict[str, str] = {
    # ── 반도체 메모리·로직 ──────────────────────────────
    "nvidia": "엔비디아",
    "micron": "마이크론",
    "microntechnology": "마이크론",
    "intel": "인텔",
    "amd": "amd",
    "qualcomm": "퀄컴",
    "broadcom": "브로드컴",
    "netlist": "넷리스트",
    "kioxia": "키옥시아",
    "westerndigital": "웨스턴디지털",
    "sandisk": "샌디스크",
    "texasinstruments": "텍사스인스트루먼트",
    "infineon": "인피니언",
    "stmicroelectronics": "st마이크로일렉트로닉스",
    "renesas": "르네사스",
    "mediatek": "미디어텍",
    "arm": "arm",
    "rambus": "램버스",
    "yangtzememorytechnologies": "ymtc",
    "changxinmemorytechnologies": "cxmt",
    "창신메모리": "cxmt",
    "창신메모리테크놀로지": "cxmt",

    # ── 반도체 장비·소재 ────────────────────────────────
    "appliedmaterials": "어플라이드머티어리얼즈",
    "어플라이드머티리얼즈": "어플라이드머티어리얼즈",
    "lamresearch": "램리서치",
    "tokyoelectron": "도쿄일렉트론",
    "도쿄일렉트론limited": "도쿄일렉트론",
    "asml": "asml",
    "kla": "kla",
    "screenholdings": "스크린홀딩스",
    "asmpt": "asmpt",
    "besi": "besi",
    "disco": "디스코",
    "shinetsu": "신에츠",
    "sumco": "sumco",

    # ── 파운드리·팹 ────────────────────────────────────
    "tsmc": "tsmc",
    "taiwansemiconductor": "tsmc",
    "globalfoundries": "글로벌파운드리",
    "umc": "umc",
    "smic": "smic",

    # ── 빅테크·플랫폼 ──────────────────────────────────
    "apple": "애플",
    "google": "구글",
    "alphabet": "구글",
    "microsoft": "마이크로소프트",
    "amazon": "아마존",
    "meta": "메타",
    "facebook": "메타",
    "tesla": "테슬라",
    "openai": "오픈ai",

    # ── 통신·전장 ──────────────────────────────────────
    "ericsson": "에릭슨",
    "nokia": "노키아",
    "huawei": "화웨이",
    "aptiv": "앱티브",
    "bosch": "보쉬",
    "continental": "콘티넨탈",
    "panasonic": "파나소닉",
    "파나소닉corporation": "파나소닉",
    "sony": "소니",
    "philips": "필립스",
    "miele": "밀레",

    # ── 국내 기업의 영문 표기 ──────────────────────────
    "samsungelectronics": "삼성전자",
    "skhynix": "sk하이닉스",
    "lgelectronics": "lg전자",
    "lginnotek": "lg이노텍",
    "hyundaimotor": "현대자동차",
    "hyundaimobis": "현대모비스",
    "hanmisemiconductor": "한미반도체",
    "yc": "와이씨",              # 사업보고서가 영문 약칭을 써서 별개 노드가 났다
    "semes": "세메스",
    "sfasemicon": "sfa반도체",
    "eotechnics": "이오테크닉스",
    "techwing": "테크윙",
    "doosantesna": "두산테스나",
    "doosanrobotics": "두산로보틱스",
    "rainbowrobotics": "레인보우로보틱스",
}


def apply_alias(norm_key: str) -> str:
    """정규화키를 대표형으로. 사전에 없으면 그대로 둔다."""
    return FOREIGN_ALIASES.get(norm_key, norm_key)
