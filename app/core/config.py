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

# LLM — 현재 프로젝트 표준은 OpenAI.
# ANTHROPIC_API_KEY는 기존 llm_postprocess.py(Claude Haiku Batch)가 참조하므로
# 마이그레이션 전까지 남겨둔다. 미설정 시 해당 모듈만 동작하지 않는다.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

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

# ── 캐시/큐 (Redis, 선택) ──────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
