"""`discovered` 앵커의 **판정 신호를 고르기 위한 계측** — 거리인가 응집도인가.

제안된 `discovered` 경로는 evidence 컬렉션을 **전역 검색**해서 질문에 맞는 기업을
찾는다. 문제는 「찾았다」와 「비슷한 게 나왔다」를 무엇으로 가르냐는 것이다.

★**거리 임계값은 이미 한 번 떨어진 시험이다.** `evidence_selector` 모듈
  독스트링(:34)이 실측을 남겨 뒀다:

      ChromaRepository.search_evidence() 로 evidence 컬렉션을 전역 검색하면
      Step1 이 막은 오염이 되돌아온다 — 실측으로 「SK하이닉스 노조」 상위 5건에
      현대오토에버·HD현대중공업이 들어왔다.

  오염이 **상위 5건 안에** 들어왔다는 것은 거리가 가까웠다는 뜻이다. 즉 거리로는
  안 걸러진다. 그래서 `evidence_selector` 는 전역 검색을 포기하고 「이미 기업
  scope 안으로 좁혀진 후보만 다시 줄 세운다」로 후퇴했다.

★**대안 가설 — 순위 응집도.** 오염은 한 건씩 흩어지고 진짜 답은 뭉친다.
  「미국 정부의 반도체 수출 규제」면 상위에 `REGULATES` 가 반복되고 미국정부가
  반복된다. 「SK하이닉스 노조」의 오염(현대오토에버·HD현대중공업)은 서로 무관해서
  반복이 안 생긴다. 순위 기반이라 **임베딩 드리프트에도 둔감하다**(현황서 §8-13 —
  값은 실행마다 흔들려도 상위 목록의 구성은 훨씬 덜 흔들린다).

★★**첫 실측(2026-08-29) — 가설이 뒤집혔다.**

      신호            answer            none            판정
      기업 응집도       최소 0.200         최대 0.400       겹침
      엣지 응집도       최소 0.300         최대 0.800       겹침
      최소 거리        최대 1.167         최소 1.364       **갈림**
      중앙 거리        최대 1.234         최소 1.461       **갈림**

  **응집도가 아니라 거리가 갈랐다.** 응집도가 실패한 이유는 표본이 작아서가
  아니라 **구조적**이다:

      ① Chroma 는 아무리 먼 질의에도 **N 건을 꼭 돌려준다.** 무의미 문자열
         「storminmvpsdjfk」이 SK하이닉스×4(응집 0.40)를 냈고, 「오늘 점심 메뉴」는
         DEVELOPS×8 로 **엣지 응집 0.80 — 전체 최고**였다. 상위 N 이 우연히 한
         문서 뭉치에서 오면 뭉친 것처럼 보인다.
      ② 반대로 **답이 여러 기업인 질의는 응집도가 낮다.** 「최근 규제당국 조사
         동향」은 답이 흩어진 여러 기관이라 0.20 이다. 응집도는 답이 하나로
         모이는 질의만 통과시키는데, 그건 우리가 원하는 성질이 아니다.

★**그런데 거리도 정작 어려운 케이스는 못 가른다.** `contested` 둘이 그 증거다:

      SK하이닉스 노조     최소거리 1.014   ← answer 대역 **안쪽**
      환율이 떨어지면…    최소거리 1.197   ← answer 대역 **안쪽**

  「SK하이닉스 노조」는 `evidence_selector` 가 **오염을 실측한 바로 그 질의**인데
  (상위에 HD현대중공업×5 가 뭉쳐 있는 것이 이 표에도 그대로 보인다), 어떤 거리
  임계값을 잡아도 answer 넷을 통과시키면 이것도 통과한다.

  ★즉 **거리는 헛소리(gibberish)를 걸러낼 뿐, 오염을 못 걸러낸다.** 「주제는
    맞는데 나온 기업이 엉뚱하다」가 discovered 가 풀어야 할 진짜 문제인데,
    잰 신호 넷 중 어느 것도 그것을 가르지 못한다.

★**이 스크립트는 임계값을 정하지 않는다.** 분포를 찍을 뿐이다 —
  「실측 없이 숫자를 정하지 않는다」([설계서 규칙 5]). 판정기를 만들면 재는
  도구가 아니라 판정기가 된다(`app/core/observe.py` 와 같은 규약).

★**LLM 을 부르지 않는다.** 질의당 임베딩 1회(`ChromaStore.query` 가 질의를
  임베딩한다)뿐이고 답변 생성이 없다. 비용은 무시할 수준이다.

    python -m batch.audit.discovered_cohesion
    python -m batch.audit.discovered_cohesion --top-k 20
    python -m batch.audit.discovered_cohesion --question "반도체 업계 파업 위험이 있나?"
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class Probe:
    """질의 하나와 **기대**. 기대는 판정이 아니라 읽는 사람의 기준선이다."""

    question: str
    # `answer`  — 근거 코퍼스에 답이 있어야 한다
    # `none`    — 답이 없어야 한다
    # `contested` — ★어느 쪽인지 우리가 아직 못 정한 것. 아래 주석 참조
    expect: str
    note: str = ""


# ★음성 질의를 **두 종류로 가른다.** 제안서 초안은 「환율이 떨어지면 타격받는
#   기업은?」과 「storminmvpsdjfk 이 뭐야」를 같은 통에 넣었는데, 둘은 성격이
#   전혀 다르다:
#
#       storminmvpsdjfk    무의미 문자열. 코퍼스에 주제가 없다 → 멀어야 정상
#       환율…              **의미가 통하고 환율 언급 근거가 실재할 수 있다.**
#                          가깝게 나오는 것이 오히려 정상이고, 문제는 「근거는
#                          있는데 인과 추론이 필요하다」는 것 — 거리의 문제가
#                          아니라 claim 검증의 영역이다.
#
#   둘을 한 통에 넣고 재면 분포가 겹치는 것이 당연하고, 그러면 「겹치니까
#   discovered 를 포기한다」는 **잘못된 이유로** 내려진 결론이 된다. 그래서
#   `contested` 로 따로 표시해 둔다.
_PROBES: tuple[Probe, ...] = (
    # ── 답이 있어야 하는 질의 ────────────────────────────────────
    Probe("미국 규제에 걸린 반도체 장비사는?", "answer",
          "제안서의 대표 예시. REGULATES → Organization 이 답이다"),
    Probe("반도체 업계 파업 위험이 있나?", "answer",
          "평가셋에 이미 있는 케이스. 지금은 워크스페이스로 갈아탄다"),
    Probe("메모리 가격 담합 관련 소식", "answer",
          "평가셋 케이스. 주제가 코퍼스에 실재한다"),
    Probe("최근 규제당국 조사 동향", "answer",
          "평가셋 케이스. 기관이 답인 질의 — §5-17 이 못 내보내는 그것"),
    # ── ★오염 확인 — evidence_selector 가 실측으로 기록한 실패 ──
    Probe("SK하이닉스 노조", "contested",
          "★전역 검색 상위 5건에 현대오토에버·HD현대중공업이 들어왔다는 "
          "그 질의(evidence_selector:34). 응집도가 이걸 거르는가가 이 시험의 핵심"),
    # ── 답이 없어야 하는 질의 ────────────────────────────────────
    Probe("storminmvpsdjfk 이 뭐야", "none",
          "무의미 문자열. 코퍼스에 주제가 없다"),
    Probe("오늘 점심 메뉴 추천해줘", "none",
          "의미는 통하지만 이 코퍼스의 주제가 아니다"),
    Probe("환율이 떨어지면 타격받는 기업은?", "contested",
          "★제안서는 음성으로 뒀지만 환율 언급 근거가 실재하면 가깝게 나온다. "
          "거리가 아니라 claim 검증의 영역 — 음성 케이스로 쓰면 안 된다"),
)

_TOP_K = 10
# 표에 찍을 상위 기업 수.
_SHOW = 3


def _corps(meta: dict) -> list[str]:
    """이 근거가 잇는 두 끝. **둘 다 센다** — 어느 쪽이 답인지는 질의마다 다르다.

    ★「미국 규제에 걸린 장비사」는 `source_corp`(미국정부)가 반복되고
      `target_corp`(어플라이드·KLA…)가 답이다. 반대인 질의도 있다. 한쪽만 세면
      질의 유형에 따라 신호가 통째로 사라진다.
    """
    return [str(meta.get(key) or "") for key in ("source_corp", "target_corp")
            if meta.get(key)]


@dataclass
class Measured:
    probe: Probe
    hits: int
    distances: list[float]
    corp_counts: Counter
    edge_counts: Counter

    @property
    def distinct_corps(self) -> int:
        return len(self.corp_counts)

    @property
    def top_corp(self) -> tuple[str, int]:
        return self.corp_counts.most_common(1)[0] if self.corp_counts else ("—", 0)

    @property
    def top_edge(self) -> tuple[str, int]:
        return self.edge_counts.most_common(1)[0] if self.edge_counts else ("—", 0)

    @property
    def corp_cohesion(self) -> float:
        """상위 목록에서 **가장 많이 반복된 기업**이 차지하는 비율.

        ★오염은 흩어지고 답은 뭉친다는 가설의 직접 측정치다. 히트가 없으면 0.
        """
        return self.top_corp[1] / self.hits if self.hits else 0.0

    @property
    def edge_cohesion(self) -> float:
        """상위 목록에서 **가장 많이 반복된 엣지 타입**이 차지하는 비율."""
        return self.top_edge[1] / self.hits if self.hits else 0.0

    @property
    def min_distance(self) -> Optional[float]:
        return min(self.distances) if self.distances else None

    @property
    def median_distance(self) -> Optional[float]:
        return statistics.median(self.distances) if self.distances else None


def measure(repo, probe: Probe, top_k: int) -> Measured:
    """★`where` 를 **안 준다** — 전역 검색이 이 시험의 대상이다."""
    got = repo.search_evidence(probe.question, n_results=top_k)
    metas = (got.get("metadatas") or [[]])[0]
    raw_distances = (got.get("distances") or [[]])[0]

    corp_counts: Counter = Counter()
    edge_counts: Counter = Counter()
    for meta in metas:
        meta = dict(meta or {})
        corp_counts.update(_corps(meta))
        if meta.get("edge_type"):
            edge_counts[str(meta["edge_type"])] += 1
    return Measured(probe=probe, hits=len(metas),
                    distances=[float(d) for d in raw_distances],
                    corp_counts=corp_counts, edge_counts=edge_counts)


def _names(keys: list[str]) -> dict[str, str]:
    """key → 이름. ★그래프가 없어도 이 스크립트는 돌아야 한다 — 이름은 읽기
    편하라고 붙이는 것이지 측정에 쓰는 값이 아니다."""
    if not keys:
        return {}
    try:
        from app.services import company_service

        return company_service.names_by_keys(keys)
    except Exception as exc:                                   # noqa: BLE001
        print(f"  (이름 조회 실패 — key 를 그대로 씁니다: {exc})")
        return {}


def _fmt(value: Optional[float], digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def report(measured: list[Measured]) -> None:
    keys = sorted({key for m in measured for key in m.corp_counts})
    names = _names(keys)

    def label(key: str) -> str:
        return names.get(key, key)

    print()
    print("응집도 — 상위 목록에서 같은 기업/엣지가 얼마나 반복되나")
    print("=" * 100)
    print(f"{'기대':<10} {'질의':<28} {'히트':>4} {'기업응집':>8} {'엣지응집':>8} "
          f"{'고유기업':>8} {'최소거리':>9} {'중앙거리':>9}")
    print("-" * 100)
    for m in measured:
        print(f"{m.probe.expect:<10} {m.probe.question[:26]:<28} {m.hits:>4} "
              f"{m.corp_cohesion:>8.2f} {m.edge_cohesion:>8.2f} "
              f"{m.distinct_corps:>8} {_fmt(m.min_distance):>9} "
              f"{_fmt(m.median_distance):>9}")
    print("=" * 100)

    print()
    print("상위에 반복된 것 — ★무엇이 뭉쳤는지 눈으로 본다")
    print("-" * 100)
    for m in measured:
        top_corps = " · ".join(
            f"{label(key)}×{n}" for key, n in m.corp_counts.most_common(_SHOW))
        top_edges = " · ".join(
            f"{edge}×{n}" for edge, n in m.edge_counts.most_common(_SHOW))
        print(f"[{m.probe.expect}] {m.probe.question}")
        print(f"    기업 {top_corps or '없음'}")
        print(f"    엣지 {top_edges or '없음'}")
        if m.probe.note:
            print(f"    ※ {m.probe.note}")
    print("-" * 100)

    # ── 읽는 법 — ★판정이 아니다 ────────────────────────────────
    answers = [m for m in measured if m.probe.expect == "answer"]
    nones = [m for m in measured if m.probe.expect == "none"]
    print()
    print("읽는 법 — ★이 스크립트는 임계값을 정하지 않습니다")
    print("-" * 100)
    if answers and nones:
        # ★**지표마다 좋은 방향이 다르다.** 응집도는 클수록 좋고 거리는
        #   **작을수록** 좋다. 한 방향으로 비교하면 거리에서 거짓 결론이 난다 —
        #   실제로 초안이 그랬다(2026-08-29).
        #
        #       클수록 좋음   answer 의 **최소**가 none 의 **최대**보다 커야 갈린다
        #       작을수록 좋음  answer 의 **최대**가 none 의 **최소**보다 작아야 갈린다
        higher_is_better = (("기업 응집도", lambda m: m.corp_cohesion),
                            ("엣지 응집도", lambda m: m.edge_cohesion))
        lower_is_better = (("최소 거리", lambda m: m.min_distance or 0.0),
                           ("중앙 거리", lambda m: m.median_distance or 0.0))
        for metric, getter in higher_is_better:
            lo = min(getter(m) for m in answers)
            hi = max(getter(m) for m in nones)
            gap = "겹침" if lo <= hi else "갈림"
            print(f"  {metric:<12} (클수록 좋음) answer 최소 {lo:.3f} · "
                  f"none 최대 {hi:.3f}  → {gap}")
        for metric, getter in lower_is_better:
            hi = max(getter(m) for m in answers)
            lo = min(getter(m) for m in nones)
            gap = "겹침" if hi >= lo else "갈림"
            print(f"  {metric:<12} (작을수록 좋음) answer 최대 {hi:.3f} · "
                  f"none 최소 {lo:.3f}  → {gap}")
        print()
        print("  「갈림」이 나온 신호가 게이트 후보입니다. 하나도 안 갈리면")
        print("  ★**전역 검색으로는 게이트를 세울 수 없다**는 뜻이고, 그러면")
        print("    discovered 경로를 열지 않는 것이 맞습니다 — 「모르겠다」가")
        print("    「엉뚱한 답」보다 낫습니다.")
    print()
    print("  ★`contested` 두 건은 위 계산에서 뺐습니다 —")
    print("    「SK하이닉스 노조」는 evidence_selector 가 오염을 실측한 질의이고,")
    print("    「환율…」은 근거가 실재할 수 있어 음성 케이스로 쓰면 안 됩니다.")
    print("    둘 다 **사람이 표를 보고 판단할 것**이지 자동 판정 대상이 아닙니다.")
    print("-" * 100)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="discovered 앵커의 판정 신호 계측 — ★LLM 을 부르지 않습니다")
    parser.add_argument("--top-k", type=int, default=_TOP_K,
                        help=f"상위 몇 건을 볼까 (기본 {_TOP_K})")
    parser.add_argument("--question", help="이 질의 하나만 잰다")
    args = parser.parse_args()

    from search.repository.chroma_repository import ChromaRepository

    repo = ChromaRepository()
    probes = ((Probe(args.question, "—", "직접 준 질의"),) if args.question
              else _PROBES)
    report([measure(repo, probe, args.top_k) for probe in probes])


if __name__ == "__main__":
    main()
