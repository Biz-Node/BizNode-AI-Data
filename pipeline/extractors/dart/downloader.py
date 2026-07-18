from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

import requests

from app.core.config import DART_KEY

DOCUMENT_API_URL = "https://opendart.fss.or.kr/api/document.xml"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_reports"


class DartDownloadError(Exception):     
    """OpenDART API 호출 실패, HTTP 오류, 응답이 zip 형식이 아닌 경우 발생시킨다."""


class DartExtractError(Exception):
    """다운로드한 zip 파일의 압축 해제에 실패했을 때 발생시킨다."""

# 다운로드한 zip 파일을 압축 해제하고, 그 안에 들어있는 본문 XML 파일 경로를 반환함.
def download_report_zip(
    rcept_no: str,
    *,
    api_key: Optional[str] = None, 
    download_dir: Path = DEFAULT_DOWNLOAD_DIR, 
) -> Path:
    
    params = {"crtfc_key": api_key or DART_KEY, "rcept_no": rcept_no}

    try:
        response = requests.get(DOCUMENT_API_URL, params=params)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DartDownloadError(f"{rcept_no} 원본 문서 다운로드 실패: {exc!r}") from exc

    content_type = response.headers.get("Content-Type", "")
    if "zip" not in content_type and not response.content.startswith(b"PK"):
        raise DartDownloadError(
            f"{rcept_no} 원본 문서 응답이 zip 형식이 아닙니다(Content-Type={content_type!r})"
        )

    download_dir.mkdir(parents=True, exist_ok=True)
    zip_path = download_dir / f"{rcept_no}.zip"
    zip_path.write_bytes(response.content)
    return zip_path

# zip_path를 압축 해제하고, 그 안에 들어있는 본문 XML 파일 경로를 반환함.
def extract_xml(zip_path: Path, *, extract_dir: Optional[Path] = None) -> Path:
    
    target_dir = extract_dir or zip_path.with_suffix("")
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)
    except zipfile.BadZipFile as exc:
        raise DartExtractError(f"{zip_path} 압축 해제 실패: {exc!r}") from exc

    xml_paths = sorted(target_dir.rglob("*.xml"))
    if not xml_paths:
        raise DartExtractError(f"{zip_path} 안에 XML 파일이 없습니다")

    return max(xml_paths, key=lambda p: p.stat().st_size)

# 다운로드+압축 해제 후 XML 경로를 반환하는 함수. 
def download_and_extract(
    rcept_no: str,
    *,
    api_key: Optional[str] = None,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
) -> Path:
    zip_path = download_report_zip(rcept_no, api_key=api_key, download_dir=download_dir)
    return extract_xml(zip_path)
