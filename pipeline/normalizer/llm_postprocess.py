"""규칙 기반으로 처리하지 못한 임원 정보를 LLM으로 후처리한다."""

from __future__ import annotations
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from app.core.config import ANTHROPIC_API_KEY
from pipeline.normalizer.base import clean_missing, clean_name
from pipeline.normalizer.executive_normalizer import MAIN_CAREER_LLM_ELIGIBLE_TAGS, _classify_main_career

MODEL = "claude-haiku-4-5"

_MAIN_CAREER_SYSTEM_PROMPT = (
    "당신은 한국 상장회사 공시(DART) 임원 데이터를 정리하는 보조 도구입니다. "
    "입력은 임원의 '주요경력' 필드 원문이며, 항목 구분자가 없거나 불명확해 여러 "
    "경력 항목이 하나의 텍스트로 뭉쳐 있을 수 있습니다.\n\n"
    "규칙:\n"
    "1. 원문의 단어, 표현, 순서를 절대 바꾸거나 요약하거나 번역하지 마세요 — "
    "오직 항목을 나눌 위치만 판단하세요.\n"
    '2. 분리된 각 항목을 정확히 " | " (공백-파이프-공백)으로 이어붙여 한 줄로만 '
    "출력하세요.\n"
    "3. 정말로 더 나눌 수 없는 단일 항목이면 원문을 그대로 한 줄로 출력하세요.\n"
    "4. 설명, 따옴표, 마크다운, 번호 매기기 등 다른 어떤 것도 출력하지 마세요 — "
    "오직 최종 결과 한 줄만 출력하세요."
)

_POSITION_SYSTEM_PROMPT = (
    "당신은 한국 상장회사 공시(DART) 임원 데이터를 정리하는 보조 도구입니다. "
    "입력은 임원의 '직위'(position) 필드 원문이며, 원본 표에서 줄바꿈이 들어가 "
    "있습니다. 이 줄바꿈은 두 가지 경우 중 하나입니다.\n"
    "(a) 하나의 직함이 화면 폭에 맞춰 중간에서 줄바뀜된 것 "
    '(예: "사외\\n이사" = "사외이사" 하나의 직함, "대표\\n이사" = "대표이사")\n'
    "(b) 한 임원이 동시에 맡고 있는 서로 다른 직함 2개 이상을 나열한 것 "
    '(예: "대표이사\\n회장" = "대표이사"와 "회장" 두 직함)\n\n'
    "규칙:\n"
    "1. 원문의 글자를 절대 바꾸거나 지어내지 마세요 — 오직 줄바꿈을 어떻게 "
    "처리할지만 판단하세요.\n"
    "2. (a)라고 판단되면 줄바꿈을 제거하고 하나의 단어/구로 합쳐서 출력하세요 "
    "(원래 한 단어였다면 공백 없이, 원래도 띄어 쓰는 구였다면 공백 하나로).\n"
    '3. (b)라고 판단되면 각 직함을 정확히 " | " (공백-파이프-공백)으로 이어붙여 '
    "출력하세요.\n"
    "4. 설명, 따옴표, 마크다운 등 다른 어떤 것도 출력하지 마세요 — 오직 최종 결과 "
    "한 줄만 출력하세요."
)

_DUTY_SYSTEM_PROMPT = (
    "당신은 한국 상장회사 공시(DART) 임원 데이터를 정리하는 보조 도구입니다. "
    "입력은 임원의 '담당업무'(duty, 겸직·위원회 소속 등) 필드 원문이며, 원본 표에서 "
    "줄바꿈이 들어가 있습니다. 이 줄바꿈은 두 가지 경우 중 하나입니다.\n"
    "(a) 하나의 업무/직함 설명이 화면 폭에 맞춰 중간에서 줄바뀜된 것 "
    '(예: "경영\\n총괄" = "경영 총괄" 하나의 설명)\n'
    "(b) 이 임원이 맡고 있는 서로 다른 업무/위원회 소속을 나열한 것 "
    '(예: "대표이사 CEO\\n경영위원회 위원장\\nESG위원회 위원" = 서로 다른 3개 항목)\n\n'
    "규칙:\n"
    "1. 원문의 글자를 절대 바꾸거나 지어내지 마세요 — 오직 줄바꿈을 어떻게 "
    "처리할지만 판단하세요.\n"
    "2. (a)라고 판단되면 줄바꿈을 제거하고 하나의 구로 합쳐서 출력하세요 "
    "(원래 한 단어였다면 공백 없이, 원래도 띄어 쓰는 구였다면 공백 하나로).\n"
    '3. (b)라고 판단되면 각 항목을 정확히 " | " (공백-파이프-공백)으로 이어붙여 '
    "출력하세요.\n"
    "4. 설명, 따옴표, 마크다운 등 다른 어떤 것도 출력하지 마세요 — 오직 최종 결과 "
    "한 줄만 출력하세요."
)


def _main_career_candidate_text(raw_row: dict) -> Optional[str]:
    cleaned = clean_missing(raw_row.get("main_career"))
    if cleaned is None:
        return None
    items, tag = _classify_main_career(cleaned)
    if tag in MAIN_CAREER_LLM_ELIGIBLE_TAGS:
        return items[0]
    return None


def _position_candidate_text(raw_row: dict) -> Optional[str]:
    cleaned = clean_missing(raw_row.get("ofcps"))
    if cleaned is not None and "\n" in cleaned:
        return cleaned
    return None


def _duty_candidate_text(raw_row: dict) -> Optional[str]:
    raw = raw_row.get("chrg_job")
    cleaned = clean_missing(raw)
    if cleaned is None:
        return None
    stripped_raw = (raw or "").strip()
    if "\n" in stripped_raw and not stripped_raw.startswith("ㆍ"):
        return cleaned
    return None


@dataclass(frozen=True)
class FieldSpec:
    key: str
    system_prompt: str
    candidate_text: Callable[[dict], Optional[str]]


FIELD_SPECS: dict[str, FieldSpec] = {
    "main_career": FieldSpec("main_career", _MAIN_CAREER_SYSTEM_PROMPT, _main_career_candidate_text),
    "position": FieldSpec("position", _POSITION_SYSTEM_PROMPT, _position_candidate_text),
    "duty": FieldSpec("duty", _DUTY_SYSTEM_PROMPT, _duty_candidate_text),
}

# 검증 시 무시할 구분자 문자 집합.
_NEUTRAL_SEPARATOR_RE = re.compile(r"[\-•·ㆍ,/]")

def _normalize_for_check(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'")
    s = _NEUTRAL_SEPARATOR_RE.sub("", s)
    return re.sub(r"\s+", "", s)


def validate_llm_output(original: str, output: str) -> bool:
    """LLM 출력이 원문과 동일한 정보를 유지하는지 검증한다."""
    if not output:
        return False
    stripped = output.replace("|", "")
    normalized_output = _normalize_for_check(stripped)
    if not normalized_output:
        return False
    return normalized_output == _normalize_for_check(original)


@dataclass
class Candidate:
    corp_code: str
    name: str
    field: str
    text: str

_EXECUTIVES_FILENAME_RE = re.compile(r"^(?P<corp_code>\d+)_executives\.json$")

def _load_raw_rows_by_name(corp_code: str, raw_dir: str) -> dict[str, dict]:
    """이름을 기준으로 원본 임원 데이터를 조회한다."""
    path = os.path.join(raw_dir, f"{corp_code}_executives.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("list") or []
    by_name: dict[str, dict] = {}
    for row in rows:
        name = clean_name(row.get("nm"))
        if name is not None:
            by_name[name] = row
    return by_name


def discover_candidates(normalized_dir: str, raw_dir: str) -> list[Candidate]:
    """LLM 후처리 대상 레코드를 수집한다."""
    candidates: list[Candidate] = []
    for filename in sorted(os.listdir(normalized_dir)):
        match = _EXECUTIVES_FILENAME_RE.match(filename)
        if not match:
            continue
        corp_code = match.group("corp_code")
        raw_by_name = _load_raw_rows_by_name(corp_code, raw_dir)
        if not raw_by_name:
            continue

        with open(os.path.join(normalized_dir, filename), encoding="utf-8") as f:
            doc = json.load(f)

        for rel in doc.get("relationships", []):
            if rel.get("to") != f"Company:{corp_code}":
                continue
            from_key = rel.get("from", "")
            if not from_key.startswith("Person:"):
                continue
            name = from_key[len("Person:") :]
            raw_row = raw_by_name.get(name)
            if raw_row is None:
                continue

            properties = rel.get("properties", {})
            for field, spec in FIELD_SPECS.items():
                if properties.get(f"{field}_llm_assisted"):
                    continue
                text = spec.candidate_text(raw_row)
                if text is not None:
                    candidates.append(Candidate(corp_code=corp_code, name=name, field=field, text=text))
    return candidates


def run_batch(candidates: list[Candidate]) -> dict[int, str]:
    """후보 레코드를 하나의 Batch 요청으로 처리한다."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    requests = [
        Request(
            custom_id=f"req-{i}",
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=500,
                system=FIELD_SPECS[candidate.field].system_prompt,
                messages=[{"role": "user", "content": candidate.text}],
            ),
        )
        for i, candidate in enumerate(candidates)
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"[llm_postprocess] 배치 생성: {batch.id} ({len(requests)}건)")

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"[llm_postprocess]   상태: {batch.processing_status}, {batch.request_counts}")
        time.sleep(15)

    print(f"[llm_postprocess] 배치 완료: {batch.request_counts}")

    outputs: dict[int, str] = {}
    for result in client.messages.batches.results(batch.id):
        index = int(result.custom_id.split("-", 1)[1])
        if result.result.type == "succeeded":
            msg = result.result.message
            text = next((b.text for b in msg.content if b.type == "text"), "")
            outputs[index] = text.strip()
        else:
            print(f"[llm_postprocess]   [{result.custom_id}] 실패: {result.result.type}")
    return outputs


def apply_results(
    candidates: list[Candidate],
    outputs: dict[int, str],
    normalized_dir: str,
    log_path: str,
) -> None:
    
    """검증을 통과한 LLM 결과를 normalized 데이터에 반영한다."""
    by_corp: dict[str, list[tuple[Candidate, Optional[str]]]] = {}
    log_entries = []

    for i, candidate in enumerate(candidates):
        raw_output = outputs.get(i)
        accepted = raw_output is not None and validate_llm_output(candidate.text, raw_output)
        final_text = raw_output if accepted else None
        by_corp.setdefault(candidate.corp_code, []).append((candidate, final_text))
        log_entries.append(
            {
                "corp_code": candidate.corp_code,
                "name": candidate.name,
                "field": candidate.field,
                "original": candidate.text,
                "llm_output": raw_output,
                "accepted": accepted,
            }
        )

    for field in FIELD_SPECS:
        field_entries = [e for e in log_entries if e["field"] == field]
        accepted_count = sum(1 for e in field_entries if e["accepted"])
        print(
            f"[llm_postprocess] {field}: {accepted_count}/{len(field_entries)}건 통과 "
            f"(fallback {len(field_entries) - accepted_count}건)"
        )

    with open(log_path, "w", encoding="utf-8") as f:
        for entry in log_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    for corp_code, items in by_corp.items():
        path = os.path.join(normalized_dir, f"{corp_code}_executives.json")
        if not os.path.exists(path):
            print(f"[llm_postprocess] {corp_code}: normalized 파일 없음, 건너뜀")
            continue
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)

        # (name, field) -> 최종 텍스트(검증 통과분만)
        by_name_field = {
            (candidate.name, candidate.field): final_text
            for candidate, final_text in items
            if final_text is not None
        }
        applied = 0
        for rel in doc["relationships"]:
            if rel.get("to") != f"Company:{corp_code}":
                continue
            from_key = rel.get("from", "")
            if not from_key.startswith("Person:"):
                continue
            name = from_key[len("Person:") :]
            for field in FIELD_SPECS:
                final_text = by_name_field.get((name, field))
                if final_text is not None:
                    rel["properties"][field] = final_text
                    rel["properties"][f"{field}_llm_assisted"] = True
                    applied += 1

        if applied:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            print(f"[llm_postprocess] {corp_code}: {applied}건 반영")


def run_llm_postprocess(normalized_dir: str) -> None:
    """전체 normalized 데이터를 대상으로 LLM 후처리를 수행한다."""

    data_dir = os.path.dirname(os.path.normpath(normalized_dir))
    raw_dir = os.path.join(data_dir, "raw_dart")
    log_path = os.path.join(data_dir, "llm_postprocess_log.jsonl")

    candidates = discover_candidates(normalized_dir, raw_dir)
    print(f"[llm_postprocess] 대상: {len(candidates)}건")
    if not candidates:
        return
    outputs = run_batch(candidates)
    apply_results(candidates, outputs, normalized_dir, log_path)

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    run_llm_postprocess(os.path.join(BASE_DIR, "data", "normalized"))
