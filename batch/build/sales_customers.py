"""사업보고서 II-4「매출 및 수주상황」에서 거래처를 추출해 적재한다.

기존 사업보고서 파서가 이 절을 보지 않아 거래처가 통째로 빠져 있었다
(자세한 경위는 `pipeline/extractors/dart/sales_customers.py` 머리말).

    python -m batch.build.sales_customers --dry-run   # 무엇이 나올지만
    python -m batch.build.sales_customers             # 스테이징 + 적재

★중복 방지 — 세 겹으로 막는다
  1) `staged_edges`에 같은 (출발,도착,엣지,subtype)이 이미 있으면 건너뛴다
  2) Neo4j에 같은 관계가 이미 있으면(공급계약 공시·뉴스에서 온 것) 건너뛴다
  3) 그래도 남으면 `MERGE`가 같은 식별자로 접는다
  실제로 한미반도체→SK하이닉스는 공급계약 공시로 이미 들어와 있어 1·2에서 걸린다.

★원문은 이미 받아둔 것을 쓴다. DART 재호출이 없으므로 비용은 LLM 추출분뿐이다.
"""

from __future__ import annotations

import argparse
import glob
import re
import json
import os
import sys

from app.core.config import ETF_LIST_PATH
from app.core.database import neo4j_session, postgres_connection
from pipeline.extractors.dart.downloader import DEFAULT_DOWNLOAD_DIR
from pipeline.extractors.dart.sales_customers import SECTION_NAME, extract_customers
from pipeline.extractors.dart.text_cleaner import clean_text
from pipeline.extractors.dart.xml_parser import parse_sections
from pipeline.importer.business_report_loader import build_contract_relation_document
from pipeline.importer.evidence import upsert_evidence
from pipeline.importer.graph_loader import load_staged_to_neo4j
from pipeline.importer.staging import stage_document

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# gpt-4o 한 번 호출당 대략치(본문 1만자 내외) — 뉴스 기사보다 길어 높게 잡는다
_COST_KRW = 40


def _section_text(rcept_no: str) -> str:
    """이미 받아둔 원문에서 「매출 및 수주상황」 본문을 꺼낸다."""
    files = glob.glob(os.path.join(DEFAULT_DOWNLOAD_DIR, rcept_no, "**", "*.xml"),
                      recursive=True)
    if not files:
        return ""
    try:
        secs = parse_sections(max(files, key=os.path.getsize))
    except Exception:
        return ""
    want = SECTION_NAME.replace(" ", "")
    key = next((t for t in secs if want in t.replace(" ", "")), None)
    return clean_text(secs[key]) if key else ""


def _existing_pairs(conn) -> set[tuple]:
    """이미 스테이징된 (출발,도착,엣지,subtype) 조합."""
    with conn.cursor() as cur:
        cur.execute("SELECT src_key, tgt_key, edge_type, coalesce(subtype,'') "
                    "FROM staged_edges")
        return {tuple(r) for r in cur.fetchall()}


def _existing_in_graph() -> set[tuple[str, str]]:
    """Neo4j에 이미 있는 공급 관계를 **키 종류를 섞어** 돌려준다.

    ★staged_edges의 키는 시드 기업이면 `corp_code`(00161383), stub이면
      `norm_name`(마이크론테크놀로지)이다. 반면 그래프 노드는 둘 다 가진다.
      처음엔 norm_name끼리만 비교해서 **중복 검사가 통째로 헛돌았다** —
      한미반도체→SK하이닉스가 공급계약 공시로 이미 있는데 「신규」로 잡혔다.
      그래서 각 노드의 corp_code와 norm_name을 **모두** 넣어 어느 쪽으로 와도
      걸리게 한다.
    """
    q = ("MATCH (a)-[r:SUPPLIES_TO]->(b) RETURN "
         "coalesce(a.corp_code,'') AS ac, coalesce(a.norm_name,'') AS an, "
         "coalesce(b.corp_code,'') AS bc, coalesce(b.norm_name,'') AS bn")
    out: set[tuple[str, str]] = set()
    with neo4j_session() as session:
        for r in session.run(q):
            for s in (r["ac"], r["an"]):
                for t in (r["bc"], r["bn"]):
                    if s and t:
                        out.add((s, t))
    return out


# 회사 이름이 아니라 **집단을 가리키는 서술** — 노드로 만들면 안 된다.
# 실측: 「삼성전자및삼성전자의종속회사」「현대자동차및그종속회사」가 그대로 들어왔다.
_GROUP_MARKERS = ("및그종속", "및종속", "의종속회사", "등계열", "계열회사",
                  "관계회사", "및그", "등의", "외다수", "기타")

# ★익명 표기 — 사업보고서는 고객사를 「A사」「S사」로 가리는 일이 매우 흔하다.
#   실측(2026-08-01): 128건 적재 중 **31건(24%)**이 이런 것이었다.
#     클로봇 → L사·A사·M사·N사·H사·K사      삼현 → A사~J사
#   노드로 만들면 이름이 같은 「A사」에 여러 회사가 뭉쳐 **거짓 연결**이 생긴다.
#   (클로봇의 A사와 삼현의 A사는 다른 회사인데 한 노드가 된다)
#
#   패턴을 좁게 잡는다 — `PTI`·`ASE`·`TEL` 같은 **실제 약칭**을 잘못 지우면 안 된다.
#   처음에 `^[a-z]{1,3}$`로 잡았다가 PTI·ASE를 익명으로 오판했다.
_ANON_RE = re.compile(r"^([a-z]{1,2}|[가-힣])사$"      # A사 · AA사 · 가사
                      r"|^customer\d*$|^client\d*$"
                      r"|^[가-힣]?[a-z]?고객사\d*$")


def _is_group_phrase(key: str) -> bool:
    k = key.replace(" ", "")
    return any(m in k for m in _GROUP_MARKERS)


def _is_anonymous(key: str) -> bool:
    return bool(_ANON_RE.match(key.replace(" ", "").lower()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="처리할 보고서 수 상한(비용)")
    args = ap.parse_args()

    with open(ETF_LIST_PATH, encoding="utf-8") as f:
        code2name = {c["corpCode"]: c["companyName"]
                     for c in json.load(f)["companies"]}

    with postgres_connection() as conn:
        docs = conn.execute(
            "SELECT corp_code, rcept_no, rcept_dt FROM documents "
            "WHERE doc_type='사업보고서' ORDER BY corp_code").fetchall()
        staged_pairs = _existing_pairs(conn)
    in_graph = _existing_in_graph()
    print(f"사업보고서 {len(docs)}건 · 기존 staged {len(staged_pairs):,}조합 · "
          f"그래프 SUPPLIES_TO {len(in_graph):,}쌍\n")

    targets = []
    for code, rcept, dt in docs:
        text = _section_text(rcept)
        if text:
            targets.append((code, rcept, dt, text))
    if args.limit:
        targets = targets[: args.limit]
    print(f"「{SECTION_NAME}」 본문 확보 {len(targets)}건 "
          f"(예상 {len(targets)*_COST_KRW:,}원)\n")

    total_new = total_dup = total_rel = total_junk = 0
    docs_out = []
    for code, rcept, dt, text in targets:
        name = code2name.get(code, code)
        rels = extract_customers(name, text)
        if not rels:
            continue
        total_rel += len(rels)
        doc, evs = build_contract_relation_document(
            code, name, rcept, rels, str(dt)[:10] if dt else None)

        # ── 중복 걸러내기 ────────────────────────────────────
        keep, dropped, junk = [], [], []
        for r in doc.relationships:
            sk = r.from_key.split(":", 1)[-1]
            tk = r.to_key.split(":", 1)[-1]
            sub = r.properties.get("subtype", "")
            if (_is_group_phrase(sk) or _is_group_phrase(tk)
                    or _is_anonymous(sk) or _is_anonymous(tk)):
                junk.append(tk)
                continue
            if (sk, tk, r.type, sub) in staged_pairs:
                dropped.append((sk, tk, "staged"))
                continue
            if (sk, tk) in in_graph:
                dropped.append((sk, tk, "graph"))
                continue
            keep.append(r)
            staged_pairs.add((sk, tk, r.type, sub))
        total_new += len(keep)
        total_dup += len(dropped)
        total_junk += len(junk)

        arrow = " · ".join(f"{r.from_key.split(':',1)[-1][:12]}→"
                           f"{r.to_key.split(':',1)[-1][:12]}" for r in keep[:4])
        print(f"  {name:14} 추출 {len(rels):>2} · 신규 {len(keep):>2} · "
              f"중복 {len(dropped):>2} · 집단서술 {len(junk):>2}  {arrow}")
        if keep:
            doc.relationships = keep
            docs_out.append((rcept, doc, evs))

    print(f"\n추출 {total_rel}건 → 신규 {total_new}건 · 중복 제외 {total_dup}건")
    if args.dry_run:
        print("[dry-run] 적재하지 않았습니다.")
        return 0
    if not docs_out:
        print("적재할 신규 관계가 없습니다.")
        return 0

    with postgres_connection() as conn:
        staged = invalid = 0
        for rcept, doc, evs in docs_out:
            n, inv = stage_document(conn, f"dart:{rcept}", doc)
            staged += n
            invalid += inv
            if evs:
                upsert_evidence(conn, evs)
    print(f"스테이징 {staged}건 (매트릭스 위반 {invalid}건 차단)")
    load_staged_to_neo4j()
    print("\n다음: python -m batch.ops.finalize 로 정리·검증하세요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
