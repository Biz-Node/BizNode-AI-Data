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
# ★**둘이 다른 값인 것이 실측 결과다**(2026-08-29 · Evaluation §10-9).
#   `gpt-5.6-sol` 로 **둘 다** 바꿔 평가셋을 돌렸더니 Agent 쪽이 계약을 깼다 —
#   한 케이스가 도구를 **14회**(상한 12) 불렀고, 총 호출이 36 → **96**(2.7배),
#   소요가 134 → 310초였다. 답변 쪽은 반대로 `unlinked` 가 4 → 2 로 줄었다.
#   그래서 **재료를 모으는 쪽은 싼 모델, 답변을 쓰는 쪽은 좋은 모델**로 둔다.
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
#   ★**기본값이 둘 다 다르다** — 모델이 다르기 때문이다. Agent 는
#     `gpt-4o-mini` 라 0 을 지킬 수 있고, 답변은 gpt-5.6 계열이라 **비워야**
#     한다. 모델과 이 값은 **항상 함께** 움직인다
#     (`tests/llm/test_model_knobs.py::test_defaults_are_a_consistent_combination`).
AGENT_TEMPERATURE = os.getenv("AGENT_TEMPERATURE", "0.0")
ANSWER_TEMPERATURE = os.getenv("ANSWER_TEMPERATURE", "")

# ★**추론 세기 — 전송 경로와 함께 움직인다.** 아래 `reasoning_kwargs` 참고.
#   기본이 **빈 값**인 것은 기본 Agent 모델(`gpt-4o-mini`)이 이 인자 자체를
#   거부하기 때문이다. Agent 를 gpt-5.6 계열로 바꾸면 **반드시 함께** 채워야
#   한다 — `none` 이면 기존 경로, `low` 이상이면 Responses API 로 넘어간다.
AGENT_REASONING_EFFORT = os.getenv("AGENT_REASONING_EFFORT", "")


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


def reasoning_kwargs(effort: str) -> dict:
    """`reasoning_effort` 와 **그것이 강제하는 전송 경로**를 함께 낸다.

    ★**둘을 따로 못 고른다.** chat.completions 는 function tools 와 추론을 함께
      못 쓴다(실측 2026-08-29):

          Function tools with reasoning_effort are not supported for
          gpt-5.6-luna in /v1/chat/completions. To use function tools,
          use /v1/responses or set reasoning_effort to 'none'.

      그래서 `none` 이 아닌 값을 고르면 **Responses API 로 가야 한다.** 노브를
      둘로 두면 한쪽만 바꿔 놓고 400 을 맞는 조합이 생긴다 — 그 조합을 만들 수
      없게 여기서 한 번에 낸다.

    ★빈 값이면 아무것도 안 보낸다. `gpt-4o-mini` 같은 비추론 모델은
      `reasoning_effort` 자체를 거부하므로, 모델을 되돌릴 때 이 값도 비워야 한다.
    """
    effort = (effort or "").strip()
    if not effort:
        return {}
    if effort == "none":
        return {"reasoning_effort": "none"}
    return {"reasoning_effort": effort, "use_responses_api": True}

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
