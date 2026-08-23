"""이름 → **정식 법인명**. 후보를 모으는 열쇠를 만들기 위한 것이다.

왜 이걸 쓰나 (2026-08-14)

같은 회사가 표기만 달라 노드가 갈리는 걸 막으려면 「이 둘이 같은 회사인가」를
알아야 한다. 쌍을 전부 비교하면 2,225곳 × 2,225 = 247만 쌍이라 못 한다.

그래서 **각 이름에 열쇠를 붙이고 같은 열쇠끼리 묶는다**(O(n)).

★열쇠를 만드는 방법 비교 — 독립 정답지(손 목록 65쌍, 전부 같은 회사)로 실측.
  이 저장소에서 두 방식의 재현율을 비교해 둔 자리는 여기 하나다.

      후보 생성 방식        잡음    놓침    재현율
      음차 골격 (문자)       35      30     54%
      정식명   (지식)       58       7     89%     ← 이 모듈
      둘 다                59       6     91%

  음차가 못 잡는 것은 구조적이다 — 문자에 정보가 없다:
      두문자어   TSMC ↔ Taiwan Semiconductor
      다른 이름  Alphabet ↔ 구글
  정식명은 그걸 잡는다. 반대로 정식명은 한글 음차 표기를 못 읽을 때가 있어
  (「오픈ai」·「밀레」·「와이씨」), 둘을 겹쳐 쓰면 91%가 된다.

  남는 9%는 사람도 논쟁할 만한 것들이다(Alphabet과 Google은 실제로 별개 법인).
  그건 레지스트리에 `source='hand'`로 넣는다.

**정식명은 노드 이름이 되지 않는다.** 열쇠일 뿐이다.

  모델은 모르는 회사에서 지어낸다(실측):
      "서룡과기" → Seoryong Technology       지어냄
      "TSTC"   → Texas State Technical College  ★대학교
      LGEBR·LGEFL·LGEPR → 전부 "LG Electronics, Inc."  ★해외법인을 본사로

  지어낸 값이 노드 이름이 되면 되돌릴 수 없다. 그래서 **후보를 모으는 데만**
  쓰고, 같은 회사인지는 쌍 판정이 확정한다. 지어내도 후보가 하나 더 생길 뿐이다.

★한 번 물으면 캐시한다 — 실행마다 답이 흔들리기 때문이다.
      1차 "Alstom SA"  ·  2차 "Alstom S.A."
      1차 재현율 78%   ·  2차 75%
  저장해 두면 흔들림이 문제가 되지 않는다.
"""

from __future__ import annotations

from pipeline.llm import ask_json
from pipeline.normalizer.company_registry import block_key, ensure, is_leaked

_BATCH = 20

_SYSTEM = """당신은 기업 이름을 **정식 명칭**으로 되돌리는 도구입니다.

주어진 표기가 어느 회사를 가리키는지 알면, 그 회사의 **영문 정식 법인명**을 쓰세요.
같은 회사는 표기가 달라도 반드시 같은 정식명이 나와야 합니다.

    "엔비디아" · "NVIDIA"   → "NVIDIA Corporation"
    "TSMC" · "대만반도체"     → "Taiwan Semiconductor Manufacturing Company"
    "마이크론" · "마이크론 테크놀로지" → "Micron Technology, Inc."

【반드시 지킬 것】
1. 모르는 회사면 canonical 에 **입력 문자열을 그대로** 넣으세요.
   설명 문장("모르겠습니다"·"입력을 그대로 돌려주세요")을 넣지 마세요.
2. **자회사·지역법인은 본사와 다른 회사**입니다. 본사 이름으로 바꾸지 마세요.
       "마이크론 메모리 말레이시아"  → 그대로
       "LGEBR"(LG전자 브라질 법인)  → 그대로
   ★약어를 아는 회사로 억지로 맞추지 마세요. 이게 가장 흔한 실수입니다.
3. 지주사와 사업회사도 다릅니다. "Alphabet" 을 "Google" 로 바꾸지 마세요.
4. 회사가 아닌 것(국가·대학·단체)은 그대로 두세요."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "canonical": {"type": "string"},
                },
                "required": ["name", "canonical"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_LOAD = ("SELECT alias_key, canon_name FROM company_aliases "
         "WHERE canon_name IS NOT NULL AND alias_key = ANY(%s)")


def canonical_names(conn, names: list[str],
                    keys: dict[str, str]) -> dict[str, tuple[str, str]]:
    """이름 목록 → {이름: (정식명, 열쇠)}. 캐시에 있으면 안 묻는다.

    `keys`는 {이름: 정규화키} — 캐시 조회에 쓴다.
    """
    ensure(conn)
    uniq = sorted({n for n in names if n and n.strip()})
    if not uniq:
        return {}

    out: dict[str, tuple[str, str]] = {}
    with conn.cursor() as cur:
        cur.execute(_LOAD, ([keys.get(n, n) for n in uniq],))
        cached = dict(cur.fetchall())
    for n in uniq:
        hit = cached.get(keys.get(n, n))
        if hit:
            out[n] = (hit, block_key(hit))

    todo = [n for n in uniq if n not in out]
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        got = ask_json(_SYSTEM, "\n".join(f"- {n}" for n in chunk),
                       schema=_SCHEMA, name="canonical_name",
                       fallback={"items": []})
        known = set(chunk)
        for it in got.get("items", []):
            if it["name"] not in known:
                continue
            canon = (it.get("canonical") or "").strip()
            # 지시문이 새어 나왔거나 비었으면 입력을 그대로 쓴다
            if is_leaked(canon):
                canon = it["name"]
            out[it["name"]] = (canon, block_key(canon))
        for n in chunk:
            out.setdefault(n, (n, block_key(n)))
    return out
