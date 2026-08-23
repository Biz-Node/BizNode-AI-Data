"""중단된 수집이 어디까지 갔는지 본다. 비용 0.

왜 필요한가 (2026-08-03)

구글이 봇 판정으로 막으면 수집이 중간에 끊긴다. 이제 끊긴 지점까지는
저장되므로(`gnews._ckpt_save`), 다시 돌리면 거기서 이어간다. 그런데
「지금 어디까지 갔나」를 볼 방법이 없으면 다음에 뭘 돌려야 할지 모른다.

    python -m batch.ops.collect_state              # 진행 중인 것 전부
    python -m batch.ops.collect_state --clear 이오테크닉스   # 처음부터 다시 받고 싶을 때
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

from pipeline.extractors.news.gnews import (
    ONTOLOGY_GROUPS,
    _CKPT_DIR,
    hours_since_block,
    _MIN_WAIT_HOURS,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clear", nargs="+", metavar="기업명",
                    help="중간본을 지운다 — 다음 실행이 처음부터 받는다")
    args = ap.parse_args()

    if args.clear:
        want = {c.replace(" ", "_") for c in args.clear}
        n = 0
        for p in _CKPT_DIR.glob("*.json"):
            if p.stem.split("__")[0] in want:
                p.unlink()
                print(f"  지움: {p.name}")
                n += 1
        print(f"중간본 {n}건 삭제 — 다음 실행이 처음부터 받습니다.")
        return 0

    # ★시각은 **KST로 찍는다**(2026-08-04). 안에서는 UTC로 재는데 그대로 보여 주니
    #   사용자 시계와 9시간 어긋나 「10시에 재개」가 실제로는 저녁 7시였다.
    #   기계용 시각과 사람이 읽는 시각을 섞으면 반드시 사고가 난다.
    since = hours_since_block()
    if since is not None:
        ok = "✅ 돌려도 됩니다" if since >= _MIN_WAIT_HOURS else "⛔ 아직 이릅니다"
        blocked_at = datetime.now(_KST) - timedelta(hours=since)
        resume_at = blocked_at + timedelta(hours=_MIN_WAIT_HOURS)
        print(f"구글 차단 이후 {since:.1f}시간 (권장 {_MIN_WAIT_HOURS:.0f})  {ok}")
        print(f"   차단 {blocked_at:%m-%d %H:%M} KST → 재개 가능 {resume_at:%m-%d %H:%M} KST\n")

    files = sorted(_CKPT_DIR.glob("*.json")) if _CKPT_DIR.exists() else []
    if not files:
        print("중단된 수집이 없습니다 — 모두 완주했거나 아직 시작 전입니다.")
        return 0

    ng = len(ONTOLOGY_GROUPS)
    print(f"■ 남아 있는 중간본 {len(files)}건  (질의어 묶음 {ng}개 기준)\n")
    print(f"   {'기업':16}{'설정':8}{'진행':>12}{'기사':>7}{'남은 질의':>10}  마지막 저장")
    n_full = 0
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            print(f"   {p.name} — 읽지 못했습니다")
            continue
        name, cfg = p.stem.split("__", 1)
        done, total = d.get("done_periods", 0), d.get("total_periods", 0)
        left = max(total - done, 0) * ng
        # ★「수집은 다 했는데 추출에서 죽은 것」과 「수집 도중 끊긴 것」은 다르다.
        #   앞의 것은 구글을 한 번도 더 안 쳐도 되고, 뒤의 것은 남은 질의가 있다.
        if d.get("collected_all"):
            n_full += 1
            cfg += " ✔수집완료"
        saved, ago = d.get("saved_at", ""), ""
        if saved:
            try:
                t = datetime.fromisoformat(saved)
                h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
                saved = f"{t.astimezone(_KST):%m-%d %H:%M} KST"
                ago = f" ({h:.1f}시간 전)"
            except Exception:
                pass
        print(f"   {name.replace('_', ' ')[:14]:16}{cfg:18}"
              f"{done:>5}/{total:<6}{len(d.get('articles', [])):>7}"
              f"{left:>10}  {saved}{ago}")

    print("\n   같은 명령을 다시 돌리면 위 지점부터 이어갑니다 — 앞선 질의는 다시 쓰지 않습니다.")
    if n_full:
        print(f"   그중 {n_full}건은 ✔수집완료 — **구글을 한 번도 더 안 칩니다.** "
              f"추출 단계에서 죽어 남은 것이라 바로 이어집니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
