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

    def match_candidates(
        self, candidates: list[str], *, threshold: float = _DEFAULT_FUZZY_THRESHOLD,
    ) -> list[tuple[str, str, float]]:
        """후보별로 corp_code_master 최고 매칭 1건씩 — threshold를 넘은 것만
        `(후보, 매칭된 법인명, 점수)`로 돌려준다(§4-1, 2026-08-22).

        前 best_candidate_match()가 `ORDER BY score DESC LIMIT 1`로 전체
        최댓값 1건만 주던 것을 대체한다(옛 API는 프로덕션 참조가 0곳이 돼
        2026-08-22 삭제). 1.000 동점이 여럿일 때 무엇이 이길지
        정의돼 있지 않아 물리적 행 순서에 좌우됐고, 실존 법인 「일이」
        (01355031)가 「SK하이닉스」를 이겼다. 선택 규칙은 호출부(AnchorExtractor)
        가 정한다 — 저장소는 "무엇이 얼마로 걸렸나"만 답한다.

        후보마다 LATERAL 서브쿼리를 돌려 `corp_name %% c`가 후보별로 GIN
        인덱스(idx_corp_name_trgm)를 그대로 타게 한다.
        """
        if not candidates:
            return []
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.cand, m.corp_name, m.sim
                FROM unnest(%(candidates)s::text[]) AS c(cand)
                CROSS JOIN LATERAL (
                    SELECT corp_name, similarity(corp_name, c.cand) AS sim
                    FROM corp_code_master
                    WHERE corp_name %% c.cand
                    ORDER BY sim DESC, corp_name
                    LIMIT 1
                ) AS m
                WHERE m.sim >= %(threshold)s
                """,
                {"candidates": candidates, "threshold": threshold},
            )
            return [(cand, corp_name, float(sim)) for cand, corp_name, sim in cur.fetchall()]

    def alias_exact_match(self, candidates: list[str]) -> Optional[str]:
        """후보 중 `company_aliases`에 정확히 등록된 것의 **정식 법인명**
        (`canon_name`) — 없으면 None.

        ★2026-08-23: 전에는 **걸린 별칭 문자열 그대로**("네이버")를 돌려줬다.
          그러면 호출부가 그 문자열을 EntityResolver에 다시 넘기고, resolve()는
          corp_code_master만 보므로 similarity('NAVER','네이버')=0.000에 걸려
          **같은 회사를 두 번째 창구에서 다시 놓쳤다**(현황서 §4-6).
          canon_name('NAVER Corporation')을 주면 resolve()의 fuzzy 경로가
          normalize_company_name()으로 'naver'를 만들어 'NAVER'에 1.000으로
          붙는다 — EntityResolver._tier()가 이미 열어 둔 「정규화/별칭 경유
          fuzzy」 계층(tier 1)이 정확히 이 경우다.

        pg_trgm이 원리적으로 못 잇는 경우를 위한 2차 창구다.
        similarity('NAVER','네이버')는 **0.000**이다 — 트라이그램은 한글과
        영문 사이에 공유하는 3글자 조각이 없다. corp_code_master에
        'NAVER'(00266961)로만 등록된 회사를 한글 질의로는 영원히 못 찾는다.

        company_aliases에는 ('네이버','네이버','NAVER Corporation')이 있다.
        표의 키는 normalize_company_name()으로 정규화된 형태라 같은 함수로
        맞춰 조회한다(resolve_candidates()의 fuzzy 경로와 같은 규약).

        ★이 창구는 일상어와 충돌한다 — 3글자 이하 별칭 523개 중 215개를 Kiwi가
        일반명사로 읽는다(실측 2026-08-22: 「기타」·「대상」·「동남」·「디스코」).
        그래서 호출부가 **Kiwi가 고유명사(NNP)로 본 후보만** 넘겨야 하고,
        DART 1차 매칭이 비었을 때만 물어야 한다. 여러 개가 걸리면 긴 것을
        고른다 — 짧을수록 일상어와 겹칠 확률이 높다.
        """
        if not candidates:
            return None
        by_key: dict[str, str] = {}
        for cand in candidates:
            key = normalize_company_name(cand)
            if key:
                by_key.setdefault(key, cand)
        if not by_key:
            return None
        with postgres_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.key, MIN(a.canon_name) AS canon_name
                FROM unnest(%(keys)s::text[]) AS k(key)
                JOIN company_aliases a
                  ON a.alias_key = k.key OR a.canonical_key = k.key
                WHERE a.canon_name IS NOT NULL AND a.canon_name <> ''
                GROUP BY k.key
                """,
                {"keys": list(by_key)},
            )
            hits = [(by_key[row[0]], row[1]) for row in cur.fetchall()]
        if not hits:
            return None
        # 여러 개가 걸리면 **후보가 긴 것**을 고른다 — 짧을수록 일상어와
        # 겹칠 확률이 높다. 고르는 기준은 후보, 돌려주는 것은 정식 법인명이다.
        return max(hits, key=lambda h: len(h[0]))[1]
