# [STEP 1] DART API 호출 -> JSON 파일 저장 스크립트

import os
import sys
import json
import time
import requests

from app.core.config import DART_KEY

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DART_API_KEY = DART_KEY
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw_dart")


# DART API 호출 시 요청 간격을 두어 서버 과부하를 방지.
REQUEST_DELAY_SECONDS = 0.3

# 저장할 폴더가 없으면 자동 생성
os.makedirs(DATA_DIR, exist_ok=True)

def save_dart_api_to_json(corp_code: str, api_name: str, url: str, params: dict):
    """OpenDART API를 호출하고 결과를 고유한 파일명으로 저장합니다."""
    params["crtfc_key"] = DART_API_KEY

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        file_path = os.path.join(DATA_DIR, f"{corp_code}_{api_name}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        print(f"✓ 성공: {file_path} 저장 완료")
    except Exception as e:
        print(f"✗ 실패: {corp_code}의 {api_name} 수집 중 오류 발생: {e}")
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)

# DART API에서 제공하는 주요 재무제표 및 기업정보를 한 번에 수집하는 함수
def fetch_all_company_data(corp_code: str, bsns_year: str, reprt_code: str):

    base_params = {"corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code}
    
    # 1. 17번 최대주주 현황
    save_dart_api_to_json(
        corp_code, "shareholders",
        "https://opendart.fss.or.kr/api/hyslrSttus.json", base_params.copy()
    )
    # 2. 18번 최대주주 변동현황
    save_dart_api_to_json(
        corp_code, "shareholder_changes",
        "https://opendart.fss.or.kr/api/hyslrChgSttus.json", base_params.copy()
    )
    # 3. 20번 임원 현황
    save_dart_api_to_json(
        corp_code, "executives",
        "https://opendart.fss.or.kr/api/exctvSttus.json", base_params.copy()
    )
    # 4. 16번 사외이사 현황
    save_dart_api_to_json(
        corp_code, "outside_directors",
        "https://opendart.fss.or.kr/api/outcmpnyDrctrNdChangeSttus.json", base_params.copy()
    )
    # 5. 30번 타법인 출자현황
    save_dart_api_to_json(
        corp_code, "investments",
        "https://opendart.fss.or.kr/api/otrCprInvstmntSttus.json", base_params.copy()
    )

if __name__ == "__main__":
    SAMPLE_CORP_CODE = "00126380" 
    fetch_all_company_data(SAMPLE_CORP_CODE, bsns_year="2025", reprt_code="11011")