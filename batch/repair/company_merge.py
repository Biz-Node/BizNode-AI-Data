"""별칭으로 갈라진 Company stub 을 **정본(corp_code)** 으로 접는다 — 사람이 확인한 것만.

왜 자동으로 못 접나 (2026-09-13 · 현황서 A-10)

「현대차」와 「현대자동차」는 같은 회사인데 노드가 둘이다. `node_identity`는
`normalize_company_name`이 같아진 stub 만 합치므로(「현대차」≠「현대자동차」)
대상이 아니었고, `foreign_merge`는 후보를 `corp_code IS NULL` 로만 뽑아
정본 쪽이 후보에 못 들어갔다. 그래서 stub 마다 「노드 자신 — 열쇠 보관」
`first_seen` 행만 남았다.

실측 — `block_key` 가 같은 stub↔정본 쌍 71개를 열어 보니 셋으로 갈렸다

  ① 같은 법인 · 표기만 다름          24   엣지 215 · 사건 14   ← 여기서 접는다
       현대차 → 현대자동차 · 네이버 → NAVER · 기아차 → 기아 · 코레일 → 한국철도공사
  ② 자회사·해외법인·조합·JV        41   엣지  55 · 사건  2   ← 접으면 안 된다
       KT AMERICA → 케이티 · SK hynix Taiwan → SK하이닉스 · 삼성전자 호주법인 → 삼성전자
  ③ 판단 못 함                       4

  ②가 걸린 이유: 모델이 `canon_name` 을 물을 때 자회사에 모회사 정식명을 답했다
  (「KT AMERICA, INC.」→ "KT Corporation"). `company_registry` 가 「열쇠는 헐겁게 —
  잘못 묶여도 쌍 판정이 거른다」고 못 박은 그대로, **열쇠 일치는 판정이 아니다.**
  그래서 배치로 접지 않고 `person_merge` 처럼 손 목록으로 간다.

★표에 쓰는 대표형은 corp_code 가 아니라 정본의 **norm_name** 이다.
  `apply_alias` 의 반환값이 `normalize_company_name` 의 반환값이 되고, 그것이
  ⑴ resolver 의 `corp_code_master` 정확 매칭 키(현대자동차 → 00164742)이고
  ⑵ 검색 fuzzy 경로의 `similarity(corp_name, norm_q)` 비교값이다 — 8자리 코드를
  넣으면 ⑵가 0 이 된다. 기존 hand·dart 행 1,028개도 전부 norm_name 이다.
  corp_code 는 「어느 노드가 정본인가」를 고정하는 기준으로 목록에 남긴다.

두 단계로 접는다 — `node_identity` 와 같은 `--only` 구조다

  1단  `company_aliases` 에 hand 행. 그 뒤로 새로 들어오는 「현대차」는
       `normalize_company_name` 끝의 `apply_alias` 에서 자동으로 「현대자동차」가 된다.
  2단  **이미 갈라진 노드**를 정본으로 옮긴다. 한 stub 이 한 단위다:
         Neo4j  mergeNodes(정본, stub) → 관계·사건이 전부 정본으로 옮겨진다
         PG     `staged_edges` 의 stub 키 → corp_code (안 하면 다음 적재 때 옛 노드가
                되살아난다 — `node_identity` 가 적어 둔 그 문제)
                `company_attributes` 의 stub 행 삭제 (`pg_tidy` 4번 · 노드 없는 고아 행)
       stub 마다 Neo4j 먼저, PG 는 그 stub 분만 커밋한다. 중간에 죽으면 다시 돌리면
       된다 — 정본의 `merged_keys` 를 보고 PG 만 마저 처리한다.

★2단은 `mergeRels: false` 다 — 다른 병합 배치(`node_identity`·`person_merge`)와 다르다.
  실측(2026-09-13): stub 관계 195건 중 36건이 정본과 「같은 유형·방향·상대」인데
  **subtype·evidence 가 전부 다르다**(완전중복 0). 현대로템 →SUPPLIES_TO→ 코레일 이
  stub 쪽은 '전동차', 정본 쪽은 '광역철도 전동차'. `mergeRels: true` 는 이 둘을
  하나로 뭉개 한쪽을 소리 없이 버린다. 이 그래프에서 그런 중복은 `batch/repair/edges.py`
  3단이 **자기 규칙**(대표 subtype 선정 · `subtypes` 배열에 전부 보존)으로 접는다.
  그래서 관계는 그대로 옮기고 정리는 그쪽에 맡긴다. 2단 뒤에 한 번 돌리면 된다:
      python -m batch.repair.edges --dry-run

★정본이 stub 을 **소유**하면 접지 않는다. 실측: 「FADU Technology Incorporated」는
  열쇠·정식명이 파두와 같아 같은 법인으로 봤는데, 그래프에
  `파두 -OWNS_STAKE_IN(자회사)-> FADU Technology Incorporated` 가 있었다 — 미국 자회사다.
  정본과 직접 관계가 있으면 다른 법인일 증거이므로 2단이 거부한다.

    python -m batch.repair.company_merge --dry-run
    python -m batch.repair.company_merge                  # 1단 → 2단
    python -m batch.repair.company_merge --only aliases
    python -m batch.repair.company_merge --only merge
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.core.database import neo4j_session, postgres_connection
from pipeline.normalizer import resolver
from pipeline.normalizer.company_registry import ensure, record

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 사람이 근거를 읽고 확인한 쌍만 적는다. **열쇠가 같다고 늘리지 말 것** —
# 자회사·해외법인이 같은 열쇠로 걸린다(아래 REVIEWED_NOT_MERGED 「다른 법인」 42건).
# 키는 stub 노드의 `norm_name`(= `company_aliases.alias_key`)이다.
CONFIRMED: list[tuple[str, str, str]] = [
    # (남길 정본 corp_code, 합칠 stub 키, 왜 같은 법인인가)
    ("00164742", "현대차",
     "KRX 종목명·언론 약칭. DART 영문명 HYUNDAI MOTOR CO ↔ 정식명 Hyundai Motor Company · "
     "손 목록 hyundaimotor → 현대자동차 와 같은 회사"),
    ("00266961", "네이버",
     "한글 표기. DART 등록명 NAVER · 정식명 NAVER Corporation 이 name_en 과 일치"),
    ("01484245", "hd현대로보틱스",
     "㈜ + 영문 HD 표기 ↔ DART 한글 등록명 에이치디현대로보틱스 (HDHyundai Robotics)"),
    ("00139214", "삼성화재",
     "약칭 ↔ 정식명 삼성화재해상보험 (SAMSUNG FIRE & MARINE INSURANCE)"),
    ("00579999", "고영테크놀러지",
     "옛 사명 — 상장사 고영(098460)의 영문명이 KohYoungTechnologyInc. · "
     "고영홀딩스(01233748)도 열쇠에 걸리지만 지주사라 다르다"),
    ("00139834", "lgcns",
     "영문 사명 ↔ DART 한글 등록명 LG씨엔에스 (LG CNS Co., Ltd.)"),
    ("01265516", "sk하이닉스시스템ic",
     "영문 혼용 ↔ DART 한글 등록명 에스케이하이닉스시스템아이씨 (SK hynix system ic)"),
    ("01628946", "kt클라우드",
     "영문 혼용 ↔ DART 한글 등록명 케이티클라우드 (kt cloud)"),
    ("01135941", "원익아이피에스",
     "㈜ + 한글 음차 ↔ DART 등록명 원익IPS (WONIK IPS)"),
    ("00106641", "기아차",
     "옛 사명 기아자동차의 약칭 — 2021년 「기아」로 사명 변경 (KIA CORPORATION)"),
    ("00585918", "코레일",
     "한국철도공사의 공식 브랜드명 (Korea Railroad Corp.)"),
    ("00219352", "현대ihl",
     "영문 혼용 ↔ DART 한글 등록명 현대아이에이치엘 (HYUNDAI IHL)"),
    ("00959070", "ktnf",
     "영문 약자 ↔ DART 한글 등록명 케이티엔에프 (KTNF Co., Ltd.)"),
    ("01010615", "sena",
     "영문 브랜드 ↔ 세나테크놀로지 (Sena Technologies Co., Ltd.)"),
    ("00468374", "원익큐엔씨",
     "㈜ + 한글 음차 ↔ DART 등록명 원익QnC (WONIK QnC)"),
    ("00486097", "케이티나스미디어",
     "㈜ + 한글 음차 ↔ DART 등록명 KT나스미디어 (kt nasmedia)"),
    ("01343665", "알에프머트리얼즈",
     "㈜ + 한글 음차 ↔ DART 등록명 RF머트리얼즈 (RF Materials)"),
    ("01286476", "알에프시스템즈",
     "㈜ + 한글 음차 ↔ DART 등록명 RF시스템즈 (RF Systems)"),
    ("00105873", "엘지디스플레이",
     "㈜ + 한글 음차 ↔ DART 등록명 LG디스플레이 (LG Display)"),
    ("00105961", "엘지이노텍",
     "㈜ + 한글 음차 ↔ DART 등록명 LG이노텍 (LG INNOTEK) · 손 목록 lginnotek → lg이노텍 과 같은 회사"),
    ("01847329", "웹툰엔터테인먼트",
     "한글 표기 ↔ DART 등록명 WEBTOONENTERTAINMENTINC. (WEBTOON ENTERTAINMENT INC.)"),
]


# ★같이 걸렸지만 **접지 않기로 한** stub — 주석이 아니라 **코드**로 둔다.
#   person_merge 와 같은 이유다: 판단을 끝낸 것이 감사·후보 목록에 다시 뜨면
#   「봐도 할 게 없는 경고」가 되어 진짜 후보를 흘려보낸다. 판단이 바뀌면 빼면 된다.
#
#   「다른 법인」  — 같은 열쇠지만 별개 법인. 접으면 두 회사의 관계가 한 노드에 섞인다.
#   「보류」      — 같은 법인이지만 지금 접으면 다른 것이 깨진다. 선행 조건을 적었다.
#   「확인 필요」 — 같은 법인인지 근거로 못 갈랐다.
REVIEWED_NOT_MERGED: dict[str, str] = {
    # ── 보류 · 같은 법인이지만 선행 조건이 있다 ──────────────────
    "퀄컴": "보류 · 손 목록에 qualcomm → 퀄컴 이 있어 퀄컴이 대표형이다. 여기서 "
           "퀄컴 → qualcommincorporated(00404358) 를 넣으면 apply_alias 가 한 홉만 타서 "
           "Qualcomm 과 퀄컴이 갈린다 — dart_aliases 가 「실측: 퀄컴」으로 막은 순환. "
           "뒤집으려면 qualcomm 행을 같이 바꿔야 한다.",
    "원익pne": "보류 · corp_code_master 에 원익피앤이가 두 법인(00785475 · 01020843)이라 "
              "resolver 가 그래프 노드(00785475)와 다른 쪽을 고른다. 별칭은 맞지만 정본이 "
              "유일하지 않다 — ambiguous_corps 에서 먼저 가려야 한다.",

    # ── 확인 필요 · 같은 법인인지 근거로 못 갈랐다 ───────────────
    "kakaog": "확인 필요 · Kakao G Corp. 가 카카오게임즈(01137383)인지 근거 없음.",
    "komicotechnology": "확인 필요 · KoMiCo Technology Inc. 는 코미코(00997812)의 미국 "
                        "자회사일 가능성 — DART 영문명은 KoMiCo Ltd.",
    "삼성전자평택캠퍼스": "확인 필요 · 법인이 아니라 삼성전자의 사업장. 사업장을 본사로 "
                    "접을지는 정한 적이 없다.",
    "일본도쿄일렉트론": "확인 필요 · 정본 도쿄일렉트론(01230264)이 일본 본사인지 한국법인인지 "
                   "안 가렸다. 손 목록 tokyoelectron → 도쿄일렉트론 도 걸려 있다.",

    # ── 다른 법인 · 자회사 ─────────────────────────────────────
    "fadutechnology": "다른 법인 · 파두(01292291)의 미국 자회사. 열쇠·정식명이 같아 같은 "
                      "법인으로 봤다가 그래프의 파두 -OWNS_STAKE_IN(자회사)-> 엣지로 "
                      "가렸다(2026-09-13). 1단에서 썼던 hand 행은 되돌렸다.",
    "포스코홀딩스": "다른 법인 · 지주사. 열쇠가 포스코(01620971·사업회사)에 걸렸지만 "
               "포스코홀딩스(00155319) 노드가 없어 stub 이 맞다.",
    "ls알스코": "다른 법인 · LS전선(00683283)의 자회사.",
    "sk하이스텍": "다른 법인 · SK하이닉스(00164779)의 자회사.",
    "sk텔레시스": "다른 법인 · SK텔레콤(00159023)의 자회사.",

    # ── 다른 법인 · 해외법인 (모델이 canon_name 에 모회사 정식명을 답했다) ──
    "삼성전자아메리카": "다른 법인 · 삼성전자(00126380)의 해외법인.",
    "삼성전자호주법인": "다른 법인 · 삼성전자(00126380)의 해외법인.",
    "삼성전자중국법인": "다른 법인 · 삼성전자(00126380)의 해외법인.",
    "ktamerica": "다른 법인 · 케이티(00190321)의 해외법인.",
    "ktdxvietnam": "다른 법인 · 케이티(00190321)의 해외법인.",
    "ktes": "다른 법인 · 케이티(00190321)의 해외법인.",
    "kthongkongtelecommunications": "다른 법인 · 케이티(00190321)의 해외법인.",
    "ktrus": "다른 법인 · 케이티(00190321)의 해외법인.",
    "ktrwandanetworks": "다른 법인 · 케이티(00190321)의 해외법인.",
    "ktjapan": "다른 법인 · 케이티(00190321)의 해외법인.",
    "naverchina": "다른 법인 · NAVER(00266961)의 해외법인.",
    "naverfrance": "다른 법인 · NAVER(00266961)의 해외법인.",
    "naverjhub": "다른 법인 · NAVER(00266961)의 해외법인.",
    "naveruhub": "다른 법인 · NAVER(00266961)의 해외법인.",
    "navervietnam": "다른 법인 · NAVER(00266961)의 해외법인.",
    "skhynixsemiconductorhongkong": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixsemiconductorindiaprivate": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixsemiconductortaiwan": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixuk": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixventureshongkong": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixmemorysolutionspolandspzoo": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "skhynixmemorysolutionstaiwan": "다른 법인 · SK하이닉스(00164779)의 해외법인.",
    "samsungsdsglobalsupplychainlogisticsspainslu": "다른 법인 · 삼성에스디에스(00126186)의 해외법인.",
    "samsungsdslatinamericatecnologiaelogisticaltda": "다른 법인 · 삼성에스디에스(00126186)의 해외법인.",
    "samsungsdsmalaysia": "다른 법인 · 삼성에스디에스(00126186)의 해외법인.",
    "samsungsdsruslimitedliability": "다른 법인 · 삼성에스디에스(00126186)의 해외법인.",
    "samsungsdsvietnam": "다른 법인 · 삼성에스디에스(00126186)의 해외법인.",
    "kohyoungvietnam": "다른 법인 · 고영(00579999)의 해외법인.",
    "philopticsvietnam": "다른 법인 · 필옵틱스(00938721)의 해외법인.",
    "robotisbeijing": "다른 법인 · 로보티즈(00946030)의 해외법인.",

    # ── 다른 법인 · 합작·조합 ──────────────────────────────────
    "hyundairotemcompany-hyundaieurotemdemiryoluaraclarisanveticasortakgirisimi":
        "다른 법인 · 현대로템(00302926)의 튀르키예 합작(JV).",
    "hyundairotem-hyundaieurotemmahmutbeyprojesiortakgirisimi":
        "다른 법인 · 현대로템(00302926)의 튀르키예 합작(JV).",
    "현대차증권코스넷미래성장제2호벤처투자조합": "다른 법인 · 현대차증권(00137997)이 운용하는 투자조합.",
    "현대차증권프라이머스모빌리티신기술사업투자조합": "다른 법인 · 현대차증권(00137997)이 운용하는 투자조합.",
    "현대차증권-인프라프론티어미래환경신기술조합1호": "다른 법인 · 현대차증권(00137997)이 운용하는 투자조합.",
    "현대차증권-타임브릿지에너지조합제1호신기술투자조합": "다른 법인 · 현대차증권(00137997)이 운용하는 투자조합.",
    "현대차증권오리엔스제1호신기술사업투자조합": "다른 법인 · 현대차증권(00137997)이 운용하는 투자조합.",
}

_NOTE = "A-10 별칭 stub → 정본 · 사람이 확인"

# stub 은 norm_name 이 키, 정본은 corp_code 가 키 — `graph_loader._company_ident` 와 같다.
_STUBS = """
MATCH (c:Company) WHERE c.corp_code IS NULL AND c.norm_name IN $keys
RETURN c.norm_name AS key, c.name AS name,
       COUNT { (c)-[]-() } AS deg, COUNT { (c)-[:HAS_EVENT]->(:Event) } AS ev
"""
_CANON = """
MATCH (c:Company) WHERE c.corp_code IN $codes
RETURN c.corp_code AS cc, c.norm_name AS norm, c.name AS name,
       COUNT { (c)-[]-() } AS deg, COUNT { (c)-[:HAS_EVENT]->(:Event) } AS ev
"""


@dataclass
class Decision:
    """stub 하나에 대한 판정. `action` 은 write · already · skip 셋 중 하나."""
    corp_code: str
    stub: str
    why: str
    action: str = "skip"
    reason: str = ""
    canonical: Optional[str] = None
    stub_node: Optional[dict] = None
    canon_node: Optional[dict] = None
    existing: Optional[tuple[str, str]] = None      # (canonical_key, source)


@dataclass
class Registry:
    """`company_aliases` 에서 판정에 필요한 만큼만 읽은 것."""
    rows: dict[str, tuple[str, str]] = field(default_factory=dict)   # alias_key → (canonical_key, source)

    def pointing_at(self, key: str) -> list[str]:
        """`key` 를 대표형으로 삼는 다른 별칭들 — 여기 뭔가 있으면 `key` 는 대표형이다."""
        return [a for a, (c, _) in self.rows.items() if c == key and a != key]


def plan(confirmed: list[tuple[str, str, str]],
         stubs: dict[str, dict], canon: dict[str, list[dict]],
         registry: Registry, master_codes: set[str],
         resolve_cc: Callable[[str], Optional[str]]) -> list[Decision]:
    """검증 순서대로 판정한다. **DB 를 만지지 않는다** — 읽어 온 것만 본다.

    순서가 곧 우선순위다: 이미 반영된 것은 노드가 사라졌어도 「이미 반영」이고,
    노드 검증은 그다음이다(병합 뒤 다시 돌려도 경고가 안 뜬다).
    """
    out: list[Decision] = []
    for cc, stub, why in confirmed:
        d = Decision(corp_code=cc, stub=stub, why=why)
        out.append(d)

        # ① 손 목록끼리 어긋나면 코드가 틀린 것 — 여기서 멈춘다
        if stub in REVIEWED_NOT_MERGED:
            d.reason = "CONFIRMED 와 REVIEWED_NOT_MERGED 에 같이 있다 — 목록을 고치세요"
            continue

        # ② 정본 노드 — corp_code 로 정확히 하나
        nodes = canon.get(cc, [])
        if not nodes:
            d.reason = f"정본 노드 없음 — corp_code {cc} 인 Company 가 그래프에 없다"
            continue
        if len(nodes) > 1:
            d.reason = f"corp_code {cc} 인 노드가 {len(nodes)}개 — node_identity 로 먼저 접어야 한다"
            continue
        d.canon_node = nodes[0]
        d.canonical = nodes[0]["norm"]
        if not d.canonical or d.canonical == stub:
            d.reason = "정본 norm_name 이 비었거나 stub 과 같다"
            continue

        # ③ 표 — 멱등성과 충돌
        d.existing = registry.rows.get(stub)
        if d.existing and d.existing[1] == "hand":
            if d.existing[0] == d.canonical:
                d.action, d.reason = "already", "이미 hand 로 반영됨"
            else:
                d.reason = (f"손 목록 충돌 — 표에는 hand 로 → {d.existing[0]} 가 있다. "
                            "둘 중 하나를 고치세요")
            continue
        holders = registry.pointing_at(stub)
        if holders:
            d.reason = (f"stub 이 다른 별칭 {len(holders)}건의 대표형이다({', '.join(holders[:3])}) — "
                        "apply_alias 는 한 홉이라 그 별칭들이 갈린다. 그 행들부터 돌려야 한다")
            continue
        canon_row = registry.rows.get(d.canonical)
        if canon_row and canon_row[0] != d.canonical:
            d.reason = (f"대표형 {d.canonical} 이 이미 별칭이다(→ {canon_row[0]}) — "
                        "순환. 대표형을 다시 고르세요")
            continue

        # ④ corp_code 가 명부에 있고, 대표형이 그 corp_code 로 해소되는가
        if cc not in master_codes:
            d.reason = f"corp_code {cc} 가 corp_code_master 에 없다"
            continue
        got = resolve_cc(d.canonical)
        if got != cc:
            d.reason = (f"resolver 가 「{d.canonical}」을 {got or '해소 못 함'} 으로 본다 — "
                        f"정본 {cc} 과 다르다(같은 이름의 법인이 둘인지 확인)")
            continue

        # ⑤ stub 노드 — 없어도 별칭은 맞지만, 측정과 어긋난 것이니 보고만 한다
        d.stub_node = stubs.get(stub)
        if d.stub_node is None:
            d.reason = "stub 노드 없음 — 이미 접혔거나 사라졌습니다"
            continue

        d.action = "write"
        if d.existing:
            d.reason = f"기존 {d.existing[1]} 행(→ {d.existing[0]}) 을 hand 로 교체"
        else:
            d.reason = "새 행"
    return out


def _load(session, conn) -> tuple[dict, dict, Registry, set[str]]:
    keys = [s for _, s, _ in CONFIRMED]
    codes = [c for c, _, _ in CONFIRMED]
    stubs = {r["key"]: dict(r) for r in session.run(_STUBS, keys=keys)}
    canon: dict[str, list[dict]] = {}
    for r in session.run(_CANON, codes=codes):
        canon.setdefault(r["cc"], []).append(dict(r))

    ensure(conn)
    reg = Registry()
    with conn.cursor() as cur:
        cur.execute("SELECT alias_key, canonical_key, source FROM company_aliases")
        reg.rows = {a: (c, s) for a, c, s in cur.fetchall()}
        cur.execute("SELECT corp_code FROM corp_code_master")
        master = {r[0] for r in cur.fetchall()}
    return stubs, canon, reg, master


def _resolve_cc(norm: str) -> Optional[str]:
    # ★resolver 는 `normalize_company_name` 을 다시 태운다. 여기 넘기는 것은 이미
    #   norm_name 이라 한 번 더 정규화해도 같은 값이다(멱등).
    r = resolver.resolve(norm)
    return r.corp_code if r else None


def _show(d: Decision) -> None:
    mark = {"write": "✎", "already": "＝", "skip": "·"}[d.action]
    s, c = d.stub_node or {}, d.canon_node or {}
    left = f"「{d.stub}」" + (f"(연결 {s['deg']} · 사건 {s['ev']})" if s else "")
    right = (f"「{d.canonical}」{d.corp_code}" + (f"(연결 {c['deg']} · 사건 {c['ev']})" if c else "")
             if d.canonical else d.corp_code)
    print(f"  {mark} {left} → {right}")
    print(f"      {d.reason}")
    if d.action == "write":
        print(f"      {d.why}")



def run_aliases(session, conn, dry_run: bool) -> int:
    """1단 — 별칭 등록. 기록한 행 수를 돌려준다."""
    stubs, canon, reg, master = _load(session, conn)
    try:
        decisions = plan(CONFIRMED, stubs, canon, reg, master, _resolve_cc)
    finally:
        resolver.close()
    for d in decisions:
        _show(d)

    writes = [d for d in decisions if d.action == "write"]
    already = sum(1 for d in decisions if d.action == "already")
    skipped = [d for d in decisions if d.action == "skip"]

    # 접지 않기로 한 것인데 hand 행이 남아 있으면 — 판단을 뒤집은 뒤 표를 안 돌린 것
    stale = [k for k in REVIEWED_NOT_MERGED
             if reg.rows.get(k, ("", ""))[1] == "hand" and reg.rows[k][0] != k]
    if stale:
        print(f"\n  ⚠ 접지 않기로 한 것에 hand 행이 있습니다: {stale} — 되돌리세요")

    print(f"\n{'[dry-run] ' if dry_run else ''}"
          f"기록 {len(writes)}건 · 이미 반영 {already}건 · 건너뜀 {len(skipped)}건")
    if skipped:
        print("   건너뜀은 위 사유를 보고 목록을 고친 뒤 다시 돌리세요")
    if dry_run:
        print("   표에 쓰지 않았습니다.")
        return 0

    # ★canon_name 은 안 넘긴다 — 기존 행의 모델 정식명(검색 alias_exact_match 가
    #   쓰는 값)을 COALESCE 가 지키게 둔다.
    for d in writes:
        record(conn, d.stub, d.canonical, source="hand",
               note=f"{_NOTE} · {d.corp_code}")
    conn.commit()
    if writes:
        print(f"\n✅ source='hand' {len(writes)}행 기록")
    return len(writes)


# ══════════════════════════════════════════════════════════════
#  2단 · 이미 갈라진 노드를 정본으로 옮긴다
# ══════════════════════════════════════════════════════════════

# 관계는 [유형, 정방향인가, 상대 elementId, subtype, evidence_id, 상대가 Event 인가,
#         상대가 정본인가] 로 뽑는다 — 「정본에 같은 관계가 있나」와 「정본과 직접
#         이어졌나」를 코드에서 가리기 위해서다.
_STUB_GRAPH = """
MATCH (s:Company {norm_name: $stub}) WHERE s.corp_code IS NULL
OPTIONAL MATCH (s)-[r]-(o)
WITH s, collect(CASE WHEN r IS NULL THEN null ELSE
     [type(r), startNode(r) = s, elementId(o), r.subtype, r.evidence_id,
      'Event' IN labels(o), coalesce(o.corp_code, '') = $cc] END) AS rels
RETURN elementId(s) AS eid, s.name AS name, coalesce(s.also_names, []) AS also,
       [x IN rels WHERE x IS NOT NULL] AS rels
"""
_KEEP_GRAPH = """
MATCH (k:Company {corp_code: $cc})
OPTIONAL MATCH (k)-[r]-(o)
WITH k, collect(CASE WHEN r IS NULL THEN null ELSE
     [type(r), startNode(r) = k, elementId(o), r.subtype, r.evidence_id] END) AS rels
RETURN elementId(k) AS eid, k.name AS name, k.norm_name AS norm,
       coalesce(k.merged_keys, []) AS merged, [x IN rels WHERE x IS NOT NULL] AS rels
"""
# 사라지는 표기를 살아남는 노드에 남긴다 — `foreign_merge` 와 같다
_MARK = """
MATCH (k:Company) WHERE elementId(k) = $keep
SET k.also_names = coalesce(k.also_names, []) + $alias
"""
# ★`mergeRels: false` — 관계를 **그대로** 옮긴다. 같은 유형·상대의 관계가 둘이 되면
#   `batch/repair/edges.py` 3단이 자기 규칙으로 접는다(모듈 설명 참조).
#   `discard` — 정본 값이 있으면 지키고, 정본에 없는 키만 stub 에서 가져온다.
#   `merged_keys` — `person_merge` 와 같다. 되짚을 수 있고, 재실행 때 「이미 병합」의 근거다.
_MERGE_NODE = """
MATCH (keep:Company) WHERE elementId(keep) = $keep
MATCH (drop:Company) WHERE elementId(drop) = $drop
CALL apoc.refactor.mergeNodes([keep, drop],
     {properties: 'discard', mergeRels: false}) YIELD node
SET node.merged_keys = coalesce(node.merged_keys, []) + $stub
RETURN elementId(node) AS eid, COUNT { (node)-[]-() } AS deg,
       COUNT { (node)-[:HAS_EVENT]->(:Event) } AS ev
"""
# 병합 뒤 정본과 그 관계에 배열이 된 스칼라가 없는지 — 전역 `unlist_scalars` 대신
# **이 노드만** 본다(22건 밖을 건드리지 않기 위해).
_LISTED_PROPS = """
MATCH (k:Company) WHERE elementId(k) = $keep
OPTIONAL MATCH (k)-[r]-()
WITH k, collect(r) AS rels
RETURN [p IN keys(k) WHERE valueType(k[p]) STARTS WITH 'LIST'] AS node_lists,
       apoc.coll.toSet(reduce(acc = [], r IN rels |
           acc + [p IN keys(r) WHERE valueType(r[p]) STARTS WITH 'LIST'])) AS rel_lists
"""

_SE_COUNT = ("SELECT count(*) FROM staged_edges "
             "WHERE {side}_node_type = 'Company' AND {side}_key = %s")
# 키를 바꾸면 정본 행과 (상대·유형·subtype) 이 겹치는 행 — 세어서 보고만 한다.
# 선례(`node_identity`)도 지우지 않는다. 적재가 subtype 으로 MERGE 하므로 그래프에는
# 하나로 들어간다.
_SE_DUP = """
SELECT count(*) FROM staged_edges a
WHERE a.{side}_node_type = 'Company' AND a.{side}_key = %s AND EXISTS (
    SELECT 1 FROM staged_edges b
    WHERE b.{side}_node_type = 'Company' AND b.{side}_key = %s
      AND b.{other}_key = a.{other}_key AND b.edge_type = a.edge_type
      AND coalesce(b.subtype, '') = coalesce(a.subtype, ''))
"""
_SE_RETARGET = ("UPDATE staged_edges SET {side}_key = %s "
                "WHERE {side}_node_type = 'Company' AND {side}_key = %s")
_ATTR_DELETE = "DELETE FROM company_attributes WHERE node_key = %s AND corp_code IS NULL"


@dataclass
class MergeDecision:
    """stub 하나의 2단 판정. `action` 은 merge · pg-only · already · skip."""
    corp_code: str
    stub: str
    why: str
    action: str = "skip"
    reason: str = ""
    stub_g: Optional[dict] = None       # _STUB_GRAPH 한 행
    keep_g: Optional[dict] = None       # _KEEP_GRAPH 한 행 (정확히 하나일 때)
    pg: Optional[dict] = None           # alias · se_src · se_tgt · se_dup_* · attr_stub · cards
    collide: int = 0                    # 정본에 같은 (유형·방향·상대) 관계가 있는 stub 관계
    exact_dup: int = 0                  # 그중 subtype·evidence 까지 같은 것
    expect_deg: Optional[int] = None    # 병합 뒤 정본 연결 수 (= 정본 + stub)
    expect_ev: Optional[int] = None     # 병합 뒤 정본 HAS_EVENT 수

    @property
    def stub_deg(self) -> int:
        return len(self.stub_g["rels"]) if self.stub_g else 0

    @property
    def stub_ev(self) -> int:
        if not self.stub_g:
            return 0
        return sum(1 for r in self.stub_g["rels"] if r[0] == "HAS_EVENT" and r[1])


def plan_merge(confirmed: list[tuple[str, str, str]],
               stub_graph: dict[str, Optional[dict]], keep_graph: dict[str, list[dict]],
               pg: dict[str, dict]) -> list[MergeDecision]:
    """2단 판정 — **DB 를 만지지 않는다.** 읽어 온 것만 본다.

    stub 이 이미 사라졌어도 정본의 `merged_keys` 에 있으면 「이미 병합」이고, 그때
    PG 에 stub 키가 남아 있으면 그것만 마저 처리한다(중간에 죽은 실행의 복구).
    """
    out: list[MergeDecision] = []
    for cc, stub, why in confirmed:
        d = MergeDecision(corp_code=cc, stub=stub, why=why)
        out.append(d)
        st = pg.get(stub, {})
        d.pg = st

        if stub in REVIEWED_NOT_MERGED:
            d.reason = "CONFIRMED 와 REVIEWED_NOT_MERGED 에 같이 있다 — 목록을 고치세요"
            continue

        keeps = keep_graph.get(cc, [])
        if not keeps:
            d.reason = f"정본 노드 없음 — corp_code {cc} 인 Company 가 그래프에 없다"
            continue
        if len(keeps) > 1:
            d.reason = f"corp_code {cc} 인 노드가 {len(keeps)}개 — node_identity 로 먼저 접어야 한다"
            continue
        d.keep_g = keeps[0]

        # 1단이 먼저다 — 표가 정본을 가리키지 않으면 다음 적재 때 stub 이 되살아난다
        alias = st.get("alias")
        if not alias or alias[1] != "hand" or alias[0] != d.keep_g["norm"]:
            d.reason = (f"별칭이 hand 로 정본을 가리키지 않는다(현재 {alias}) — "
                        "먼저 --only aliases")
            continue

        pg_left = st.get("se_src", 0) + st.get("se_tgt", 0) + (1 if st.get("attr_stub") else 0)
        sg = stub_graph.get(stub)
        if sg is None:
            if stub in d.keep_g["merged"]:
                if pg_left:
                    d.action, d.reason = "pg-only", f"노드는 이미 병합됨 · PG 에 stub 키 {pg_left}건 남음"
                else:
                    d.action, d.reason = "already", "이미 병합됨 (정본 merged_keys 에 있음)"
            else:
                d.reason = "stub 노드 없음 — merged_keys 에도 없어 어디로 갔는지 모른다. PG 는 두었다"
            continue
        d.stub_g = sg

        direct = sum(1 for r in sg["rels"] if r[6])
        if direct:
            d.reason = (f"정본과 직접 관계 {direct}건 — 정본이 stub 을 소유·거래하는 것은 "
                        "다른 법인이라는 증거(FADU 사례). 접지 않는다")
            continue

        kset = {(r[0], r[1], r[2]) for r in d.keep_g["rels"]}
        kfull = {tuple(r) for r in d.keep_g["rels"]}
        d.collide = sum(1 for r in sg["rels"] if (r[0], r[1], r[2]) in kset)
        d.exact_dup = sum(1 for r in sg["rels"] if tuple(r[:5]) in kfull)
        keep_ev = sum(1 for r in d.keep_g["rels"] if r[0] == "HAS_EVENT" and r[1])
        d.expect_deg = len(d.keep_g["rels"]) + len(sg["rels"])
        d.expect_ev = keep_ev + d.stub_ev
        d.action = "merge"
        d.reason = (f"관계 {len(sg['rels'])}·사건 {d.stub_ev} 를 옮긴다 · "
                    f"같은 유형·상대 {d.collide}건(완전중복 {d.exact_dup}) 은 edges.py 몫 · "
                    f"staged_edges {st.get('se_src', 0)}+{st.get('se_tgt', 0)}행 · "
                    f"company_attributes {'1' if st.get('attr_stub') else '0'}행")
    return out


def _load_merge(session, conn) -> tuple[dict, dict, dict]:
    stub_graph: dict[str, Optional[dict]] = {}
    keep_graph: dict[str, list[dict]] = {}
    pg: dict[str, dict] = {}
    with conn.cursor() as cur:
        for cc, stub, _ in CONFIRMED:
            r = session.run(_STUB_GRAPH, stub=stub, cc=cc).single()
            stub_graph[stub] = dict(r) if r else None
            keep_graph[cc] = [dict(x) for x in session.run(_KEEP_GRAPH, cc=cc)]

            cur.execute("SELECT canonical_key, source FROM company_aliases WHERE alias_key = %s", (stub,))
            row = cur.fetchone()
            st = {"alias": tuple(row) if row else None}
            for side, other in (("src", "tgt"), ("tgt", "src")):
                cur.execute(_SE_COUNT.format(side=side), (stub,))
                st[f"se_{side}"] = cur.fetchone()[0]
                cur.execute(_SE_DUP.format(side=side, other=other), (stub, cc))
                st[f"se_dup_{side}"] = cur.fetchone()[0]
            cur.execute("SELECT 1 FROM company_attributes WHERE node_key = %s AND corp_code IS NULL", (stub,))
            st["attr_stub"] = cur.fetchone() is not None
            name = stub_graph[stub]["name"] if stub_graph[stub] else stub
            cur.execute("SELECT count(*) FROM vector_chunks WHERE collection = 'company' "
                        "AND is_active AND owner_key = %s", (name,))
            st["cards"] = cur.fetchone()[0]
            pg[stub] = st
    return stub_graph, keep_graph, pg


def _show_merge(d: MergeDecision) -> None:
    mark = {"merge": "✎", "pg-only": "↺", "already": "＝", "skip": "·"}[d.action]
    k = d.keep_g or {}
    right = f"「{k.get('norm', '?')}」{d.corp_code}" + (f"(연결 {len(k['rels'])})" if k else "")
    print(f"  {mark} 「{d.stub}」(연결 {d.stub_deg} · 사건 {d.stub_ev}) → {right}")
    print(f"      {d.reason}")
    if d.action == "merge" and d.pg and d.pg.get("cards"):
        print(f"      기업 카드 {d.pg['cards']}건이 stub 이름으로 남는다 → batch.repair.stale_cards")


def _merge_one(session, conn, d: MergeDecision) -> None:
    """stub 하나를 접는다 — Neo4j 먼저, 그다음 PG 를 그 stub 분만 커밋한다."""
    if d.action == "merge":
        sg, kg = d.stub_g, d.keep_g
        session.run(_MARK, keep=kg["eid"], alias=[sg["name"], d.stub])
        got = session.run(_MERGE_NODE, keep=kg["eid"], drop=sg["eid"], stub=d.stub).single()
        if got["deg"] != d.expect_deg or got["ev"] != d.expect_ev:
            raise RuntimeError(
                f"{d.stub}: 병합 뒤 정본 연결 {got['deg']}(기대 {d.expect_deg}) · "
                f"HAS_EVENT {got['ev']}(기대 {d.expect_ev}) — 여기서 멈춥니다. PG 는 안 건드렸습니다")
        lists = session.run(_LISTED_PROPS, keep=kg["eid"]).single()
        from batch.repair.node_identity import _LIST_BY_DESIGN
        odd = [p for p in lists["node_lists"] + lists["rel_lists"]
               if p not in _LIST_BY_DESIGN and not p.endswith("_variants")]
        if odd:
            raise RuntimeError(f"{d.stub}: 병합이 스칼라를 배열로 바꿨습니다 {odd} — 멈춥니다")

    with conn.cursor() as cur:
        for side in ("src", "tgt"):
            cur.execute(_SE_RETARGET.format(side=side), (d.corp_code, d.stub))
        cur.execute(_ATTR_DELETE, (d.stub,))
    conn.commit()


def run_merge(session, conn, dry_run: bool) -> int:
    """2단 — 노드 병합 + PG 정합. 접은 stub 수를 돌려준다."""
    stub_graph, keep_graph, pg = _load_merge(session, conn)
    decisions = plan_merge(CONFIRMED, stub_graph, keep_graph, pg)
    for d in decisions:
        _show_merge(d)

    todo = [d for d in decisions if d.action in ("merge", "pg-only")]
    merges = [d for d in todo if d.action == "merge"]
    already = sum(1 for d in decisions if d.action == "already")
    skipped = [d for d in decisions if d.action == "skip"]
    se_rows = sum(d.pg["se_src"] + d.pg["se_tgt"] for d in todo)
    se_dup = sum(d.pg["se_dup_src"] + d.pg["se_dup_tgt"] for d in todo)
    attr_rows = sum(1 for d in todo if d.pg["attr_stub"])

    print(f"\n{'[dry-run] ' if dry_run else ''}병합 {len(merges)}건 · PG 만 {len(todo) - len(merges)}건 · "
          f"이미 병합 {already}건 · 건너뜀 {len(skipped)}건")
    print(f"   옮길 관계 {sum(d.stub_deg for d in merges)}건 (HAS_EVENT {sum(d.stub_ev for d in merges)}) · "
          f"같은 유형·상대 {sum(d.collide for d in merges)}건 → 병합 뒤 batch.repair.edges 로 정리")
    print(f"   staged_edges 키 교체 {se_rows}행 (교체 뒤 정본 행과 겹침 {se_dup}) · "
          f"company_attributes 삭제 {attr_rows}행")
    if skipped:
        print("   건너뜀은 위 사유를 보고 목록을 고친 뒤 다시 돌리세요")
    if dry_run:
        print("   그래프·표에 쓰지 않았습니다.")
        return 0

    done = 0
    for d in todo:
        _merge_one(session, conn, d)      # 예외면 여기서 멈춘다 — 다시 돌리면 이어서 한다
        done += 1
    if done:
        print(f"\n✅ stub {done}건을 정본으로 접었습니다 (관계는 그대로 옮김 · merged_keys 에 기록)")
        print("   같은 유형·상대 관계 정리:  python -m batch.repair.edges --dry-run")
        print("   stub 이름의 기업 카드:      python -m batch.repair.stale_cards --dry-run")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["aliases", "merge"],
                    help="한 단계만 (기본: aliases → merge)")
    args = ap.parse_args()

    print(f"근거를 읽고 확인한 별칭 stub {len(CONFIRMED)}건 · "
          f"접지 않기로 한 것 {len(REVIEWED_NOT_MERGED)}건")

    with neo4j_session() as session, postgres_connection() as conn:
        if args.only in (None, "aliases"):
            print("\n■ 1단 · 별칭 등록")
            run_aliases(session, conn, args.dry_run)
        if args.only in (None, "merge"):
            print("\n■ 2단 · 노드 병합")
            run_merge(session, conn, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
