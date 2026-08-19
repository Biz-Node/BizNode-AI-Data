"""PostgresRepository — Search Layer의 PostgreSQL 접근 계층.

기업 후보 조회(이름/종목코드 exact + pg_trgm fuzzy 다중 후보)를 담당한다.
검색 orchestration·랭킹·결과 조합은 하지 않는다(기술설계서 §7, Task 2 지침 7절).

★`companies` 표를 읽던 find_by_corp_code()/find_corp_codes_by_sector()는
  제거했다 — `companies`는 ERD 정리 때 삭제된 표이고(후속: company_attributes),
  프로덕션 호출처가 0곳이었다. sector 선필터를 쓰던 SearchRequest.filters도
  계약에서 함께 뺐다.

pipeline/normalizer/resolver.py의 resolve()는 재사용하지 않는다 — 내부적으로
후보를 이미 1건으로 축약하는 구조(_exact_index의 sorted(...)[0], _fuzzy의
break)라 다중 후보를 낼 수 없고, 전역 캐시·전역 커넥션(@lru_cache(maxsize=1),
모듈 전역 _conn/_fuzzy_cache)이 배치(단일 프로세스 순차 실행) 워크로드 전제라
동시 다중 요청을 받는 API 서버 환경에 그대로 재사용하기엔 결합도가 높다.
다만 Resolution dataclass와 normalize_company_name()은 그대로 재사용한다.
"""

from __future__ import annotations

from typing import Optional

from app.core.database import postgres_connection
from pipeline.normalizer.base import normalize_company_name
from pipeline.normalizer.resolver import Resolution

# resolver._FUZZY_THRESHOLD(0.50, ER 목적 정밀도 우선)를 그대로 가져온 잠정값이다.
# 검색 UX에 맞는 threshold는 실측 후 조정 필요(기술설계서 8-1절 "검색용 threshold
# 분리 필요" — 구체적 수치는 아직 미확정이라 ER과 같은 값으로 시작한다).
_DEFAULT_FUZZY_THRESHOLD = 0.50


class PostgresRepository:
    def resolve_candidates(
        self, query: Optional[str], *, limit: int = 10,
        threshold: float = _DEFAULT_FUZZY_THRESHOLD,
    ) -> list[Resolution]:
        """검색어 → 기업 후보 여러 건. resolver.resolve()와 달리 1건으로
        축약하지 않는다(다중 후보 반환이 이 메서드의 존재 이유, Task 2 지침 4-3절).

        이름 정확 일치(대소문자/공백 무시) + 종목코드 정확 일치 + pg_trgm 유사
        일치를 corp_code_master(전 종목, 118,535건 — resolver.resolve()와 동일한
        대상 테이블) 대상으로 모아 limit개까지 반환한다. 매칭 실패는 예외가
        아니라 빈 리스트다.

        영문 회사명("Samsung Electronics") 매칭은 지원하지 않는다 —
        corp_code_master 스키마에 영문명 컬럼이 없다.
        """
        if not query or not query.strip():
            return []
        q = query.strip()

        candidates: dict[str, Resolution] = {}
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT corp_code, corp_name, stock_code FROM corp_code_master "
                "WHERE lower(trim(corp_name)) = lower(trim(%s))",
                (q,),
            )
            for corp_code, corp_name, stock_code in cur.fetchall():
                candidates[corp_code] = Resolution(corp_code, corp_name, stock_code, "exact", 1.0)

            cur.execute(
                "SELECT corp_code, corp_name, stock_code FROM corp_code_master "
                "WHERE stock_code = %s",
                (q,),
            )
            for corp_code, corp_name, stock_code in cur.fetchall():
                candidates.setdefault(
                    corp_code, Resolution(corp_code, corp_name, stock_code, "exact", 1.0)
                )

            remaining = limit - len(candidates)
            if remaining > 0:
                norm_q = normalize_company_name(q)
                cur.execute(
                    """
                    SELECT corp_code, corp_name, stock_code,
                           similarity(corp_name, %(q)s) AS sim
                    FROM corp_code_master
                    WHERE corp_name %% %(q)s
                    ORDER BY sim DESC
                    LIMIT %(k)s
                    """,
                    {"q": norm_q, "k": remaining + len(candidates)},
                )
                for corp_code, corp_name, stock_code, sim in cur.fetchall():
                    if corp_code in candidates:
                        continue
                    if sim is not None and sim >= threshold:
                        candidates[corp_code] = Resolution(
                            corp_code, corp_name, stock_code, "fuzzy", float(sim)
                        )

        return list(candidates.values())[:limit]

    def best_candidate_match(self, candidates: list[str]) -> Optional[tuple[str, float]]:
        """후보 문자열 목록 중 corp_code_master와 가장 유사한 1건을 고른다
        (AnchorExtractor 전용, Task 9). resolve_candidates()와 달리 다중
        후보를 반환하지 않는다 — "이 후보들 중 실제 회사명에 제일 가까운 게
        뭔가"라는 존재 확인 질의라 최고점 1건이면 충분하다.

        `corp_name % ANY(candidates)`로 GIN 인덱스(idx_corp_name_trgm)를 그대로
        타면서(Task 9 실측: Bitmap Index Scan, 후보 4~8개 기준 15~25ms) 후보
        전체를 단일 쿼리로 처리한다. `word_similarity()`/`<%` 연산자는 이
        컬럼 구조에서 인덱스를 타지 않아(Task 9 실측: Seq Scan, 400~800ms)
        채택하지 않았다 — gin_trgm_ops opclass가 `%`/`%>`만 지원하고 우리가
        필요한 방향(단어가 문장 안에 있는지)에 대응하는 `<%`는 인덱스
        전략이 없다.
        """
        if not candidates:
            return None
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  (SELECT c FROM unnest(%(candidates)s::text[]) AS c
                   ORDER BY similarity(corp_name, c) DESC LIMIT 1) AS matched_candidate,
                  (SELECT max(similarity(corp_name, c))
                   FROM unnest(%(candidates)s::text[]) AS c) AS score
                FROM corp_code_master
                WHERE corp_name %% ANY(%(candidates)s::text[])
                ORDER BY score DESC
                LIMIT 1
                """,
                {"candidates": candidates},
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None
            matched_candidate, score = row
            return (matched_candidate, float(score))
