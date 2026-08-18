"""텍스트 정제기 — DART 원문의 태그·엔티티·공백을 씻어 낸다.

`xml_parser`가 잘라 준 섹션 본문에는 표 태그와 HTML 엔티티가 그대로 남아 있다.
LLM에 넣기 전에, 또는 규칙으로 훑기 전에 이걸 정리한다.

태그를 **빈 문자열이 아니라 개행으로** 바꾼다
  사업보고서 본문은 대부분 표(`<TABLE>`)다. 태그를 빈 문자열로 지우면
  칸 값이 붙어 버려 「삼성전자1,234」처럼 읽을 수 없게 된다. 개행으로 바꾸면
  칸마다 줄이 나뉘어 사람도 LLM도 읽을 수 있다.

쓰는 곳: `audit/dart` · `build/company_detail` · `build/sales_customers`
        · `extractors/dart/business_report`
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WHITESPACE_RE = re.compile(r"[ \t　]+")


def clean_text(raw_text: str) -> str:
    """태그·엔티티를 걷어내고 줄 단위로 정돈한 텍스트를 돌려준다.

    순서가 중요하다 — 태그를 먼저 개행으로 바꾸고 나서 엔티티를 풀어야 한다.
    엔티티를 먼저 풀면 `&lt;b&gt;`가 진짜 태그가 되어 다음 단계에서 지워진다.
    """
    without_tags = _TAG_RE.sub("\n", raw_text)
    unescaped = html.unescape(without_tags)
    collapsed = _INLINE_WHITESPACE_RE.sub(" ", unescaped)
    lines = (line.strip() for line in collapsed.splitlines())
    return "\n".join(line for line in lines if line).strip()
