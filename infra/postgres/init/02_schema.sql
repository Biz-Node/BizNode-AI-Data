-- BizNode PostgreSQL 스키마
--
-- ★손으로 고치지 마세요. **실제 DB에서 뽑아낸 것**입니다.
--   전에는 손으로 관리했는데 실제 스키마와 어긋났습니다 — 이미 없앤 `companies`
--   표를 만들면서, 실제로 쓰는 표 14개(company_attributes·name_verdicts·
--   purged_edges …)와 뷰 market_metrics 는 빠져 있었습니다.
--
-- 출처   infra/share/postgres.sql.gz 덤프 (PostgreSQL 16)
-- 검증   빈 DB에 이 파일을 돌려 실DB와 테이블·뷰·컬럼·인덱스·제약이 모두
--        일치함을 확인했습니다.
--
-- ── 다시 뽑는 법 ────────────────────────────────────────────────
--   docker exec biznode-postgres pg_dump -U biznode -d biznode \
--     --schema-only --no-owner --no-privileges \
--     > infra/postgres/init/02_schema.sql
--
--   ★**지금 컨테이너의 pg_dump 로는 이 파일이 그대로 안 나옵니다**(2026-08-28).
--     빌드가 달라져 `\restrict` 줄과 `Owner: -` 주석이 붙고, `--schema-only` 는
--     이 파일에 있는 빈 `Data for Name:` 헤더(빈 DB 를 통째로 덤프한 흔적)를
--     지웁니다. 그대로 덮으면 **1,000줄 넘는 서식 잡음**에 진짜 변경이 묻힙니다.
--     그래서 embedding_cache 는 표 하나 분량만 손으로 넣고, 아래 방법으로
--     실DB 와 일치함을 확인했습니다. 다음에도 표 한둘이면 같은 방식이 낫습니다.
--
--   ── 손으로 넣었으면 반드시 이렇게 검증하세요 ──
--     docker exec biznode-postgres psql -U biznode -d postgres \
--       -c 'CREATE DATABASE schema_check'
--     docker exec -i biznode-postgres psql -U biznode -d schema_check \
--       < infra/postgres/init/02_schema.sql
--     두 DB 의 information_schema 컬럼·제약·인덱스를 대조해 차이 0 을 확인하고
--     schema_check 를 지웁니다. 2026-08-28 실측: 차이 0.
--
--   ★스키마를 바꿨으면 반드시 다시 뽑으세요. 안 하면 또 어긋납니다.
--     덤프가 없는 새 클론에서는 이 파일이 **DB를 세우는 유일한 길**입니다
--     (infra/share/ 는 124MB라 .gitignore 에 있습니다).
--
-- ── 언제 실행되나 ──────────────────────────────────────────────
--   컨테이너가 **데이터 디렉터리가 비었을 때만** 실행합니다.
--   ★이미 표가 있는 DB에 돌리면 에러가 납니다 — 덮어쓰지 않는 게 맞습니다.
--     데이터까지 필요하면 `bash infra/share_all.sh load` 로 덤프를 복원하세요.
--
--   확장(pg_trgm)은 01_extensions.sql 과 겹치지만 둘 다 IF NOT EXISTS 라
--   무해하고, 이 파일 하나만으로도 스키마가 서도록 남겨 둡니다.

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: business_overview; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.business_overview (
    corp_code character(8) NOT NULL,
    bsns_year smallint NOT NULL,
    overview_text text,
    products_text text,
    source_doc character(14),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: business_segments; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.business_segments (
    corp_code character(8) NOT NULL,
    bsns_year smallint NOT NULL,
    segment_name text NOT NULL,
    revenue bigint,
    revenue_ratio numeric(5,2),
    source_doc character(14),
    revenue_trusted boolean DEFAULT true NOT NULL,
    ratio_trusted boolean DEFAULT true NOT NULL,
    trust_reason text
);


--
-- Name: company_aliases; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.company_aliases (
    alias_key text NOT NULL,
    canonical_key text NOT NULL,
    canon_name text,
    block_key text,
    source text NOT NULL,
    note text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: company_attributes; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.company_attributes (
    node_key text NOT NULL,
    corp_code character(8),
    name text NOT NULL,
    norm_name text,
    induty text,
    ceo_nm text,
    est_dt date,
    name_en text,
    sector_label text,
    sector jsonb,
    etf_list jsonb,
    is_seed boolean DEFAULT false NOT NULL,
    vehicle_type text,
    resolution_note text,
    revenue_snapshot bigint,
    revenue_year smallint,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: company_profiles; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.company_profiles (
    corp_code character(8) NOT NULL,
    version integer NOT NULL,
    text text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: corp_code_master; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.corp_code_master (
    corp_code character(8) NOT NULL,
    corp_name text NOT NULL,
    stock_code character varying(6),
    market text,
    modify_date date
);


--
-- Name: corp_code_verdicts; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.corp_code_verdicts (
    node_key text NOT NULL,
    name text NOT NULL,
    verdict text NOT NULL,
    corp_code character(8),
    why text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    deg integer
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.documents (
    rcept_no character(14) NOT NULL,
    corp_code character(8),
    doc_type text,
    title text,
    rcept_dt date,
    raw_path text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: edge_audits; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.edge_audits (
    id bigint NOT NULL,
    src_name text,
    edge_type text NOT NULL,
    tgt_name text,
    evidence_id text,
    source_doc text,
    trail jsonb NOT NULL,
    moved_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: edge_audits_id_seq; Type: SEQUENCE; Schema: public; Owner: biznode
--

CREATE SEQUENCE public.edge_audits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: edge_audits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: biznode
--

ALTER SEQUENCE public.edge_audits_id_seq OWNED BY public.edge_audits.id;


--
-- Name: edge_subtypes; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.edge_subtypes (
    edge_type text NOT NULL,
    subtype text NOT NULL,
    seen_count integer DEFAULT 1 NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: embedding_cache; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.embedding_cache (
    embedding_model text NOT NULL,
    text_hash text NOT NULL,
    embedding double precision[] NOT NULL,
    text_preview text,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_merge_verdicts; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.event_merge_verdicts (
    id_a text NOT NULL,
    id_b text NOT NULL,
    verdict text NOT NULL,
    reason text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: extraction_runs; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.extraction_runs (
    corp_code character(8) NOT NULL,
    company_name text NOT NULL,
    run_at timestamp with time zone DEFAULT now() NOT NULL,
    years smallint,
    month_split boolean DEFAULT false,
    extract_limit integer,
    collected integer,
    rule_passed integer,
    url_resolved integer,
    body_ok integer,
    router_passed integer,
    extracted integer,
    edges integer,
    cost_krw integer,
    note text
);


--
-- Name: financials; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.financials (
    corp_code character(8) NOT NULL,
    bsns_year smallint NOT NULL,
    reprt_code character varying(5) NOT NULL,
    revenue bigint,
    operating_profit bigint,
    net_profit bigint,
    total_assets bigint,
    total_equity bigint,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    fs_div character varying(3),
    total_liabilities bigint
);


--
-- Name: listed_shares; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.listed_shares (
    corp_code character(8) NOT NULL,
    stock_code character varying(6),
    listed bigint NOT NULL,
    issued bigint,
    treasury bigint,
    bsns_year smallint,
    reprt_code character varying(5),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    suspect boolean DEFAULT false NOT NULL,
    suspect_why text
);


--
-- Name: market_data; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.market_data (
    stock_code character varying(6) NOT NULL,
    trade_date date NOT NULL,
    close_price bigint,
    market_cap bigint,
    volume bigint,
    trade_value bigint,
    listed_shares bigint,
    source text,
    open_price bigint,
    high_price bigint,
    low_price bigint,
    change_pct numeric(8,2)
);


--
-- Name: market_metrics; Type: VIEW; Schema: public; Owner: biznode
--

CREATE VIEW public.market_metrics AS
 WITH latest_fin AS (
         SELECT DISTINCT ON (financials.corp_code) financials.corp_code,
            financials.bsns_year,
            financials.fs_div,
            financials.revenue,
            financials.net_profit,
            financials.total_equity
           FROM public.financials
          ORDER BY financials.corp_code, financials.bsns_year DESC
        )
 SELECT s.corp_code,
    m.stock_code,
    m.trade_date,
    m.close_price,
    m.change_pct,
    m.volume,
    m.trade_value,
    s.listed AS listed_shares,
    ((m.close_price)::numeric * (s.listed)::numeric) AS market_cap,
    f.bsns_year AS fin_year,
    f.fs_div,
        CASE
            WHEN (f.net_profit > 0) THEN round((((m.close_price)::numeric * (s.listed)::numeric) / (f.net_profit)::numeric), 2)
            ELSE NULL::numeric
        END AS per,
        CASE
            WHEN (f.total_equity > 0) THEN round((((m.close_price)::numeric * (s.listed)::numeric) / (f.total_equity)::numeric), 2)
            ELSE NULL::numeric
        END AS pbr,
        CASE
            WHEN (f.revenue > 0) THEN round((((m.close_price)::numeric * (s.listed)::numeric) / (f.revenue)::numeric), 2)
            ELSE NULL::numeric
        END AS psr
   FROM ((public.market_data m
     JOIN public.listed_shares s ON ((((s.stock_code)::text = (m.stock_code)::text) AND (NOT s.suspect))))
     LEFT JOIN latest_fin f ON ((f.corp_code = s.corp_code)));


--
-- Name: name_merge_verdicts; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.name_merge_verdicts (
    key_a text NOT NULL,
    key_b text NOT NULL,
    same boolean NOT NULL,
    reason text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: name_verdicts; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.name_verdicts (
    name text NOT NULL,
    kind text DEFAULT 'entity'::text NOT NULL,
    is_proper boolean NOT NULL,
    reason text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: news_articles; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.news_articles (
    url text NOT NULL,
    title text NOT NULL,
    press text,
    published_at timestamp with time zone,
    source_channel text,
    title_hash text,
    body_length integer,
    rule_passed boolean,
    llm_relevant boolean,
    matched_corps jsonb,
    extracted_at timestamp with time zone,
    collected_at timestamp with time zone DEFAULT now() NOT NULL,
    topics jsonb
);


--
-- Name: person_merge_verdicts; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.person_merge_verdicts (
    key_a text NOT NULL,
    key_b text NOT NULL,
    same boolean NOT NULL,
    reason text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: product_names; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.product_names (
    norm_key text NOT NULL,
    display text NOT NULL,
    seen_count integer DEFAULT 1 NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: purged_edges; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.purged_edges (
    id bigint NOT NULL,
    purged_at timestamp with time zone DEFAULT now() NOT NULL,
    src_name text,
    edge_type text,
    tgt_name text,
    subtype text,
    source_type text,
    source_doc text,
    evidence_id text,
    stage1 text,
    verdict text,
    verdict_why text
);


--
-- Name: purged_edges_id_seq; Type: SEQUENCE; Schema: public; Owner: biznode
--

CREATE SEQUENCE public.purged_edges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purged_edges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: biznode
--

ALTER SEQUENCE public.purged_edges_id_seq OWNED BY public.purged_edges.id;


--
-- Name: purged_nodes; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.purged_nodes (
    id bigint NOT NULL,
    purged_at timestamp with time zone DEFAULT now() NOT NULL,
    label text NOT NULL,
    node_key text,
    name text,
    reason text,
    props jsonb
);


--
-- Name: purged_nodes_id_seq; Type: SEQUENCE; Schema: public; Owner: biznode
--

CREATE SEQUENCE public.purged_nodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purged_nodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: biznode
--

ALTER SEQUENCE public.purged_nodes_id_seq OWNED BY public.purged_nodes.id;


--
-- Name: staged_edges; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.staged_edges (
    id bigint NOT NULL,
    run_id bigint,
    src_node_type text NOT NULL,
    src_key text NOT NULL,
    tgt_node_type text NOT NULL,
    tgt_key text NOT NULL,
    edge_type text NOT NULL,
    subtype text,
    properties jsonb NOT NULL,
    origin text NOT NULL,
    source_doc text,
    validated boolean,
    validation_error text,
    loaded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: staged_edges_id_seq; Type: SEQUENCE; Schema: public; Owner: biznode
--

CREATE SEQUENCE public.staged_edges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: staged_edges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: biznode
--

ALTER SEQUENCE public.staged_edges_id_seq OWNED BY public.staged_edges.id;


--
-- Name: unmapped_relations; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.unmapped_relations (
    expression text NOT NULL,
    source_name text,
    target_name text,
    evidence text,
    source_doc text,
    seen_count integer DEFAULT 1 NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vector_chunks; Type: TABLE; Schema: public; Owner: biznode
--

CREATE TABLE public.vector_chunks (
    chunk_id text NOT NULL,
    chunk_type text NOT NULL,
    collection text NOT NULL,
    owner_key text NOT NULL,
    corp_code character(8),
    source_doc text,
    embedding_model text NOT NULL,
    content_hash text,
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    embedded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: edge_audits id; Type: DEFAULT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.edge_audits ALTER COLUMN id SET DEFAULT nextval('public.edge_audits_id_seq'::regclass);


--
-- Name: purged_edges id; Type: DEFAULT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.purged_edges ALTER COLUMN id SET DEFAULT nextval('public.purged_edges_id_seq'::regclass);


--
-- Name: purged_nodes id; Type: DEFAULT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.purged_nodes ALTER COLUMN id SET DEFAULT nextval('public.purged_nodes_id_seq'::regclass);


--
-- Name: staged_edges id; Type: DEFAULT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.staged_edges ALTER COLUMN id SET DEFAULT nextval('public.staged_edges_id_seq'::regclass);


--
-- Data for Name: business_overview; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: business_segments; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: company_aliases; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: company_attributes; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: company_profiles; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: corp_code_master; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: corp_code_verdicts; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: documents; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: edge_audits; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: edge_subtypes; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: embedding_cache; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: event_merge_verdicts; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: extraction_runs; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: financials; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: listed_shares; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: market_data; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: name_merge_verdicts; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: name_verdicts; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: news_articles; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: person_merge_verdicts; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: product_names; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: purged_edges; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: purged_nodes; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: staged_edges; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: unmapped_relations; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Data for Name: vector_chunks; Type: TABLE DATA; Schema: public; Owner: biznode
--


--
-- Name: edge_audits_id_seq; Type: SEQUENCE SET; Schema: public; Owner: biznode
--


--
-- Name: purged_edges_id_seq; Type: SEQUENCE SET; Schema: public; Owner: biznode
--


--
-- Name: purged_nodes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: biznode
--


--
-- Name: staged_edges_id_seq; Type: SEQUENCE SET; Schema: public; Owner: biznode
--


--
-- Name: business_overview business_overview_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.business_overview
    ADD CONSTRAINT business_overview_pkey PRIMARY KEY (corp_code, bsns_year);


--
-- Name: business_segments business_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.business_segments
    ADD CONSTRAINT business_segments_pkey PRIMARY KEY (corp_code, bsns_year, segment_name);


--
-- Name: company_aliases company_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.company_aliases
    ADD CONSTRAINT company_aliases_pkey PRIMARY KEY (alias_key);


--
-- Name: company_attributes company_attributes_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.company_attributes
    ADD CONSTRAINT company_attributes_pkey PRIMARY KEY (node_key);


--
-- Name: company_profiles company_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT company_profiles_pkey PRIMARY KEY (corp_code, version);


--
-- Name: corp_code_master corp_code_master_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.corp_code_master
    ADD CONSTRAINT corp_code_master_pkey PRIMARY KEY (corp_code);


--
-- Name: corp_code_verdicts corp_code_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.corp_code_verdicts
    ADD CONSTRAINT corp_code_verdicts_pkey PRIMARY KEY (node_key);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (rcept_no);


--
-- Name: edge_audits edge_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.edge_audits
    ADD CONSTRAINT edge_audits_pkey PRIMARY KEY (id);


--
-- Name: edge_subtypes edge_subtypes_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.edge_subtypes
    ADD CONSTRAINT edge_subtypes_pkey PRIMARY KEY (edge_type, subtype);


--
-- Name: embedding_cache embedding_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.embedding_cache
    ADD CONSTRAINT embedding_cache_pkey PRIMARY KEY (embedding_model, text_hash);


--
-- Name: event_merge_verdicts event_merge_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.event_merge_verdicts
    ADD CONSTRAINT event_merge_verdicts_pkey PRIMARY KEY (id_a, id_b);


--
-- Name: extraction_runs extraction_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.extraction_runs
    ADD CONSTRAINT extraction_runs_pkey PRIMARY KEY (corp_code, run_at);


--
-- Name: financials financials_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.financials
    ADD CONSTRAINT financials_pkey PRIMARY KEY (corp_code, bsns_year, reprt_code);


--
-- Name: listed_shares listed_shares_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.listed_shares
    ADD CONSTRAINT listed_shares_pkey PRIMARY KEY (corp_code);


--
-- Name: market_data market_data_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.market_data
    ADD CONSTRAINT market_data_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: name_merge_verdicts name_merge_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.name_merge_verdicts
    ADD CONSTRAINT name_merge_verdicts_pkey PRIMARY KEY (key_a, key_b);


--
-- Name: name_verdicts name_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.name_verdicts
    ADD CONSTRAINT name_verdicts_pkey PRIMARY KEY (name, kind);


--
-- Name: news_articles news_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.news_articles
    ADD CONSTRAINT news_articles_pkey PRIMARY KEY (url);


--
-- Name: person_merge_verdicts person_merge_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.person_merge_verdicts
    ADD CONSTRAINT person_merge_verdicts_pkey PRIMARY KEY (key_a, key_b);


--
-- Name: product_names product_names_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.product_names
    ADD CONSTRAINT product_names_pkey PRIMARY KEY (norm_key);


--
-- Name: purged_edges purged_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.purged_edges
    ADD CONSTRAINT purged_edges_pkey PRIMARY KEY (id);


--
-- Name: purged_nodes purged_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.purged_nodes
    ADD CONSTRAINT purged_nodes_pkey PRIMARY KEY (id);


--
-- Name: staged_edges staged_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.staged_edges
    ADD CONSTRAINT staged_edges_pkey PRIMARY KEY (id);


--
-- Name: unmapped_relations unmapped_relations_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.unmapped_relations
    ADD CONSTRAINT unmapped_relations_pkey PRIMARY KEY (expression);


--
-- Name: vector_chunks vector_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: biznode
--

ALTER TABLE ONLY public.vector_chunks
    ADD CONSTRAINT vector_chunks_pkey PRIMARY KEY (chunk_id);


--
-- Name: company_aliases_block; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX company_aliases_block ON public.company_aliases USING btree (block_key);


--
-- Name: idx_company_attributes_corp; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_company_attributes_corp ON public.company_attributes USING btree (corp_code) WHERE (corp_code IS NOT NULL);


--
-- Name: idx_corp_name_trgm; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_corp_name_trgm ON public.corp_code_master USING gin (corp_name public.gin_trgm_ops);


--
-- Name: idx_corp_stock_code; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_corp_stock_code ON public.corp_code_master USING btree (stock_code) WHERE (stock_code IS NOT NULL);


--
-- Name: idx_documents_corp; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_documents_corp ON public.documents USING btree (corp_code, rcept_dt DESC);


--
-- Name: idx_documents_type; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_documents_type ON public.documents USING btree (doc_type);


--
-- Name: idx_edge_audits_ev; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_edge_audits_ev ON public.edge_audits USING btree (evidence_id) WHERE (evidence_id IS NOT NULL);


--
-- Name: idx_edge_audits_type; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_edge_audits_type ON public.edge_audits USING btree (edge_type);


--
-- Name: idx_extraction_runs_corp; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_extraction_runs_corp ON public.extraction_runs USING btree (corp_code);


--
-- Name: idx_listed_shares_stock; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_listed_shares_stock ON public.listed_shares USING btree (stock_code);


--
-- Name: idx_market_data_recent; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_market_data_recent ON public.market_data USING btree (stock_code, trade_date DESC);


--
-- Name: idx_news_corps; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_news_corps ON public.news_articles USING gin (matched_corps);


--
-- Name: idx_news_pending; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_news_pending ON public.news_articles USING btree (collected_at) WHERE (extracted_at IS NULL);


--
-- Name: idx_news_title_hash; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_news_title_hash ON public.news_articles USING btree (title_hash);


--
-- Name: idx_staged_edges_invalid; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_staged_edges_invalid ON public.staged_edges USING btree (edge_type) WHERE (validated IS FALSE);


--
-- Name: idx_staged_edges_pending; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_staged_edges_pending ON public.staged_edges USING btree (edge_type) WHERE (loaded_at IS NULL);


--
-- Name: idx_staged_edges_run; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_staged_edges_run ON public.staged_edges USING btree (run_id);


--
-- Name: idx_staged_edges_src; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_staged_edges_src ON public.staged_edges USING btree (src_key);


--
-- Name: idx_staged_edges_tgt; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_staged_edges_tgt ON public.staged_edges USING btree (tgt_key);


--
-- Name: idx_unmapped_freq; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_unmapped_freq ON public.unmapped_relations USING btree (seen_count DESC);


--
-- Name: idx_vector_chunks_corp; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_vector_chunks_corp ON public.vector_chunks USING btree (corp_code) WHERE (corp_code IS NOT NULL);


--
-- Name: idx_vector_chunks_model; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_vector_chunks_model ON public.vector_chunks USING btree (embedding_model) WHERE is_active;


--
-- Name: idx_vector_chunks_owner; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX idx_vector_chunks_owner ON public.vector_chunks USING btree (chunk_type, owner_key);


--
-- Name: ix_news_corps; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX ix_news_corps ON public.news_articles USING gin (matched_corps);


--
-- Name: ix_news_published; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX ix_news_published ON public.news_articles USING btree (published_at DESC);


--
-- Name: ix_news_topics; Type: INDEX; Schema: public; Owner: biznode
--

CREATE INDEX ix_news_topics ON public.news_articles USING gin (topics);


--
-- PostgreSQL database dump complete
--


