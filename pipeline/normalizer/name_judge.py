"""이름이 **고유명인가 설명인가** — 모델에게 묻고 결과를 캐시한다.

★왜 목록을 버렸나 (2026-08-13)

전에는 `generic_names.py`가 의미 키워드 목록으로 판별했다:

    _GENERIC_NOUNS   고객사·거래처·협력사·수요처 …   15개
    _QUALIFIERS      글로벌·해외·국내·대형 …        19개
    _CATEGORY_NOUNS  기업·업체·회사·빅테크 …        14개

이 방식은 **열린 어휘를 닫힌 목록으로 막으려는 것**이라 두 방향으로 실패한다.

  ① 놓친다 — 「국내 유수의 배터리 소재 공급 파트너」처럼 목록에 없는 조합은 통과
  ② **친다** — 부분 문자열이 실명을 때린다. 실측으로 두 건이나 나왔다:
        「삼성전자 총파업 계획」  ← 「총파**업계**획」의 '업계'에 걸림
        「한국정보통신」 등 417곳 ← supply_contract의 '정보'에 걸림(2026-08-12)

  ②가 특히 나쁘다. 실명이 조용히 버려지고 **엣지가 아예 안 만들어진다.**

★그래서 2단으로 나눈다

    1차  문법·닫힌 목록 (무료·전수)
         「…들」·「L사」·용언 어미·비공개·타입명 — `generic_names.py`
         한국어 문법과 공시 정형 표현은 **닫혀 있어** 목록이 옳다.

    2차  모델 판정 (0.25원·해소 안 된 새 이름만)   ← 이 모듈
         「이 문자열이 특정 회사·기관의 고유명인가, 아니면 설명인가」
         corp_code로 해소된 이름은 애초에 물을 필요가 없다.

★결과는 반드시 캐시한다. 같은 이름이 기사마다 나오는데 매번 물으면 돈이 는다.
"""

from __future__ import annotations

from typing import Optional

from pipeline.llm import ask_json

_MODEL = "gpt-4o-mini"      # 라우터급 — 판정만 하면 되므로 충분
_BATCH = 20

_CREATE = """
CREATE TABLE IF NOT EXISTS name_verdicts (
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'entity',
    is_proper   BOOLEAN NOT NULL,
    reason      TEXT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name, kind)
)
"""
_LOAD = "SELECT name, is_proper FROM name_verdicts WHERE kind = %s AND name = ANY(%s)"
_SAVE = """
INSERT INTO name_verdicts (name, kind, is_proper, reason) VALUES (%s, %s, %s, %s)
ON CONFLICT (name, kind) DO UPDATE SET is_proper = EXCLUDED.is_proper,
                                       reason = EXCLUDED.reason, decided_at = now()
"""

# ── 개체(회사·인물·기관) — 고유명이어야 한다 ──────────────────
_SYSTEM_ENTITY = """당신은 기업 지식그래프에 넣을 **개체 이름**을 가려내는 도구입니다.

각 문자열이 **특정 회사·기관·인물의 고유한 이름**인지, 아니면
**설명하는 말**인지 판정하세요.

【고유명 (is_proper=true)】 그것 하나를 가리키는 이름
   "SK하이닉스" · "한미반도체" · "엔비디아" · "한국전자기술연구원"
   "전국금속노동조합" · "공정거래위원회"
   ★모르는 회사여도 이름처럼 생겼으면 true입니다. 유명한지는 묻지 않습니다.
   ★외국 회사·자회사·펀드 이름도 고유명입니다.
     "YIK JAPAN" · "하나에스앤비 소부장2호신기술조합"

【설명 (is_proper=false)】 여럿을 가리키거나, 무엇인지 특정되지 않는 말
   "고객사" · "거래처" · "협력사" · "복수의 매입처"
   "글로벌 빅테크" · "해외 SSD 전문업체" · "국내 대형 반도체 기업"
   "익명의 투자자" · "관련 업체"
   ★수식어 + 일반명사 조합이 전형입니다. 어느 회사인지 알 수 없습니다.

【판단이 어려울 때】
   · 회사 이름 같은데 처음 본다  → true (모르는 회사일 뿐입니다)
   · 업종을 설명하는 말 같다     → false
   · 사건·현상을 가리키는 말     → false ("총파업 계획"·"주가 급등")

reason은 판정 근거를 5~15자로 짧게."""

# ── 제품·기술 — **카테고리도 허용한다** ────────────────────────
# ★2026-08-13: 개체와 같은 기준을 쓰다가 「휴머노이드 로봇」이 「일반명사 조합」으로
#   버려졌다. 우리 그래프는 **제품군을 일부러 노드로 쓴다** — 상위 제품 노드가
#   「산업용 로봇」·「액추에이터」·「감속기」·「변압기」처럼 대부분 카테고리다.
#   「A사가 감속기를 만든다」는 그 자체로 쓸모 있는 사실이라 버리면 안 된다.
_SYSTEM_PRODUCT = """당신은 기업 지식그래프에 넣을 **제품·기술 이름**을 가려내는 도구입니다.

각 문자열이 **제품·기술·부품·소재를 가리키는 이름**인지, 아니면
**설명하는 문장**인지 판정하세요.

【이름 (is_proper=true)】 그 물건을 부르는 말
   구체적 제품   "HBM3E" · "TC 본더" · "갤럭시 S25" · "그리핀"
   ★제품군·기술 분류도 이름입니다 — 이것도 true입니다:
     "휴머노이드 로봇" · "산업용 로봇" · "협동로봇" · "감속기" · "액추에이터"
     "메모리반도체" · "파운드리" · "유리기판" · "변압기"
   ★모르는 제품이어도 이름처럼 생겼으면 true입니다.

【설명 (is_proper=false)】 이름이 아니라 서술
   "AI 연산에 필수적인 고대역폭메모리" · "전공정에서 발생하는 결함을 검사하는 장비"
   "락(위상) 고정 루프(PLL) 회로 및 이를 포함하는 디스플레이 구동기"   ← 특허 제목
   "고객사가 요구한 사양" · "차세대 기술"
   ★용언(하는·되는·위한)이 들어가면 서술입니다.
   ★무엇인지 특정되지 않는 말도 false입니다 — "관련 부품" · "각종 소재"

reason은 판정 근거를 5~15자로 짧게."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "is_proper": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "is_proper", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _cached(conn, names: list[str], kind: str) -> dict[str, bool]:
    with conn.cursor() as cur:
        cur.execute(_CREATE)
        cur.execute(_LOAD, (kind, names))
        return dict(cur.fetchall())


def judge_names(conn, names: list[str], kind: str = "entity") -> dict[str, bool]:
    """이름 목록 → {이름: 노드로 쓸 수 있는가}. 캐시에 있으면 안 묻는다.

    `kind`는 `"entity"`(회사·인물·기관) 또는 `"product"`(제품·기술)다.
    **기준이 다르다** — 제품은 카테고리(「감속기」)도 이름으로 인정한다.
    같은 기준을 쓰다가 「휴머노이드 로봇」이 버려진 적이 있다(2026-08-13).

    실패하면 **true로 본다** — 판정기가 죽었다고 실명을 버리면 안 된다.
    (놓친 설명형은 뒤의 검증·감사가 다시 잡는다. 반대는 복구가 안 된다.)
    """
    uniq = sorted({n.strip() for n in names if n and n.strip()})
    if not uniq:
        return {}

    system = _SYSTEM_PRODUCT if kind == "product" else _SYSTEM_ENTITY
    out = _cached(conn, uniq, kind)
    todo = [n for n in uniq if n not in out]
    if not todo:
        return out

    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        got = ask_json(system, "\n".join(f"- {n}" for n in chunk),
                       schema=_SCHEMA, name="name_judge", fallback={"items": []})
        if got.get("failed"):
            for n in chunk:
                out[n] = True          # 보수적 통과
            continue
        known = set(chunk)
        with conn.cursor() as cur:
            for it in got["items"]:
                if it["name"] not in known:
                    continue
                out[it["name"]] = bool(it["is_proper"])
                cur.execute(_SAVE, (it["name"], kind, bool(it["is_proper"]),
                                    (it.get("reason") or "")[:60]))
        for n in chunk:
            out.setdefault(n, True)    # 모델이 빠뜨린 것도 보수적 통과
    return out


def is_descriptive(conn, name: Optional[str], kind: str = "entity") -> bool:
    """이 이름이 설명형인가(= 노드로 만들면 안 되는가)."""
    if not name:
        return True
    return not judge_names(conn, [name], kind).get(name.strip(), True)
