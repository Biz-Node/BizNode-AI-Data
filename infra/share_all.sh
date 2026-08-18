#!/usr/bin/env bash
# 팀원에게 **데이터 전체**를 넘긴다 — 그래프 + 관계형 + 벡터.
#
#   bash infra/share_all.sh dump     # 내보내기 → infra/share/
#   bash infra/share_all.sh load     # 받은 것을 복원
#
# ★왜 셋 다 필요한가
#   그래프(Neo4j)만 받으면 화면이 반쯤 빈다. 셋이 서로를 참조하기 때문이다:
#
#       Neo4j       관계 · 노드 — 엣지가 `evidence_id`로 근거를 가리킨다
#       PostgreSQL  기사 메타(제목·언론사·URL) · 재무 · 사업부문 · 공시
#       ChromaDB    `evidence_id`가 가리키는 **근거 문장 본문** + 기업 검색 카드
#
#   즉 Neo4j만 있으면 「이 관계의 근거는?」에 답할 수 없고, 재무·뉴스 화면도 못 그린다.
#
# ★넘기면 안 되는 것
#   `.env` — DART · OpenAI · 네이버 키가 들어 있다. `.env.example`만 주고
#   받는 쪽이 자기 키를 넣게 한다. (키가 없어도 **조회는 되고 수집만 안 된다**.)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$HERE/share"
IMG="neo4j:5.26-community"

# ★Git Bash(MSYS)가 `/out` 같은 **컨테이너 안 경로**를 윈도우 경로로 바꿔 버린다.
#   실측: `tar czf /out/chroma.tar.gz` → `C:/Program Files/Git/out/chroma.tar.gz`
#   로 번역돼 「No such file or directory」로 죽었다.
#   `MSYS_NO_PATHCONV=1`로 그 변환을 끈다(리눅스·맥에서는 무시된다).
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

busy_check() {
  if pgrep -f "batch.ops.(run_companies|pilot_company|finalize)" >/dev/null 2>&1; then
    echo "⛔ 수집·정리 배치가 돌고 있습니다. 끝난 뒤에 실행하세요."
    exit 2
  fi
}

case "${1:-}" in
  dump)
    busy_check
    mkdir -p "$OUT"

    echo "■ [1/3] PostgreSQL — 기사 메타 · 재무 · 공시"
    docker exec biznode-postgres pg_dump -U biznode -d biznode --clean --if-exists \
      | gzip > "$OUT/postgres.sql.gz"

    echo "■ [2/3] ChromaDB — 근거 문장 · 기업 카드"
    #   실행 중 복사해도 되지만 안전하게 멈췄다 켠다
    docker stop biznode-chroma >/dev/null
    docker run --rm -v biznode-ai-data_chroma_data:/src -v "$OUT":/out alpine \
      tar czf /out/chroma.tar.gz -C /src .
    docker start biznode-chroma >/dev/null

    echo "■ [3/3] Neo4j — 그래프 (오프라인 덤프라 잠깐 멈춥니다)"
    docker stop biznode-neo4j >/dev/null
    docker run --rm -v biznode-ai-data_neo4j_data:/data -v "$OUT":/dump "$IMG" \
      neo4j-admin database dump neo4j --to-path=/dump --overwrite-destination=true
    docker start biznode-neo4j >/dev/null

    echo
    du -h "$OUT"/* 2>/dev/null | sort -h
    echo
    echo "✅ infra/share/ 를 통째로 넘기면 됩니다."
    echo "   ※ .env 는 넣지 마세요. 받는 쪽은 .env.example 로 자기 키를 씁니다."
    ;;

  load)
    busy_check
    for f in postgres.sql.gz chroma.tar.gz neo4j.dump; do
      [ -f "$OUT/$f" ] || { echo "infra/share/$f 가 없습니다"; exit 1; }
    done
    echo "⚠ 지금 로컬 DB 3개를 **전부 덮어씁니다**. 5초 안에 Ctrl+C."
    sleep 5

    echo "■ [1/3] PostgreSQL 복원"
    gunzip -c "$OUT/postgres.sql.gz" | docker exec -i biznode-postgres psql -U biznode -d biznode

    echo "■ [2/3] ChromaDB 복원"
    docker stop biznode-chroma >/dev/null
    docker run --rm -v biznode-ai-data_chroma_data:/dst -v "$OUT":/in alpine \
      sh -c "rm -rf /dst/* && tar xzf /in/chroma.tar.gz -C /dst"
    docker start biznode-chroma >/dev/null

    echo "■ [3/3] Neo4j 복원"
    docker stop biznode-neo4j >/dev/null
    docker run --rm -v biznode-ai-data_neo4j_data:/data -v "$OUT":/dump "$IMG" \
      neo4j-admin database load neo4j --from-path=/dump --overwrite-destination=true
    docker start biznode-neo4j >/dev/null

    echo
    echo "✅ 복원 완료. 확인:"
    echo "   python -m batch.audit.graph        # 무결성 (크로스-DB 참조까지 봅니다)"
    echo "   http://localhost:7474             # Neo4j 브라우저"
    ;;

  *)
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
