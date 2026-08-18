#!/usr/bin/env bash
# Neo4j 그래프를 팀원에게 넘기기 — 덤프 만들기 / 받아서 복원하기.
#
#   bash infra/share_graph.sh dump     # 내보내기 → infra/share/graph.dump
#   bash infra/share_graph.sh load     # 받은 덤프를 복원
#
# ★왜 컨테이너를 멈추나
#   `neo4j-admin database dump`는 **오프라인 도구**다. 돌고 있는 DB를 덤프하면
#   파일이 깨진다. Community 에디션에는 온라인 백업이 없으므로 잠깐 멈춰야 한다.
#   ※ 수집 배치가 도는 중이면 절대 실행하지 말 것 — 적재가 끊긴다.
#
# ★무엇이 들어가고 무엇이 안 들어가나
#   들어감  : 그래프 전체(노드 · 엣지 · 속성 · 인덱스)
#   안 들어감: PostgreSQL(기사 메타 · 재무 · 공시) · ChromaDB(벡터) · .env
#             → 화면을 띄우려면 그 둘도 필요하다. `share_all.sh` 참고.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/share"
VOL="biznode-ai-data_neo4j_data"
CONT="biznode-neo4j"
IMG="neo4j:5.26-community"          # docker-compose.yml과 같은 버전이어야 한다

# ★Git Bash가 컨테이너 안 경로(`/dump`)를 윈도우 경로로 번역하는 것을 막는다.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

need_docker() {
  command -v docker >/dev/null || { echo "docker가 없습니다"; exit 1; }
}

busy_check() {
  # 수집·적재가 도는 중이면 막는다
  if pgrep -f "batch.ops.(run_companies|pilot_company|finalize)" >/dev/null 2>&1; then
    echo "⛔ 수집·정리 배치가 돌고 있습니다. 끝난 뒤에 실행하세요."
    exit 2
  fi
}

case "${1:-}" in
  dump)
    need_docker; busy_check
    mkdir -p "$OUT"
    echo "■ Neo4j 정지 (덤프는 오프라인 도구입니다)"
    docker stop "$CONT" >/dev/null
    echo "■ 덤프 생성 중 …"
    docker run --rm \
      -v "$VOL":/data \
      -v "$OUT":/dump \
      "$IMG" \
      neo4j-admin database dump neo4j --to-path=/dump --overwrite-destination=true
    echo "■ Neo4j 재시작"
    docker start "$CONT" >/dev/null
    ls -lh "$OUT"/neo4j.dump 2>/dev/null || ls -lh "$OUT"
    echo
    echo "✅ 팀원에게 넘길 파일: infra/share/neo4j.dump"
    echo "   ※ .env는 절대 함께 보내지 마세요 (DART·OpenAI·네이버 키)."
    ;;

  load)
    need_docker; busy_check
    [ -f "$OUT/neo4j.dump" ] || { echo "infra/share/neo4j.dump 가 없습니다"; exit 1; }
    echo "⚠ 지금 그래프를 **덮어씁니다**. 계속하려면 5초 안에 Ctrl+C 하지 마세요."
    sleep 5
    echo "■ Neo4j 정지"
    docker stop "$CONT" >/dev/null 2>&1 || true
    echo "■ 복원 중 …"
    docker run --rm \
      -v "$VOL":/data \
      -v "$OUT":/dump \
      "$IMG" \
      neo4j-admin database load neo4j --from-path=/dump --overwrite-destination=true
    echo "■ Neo4j 시작"
    docker start "$CONT" >/dev/null
    echo "✅ 복원 완료 — 잠시 뒤 http://localhost:7474 에서 확인하세요."
    ;;

  *)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
