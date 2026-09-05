"""DART XML을 목차 제목별로 잘라 `{제목: 본문}` 딕셔너리로 만든다.

사업보고서 원문은 수백 페이지인데 우리가 쓰는 건 몇 절뿐이다(주요제품·계열회사·
주요계약·매출및수주상황). 여기서 제목별로 갈라 두면 뒷단계가 필요한 절만 집어 간다.
"""


from __future__ import annotations

import re
from pathlib import Path
from typing import Union

from lxml import etree as LET

_ENCODING_DECL_RE = re.compile(rb'encoding=["\']([\w-]+)["\']')
_XML_DECL_RE = re.compile(r"<\?xml[^>]*\?>")
_BARE_AMPERSAND_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
_BARE_LESS_THAN_RE = re.compile(r"<(?![a-zA-Z_/!?])")
# XML 1.0에서 허용 안 되는 제어문자(탭·개행 제외) — DART 원문에 종종 섞임
_INVALID_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TITLE_PREFIX_RE = re.compile(r"^(?:[IVXLCDM]+\.|\d+(?:-\d+)?\.)\s*")
_TITLE_BRACKET_RE = re.compile(r"^【\s*(.+?)\s*】$")


class DartXmlParseError(Exception):
    """XML 파일을 읽거나 파싱하는 데 실패했을 때 발생시킨다."""

def _read_xml_text(xml_path: Union[str, Path]) -> str:
    """XML 파일을 읽어 문자열로 반환한다. 인코딩 선언이 있으면 그에 맞춰 디코딩한다."""
    raw_bytes = Path(xml_path).read_bytes()
    match = _ENCODING_DECL_RE.search(raw_bytes[:200])
    encoding = match.group(1).decode("ascii") if match else "utf-8"
    return raw_bytes.decode(encoding)


def _sanitize_for_parsing(xml_text: str) -> str:
    """파서가 처리할 수 없는 bare &·<, 제어문자, XML 선언을 정리한다."""
    text = _XML_DECL_RE.sub("", xml_text)  # 선언 제거(lxml str 파싱 위해)
    text = _INVALID_XML_CHARS_RE.sub("", text)
    text = _BARE_AMPERSAND_RE.sub("&amp;", text)
    text = _BARE_LESS_THAN_RE.sub("&lt;", text)
    return text


def _normalize_section_title(raw_title: str) -> str:
    """목차 제목에서 번호 접두어("II.", "1.", "2-1.")나 전각 괄호(【 】)를 벗겨낸다."""
    title = raw_title.strip()
    bracket_match = _TITLE_BRACKET_RE.match(title)
    if bracket_match:
        return bracket_match.group(1).strip()
    return _TITLE_PREFIX_RE.sub("", title).strip()


def parse_sections(xml_path: Union[str, Path]) -> dict[str, str]:
    """XML 파일을 읽어, 섹션명을 키로, 섹션 원문(XML 문자열)을 값으로 하는 dict를 반환한다."""
    try:
        xml_text = _read_xml_text(xml_path)
    except OSError as exc:
        raise DartXmlParseError(f"{xml_path} 파일을 읽을 수 없습니다: {exc!r}") from exc

    sanitized = _sanitize_for_parsing(xml_text)

    # DART 원문은 지저분해 recover 파서로 malformed를 복구한다. huge_tree=대형 보고서.
    parser = LET.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    try:
        root = LET.fromstring(sanitized, parser)
    except Exception as exc:
        raise DartXmlParseError(f"{xml_path} XML 파싱 실패: {exc!r}") from exc
    if root is None:
        raise DartXmlParseError(f"{xml_path} XML 파싱 실패: 루트 없음")

    sections: dict[str, str] = {}

    for elem in root.iter():
        if not isinstance(elem.tag, str):  # 주석·PI 등 제외
            continue
        children = list(elem)
        if not children or children[0].tag != "TITLE":  # TITLE 없는 섹션 건너뜀
            continue
        raw_title = "".join(children[0].itertext()).strip()
        if not raw_title:
            continue
        title = _normalize_section_title(raw_title)
        if not title or title in sections:
            continue
        sections[title] = LET.tostring(elem, encoding="unicode")

    return sections
