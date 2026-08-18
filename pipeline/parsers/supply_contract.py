"""「단일판매ㆍ공급계약체결」 공시 원문 파서 → SUPPLIES_TO + evidence.

Sprint 0에서 필드 매핑 검증 완료(한미반도체→SK하이닉스). 필드명은 계약상대'방'.
공급 방향: 공시 주체(filer) → 계약상대방(고객). 표준 서식이라 규칙 파싱.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from pipeline.normalizer.generic_names import is_generic_name

# 태그 제거·공백 축약된 평문에서 필드 추출
# 라벨은 문서마다 "계약상대"/"계약상대방", 다음 필드 순서도 문서마다 다름
# → 방은 선택적, 종료는 다음 필드 라벨 중 가장 먼저 오는 것에서 끊는다(정밀 포착)
_RE_COUNTERPARTY = re.compile(
    r"계약상대방?\s*(.+?)\s*-?\s*(?:최근\s*매출액|주요\s*사업|회사와의|계약내역|\d+\s*\.)"
)

# 계약상대 공시유보(실명 미기재) 표기
_ANON_MARKERS = ("비공개", "기재생략", "기재 생략", "영업기밀", "영업 기밀")
# 실명이 아닌 '설명형' 상대 — stub도 만들지 않는다(방법서 §7)
_DESCRIPTION_MARKERS = (
    "제조사", "고객사", "거래처", "수요처", "매입처", "구매처", "발주처", "발주사",
    "복수", "관련업체", "관련 업체", "대형기업", "대형업체", "선주", "수요자", "고객",
)
# 문장 조각·오추출 감지 (계약상대가 아닌 다른 필드/설명을 잡은 경우)
#
# ★「정보」를 뺐다(2026-08-12). 문장 조각을 잡으려던 낱말인데 **실명을 치고
#   있었다.** 법인 마스터 118,535건 중 이름에 「정보」가 든 곳이 417곳이다:
#       한국정보통신 · 쌍용정보통신 · E&C정보기술 · 아이티정보
#   걸리면 `is_anonymous=True`가 되어 **공급 엣지가 아예 안 만들어진다.**
#   조용히 사라지므로 감사에도 안 잡힌다 — IT 서비스 업체가 계약상대인 공시가
#   통째로 이 구멍으로 빠지고 있었다.
#
#   「영업비밀」이 남아 있어 원래 잡으려던 문구(「영업비밀 정보 보호 요청」)는
#   그대로 걸린다. 「정보」 단독은 얻는 것보다 잃는 게 컸다.
#
#   나머지 마커도 같이 재봤다 — 선주 8곳(대선주조)·동의 8곳(동의건설)·
#   보호 4곳(정보보호기술)이 실명을 치지만, 이들이 공급계약 상대로 나올 일은
#   사실상 없어 남겼다. 나머지는 2곳 이하다.
_SENTENCE_MARKERS = (
    "매출액", "기재", "요청", "동의", "보호", "영업비밀", "중에서",
    "관련하여", "해당", "당사", "예정", "협의",
)
_MAX_NAME_LEN = 60  # 실명이라기엔 지나치게 긴 경우(문장) 거부
_RE_AMOUNT = re.compile(r"계약금액\(원\)\s*([\d,]+)")
_RE_RECENT_REVENUE = re.compile(r"최근매출액\(원\)\s*([\d,]+)")
_RE_RATIO = re.compile(r"매출액대비\(%\)\s*([\d.]+)")
_RE_START = re.compile(r"계약기간\s*시작일\s*(\d{4}-\d{2}-\d{2})")
_RE_END = re.compile(r"종료일\s*(\d{4}-\d{2}-\d{2})")
_RE_SIGNED = re.compile(r"계약\(수주\)일자\s*(\d{4}-\d{2}-\d{2})")

@dataclass
class ContractInfo:
    counterparty: Optional[str]        # 계약상대방(정제) — None이면 공시유보
    contract_amount: Optional[int]     # 계약금액(원)
    revenue_ratio: Optional[float]     # 매출액 대비 (0~1)
    valid_from: Optional[str]          # 계약기간 시작일
    valid_until: Optional[str]         # 계약기간 종료일
    occurred_at: Optional[str]         # 계약(수주)일자
    is_anonymous: bool                 # 계약상대 공시유보 여부


def _plain(xml_text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml_text))


def _clean_counterparty(raw: str) -> Optional[str]:
    """계약상대 정제. 공시유보·설명형·문장조각이면 None(엣지 생성 안 함)."""
    # 영문 병기 제거: "SK하이닉스(SK Hynix Inc.)" -> "SK하이닉스"
    name = re.sub(r"\(.*?\)", "", raw).strip()
    name = name.strip("-· ").strip()
    if not name or name == "-":
        return None
    if len(name) > _MAX_NAME_LEN:
        return None
    if any(m in name for m in _ANON_MARKERS):
        return None
    # 설명형("해외 Nand 제조사", "아시아 지역 선주") — 실명 아님 → 스킵
    if any(m in name for m in _DESCRIPTION_MARKERS):
        return None
    # 공용 가드: "글로벌 항공우주 업체"처럼 수식어+일반명사 조합도 차단
    if is_generic_name(name):
        return None
    # 문장 조각("의 최근 매출액은", "이 영업비밀 보호 요청 중...") → 스킵
    if any(m in name for m in _SENTENCE_MARKERS):
        return None
    return name


def _to_int(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return None


def parse_supply_contract(xml_text: str) -> ContractInfo:
    plain = _plain(xml_text)

    cp_match = _RE_COUNTERPARTY.search(plain)
    counterparty = _clean_counterparty(cp_match.group(1)) if cp_match else None

    ratio_match = _RE_RATIO.search(plain)
    revenue_ratio = round(float(ratio_match.group(1)) / 100, 4) if ratio_match else None

    start = _RE_START.search(plain)
    end = _RE_END.search(plain)
    signed = _RE_SIGNED.search(plain)
    amount = _RE_AMOUNT.search(plain)

    return ContractInfo(
        counterparty=counterparty,
        contract_amount=_to_int(amount.group(1) if amount else None),
        revenue_ratio=revenue_ratio,
        valid_from=start.group(1) if start else None,
        valid_until=end.group(1) if end else None,
        occurred_at=signed.group(1) if signed else None,
        is_anonymous=(cp_match is not None and counterparty is None),
    )


def build_evidence_text(supplier_name: str, info: ContractInfo) -> str:
    """근거 스니펫 — 파싱 필드로 구조화 문장 생성(의미검색·팩트체크용).

    외국 회사명이 상대방인 경우가 많아 조사를 붙이지 않는 구조를 쓴다
    (`Fujitsu와(과)` 같은 표기 방지).
    """
    parts = [f"공급 관계 — 공급자: {supplier_name} / 수요자: {info.counterparty}",
             "근거: 「단일판매ㆍ공급계약체결」 공시"]
    if info.occurred_at:
        parts.append(f"계약(수주)일: {info.occurred_at}")
    if info.contract_amount:
        parts.append(f"계약금액: {info.contract_amount:,}원")
    if info.revenue_ratio is not None:
        parts.append(f"최근 매출액 대비: {info.revenue_ratio * 100:.2f}%"
                     f"{'  ← 매출을 초과하는 대형 계약' if info.revenue_ratio > 1 else ''}")
    if info.valid_from and info.valid_until:
        parts.append(f"계약기간: {info.valid_from} ~ {info.valid_until}")
    return "\n".join(parts)
