# 추출된 텍스트의 출처(원본 공시 문서) 정보를 담는 DTO.

from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Source:
    dart_rcept_no: str
    xml_path: str
