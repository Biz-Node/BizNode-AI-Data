-- BizNode PostgreSQL 확장
-- pg_trgm: 개체 해소(ER) Lexical 블로킹용 trigram 유사도 + GIN 인덱스
--          방법서 12-3 "임베딩 금지, 표기 유사성으로 후보 축소"의 구현 수단
CREATE EXTENSION IF NOT EXISTS pg_trgm;
