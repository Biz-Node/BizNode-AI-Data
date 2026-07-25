-- BizNode PostgreSQL 스키마 (개발계획서 §6)
-- 전부 멱등(IF NOT EXISTS) — 수동 재실행 안전

-- ─────────────────────────────────────────────────────────────
-- 전 종목 마스터 (Tier 1 — 전 종목 검색 지원)
-- corpCode.xml 전량 적재. 관계가 없어도 검색은 되게 한다.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corp_code_master (
    corp_code   CHAR(8) PRIMARY KEY,
    corp_name   TEXT NOT NULL,
    stock_code  VARCHAR(6),
    market      TEXT,
    modify_date DATE
);

-- ★ER 블로킹 인덱스: 표기가 닮은 후보를 DB가 축소해준다.
--   사용 예) SELECT corp_code, corp_name, similarity(corp_name, '삼성전자')
--            FROM corp_code_master WHERE corp_name % '삼성전자' ORDER BY 3 DESC LIMIT 20;
CREATE INDEX IF NOT EXISTS idx_corp_name_trgm
    ON corp_code_master USING GIN (corp_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_corp_stock_code
    ON corp_code_master (stock_code) WHERE stock_code IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 시드 기업 상세 (기업개황 API + 시드 JSON 병합)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    corp_code  CHAR(8) PRIMARY KEY REFERENCES corp_code_master(corp_code),
    name       TEXT NOT NULL,
    stock_code VARCHAR(6),
    market     TEXT,
    sector     JSONB,                    -- ["반도체","로봇"]
    etf_list   JSONB,                    -- ["KODEX 반도체", ...]
    ceo_nm     TEXT,
    induty     TEXT,                     -- 업종
    est_dt     DATE,                     -- 설립일
    is_seed    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 섹터·ETF 필터를 인덱스로 처리 (JSONB containment: sector @> '"반도체"')
CREATE INDEX IF NOT EXISTS idx_companies_sector   ON companies USING GIN (sector);
CREATE INDEX IF NOT EXISTS idx_companies_etf_list ON companies USING GIN (etf_list);
CREATE INDEX IF NOT EXISTS idx_companies_name_trgm
    ON companies USING GIN (name gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────
-- 재무 (RDB 전용 시계열 — 개발계획서 §6-2)
--   Neo4j Company 노드엔 표시용 최근 스냅샷(매출·시총)만 두고,
--   전체 연도·분기 시계열은 여기에만 둔다. 이중 저장 아님.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financials (
    corp_code         CHAR(8) NOT NULL,
    bsns_year         SMALLINT NOT NULL,
    reprt_code        VARCHAR(5) NOT NULL,   -- 11011=사업, 11012=반기 ...
    fs_div            VARCHAR(3),            -- CFS(연결)/OFS(별도) — 수치 기준 명시
    revenue           BIGINT,                -- 매출액
    operating_profit  BIGINT,                -- 영업이익
    net_profit        BIGINT,                -- 당기순이익
    total_assets      BIGINT,                -- 자산총계
    total_liabilities BIGINT,                -- 부채총계 (부채비율 지표)
    total_equity      BIGINT,                -- 자본총계
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, bsns_year, reprt_code)
);
-- 기존 테이블 대비 컬럼 보강(멱등)
ALTER TABLE financials ADD COLUMN IF NOT EXISTS fs_div VARCHAR(3);
ALTER TABLE financials ADD COLUMN IF NOT EXISTS total_liabilities BIGINT;
-- 규모 확대 시 bsns_year 기준 파티셔닝 검토

-- ─────────────────────────────────────────────────────────────
-- 공시 원문 메타 (전문은 파일 스토리지 — "저장은 전부, 임베딩은 선별")
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    rcept_no   CHAR(14) PRIMARY KEY,
    corp_code  CHAR(8),
    doc_type   TEXT,                      -- 공급계약 / 합병 / 소송 / 사업보고서 ...
    title      TEXT,
    rcept_dt   DATE,
    raw_path   TEXT,                      -- data/documents/{rcept_no}/...
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_corp ON documents (corp_code, rcept_dt DESC);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents (doc_type);

-- ─────────────────────────────────────────────────────────────
-- 기업 프로파일 (Vector 청크의 원본 — RDB가 소유, 갱신 시 Vector 동기)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS company_profiles (
    corp_code  CHAR(8) NOT NULL,
    version    INT NOT NULL,
    text       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, version)
);

-- ─────────────────────────────────────────────────────────────
-- 배치 실행 로그 (corp_code 매칭 실패율 모니터링 — 방법서 13장)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_runs (
    id            BIGSERIAL PRIMARY KEY,
    run_type      TEXT NOT NULL,          -- path_a / path_b / path_c
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    total_records INT DEFAULT 0,
    failed_records INT DEFAULT 0,
    unresolved_entities INT DEFAULT 0,    -- corp_code 매칭 실패 건수
    notes         JSONB
);

-- ─────────────────────────────────────────────────────────────
-- 주주 요약 (방법서 10장 [2] "개인 주주 폭발 방지 정책")
--
-- 개인 주주·기관투자자는 Person/Company 노드로 만들지 않고 요약해서 보관한다.
-- 이 테이블이 없으면 개발 중 "이건 어디 넣지?" → Neo4j 노드화 → Person 폭발.
-- companies가 아닌 별도 테이블인 이유: 지분 요약은 공시 주기마다 바뀌는 시점
-- 데이터라, 정적 마스터인 companies에 섞으면 이력이 덮어쓰기로 소멸한다
-- (financials를 companies에서 분리한 것과 같은 이유).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shareholder_summaries (
    corp_code                 CHAR(8) NOT NULL,
    base_date                 DATE NOT NULL,      -- 공시 기준일(결산일)
    major_shareholder_summary JSONB,              -- 개인 주주 요약 (노드화 제외분)
    institutional_holders     JSONB,              -- 연기금·공모펀드 등 기관 요약
    minority_shareholder_stats JSONB,             -- 소액주주 통계 (선택)
    source_doc                TEXT,               -- 근거 rcept_no
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, base_date)
);
CREATE INDEX IF NOT EXISTS idx_shareholder_summaries_corp
    ON shareholder_summaries (corp_code, base_date DESC);

-- ─────────────────────────────────────────────────────────────
-- Vector 청크 레지스트리 (방법서 5장 · 10장 [6] 갱신 흐름)
--
-- "RDBMS 갱신 시 Vector도 동시 갱신"을 실행하려면 무엇이 ChromaDB에 있는지
-- RDB가 알아야 한다. 이 표가 없으면 (a)프로파일 갱신 시 지울 대상 특정 불가
-- (b)엣지 evidence_id의 실존 검증 불가 (c)모델 교체 시 재임베딩 대상 특정 불가.
-- chunk_id는 ChromaDB의 id와 동일 값 = Neo4j 엣지의 evidence_id.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vector_chunks (
    chunk_id        TEXT PRIMARY KEY,             -- = ChromaDB id (= evidence_id)
    chunk_type      TEXT NOT NULL,                -- evidence | profile | event
    collection      TEXT NOT NULL,                -- chroma 컬렉션명
    owner_key       TEXT NOT NULL,                -- edge_id | corp_code | event_id
    corp_code       CHAR(8),                      -- 조회 편의 (기업 단위 일괄 삭제)
    source_doc      TEXT,                         -- rcept_no 등 원문 FK
    embedding_model TEXT NOT NULL,                -- 모델 교체 시 재임베딩 대상 특정
    content_hash    TEXT,                         -- 내용 무변경 시 재임베딩 skip
    version         INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    embedded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vector_chunks_owner
    ON vector_chunks (chunk_type, owner_key);
CREATE INDEX IF NOT EXISTS idx_vector_chunks_corp
    ON vector_chunks (corp_code) WHERE corp_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vector_chunks_model
    ON vector_chunks (embedding_model) WHERE is_active;

-- ─────────────────────────────────────────────────────────────
-- 엣지 스테이징 (파서 → [여기] → Neo4j)
--
-- 파서 산출물을 Neo4j 직행시키지 않고 한 번 받아두는 착지대.
--  (1) 재적재: Neo4j를 비우고 다시 넣을 때 DART 재호출 불필요 (호출 한도 방어)
--  (2) 품질 점검: 노드-엣지 매트릭스 위반 건을 남겨 파서 정확도 추적
--  (3) P2 대조: 뉴스에서 나온 관계가 DART에 있는지 SQL로 비교
-- origin은 엣지 속성 source_type(dart/news)과 같은 의미다. 노드 타입 컬럼과
-- 이름이 겹치지 않도록 origin으로 부른다.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staged_edges (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT REFERENCES ingest_runs(id),
    src_node_type    TEXT NOT NULL,               -- Company | Person | Product ...
    src_key          TEXT NOT NULL,               -- corp_code 또는 정규화 명칭
    tgt_node_type    TEXT NOT NULL,
    tgt_key          TEXT NOT NULL,
    edge_type        TEXT NOT NULL,               -- OWNS_STAKE_IN | SUPPLIES_TO ...
    subtype          TEXT,
    properties       JSONB NOT NULL,              -- 표준 메타 전체(시점·근거·신뢰)
    origin           TEXT NOT NULL,               -- dart | news
    source_doc       TEXT,                        -- rcept_no
    validated        BOOLEAN,                     -- 매트릭스 검증 통과 여부
    validation_error TEXT,                        -- 위반 사유
    loaded_at        TIMESTAMPTZ,                 -- NULL이면 Neo4j 미적재
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_staged_edges_pending
    ON staged_edges (edge_type) WHERE loaded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_staged_edges_invalid
    ON staged_edges (edge_type) WHERE validated IS FALSE;
CREATE INDEX IF NOT EXISTS idx_staged_edges_src ON staged_edges (src_key);
CREATE INDEX IF NOT EXISTS idx_staged_edges_tgt ON staged_edges (tgt_key);
CREATE INDEX IF NOT EXISTS idx_staged_edges_run ON staged_edges (run_id);

-- ─────────────────────────────────────────────────────────────
-- 주가·시가총액 (일별 시세) — DART 아님. data.go.kr 금융위 API 또는 pykrx.
--   주가 API는 stock_code(종목코드) 기준이라 companies.stock_code로 조인한다.
--   Company 노드의 market_cap_snapshot은 여기 최신 행에서 가져온다.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_data (
    stock_code    VARCHAR(6) NOT NULL,
    trade_date    DATE NOT NULL,
    close_price   BIGINT,                  -- 종가
    market_cap    BIGINT,                  -- 시가총액
    volume        BIGINT,                  -- 거래량
    trade_value   BIGINT,                  -- 거래대금
    listed_shares BIGINT,                  -- 상장주식수
    source        TEXT,                    -- data.go.kr | pykrx
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_data_recent
    ON market_data (stock_code, trade_date DESC);

-- ─────────────────────────────────────────────────────────────
-- 사업 개요·현황 (사업보고서 「II. 사업의 내용」 서술 — 경로 C, Sprint 3)
--   원문은 여기, 요약은 ChromaDB profile 컬렉션에 임베딩.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS business_overview (
    corp_code     CHAR(8) NOT NULL,
    bsns_year     SMALLINT NOT NULL,
    overview_text TEXT,                    -- II-1 사업의 개요
    products_text TEXT,                    -- II-2 주요 제품 및 서비스
    source_doc    CHAR(14),                -- 사업보고서 rcept_no
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, bsns_year)
);

-- ─────────────────────────────────────────────────────────────
-- 사업부문별 매출 (사업보고서 II-4 — 정량, 기업상세 매출구성 차트용)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS business_segments (
    corp_code     CHAR(8) NOT NULL,
    bsns_year     SMALLINT NOT NULL,
    segment_name  TEXT NOT NULL,           -- 반도체(DS)/DX 등 사업부문
    revenue       BIGINT,
    revenue_ratio NUMERIC(5,2),            -- 부문 매출 비중(%)
    source_doc    CHAR(14),
    PRIMARY KEY (corp_code, bsns_year, segment_name)
);
