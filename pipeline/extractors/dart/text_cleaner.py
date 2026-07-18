# 텍스트 정제기: HTML 태그 제거, HTML 엔티티 unescape, 공백 정리, 줄바꿈 정리.

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WHITESPACE_RE = re.compile(r"[ \t　]+")


def clean_text(raw_text: str) -> str:

    without_tags = _TAG_RE.sub("\n", raw_text)
    unescaped = html.unescape(without_tags)
    collapsed = _INLINE_WHITESPACE_RE.sub(" ", unescaped)
    lines = (line.strip() for line in collapsed.splitlines())
    return "\n".join(line for line in lines if line).strip()
