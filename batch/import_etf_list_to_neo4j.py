# ETF 구성종목 마스터 리스트 적재 실행 스크립트 (CLI 진입점)

from __future__ import annotations

from pipeline.importer.neo4j_importer import import_etf_master_list

if __name__ == "__main__":
    import_etf_master_list()
