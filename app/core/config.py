"""환경 설정. `.env`를 읽고, 없으면 docker-compose 기본값으로 폴백한다.

로컬 개발은 `docker compose up -d`만 하면 저장소 접속이 바로 되도록
기본값을 compose 설정과 일치시켰다. 외부 API 키만 `.env`에 채우면 된다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# app/core/config.py → app/core → app → <project root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
COMPANY_LIST_DIR = DATA_DIR / "company_list"
COMPANY_MAP_PATH = COMPANY_LIST_DIR / "company_map.json"
ETF_LIST_PATH = COMPANY_LIST_DIR / "company_list_etf.json"
DOCUMENTS_DIR = DATA_DIR / "documents"  # 공시 원문 보관 (임베딩 안 함)

# ── 외부 API 키 (.env 필수) ─────────────────────────────────────
DART_KEY = os.getenv("DART_KEY")
DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

# LLM — 프로젝트 표준은 OpenAI (gpt-4o 추출 · gpt-4o-mini 검증·분류·임베딩).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ── LLM 모델 — ★**Agent 와 답변을 가른다** (2026-08-29) ─────────
#
# 전에는 `app/llm/adapter.DEFAULT_MODEL` **하나**를 Agent 루프와 답변 생성이
# 같이 썼다. 그래서 「Agent 만 바꿔 도구 선택 분산을 본다」가 **구조적으로
# 불가능**했다 — 노브가 하나면 바꾸는 순간 답변 모델도 같이 움직이고, 평가셋
# 점수 차이를 어느 쪽에 귀속시킬지 못 가른다. 이 저장소가 임베딩 드리프트와
# 링 계측기에서 두 번 겪은 것과 **같은 종류의 귀속 문제**다.
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o-mini")
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-4o-mini")

# ★**temperature 도 노브다 — 모델과 붙어 움직이기 때문이다.**
#
#   판정은 재현 가능해야 하므로 0 이 규약이다(`pipeline.llm` 과 같은 말).
#   그런데 **0 을 거부하는 모델이 있다**(실측 2026-08-29):
#
#       gpt-5.6-luna · gpt-5.6-terra · gpt-5.6-sol
#       → Unsupported value: 'temperature' does not support 0.0 with this
#         model. Only the default (1) value is supported.
#
#   즉 모델 이름만 갈아끼우면 400 이 난다. **빈 값이면 아예 안 보낸다** —
#   모델 기본값(1)으로 돈다는 뜻이고, 그 실행은 0 때보다 더 흔들린다. 그래서
#   「luna 로 바꿨더니 분산이 줄었다」를 모델의 효과로 읽으면 안 된다.
#
#   ★**자동으로 빼지 않는다.** 「왜 0 이 아닌가」가 코드에 숨지 않고 `.env` 에
#     보여야 한다. 잘못 조합하면 API 가 400 으로 **크게** 실패하는데, 조용히
#     다른 값으로 도는 것보다 그쪽이 낫다.
AGENT_TEMPERATURE = os.getenv("AGENT_TEMPERATURE", "0.0")
ANSWER_TEMPERATURE = os.getenv("ANSWER_TEMPERATURE", "0.0")


def temperature_kwargs(value) -> dict:
    """`ChatOpenAI` 에 넘길 temperature 인자. **빈 값이면 빈 dict** — 안 보낸다.

    ★한 곳에 둔다. 부르는 자리가 둘(Agent·답변)이라 규칙이 갈리면 한쪽만 0 으로
      도는 실행이 생기고, 그건 어디에도 안 남는다.

    ★**`0.0` 을 「빈 값」으로 읽지 않는다.** 파이썬에서 `0.0` 은 거짓이라
      `if not value` 로 쓰면 **0 을 지정한 실행이 조용히 모델 기본값(1)으로**
      돈다 — 재현성을 지키려고 둔 값이 정확히 반대로 뒤집힌다. 그래서 비어
      있는지는 **문자열일 때만** 본다.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
    return {"temperature": float(value)}

# ── 뉴스 수집 (P2) ─────────────────────────────────────────────
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
BIGKINDS_ACCESS_KEY = os.getenv("BIGKINDS_ACCESS_KEY")

# ── Graph DB (Neo4j) ───────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "biznode_dev_pw")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── RDBMS (PostgreSQL) ─────────────────────────────────────────
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "biznode")
POSTGRES_USER = os.getenv("POSTGRES_USER", "biznode")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "biznode_dev_pw")

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

# ── Vector DB (ChromaDB) ───────────────────────────────────────
# compose에서 8000(FastAPI)과 겹치지 않도록 8001로 노출
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8001"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# ── 캐시/큐 (Redis, 선택) ──────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── 로깅 ───────────────────────────────────────────────────────
# 검색·답변 경계의 trace 로그가 INFO 다. 시끄러우면 `.env` 에 LOG_LEVEL=WARNING.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
