"""배치 공통 — **콘솔이 한글을 삼키지 않게** 한다.

왜 여기에 있나 (2026-08-02)

배치 두 개가 **아무 메시지도 없이** 죽었다. `stub_profiles`는 LLM 2,267건을
10분 돌리고 사라졌고, `company_vectors`는 종료코드 1만 남기고 로그가 비었다.
원인은 각자의 버그였는데, **그 버그가 안 보인 이유는 하나**였다.

    sys.stdout.reconfigure(encoding="utf-8")   ← 모든 배치가 이 줄만 갖고 있었다
    sys.stderr                                  ← 그대로 cp949

파이썬이 예외를 찍을 때는 stderr를 쓴다. 트레이스백에 한글이나 「✎」 같은 글자가
섞이면 cp949로 인코딩이 안 되고, **트레이스백을 찍다가 다시 죽는다.** 결과는
「종료코드 1, 출력 없음」 — 원인을 알 길이 없는 가장 나쁜 실패다.
(실제로 숨어 있던 건 `owner_key` NOT NULL 위반이었고, 한 줄이면 고칠 것이었다.)

`python -m batch.*`는 무엇을 돌리든 이 파일을 먼저 통과하므로, 여기서 한 번만
고쳐 두면 배치마다 잊을 일이 없다.
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        # errors="backslashreplace" — 콘솔이 못 그리는 글자가 있어도 **죽지 않고**
        # 이스케이프로 남긴다. 로그가 조금 지저분한 편이 사라지는 것보다 낫다.
        #
        # ★line_buffering — 출력을 파일로 넘기면 파이썬이 8KB씩 모아 뒀다가
        #   쓴다(2026-08-03). 뉴스 재수집을 로그로 돌렸더니 35분 동안 로그가
        #   **빈 파일**이었다. 죽은 건지 도는 건지 알 수가 없어 프로세스 CPU를
        #   재 봐야 했다. 몇 시간짜리 배치에서 그건 못 쓸 상태다.
        #   줄 단위로 흘려보내면 `tail -f`로 그대로 따라갈 수 있다.
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace",
                            line_buffering=True)
