"""저장소 접속 헬퍼.

접속정보는 `app.core.config`에서 읽는다(= .env 또는 docker-compose 기본값).
os.getenv를 직접 쓰지 않는 이유: 기본값 폴백이 config에 한 곳으로 모여 있어야
로컬(docker) / 운영 전환 시 누락이 생기지 않는다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import GraphDatabase

from app.core import config


# ── Graph DB ───────────────────────────────────────────────────
class Neo4jClient:
    def __init__(self) -> None:
        self.uri = config.NEO4J_URI
        self.user = config.NEO4J_USER
        self.password = config.NEO4J_PASSWORD
        self.database = config.NEO4J_DATABASE
        self._driver = None

    def connect(self):
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        return self._driver

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    @property
    def driver(self):
        return self._driver


neo4j_client = Neo4jClient()


@contextmanager
def neo4j_session() -> Iterator:
    """일회성 배치용 세션. 드라이버 생성·해제를 함께 관리한다."""
    driver = GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
    )
    try:
        with driver.session(database=config.NEO4J_DATABASE) as session:
            yield session
    finally:
        driver.close()


# ── RDBMS (PostgreSQL) ─────────────────────────────────────────
@contextmanager
def postgres_connection() -> Iterator:
    """psycopg3 커넥션. 정상 종료 시 커밋, 예외 시 롤백."""
    import psycopg  # 지연 임포트 — Neo4j만 쓰는 배치에서 불필요한 의존을 피한다

    conn = psycopg.connect(config.POSTGRES_DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
