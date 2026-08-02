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
    Step("그래프 무결성 감사", "batch.audit.graph",
         halt_on_fail=False, takes_dry_run=False),
    # 마지막은 "무엇이 걸렸나"가 아니라 **"무엇을 아직 안 봤나"**다.
    # 검사를 다 돌렸다는 것과 그래프가 정확하다는 것은 다른 얘기다.
    Step("검사 커버리지", "batch.audit.coverage",
         halt_on_fail=False, takes_dry_run=False),
]


def run(step: Step, *, dry_run: bool, full: bool, tail: int) -> tuple[bool, str]:
    """한 단계를 실행하고 (성공여부, 마지막 출력)을 돌려준다."""
    cmd = [sys.executable, "-m", step.module, *step.args]
    if dry_run and step.takes_dry_run:
        cmd.append("--dry-run")
    if full and step.takes_full:
        cmd.append("--full")

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    elapsed = time.time() - started

    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for ln in lines[-tail:]:
        print(f"    │ {ln}")
    if proc.returncode != 0:
        for ln in (proc.stderr or "").splitlines()[-6:]:
            print(f"    ! {ln}")
    print(f"    └ {elapsed:.1f}초  "
          f"{'✅' if proc.returncode == 0 else '❌ 종료코드 ' + str(proc.returncode)}")
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

    started = time.time()
    ran = skipped = failed = 0
    summaries: list[tuple[str, str]] = []

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
                              tail=args.tail)
            ran += 1
            summaries.append((step.name, summary))
            if not ok:
                failed += 1
                if step.halt_on_fail:
                    # 뒷단계가 이 결과를 전제한다 — 망가진 채로 진행하면 더 나빠진다
                    print(f"\n❌ 「{step.name}」 실패로 중단합니다. "
                          f"고친 뒤 다시 실행하세요.")
                    return 1
                print(f"    ⚠ 검사 단계라 계속 진행합니다.")

    print("\n" + "=" * 66)
    print(f"  실행 {ran} · 건너뜀 {skipped} · 실패 {failed} · "
          f"총 {time.time()-started:.0f}초")
    if args.dry_run:
        print("  [dry-run] 실제로 바뀐 것은 없습니다.")
    print("=" * 66)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
