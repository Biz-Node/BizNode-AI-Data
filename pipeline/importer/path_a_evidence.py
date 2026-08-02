"""경로 A(정형 API) 엣지의 evidence 스니펫 생성 (팩트체크용).

경로 B·C는 공시 원문에서 근거 문장을 뽑지만, 경로 A는 구조화 JSON이라
원문 문장이 없다. 대신 **필드 값을 사람이 읽는 문장으로 구조화**해 evidence로 만든다.
(방법서 §5 "엣지 근거 스니펫" — 엣지 하나당 하나)

evidence_id는 staged_edges에 이미 있는 값을 재사용한다(결정적 해시, §5-3).
"""

from __future__ import annotations

from typing import Any, Optional

from pipeline.importer.evidence import EvidenceRecord
from pipeline.text import eun_neun


def _fmt_ratio(value: Any) -> str:
    return f"{value}%" if value is not None else "비공개"


def _snippet_owns_stake(props: dict[str, Any], src_name: str, tgt_name: str) -> str:
    subtype = props.get("subtype") or "지분"
    ratio = _fmt_ratio(props.get("ratio"))
    parts = [f"{eun_neun(src_name)} {tgt_name}의 {subtype}로 지분 {ratio}를 보유하고 있다."]

    relation = props.get("shareholder_relation")
    if relation:
        parts.append(f"최대주주와의 관계: {relation}.")
    purpose = props.get("purpose")
    if purpose:
        parts.append(f"출자목적: {purpose}.")
    previous = props.get("previous_ratio")
    if previous is not None and props.get("ratio") is not None and previous != props.get("ratio"):
        parts.append(f"직전 지분율 {previous}%에서 변동되었다.")
    settlement = props.get("settlement_date")
    if settlement:
        parts.append(f"기준일 {settlement}.")
    return " ".join(parts)


def _snippet_executive(props: dict[str, Any], src_name: str, tgt_name: str) -> str:
    position = props.get("position") or props.get("subtype") or "임원"
    parts = [f"{eun_neun(src_name)} {tgt_name}의 {position}으로 재직 중이다."]

    duty = props.get("duty")
    if duty:
        parts.append(f"담당업무: {duty}.")
    career = props.get("main_career")
    if career:
        parts.append(f"주요경력: {career}.")
    tenure = props.get("tenure_end")
    if tenure:
        parts.append(f"임기만료일 {tenure}.")
    relation = props.get("shareholder_relation")
    if relation:
        parts.append(f"최대주주와의 관계: {relation}.")
    return " ".join(parts)


_BUILDERS = {
    "OWNS_STAKE_IN": _snippet_owns_stake,
    "IS_EXECUTIVE_OF": _snippet_executive,
}


def build_snippet(edge_type: str, props: dict[str, Any], src_name: str, tgt_name: str) -> Optional[str]:
    """엣지 타입별 근거 문장. 지원하지 않는 타입이면 None."""
    builder = _BUILDERS.get(edge_type)
    return builder(props, src_name, tgt_name) if builder else None


def _to_int_date(iso: Optional[str]) -> int:
    if not iso:
        return 0
    digits = iso.replace("-", "")
    return int(digits) if digits.isdigit() and len(digits) == 8 else 0


def build_evidence_record(
    edge_type: str,
    props: dict[str, Any],
    src_key: str,
    tgt_key: str,
    src_name: str,
    tgt_name: str,
    evidence_id: str,
    source_doc: str,
    corp_code: str,
) -> Optional[EvidenceRecord]:
    """경로 A 엣지 → EvidenceRecord. 스니펫을 못 만들면 None."""
    text = build_snippet(edge_type, props, src_name, tgt_name)
    if not text:
        return None
    return EvidenceRecord(
        evidence_id=evidence_id,
        text=text,
        corp_code=corp_code,
        source_doc=source_doc,
        metadata={
            "edge_type": edge_type,
            "subtype": props.get("subtype") or "",
            "source_corp": src_key,
            "target_corp": tgt_key,
            "rcept_no": source_doc,
            "occurred_at": _to_int_date(props.get("valid_from")),
        },
    )
