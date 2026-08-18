"""추출 이후 정리·검사를 한 번에 — 후처리 파이프라인.

추출(`pilot_company`)이 끝나면 그래프는 **날것**이다. 같은 사건이 이름만 달리 갈려
있고, 같은 관계가 subtype만 달라 여러 엣지로 남아 있고, 방향이 뒤집힌 것도 섞여 있다.
지금까지는 이 뒷정리를 사람이 스크립트 9개를 순서대로 쳐서 했다 —
**순서를 틀리거나 한 단계를 빼먹으면 조용히 망가진다.**

이 모듈이 그 순서를 코드로 고정한다:

    python -m batch.ops.finalize                 # 증분 (신규분만 LLM 검사)
    python -m batch.ops.finalize --dry-run       # 무엇이 바뀔지만 보기
    python -m batch.ops.finalize --skip-llm      # 무료 단계만 (돈 안 씀)
    python -m batch.ops.finalize --full          # 전수 재검사 (렉시콘 고친 뒤)

【순서가 왜 이런가】 — 각 단계가 앞 단계의 결과를 먹는다.

  1) 노드 이름부터 고친다      개체해소(ER)는 **이름으로** 같은 것을 찾는다.
     ├ repair_nodes            이름이 지저분한 채로 ER을 돌리면 못 합친다.
     └ rename_leaked_events

  2) 노드를 합친다             ★여기가 핵심 의존성이다.
     ├ person_er               노드 두 개를 하나로 합치면 **그 노드에 붙어 있던
     ├ event_er                  엣지들이 한 자리에 모여 새 중복이 생긴다.**
     └ normalize_products        그러니 반드시 엣지 정리보다 **먼저**다.

  3) 엣지를 정리한다           2)가 만든 중복까지 여기서 흡수된다.
     ├ normalize_edges         subtype 대표형 → 완전중복 제거 → 클러스터링
     └ consolidate_subtypes    유사 subtype을 관리 목록으로 수렴

  4) 찌꺼기를 치운다
     └ prune_evidence          어느 엣지도 안 가리키는 근거 삭제

  5) 검사한다                  ★고친 뒤에 검사해야 의미가 있다.
     ├ verify_evidence_grounding   근거가 주장을 뒷받침하는가 (LLM)
     ├ audit_relation_quality      방향이 맞는가 (LLM)
     └ audit_graph                 구조 무결성 (무료)

1~4는 실패하면 **멈춘다**(뒷단계가 앞 결과를 전제하므로). 5는 실패해도 계속한다
(검사는 서로 독립이고, 하나 실패했다고 나머지 결과를 버릴 이유가 없다).

【데이터가 커지면】
검사 단계는 엣지에 `*_checked_at`을 남겨 **다음 실행이 신규분만 본다.** 그래서
비용이 그래프 크기가 아니라 **이번에 늘어난 양**에 비례한다. 정규화 규칙을 바꿔
과거 판정이 무효가 된 때만 `--full`로 전수 재검사한다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Step:
    name: str
    module: str
    args: list[str] = field(default_factory=list)
    # 실패 시 파이프라인을 멈출 것인가 (정리 단계는 True, 검사 단계는 False)
    halt_on_fail: bool = True
    costs_money: bool = False
    # --dry-run / --full 을 이 단계가 받는가 (모듈마다 다르다)
    takes_dry_run: bool = True
    takes_full: bool = False


CLEANUP: list[Step] = [
    Step("노드 이름 정리", "batch.repair.node_names"),
    Step("유출·제목형 사건 개명", "batch.repair.event_names", costs_money=True),
    Step("인물 개체해소", "pipeline.importer.person_er"),
    Step("사건 개체해소", "pipeline.importer.event_er"),
    # ★중복 노드 병합은 **엣지 정리보다 먼저**다. 노드 두 개를 하나로 합치면
    #   거기 붙어 있던 엣지가 한 자리에 모여 **새 중복**이 생기기 때문이다.
    Step("중복 노드 병합", "batch.repair.node_identity"),
    Step("제품 표기 통일", "batch.repair.products"),
    Step("엣지 정규화·클러스터링", "batch.repair.edges"),
    Step("subtype 수렴", "batch.repair.subtypes"),
    # 근거에서 되찾을 수 있는 값들 — LLM을 부르지 않는다.
    Step("인물 직위 복원", "batch.repair.executive_titles"),
    Step("지분 subtype 교정", "batch.repair.stake_subtypes"),
    Step("근거 청크 정리", "batch.repair.evidence"),   # 중복 병합 → 고아 삭제

    # ── 4') 화면이 읽을 파생값을 다시 만든다 ────────────────────
    #
    # ★여기가 비어 있어서 2026-08-02까지 **손으로 쳐야 했다.** 위 단계들이 노드를
    #   합치고 엣지를 클러스터링하면 아래 값들이 전부 낡는다 — 사건에 붙은 기사
    #   목록도, 회사 카드 임베딩도, stub 라벨도. 낡은 채로 두면 화면이 조용히
    #   옛 그래프를 보여 준다. 파생값은 **원본이 바뀔 때마다 다시 만들어야** 한다.
    Step("사건 기사 목록 갱신", "batch.repair.event_sources"),
    Step("언론사명 복구", "batch.repair.press_names"),
    # stub 라벨·기업 카드는 신규분만 처리한다(해시·NULL 비교) — 재실행이 싸다.
    Step("stub 정체 라벨", "batch.build.stub_profiles",
         costs_money=True, takes_dry_run=True),
    Step("기업 카드 임베딩", "batch.build.company_vectors",
         costs_money=True, takes_dry_run=True),
]

# ★검사기 자체가 회귀했는지 **먼저** 본다. 프롬프트를 고친 뒤 전량(1,600원·12분)을
#   돌려서야 한쪽으로 쏠린 걸 알게 되는 일이 실제로 있었다(거절률 8%→49%).
#   합쳐서 150원·30초다. 실패해도 멈추지 않고 경고만 남긴다.
SELFTEST: list[Step] = [
    Step("추출기·검증기 회귀 확인", "batch.audit.selftest",
         halt_on_fail=False, costs_money=True, takes_dry_run=False),
]

VERIFY: list[Step] = [
    Step("근거-주장 정합성", "batch.audit.grounding",
         args=["--llm", "--apply", "--all", "--source", "news"], halt_on_fail=False,
         costs_money=True, takes_dry_run=False, takes_full=True),
    # 위 검사는 **저장된 문장 한두 개**만 본다. 「뒷받침 안 됨」은 「관계가 거짓」이
    # 아니라 「그 문장만으로는 확인이 안 된다」는 뜻이라, 기사 전문으로 한 번 더 본다.
    Step("의심분 전문 재검증", "batch.audit.grounding_fulltext",
         halt_on_fail=False, costs_money=True, takes_full=True),
    # 유형만 틀린 것을 고친다. **매트릭스가 최종 방어선** — 무효한 제안은 기각한다.
    Step("유형오류 교정", "batch.repair.retypes", halt_on_fail=False),
    # ── DART는 뉴스와 다른 검사를 쓴다 (근거가 우리가 만든 템플릿이라 §15-2) ──
    #   ①필드 무결성(값 범위·구조) + ②원문 대조(사업보고서 XML) 둘 다 돈다.
    Step("DART 필드·원문 검사", "batch.audit.dart",
         args=["--apply"], halt_on_fail=False, takes_dry_run=False),
    Step("관계 방향 검사", "batch.audit.relations",
         args=["--scope", "direction"], halt_on_fail=False,
         costs_money=True, takes_full=True),
    Step("대칭 엣지 병렬언급 검사", "batch.audit.relations",
         args=["--scope", "symmetric"], halt_on_fail=False,
         costs_money=True, takes_full=True),
    Step("양방향 공급 검사", "batch.audit.relations",
         args=["--scope", "bidir"], halt_on_fail=False,
         costs_money=True, takes_full=True),
    Step("사건성 검사", "batch.audit.relations",
         args=["--scope", "event"], halt_on_fail=False, costs_money=True),
    # 관계의 **종료**를 두 경로로 찾아 is_current=false. 삭제하지 않는다 —
    # 신선도 판정이 expired(가중 0.3)로 낮추고 조회 기본값이 답에서 뺀다.
    #   ①뉴스 근거가 종료를 말함(LLM)  ②DART 재적재에서 사라짐(무료)
    Step("종료된 관계 탐지", "batch.audit.freshness",
         halt_on_fail=False, costs_money=True, takes_full=True),
    # ★검사 단계도 노드·엣지를 지운다 → 근거가 다시 고아가 된다.
    #   그래서 정리를 한 번 더 하고 나서 감사한다. 이 순서가 아니면
    #   마지막 감사가 자기가 방금 만든 찌꺼기를 경고로 띄운다(실측 25건).
    Step("고아 근거 정리(2차)", "batch.repair.evidence",
         args=["--only", "prune"], halt_on_fail=False),
    # ★고아 **노드**도 같은 이유로 생긴다(2026-08-12). 검사가 엣지를 지우면
    #   그 엣지 하나만 붙들고 있던 노드가 홀로 남는다.
    #     실측: 근거 없는 엣지 485건을 지웠더니 고아 노드 273건이 생겼다.
    #   그런데 이 단계가 `finalize`에 **배선돼 있지 않아** 감사가 273건을
    #   경고로 띄우고, 사람이 따로 돌려야 했다. 만든 자리에서 치우게 한다.
    #   (삭제가 아니라 표시 + 벡터 검색에서 제외 — 엣지가 붙으면 표시가 풀린다)
    Step("고아 노드 표시", "batch.repair.orphan_nodes", halt_on_fail=False),
    # ★기업 카드도 같은 이유로 찌꺼기가 남는다(2026-08-07). 앞 단계들이 노드를
    #   병합·삭제하면 그래프에서는 사라지는데 **검색 카드는 남는다.** 검색으로
    #   들어가면 관계가 하나도 없는 빈 화면이 열린다.
    #   `repair.orphan_nodes`는 「엣지 0인 노드」만 봐서 이걸 못 잡는다 —
    #   병합·삭제된 것은 노드 자체가 없기 때문이다.
    Step("유효하지 않은 기업 카드 정리", "batch.repair.stale_cards",
         halt_on_fail=False),
    # 문서 정합은 뺀다 — 바로 뒤 `DOC_STEP`이 맞추므로 여기서 뜨면 헛경보다
    Step("그래프 무결성 감사", "batch.audit.graph",
         args=["--skip-doc-check"], halt_on_fail=False, takes_dry_run=False),
    # 마지막은 "무엇이 걸렸나"가 아니라 **"무엇을 아직 안 봤나"**다.
    # 검사를 다 돌렸다는 것과 그래프가 정확하다는 것은 다른 얘기다.
    Step("검사 커버리지", "batch.audit.coverage",
         halt_on_fail=False, takes_dry_run=False),
]

# ★진행현황 문서 갱신은 **어느 단계에서 끝나든 반드시 돈다**(2026-08-09).
#
#   원래 VERIFY의 마지막 줄이었는데 그러면 세 갈래로 빠진다:
#     ① halt_on_fail 단계가 실패 → `return 1`이 뒷단계를 통째로 건너뛴다
#     ② `--only cleanup` / `--only selftest` → VERIFY 자체를 안 돈다
#     ③ 사람이 Ctrl-C
#   ①이 실제로 났다. 문서는 01:29 숫자에 멈춰 있는데 그 뒤 정리가 돌아
#   **뉴스 엣지 102개가 지워졌다.** 문서와 DB가 1시간 45분 어긋났다.
#
#   문서는 「성공했을 때의 상보」가 아니라 **「지금 DB가 이렇다」는 기록**이다.
#   중간에 멈췄으면 멈춘 시점의 숫자가 적혀야 맞다.
DOC_STEP = Step("진행현황 문서 갱신", "batch.ops.status",
                args=["--write-doc"], halt_on_fail=False, takes_dry_run=False)


LOG_DIR = Path("logs")


def graph_counts() -> dict[str, int] | None:
    """노드·엣지 수를 센다. Neo4j가 없으면 None — **기록 때문에 배치를 죽이지 않는다.**"""
    try:
        from app.core.database import neo4j_session
        with neo4j_session() as s:
            return {
                "노드": s.run("MATCH (n) RETURN count(*) AS n").single()["n"],
                "엣지": s.run("MATCH ()-[r]->() RETURN count(*) AS n").single()["n"],
                "뉴스엣지": s.run("MATCH ()-[r]->() WHERE r.source_type='news' "
                              "RETURN count(*) AS n").single()["n"],
            }
    except Exception:
        return None


def fmt_delta(before: dict | None, after: dict | None) -> str:
    """「엣지 -102」처럼 **변한 것만** 적는다. 안 변한 줄이 많으면 변한 줄이 안 보인다."""
    if not before or not after:
        return ""
    parts = [f"{k} {after[k]-before[k]:+,}" for k in before if after[k] != before[k]]
    return " · ".join(parts)


class RunLog:
    """단계별 **전체 출력**과 그래프 증감을 파일로 남긴다.

    ★왜 필요한가 (2026-08-09)

    정리 단계가 뉴스 엣지 102개를 지웠는데 **무엇이 지웠는지 알 방법이 없었다.**
    화면에는 단계마다 마지막 6줄만 찍히고, 세션이 끝나면 그마저 사라진다.
    남은 단서가 Neo4j 트랜잭션 카운터뿐이라 「01:29~01:44에 쓰기 1,055건」까지만
    알아내고 멈췄다. 되짚을 수 없는 변경은 **되돌릴 수도 없다.**

    그래서 두 가지를 남긴다:
      ① 단계별 stdout/stderr **전문** — 각 스크립트가 무엇을 몇 건 고쳤다고 했는지
      ② 단계 **전후의 노드·엣지 수** — 스크립트가 말하지 않은 것까지 잡힌다

    ②가 ①보다 중요하다. 스크립트는 자기가 지운 것만 세는데, `mergeNodes`처럼
    **부수적으로 사라지는 엣지**는 아무도 세지 않는다. 전후 차이는 그걸 잡는다.
    """

    def __init__(self, argv: list[str]):
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = LOG_DIR / f"finalize_{stamp}.log"
        self.deltas: list[tuple[str, str]] = []
        self._fh = self.path.open("w", encoding="utf-8", buffering=1)
        self.write(f"# finalize {datetime.now():%Y-%m-%d %H:%M:%S} KST")
        # argv[0]은 스크립트 **경로**라 그대로 적으면 다시 못 친다 —
        # 되짚는 사람이 복사해 붙일 수 있는 형태로 남긴다.
        self.write(f"# 명령: python -m batch.ops.finalize {' '.join(argv[1:])}")

    def write(self, text: str) -> None:
        self._fh.write(text.rstrip() + "\n")

    def step(self, step: Step, cmd: list[str], proc, elapsed: float,
             before: dict | None, after: dict | None) -> None:
        d = fmt_delta(before, after)
        self.write("\n" + "=" * 72)
        self.write(f"[{datetime.now():%H:%M:%S}] {step.name}"
                   f"  ({elapsed:.1f}초 · 종료코드 {proc.returncode})")
        self.write(f"  $ {' '.join(cmd[2:])}")
        if before and after:
            self.write(f"  그래프  {before['노드']:,}노드 {before['엣지']:,}엣지"
                       f"  →  {after['노드']:,}노드 {after['엣지']:,}엣지"
                       f"{'   Δ ' + d if d else '   (변화 없음)'}")
        self.write("=" * 72)
        if proc.stdout:
            self.write(proc.stdout)
        if proc.stderr:
            self.write("\n--- stderr ---")
            self.write(proc.stderr)
        if d:
            self.deltas.append((step.name, d))

    def close(self) -> None:
        self._fh.close()


def run(step: Step, *, dry_run: bool, full: bool, tail: int,
        log: RunLog | None = None) -> tuple[bool, str]:
    """한 단계를 실행하고 (성공여부, 마지막 출력)을 돌려준다."""
    cmd = [sys.executable, "-m", step.module, *step.args]
    if dry_run and step.takes_dry_run:
        cmd.append("--dry-run")
    if full and step.takes_full:
        cmd.append("--full")

    before = graph_counts() if log else None
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    elapsed = time.time() - started
    after = graph_counts() if log else None

    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for ln in lines[-tail:]:
        print(f"    │ {ln}")
    if proc.returncode != 0:
        for ln in (proc.stderr or "").splitlines()[-6:]:
            print(f"    ! {ln}")
    delta = fmt_delta(before, after)
    print(f"    └ {elapsed:.1f}초  "
          f"{'✅' if proc.returncode == 0 else '❌ 종료코드 ' + str(proc.returncode)}"
          f"{'   Δ ' + delta if delta else ''}")
    if log:
        log.step(step, cmd, proc, elapsed, before, after)
    return proc.returncode == 0, "\n".join(lines[-3:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="무엇이 바뀔지만 출력, 실제 변경 없음")
    ap.add_argument("--skip-llm", action="store_true",
                    help="돈이 드는 단계를 건너뛴다(무료 검사만)")
    ap.add_argument("--full", action="store_true",
                    help="기검사분까지 전수 재검사")
    ap.add_argument("--only", choices=["selftest", "cleanup", "verify"],
                    help="회귀확인만 / 정리만 / 검사만")
    ap.add_argument("--skip-selftest", action="store_true",
                    help="검사기 회귀 확인을 건너뛴다 (프롬프트를 안 고쳤을 때)")
    ap.add_argument("--tail", type=int, default=6,
                    help="단계별로 보여줄 출력 줄 수")
    ap.add_argument("--no-log", action="store_true",
                    help=f"{LOG_DIR}/ 에 실행 기록을 남기지 않는다")
    args = ap.parse_args()

    phases = []
    # ★검사기 회귀 확인이 **맨 앞**이다. 프롬프트가 쏠린 채로 전량을 돌리면
    #   결과를 못 믿고 다시 돌려야 한다(1,600원·12분을 두 번 쓴 적이 있다).
    if args.only in (None, "selftest") and not args.skip_selftest:
        phases.append(("회귀확인", SELFTEST))
    if args.only in (None, "cleanup"):
        phases.append(("정리", CLEANUP))
    if args.only in (None, "verify"):
        phases.append(("검사", VERIFY))

    mode = []
    if args.dry_run:
        mode.append("dry-run")
    if args.skip_llm:
        mode.append("무료만")
    if args.full:
        mode.append("전수")
    print("=" * 66)
    print(f"  후처리 파이프라인{'  [' + ' · '.join(mode) + ']' if mode else ''}")
    print("=" * 66)

    log = None if args.no_log else RunLog(sys.argv)
    if log:
        print(f"  기록: {log.path}")

    started = time.time()
    began = graph_counts()
    ran = skipped = failed = 0
    summaries: list[tuple[str, str]] = []

    def write_doc() -> None:
        """중단·완주·Ctrl-C 어느 쪽이든 문서를 지금 DB 상태로 맞춘다."""
        if args.dry_run:                    # 바뀐 게 없으니 문서도 그대로 둔다
            return
        print(f"\n[마무리] {DOC_STEP.name}")
        run(DOC_STEP, dry_run=False, full=False, tail=args.tail, log=log)

    def finish(code: int) -> int:
        """어떻게 끝나든 **무엇이 얼마나 바뀌었는지**를 남긴다."""
        if log:
            ended = graph_counts()
            total = fmt_delta(began, ended)
            log.write("\n" + "#" * 72)
            log.write(f"# 종료코드 {code} · 총 {time.time()-started:.0f}초")
            log.write(f"# 전체 증감: {total or '변화 없음'}")
            for name, d in log.deltas:
                log.write(f"#   {name:<28} {d}")
            log.close()
            if log.deltas:
                print("\n  그래프를 바꾼 단계")
                for name, d in log.deltas:
                    print(f"    {name:<28} {d}")
                print(f"    {'─'*28} {'─'*20}")
                print(f"    {'합계':<28} {total or '변화 없음'}")
            print(f"\n  기록: {log.path}")
        return code

    try:
        for phase, steps in phases:
            print(f"\n── {phase} ──────────────────────────────────────")
            for i, step in enumerate(steps, 1):
                if args.skip_llm and step.costs_money:
                    print(f"\n[{phase} {i}/{len(steps)}] {step.name}  ⏭ 건너뜀(유료)")
                    skipped += 1
                    continue
                print(f"\n[{phase} {i}/{len(steps)}] {step.name}"
                      f"{'  💰' if step.costs_money else ''}")
                ok, summary = run(step, dry_run=args.dry_run, full=args.full,
                                  tail=args.tail, log=log)
                ran += 1
                summaries.append((step.name, summary))
                if not ok:
                    failed += 1
                    if step.halt_on_fail:
                        # 뒷단계가 이 결과를 전제한다 — 망가진 채로 가면 더 나빠진다
                        print(f"\n❌ 「{step.name}」 실패로 중단합니다. "
                              f"고친 뒤 다시 실행하세요.")
                        write_doc()   # ★멈춘 시점까지의 변경도 문서에 남긴다
                        return finish(1)
                    print(f"    ⚠ 검사 단계라 계속 진행합니다.")
    except KeyboardInterrupt:
        print("\n\n⛔ 사람이 중단했습니다.")
        write_doc()
        return finish(130)

    write_doc()

    print("\n" + "=" * 66)
    print(f"  실행 {ran} · 건너뜀 {skipped} · 실패 {failed} · "
          f"총 {time.time()-started:.0f}초")
    if args.dry_run:
        print("  [dry-run] 실제로 바뀐 것은 없습니다.")
    print("=" * 66)
    return finish(0 if failed == 0 else 2)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
