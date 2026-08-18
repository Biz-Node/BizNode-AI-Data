"""관계 품질 정밀 점검 — 근거를 읽어 **방향**과 **사건성**을 재판정한다.

문자 규칙으로는 못 잡는 두 가지를 LLM에 맡긴다.

① SUPPLIES_TO 방향
   한국어 「A는 B와 공급계약을 체결했다」는 누가 공급하는지 말해주지 않는다.
   실측: 「SK하이닉스 -공급-> 테스」 — 테스가 장비업체이므로 방향이 반대다.
   근거 문장과 기업의 업종을 함께 보면 판단할 수 있다.

② Event 사건성
   「설비 증설 필요성」·「메모리 생산 확대」는 전망·추세이지 사건이 아니다.
   판별 기준: **언제 일어났는지 날짜를 댈 수 있는가.**

발견한 것은 표시만 하거나(방향 반전) 삭제한다. 어느 쪽이든 `--dry-run`으로 먼저 본다.

실행:
  python -m batch.audit.relations --dry-run
  python -m batch.audit.relations --scope supply     # 방향만
  python -m batch.audit.relations --scope event      # 사건성만
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor


from app.core.database import neo4j_session
from pipeline.llm import ask_json
from pipeline.importer.evidence import EVIDENCE_COLLECTION
from pipeline.validators.matrix import validate_edge
from pipeline.vectorstore.chroma_store import get_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_WORKERS = 8

# ── ① 방향 엣지 전반 ────────────────────────────────────────
#
# ★뉴스 기반 방향 엣지만 검사한다. DART 기반은 구조화 데이터라 방향이 보장된다
#   (「최대주주 현황」 API는 누가 주주인지 알려준다). 문제는 **문장에서 추론한 것**뿐이다.
#   실측: 전체 4,000엣지 중 검사 대상은 390건(10%).
#
# 오류 유형이 엣지마다 같다 — 한국어 **피동·사역 표현**을 문장 주어 기준으로 뽑는다:
#   "레인보우로보틱스가 삼성전자에 **인수되는**"  → 레인보우 -ACQUIRES-> 삼성  ✗
#   "삼성전자의 콜옵션 행사로 지분 확보"          → 레인보우 -OWNS-> 삼성      ✗
#   "SK하이닉스는 테스와 공급계약 체결"           → SK -SUPPLIES-> 테스        ✗
DIRECTIONAL_EDGES = {
    "SUPPLIES_TO":     ("공급자", "수요자"),
    "ACQUIRES":        ("인수자", "피인수기업"),
    "OWNS_STAKE_IN":   ("주주", "피투자기업"),
    "SUES":            ("원고(제소한 쪽)", "피고"),
    "REGULATES":       ("규제·수사 기관", "규제 대상 기업"),
    "DEPENDS_ON":      ("의존하는 기업", "의존 대상 기술·제품"),
    "IS_EXECUTIVE_OF": ("임원(인물)", "소속 기업"),
}

# ★증분 검사 — 이미 본 엣지는 건너뛴다.
# 데이터가 커지면 매번 전수 검사할 수 없다. 검사한 엣지에 시점을 남겨,
# 다음 실행은 **새로 들어온 것만** 본다(O(신규)).
# 렉시콘·프롬프트를 고친 뒤에는 `--full`로 전수 재검사한다.
_FIND_DIRECTIONAL = """
MATCH (a)-[r]->(b)
WHERE r.source_type = 'news' AND type(r) = $edge_type
      AND ($full OR r.direction_checked_at IS NULL)
RETURN elementId(r) AS eid, type(r) AS edge,
       coalesce(a.name,'?') AS supplier, coalesce(b.name,'?') AS customer,
       labels(a)[0] AS a_label, labels(b)[0] AS b_label,
       coalesce(r.evidence_id, '') AS ev, coalesce(r.evidence_ids, []) AS evs
"""

_FIND_SUPPLY = """
MATCH (a:Company)-[r:SUPPLIES_TO]->(b:Company)
WHERE r.source_type = 'news'
RETURN elementId(r) AS eid, a.name AS supplier, b.name AS customer,
       coalesce(r.evidence_id, '') AS ev, coalesce(r.evidence_ids, []) AS evs
"""

_SUPPLY_SYSTEM = """관계의 **방향**을 판정하세요.

한국어는 피동·사역 표현이 흔해서 문장 주어가 곧 행위자가 아닙니다.
    「A가 B에 인수되는」        → 인수자는 B
    「A의 콜옵션 행사로 확보」   → 확보한 쪽은 A
    「A는 B와 공급계약 체결」    → 누가 공급하는지 문장만으로는 알 수 없음
근거 문장과 두 개체의 **성격**을 보고 판정하세요.

【판단 기준】
· 공급: 장비·부품·소재·후공정 업체 → 칩메이커 / 칩메이커 → 세트업체·빅테크
        「발주」한 쪽이 수요자, 「수주」·「납품」한 쪽이 공급자
· 인수: 지분을 **더 갖게 된** 쪽이 인수자
· 지분: 주식을 **보유한** 쪽이 주주
· 소송: **제소한** 쪽이 원고
· 규제: 검찰·공정위·금융위 등 **기관**이 항상 주체

【답변 방식】
「현재 방향이 맞다/틀리다」로 답하지 말고, **주체와 대상의 이름을 그대로 쓰세요.**
방향 비교는 시스템이 합니다.
  · supplier : 관계의 **주체**(공급자·인수자·주주·원고·규제기관)
  · customer : 관계의 **대상**(수요자·피인수·피투자·피고·규제대상)
  · 둘 다 입력에 나온 이름을 **그대로** 쓰세요(새 이름을 만들지 마세요).
  · 근거로 판단할 수 없으면 confident=false."""

# ★불리언(correct)으로 물으면 근거는 맞는데 값이 뒤집혀 나오는 일이 잦았다(실측).
#   「누가 공급자인가」를 이름으로 답하게 하고 비교는 코드가 한다.
_SUPPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "confident": {"type": "boolean"},
        "supplier": {"type": "string"},
        "customer": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["confident", "supplier", "customer", "reason"],
    "additionalProperties": False,
}

# ── ②' 대칭 엣지의 「병렬 언급」 오독 ────────────────────────
#
# PARTNERS_WITH·COMPETES_WITH는 방향이 없어 앞의 검사가 못 잡는다. 대신 다른
# 오류가 난다 — **한 문장에 같이 나왔다는 이유로 관계를 만든다.**
#
# 한국어 「A와 B의 X」는 두 가지로 읽힌다:
#   (가) A와 B **사이의** X        → 관계 맞음
#   (나) A와 B **각각의** X        → 관계 아님. 나란히 언급했을 뿐
# 실측된 오추출:
#   "삼성전자와 SK하이닉스의 장기공급계약(LTA)이 …물량을 배분하는 기준"
#     → 두 회사가 **고객사와** 맺은 각자의 계약인데 「삼성 -PARTNERS_WITH-> SK」가 났다.
#
# 같은 오독이 나열문에서도 난다: "▲삼성전자 ▲SK하이닉스 ▲마이크론" · "국내 3사는 모두"
#
# ★`SUES`도 대상에 넣었다(표본 심층검사 2026-08-02). 방향 엣지지만 **같은 병렬
#   언급 오류**가 난다 — 둘이 나란히 **원고**인데 서로 제소한 것으로 읽는다:
#     "경제개혁연대와 DB하이텍 소액주주연대가 총수일가의 과다 보수를 문제 삼고
#      제기한 주주대표소송"
#       → 「경제개혁연대 -SUES-> DB하이텍 소액주주연대」가 났다. 피고는 총수일가다.
#   방향 검사는 「누가 원고인가」만 보므로 **둘 다 원고인 경우**를 못 잡는다.
_FIND_SYMMETRIC = """
MATCH (a)-[r]->(b)
WHERE r.source_type = 'news'
      AND type(r) IN ['PARTNERS_WITH', 'COMPETES_WITH', 'SUES']
      AND ($full OR r.parallel_checked_at IS NULL)
RETURN elementId(r) AS eid, type(r) AS edge,
       coalesce(a.name, '') AS supplier, coalesce(b.name, '') AS customer,
       labels(a)[0] AS a_label, labels(b)[0] AS b_label,
       coalesce(r.subtype, '') AS subtype,
       coalesce(r.evidence_id, '') AS ev, coalesce(r.evidence_ids, []) AS evs
"""

_SYM_SYSTEM = """근거 문장이 두 대상 **사이의 관계**를 말하는지 판정하세요.

★두 가지를 **반드시 구분**하세요. 섞으면 진짜 관계를 지우게 됩니다.
  (1) 두 대상 사이에 **아무 관계도 없다** (나란히 언급만 됐다)  → "parallel"
  (2) 관계는 **있는데 유형이 다르다** (협력이 아니라 거래·인수 등) → "other_relation"

【verdict="parallel" — 관계 자체가 없음. 이것만 삭제 대상입니다】
· 같은 시장·산업이라 함께 **열거**된 경우
    "▲삼성전자 ▲SK하이닉스 ▲마이크론의 진검승부"
    "국내 메모리 3사는 모두 감산에 들어갔다"
· 각자 **제3자와** 맺은 관계를 나란히 서술한 경우 — 서로는 무관
    "삼성전자와 SK하이닉스의 장기공급계약(LTA)"   (상대는 각자의 고객사)
    "양사 모두 엔비디아에 HBM을 납품한다"         (관계는 둘 다 엔비디아와)
· ★**둘 다 원고**인데 서로 제소한 것으로 읽은 경우 (SUES에서 자주 난다)
    "경제개혁연대와 DB하이텍 소액주주연대**가** … 제기한 주주대표소송"
      → 둘은 **함께 소송을 낸 쪽**이다. 피고는 제3자(총수일가)다.
    "삼성전자와 LG전자를 **상대로** 소송을 제기했다"
      → 이건 반대다. 둘은 함께 **피고**이고, 원고는 문장 앞의 제3자다.
    구분법: 두 회사 뒤에 붙은 조사를 보세요. 「A와 B**가** 제기」면 둘 다 원고,
    「A와 B**를 상대로**」면 둘 다 피고. 어느 쪽이든 **서로 소송한 게 아닙니다.**
· 한쪽만 주체이고 다른 쪽은 배경으로만 나온 경우

【verdict="other_relation" — 관계는 실재하나 유형이 틀림. 삭제하지 않습니다】
근거가 두 대상 사이의 **실제 거래·자본·법적 행위**를 말하는데 협력·경쟁이 아닐 때.
    "AMD는 TSMC의 3나노 공정을 이용한다"          → 실제로는 공급 관계
    "두산테스나가 테라다인으로부터 장비를 구입했다"  → 실제로는 공급 관계
    "두산테스나가 세메스로부터 자산을 양수한다"      → 실제로는 인수 관계
    "SFA반도체는 삼성전자에서 분사했다"            → 실제로는 지분·모회사 관계
  어떤 유형인지도 `actual`에 적으세요
  (SUPPLIES_TO · ACQUIRES · OWNS_STAKE_IN · DEVELOPS · SUES · REGULATES 중 하나,
   모르면 빈 문자열).

【verdict="between" — 이 유형의 관계가 맞음】
· PARTNERS_WITH : 두 대상이 **서로** 합작·공동개발·기술제휴·크로스라이선스
    "삼성전자는 SK하이닉스와 표준화 협의체를 공동 설립했다"
    ※ 담합·공모도 **서로 조율한 행위**이므로 between입니다.
· COMPETES_WITH : 두 대상이 **같은 것을 두고** 다툰다(시장·수주·점유율)
    "글로벌 D램 시장을 놓고 삼성전자와 SK하이닉스의 선두 경쟁이 치열하다"
    ※ 제3자를 두고도 성립합니다("엔비디아 물량을 두고 A와 B가 경쟁")
· SUES : **앞의 대상이 뒤의 대상을 제소**했다 (원고 → 피고)
    "넷리스트는 SK하이닉스를 텍사스 서부지법에 제소했다"
    "티씨케이가 와이엠씨를 상대로 특허침해 소송을 제기했다"
    ※ 맞소송이면 양쪽 다 between입니다(각자 상대를 제소했으므로).
    ※ 같은 시장의 1·2위로 **비교**되면 경쟁으로 봅니다.

【주의】
· 근거가 여러 개면 **하나라도** between이면 between입니다.
· 판단할 수 없으면 "unclear". **애매하면 지우지 않습니다.**"""

_SYM_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["between", "other_relation", "parallel", "unclear"]},
        "actual": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "actual", "reason"],
    "additionalProperties": False,
}

# ── ②'' 양방향 공급 — **쌍으로 봐야 보이는** 오류 ──────────────
#
# 방향 검사는 엣지를 **하나씩** 본다. 그래서 A→B와 B→A가 **둘 다** 있는 상황을
# 못 잡는다. 각각 따로 보면 둘 다 그럴듯하기 때문이다.
# 실측(2026-07-29): 엔비디아↔삼성전자, 엔비디아↔SK하이닉스가 쌍방으로 있었다.
#
# ★그런데 이건 오류가 아닐 수도 있다 — 실제로 서로 파는 관계가 있다:
#     삼성전자 → 엔비디아   HBM 공급
#     엔비디아 → 삼성전자   AI 데이터센터용 GPU 공급
# 그러니 자동으로 지우면 안 된다. **한쪽이 근거 없이 만들어진 경우만** 걸러야 한다.
# 판정은 두 방향의 근거를 **함께** 보여주고 묻는다.
_FIND_BIDIR = """
MATCH (a:Company)-[r1:SUPPLIES_TO]->(b:Company)
MATCH (b)-[r2:SUPPLIES_TO]->(a)
WHERE elementId(a) < elementId(b)
      AND ($full OR r1.bidir_checked_at IS NULL OR r2.bidir_checked_at IS NULL)
RETURN elementId(r1) AS eid1, elementId(r2) AS eid2,
       coalesce(a.name,'') AS a_name, coalesce(b.name,'') AS b_name,
       coalesce(r1.subtype,'') AS sub1, coalesce(r2.subtype,'') AS sub2,
       coalesce(r1.corroboration,1) AS corr1,
       coalesce(r2.corroboration,1) AS corr2,
       coalesce([r1.evidence_id],[]) + coalesce(r1.evidence_ids,[]) AS ev1,
       coalesce([r2.evidence_id],[]) + coalesce(r2.evidence_ids,[]) AS ev2
"""

_BIDIR_SYSTEM = """두 기업이 **서로에게** 공급한다고 되어 있습니다. 맞는지 판정하세요.

양방향 공급은 **실제로 존재합니다.** 흔한 예:
  · 삼성전자 → 엔비디아 (HBM 메모리)   /  엔비디아 → 삼성전자 (AI 서버용 GPU)
  · 삼성전자 → 애플 (디스플레이·칩)     /  애플 → 삼성전자 (해당 없음, 이건 단방향)
  · 소재사끼리 서로 다른 품목을 주고받는 경우
따라서 **양방향이라는 사실만으로 오류가 아닙니다.**

【판정 — 양쪽 근거를 각각 보고】
· both      두 방향 모두 근거가 있다 (서로 다른 품목을 주고받는다)
· only_a2b  A→B만 근거가 있다. B→A는 근거 없음
· only_b2a  B→A만 근거가 있다. A→B는 근거 없음
· neither   양쪽 다 근거가 부실하다

【판단 기준】
· 각 방향의 근거에 **무엇을 공급하는지**가 드러나야 그 방향을 인정합니다.
· 「A와 B가 계약을 체결」처럼 누가 주는지 모르는 문장은 근거로 보지 마세요.
· 산업 역할을 고려하세요 — 장비·소재사는 칩메이커에 공급하고,
  칩메이커는 세트업체·빅테크에 공급합니다.
· 애매하면 both. 참인 관계를 지우는 손해가 더 큽니다."""

_BIDIR_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["both", "only_a2b", "only_b2a", "neither"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

# ── ② 사건성 ─────────────────────────────────────────────────
# ★조건이 `event_type = '뉴스이슈'`였는데, `classify_events`가 11종 유형을
#   붙이면서 **그 값이 그래프에서 사라졌다.** 검사는 계속 「0건 점검」을
#   출력했고, 아무도 안 보는 통과로 보였다. 조용한 무력화다.
#   유형에 기대지 말고 **검사 시점 표시**로 증분을 관리한다(다른 검사와 동일).
_FIND_EVENT = """
MATCH (e:Event)
WHERE $full OR e.eventness_checked_at IS NULL
OPTIONAL MATCH (e)-[r]-(c)
RETURN e.event_id AS eid, e.name AS name,
       count(r) AS deg,
       collect(DISTINCT coalesce(r.occurred_at, r.valid_from))[0..3] AS dates,
       collect(DISTINCT coalesce(c.name, ''))[0..4] AS linked
"""

_MARK_EVENTNESS = ("MATCH (e:Event) WHERE e.event_id IN $eids "
                   "SET e.eventness_checked_at = datetime()")

_MARK_SUSPECT = ("MATCH (e:Event {event_id: $eid}) "
                 "SET e.eventness_suspect = true, e.eventness_why = $why, "
                 "    e.eventness_checked_at = datetime()")

_EVENT_SYSTEM = """Event 노드로 남길 가치가 있는지 판정하세요.

지식그래프의 Event는 **특정 시점에 벌어진 일**입니다.
지속되는 상태·시장 추세·전망·의견은 Event가 아닙니다.

【판별 질문 — 딱 하나】
"이것은 **어느 시점에 벌어진 일**인가, 아니면 **한동안 이어지는 상태**인가?"

✓ Event   청주 공장 화재 · 담합 혐의 피소 · HBM4 양산 일정 연기 · 압수수색
          중국 텅스텐 수출 제한 · 개인정보 유출 · 이란 전쟁 · 파업 결의
          용인 클러스터 착공 · 조직 신설 · 시제품 납품
          → 모두 "언제 그런 일이 있었다"고 말할 수 있습니다.

✗ Event 아님
          설비 증설 필요성 · D램 추가수익 확보 포석      (의견·해석)
          메모리 공급 부족 · 스마트폰 시장 침체          (시장 상황)
          D램 시장 점유율 증가 · HBM4 매출 비중 확대     (추세·지표)
          AI 메모리 전쟁 본격화…SK하이닉스 독주 [아듀2025]  (기사 제목)

✗ Event 아님 — **사물·장소·제품·행사장의 이름**만 있고 벌어진 일이 없는 것
          모바일 운전면허증 · 디지털 리빙 포털 스토어      (서비스·제품 이름)
          히로시마 공장 · 테일러 팹                        (장소·설비)
          구글 캠프 · ICRA 2025 · 국제 장애인올림픽의 날    (행사·기념일 이름)
          → 「무엇이 어떻게 됐다」가 없습니다. 이름만으로는 무슨 일인지 모릅니다.
          ★단 **행위가 붙어 있으면 사건입니다**: 「히로시마 공장 가동 중단」 ✓
            「모바일 운전면허증 출시」 ✓ 「테일러 팹 착공」 ✓

【주의】
· 이름에 날짜가 없어도 됩니다 — 발생일은 별도로 기록돼 있고 참고로 제공됩니다.
· 「~ 필요성」「~ 포석」「~ 전망」「~ 가능성」처럼 **일어나지 않은 것**만 제외하세요.
· **애매하면 is_event=true.** 지우는 것보다 남기는 편이 낫습니다.
· 여러 기업과 연결된 것은 파급 구조를 담고 있으니 특히 보수적으로 판단하세요."""

_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_event": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_event", "reason"],
    "additionalProperties": False,
}


def _ask(system: str, user: str, schema: dict, name: str) -> dict:
    """실패 fallback은 **아무것도 바꾸지 않는 쪽**이다.
    confident=False면 판정을 적용하지 않고, is_event=True면 사건을 지우지 않는다.
    `ask_json`이 failed 표시를 붙이므로 호출부가 검사 완료로 기록하지 않는다.
    """
    return ask_json(system, user, schema=schema, name=name,
                    fallback={"confident": False, "is_event": True, "reason": ""})


def _evidence(row: dict) -> str:
    """엣지의 근거 문장. 실패하면 빈 문자열."""
    # ★중복 제거가 필수다. 클러스터링된 엣지는 `evidence_id`가 `evidence_ids`에도
    #   들어 있어 그냥 이으면 같은 id가 두 번 들어간다. ChromaDB는 중복 id를
    #   DuplicateIDError로 거부하고, 그러면 아래 except가 삼켜 **근거 없이**
    #   LLM에 묻게 된다 → 전부 「판단불가」. 조용히 검사가 무력화된다.
    seen: set[str] = set()
    ids = []
    for e in [row.get("ev", ""), *row.get("evs", [])]:
        if e and e not in seen:
            seen.add(e)
            ids.append(e)
    if not ids:
        return ""
    try:
        got = get_store().get(EVIDENCE_COLLECTION, ids[:3])
        return "\n---\n".join(d for d in got.get("documents", []) if d)[:1500]
    except Exception as exc:
        print(f"    [근거조회 실패] {ids[:3]} → {exc!r}")
        return ""


_INVERT = ("MATCH ()-[r]->() WHERE elementId(r)=$eid "
           "CALL apoc.refactor.invert(r) YIELD output "
           "SET output.direction_corrected=true, "
           "    output.direction_checked_at=datetime() RETURN 1 AS ok")
# 검사 완료 표시 — 반전하지 않은(정상·판단불가) 엣지에 남긴다.
# 다음 실행이 이들을 건너뛰어 LLM 비용이 신규분에만 든다.
_MARK_CHECKED = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
                 "SET r.direction_checked_at = datetime()")
_DEL_EVENT = "MATCH (e:Event {event_id:$eid}) DETACH DELETE e"


def check_supply(session, dry_run: bool, edge_type: str = "SUPPLIES_TO",
                 full: bool = False) -> tuple[int, int]:
    """방향 엣지 하나를 점검한다. `full=False`면 **미검사분만**(증분)."""
    rows = [dict(r) for r in session.run(_FIND_DIRECTIONAL,
                                         edge_type=edge_type, full=full)]
    role_a, role_b = DIRECTIONAL_EDGES.get(edge_type, ("주체", "대상"))
    scope = "전수" if full else "신규"
    print(f"\n[{edge_type}] 뉴스 기반 {scope} {len(rows)}건 점검  ({role_a} → {role_b})")
    if not rows:
        return 0, 0

    def judge(row: dict) -> tuple[dict, dict]:
        ev = _evidence(row)
        user = (f"엣지 유형: {edge_type} — {role_a} → {role_b}\n"
                f"현재 방향: {row['supplier']} → {row['customer']}\n\n"
                f"근거:\n{ev or '(근거 없음)'}")
        return row, _ask(_SUPPLY_SYSTEM, user, _SUPPLY_SCHEMA, "direction")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(judge, rows))

    def same(a: str, b: str) -> bool:
        """이름 비교 — 공백·대소문자 무시, 부분일치 허용(표기 흔들림 흡수)."""
        x, y = a.replace(" ", "").lower(), b.replace(" ", "").lower()
        return x == y or x in y or y in x

    flipped = unsure = 0
    checked: list[str] = []      # 반전하지 않은 것 = 다음 실행에서 건너뛸 것
    for row, v in results:
        judged = (v.get("supplier") or "").strip()
        if not v.get("confident") or not judged:
            unsure += 1
            checked.append(row["eid"])
            continue
        # LLM이 답한 공급자가 현재 source와 같으면 정상, 현재 target과 같으면 반전
        if same(judged, row["supplier"]):
            checked.append(row["eid"])
            continue
        if not same(judged, row["customer"]):
            unsure += 1          # 둘 다 아니면 판단 불가로 본다
            checked.append(row["eid"])
            continue

        # ★반전 결과가 매트릭스에 맞는지 확인한다.
        #   이 검증이 없어서 「엔비디아 -DEPENDS_ON-> HBM」을 뒤집어
        #   「HBM(Product) -DEPENDS_ON-> 엔비디아(Company)」를 만들 뻔했다.
        #   방향 엣지 중 양끝 노드 타입이 다른 것(DEPENDS_ON·IS_EXECUTIVE_OF·
        #   REGULATES)은 애초에 반전이 성립하지 않는다.
        ok, why = validate_edge(row["b_label"], row["edge"], row["a_label"])
        if not ok:
            print(f"  · 반전불가 {row['supplier']} → {row['customer']}  "
                  f"({row['b_label']}→{row['a_label']} 매트릭스 위반)")
            unsure += 1
            checked.append(row["eid"])
            continue

        print(f"  ↻ 반전 {row['supplier']} → {row['customer']}  "
              f"(실제 주체: {judged})")
        flipped += 1
        if not dry_run:
            session.run(_INVERT, eid=row["eid"])
    if checked and not dry_run:
        session.run(_MARK_CHECKED, eids=checked)
    print(f"  → 반전 {flipped}건 · 판단불가 {unsure}건 · 정상 "
          f"{len(rows)-flipped-unsure}건")
    return flipped, unsure


_MARK_PARALLEL = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
                  "SET r.parallel_checked_at = datetime()")
_DEL_REL = "MATCH ()-[r]->() WHERE elementId(r)=$eid DELETE r"
# 유형이 틀린 엣지에 표시만 남긴다 — 재분류는 매트릭스 검증을 하는 쪽에서.
# ★어떤 유형이 맞는지(`actual`)도 같이 남긴다. 처음엔 화면에만 찍고 저장하지
#   않아서, 나중에 `apply_retypes`가 고칠 근거가 없었다.
_MARK_RETYPE = ("UNWIND $items AS it "
                "MATCH ()-[r]->() WHERE elementId(r) = it.eid "
                "SET r.retype_suspect = true, r.retype_source = $note, "
                "    r.retype_hint = it.actual")


def check_symmetric(session, dry_run: bool, full: bool = False) -> tuple[int, int]:
    """대칭 엣지가 **관계**인지 **나란한 언급**인지 가린다."""
    rows = [dict(r) for r in session.run(_FIND_SYMMETRIC, full=full)]
    print(f"\n[대칭 엣지] 뉴스 기반 {'전수' if full else '신규'} {len(rows)}건 점검  "
          f"(관계인가, 나란히 언급인가)")
    if not rows:
        return 0, 0

    # ★엣지마다 **주장 문장을 달리** 만든다. 처음엔 「협력이냐 경쟁이냐」 둘 중
    #   하나로만 물었더니, SUES 엣지에도 "서로 경쟁하는 관계다"라고 물어 판정
    #   사유에 「경쟁 관계를 나타내는 내용이 없다」가 나왔다 — 소송을 묻는데
    #   경쟁으로 답하니 판정을 믿을 수 없다.
    _CLAIM = {
        "PARTNERS_WITH": "「{a}」와 「{b}」는 서로 협력하는 관계다",
        "COMPETES_WITH": "「{a}」와 「{b}」는 서로 경쟁하는 관계다",
        "SUES": "「{a}」가 「{b}」를 상대로 소송을 제기했다 (원고 → 피고)",
    }

    def judge(row: dict) -> tuple[dict, dict]:
        ev = _evidence(row)
        tpl = _CLAIM.get(row["edge"], "「{a}」와 「{b}」는 " + row["edge"] + " 관계다")
        claim = tpl.format(a=row["supplier"], b=row["customer"])
        user = (f"주장: {claim} (표현: {row['subtype'] or '-'})\n\n"
                f"근거:\n{ev or '(근거 없음)'}")
        return row, _ask(_SYM_SYSTEM, user, _SYM_SCHEMA, "symmetric")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(judge, rows))

    removed = mistyped = unclear = 0
    checked: list[str] = []
    retype: list[tuple[dict, str]] = []
    for row, v in results:
        verdict = v.get("verdict", "unclear")
        if verdict == "parallel":
            print(f"  ✗ 삭제 [{row['edge']}] {row['supplier']} ↔ {row['customer']}  "
                  f"{v.get('reason','')[:52]}")
            removed += 1
            if not dry_run:
                session.run(_DEL_REL, eid=row["eid"])
            continue
        if verdict == "other_relation":
            # ★지우지 않는다 — 관계는 실재한다. 유형만 틀렸다.
            #   매트릭스 검증이 필요하므로 `repair.misclassified_edges`에 넘긴다.
            actual = (v.get("actual") or "").strip() or "?"
            # ★제안이 **원래 유형과 같으면** 재분류가 아니다. 「유형이 틀렸다」면서
            #   같은 유형을 내놓은 것이므로 실은 「관계가 맞다」는 뜻이다.
            #   실측: SUES를 검사에 넣자 `SUES→SUES`가 47건 나왔다. 그대로 두면
            #   `misclassified_edges`가 **자기 자신으로 재분류**하려 든다.
            if actual.upper() == row["edge"]:
                checked.append(row["eid"])    # 정상으로 보고 **검사 완료 표시**
                continue                      # (안 남기면 다음 실행이 또 본다)
            print(f"  ⇄ 유형오류 [{row['edge']}→{actual}] "
                  f"{row['supplier']} ↔ {row['customer']}")
            mistyped += 1
            retype.append((row, actual))
        elif verdict == "unclear":
            unclear += 1
        checked.append(row["eid"])

    if checked and not dry_run:
        session.run(_MARK_PARALLEL, eids=checked)
        if retype:
            session.run(_MARK_RETYPE,
                        items=[{"eid": r["eid"], "actual": a}
                               for r, a in retype],
                        note="symmetric_audit")
    print(f"  → 관계 아님(삭제) {removed}건 · 유형오류(표시만) {mistyped}건 · "
          f"판단불가 {unclear}건 · 정상 {len(rows)-removed-mistyped-unclear}건")
    if mistyped:
        print(f"    ※ 유형오류는 지우지 않았습니다. "
              f"`python -m batch.repair.misclassified_edges`로 재분류하세요"
              f"(매트릭스 검증 포함).")
    return removed, unclear


_MARK_BIDIR = ("MATCH ()-[r]->() WHERE elementId(r) IN $eids "
               "SET r.bidir_checked_at = datetime()")


def check_bidirectional(session, dry_run: bool, full: bool = False) -> tuple[int, int]:
    """A→B와 B→A가 둘 다 있는 공급 쌍을 **쌍으로** 판정한다.

    한쪽만 근거가 있으면 근거 없는 쪽에 표시만 남긴다(삭제하지 않는다) —
    양방향 공급은 실재하는 관계이므로 자동 삭제가 위험하다.
    """
    rows = [dict(r) for r in session.run(_FIND_BIDIR, full=full)]
    print(f"\n[양방향 공급] {'전수' if full else '신규'} {len(rows)}쌍 점검")
    if not rows:
        return 0, 0

    def ev_text(ids: list) -> str:
        seen, out = set(), []
        for e in ids:
            if e and e not in seen:
                seen.add(e)
                out.append(e)
        if not out:
            return ""
        try:
            got = get_store().get(EVIDENCE_COLLECTION, out[:2])
            return "\n".join(d.split("\n")[0] for d in got.get("documents", []) if d)[:600]
        except Exception as exc:
            print(f"    [근거조회 실패] {out[:2]} → {exc!r}")
            return ""

    def judge(row: dict) -> tuple[dict, dict]:
        a, b = row["a_name"], row["b_name"]
        user = (f"A = 「{a}」   B = 「{b}」\n\n"
                f"[A→B 공급 주장]  subtype: {row['sub1'] or '-'} "
                f"(뒷받침 {row['corr1']}건)\n{ev_text(row['ev1']) or '(근거 없음)'}\n\n"
                f"[B→A 공급 주장]  subtype: {row['sub2'] or '-'} "
                f"(뒷받침 {row['corr2']}건)\n{ev_text(row['ev2']) or '(근거 없음)'}")
        return row, _ask(_BIDIR_SYSTEM, user, _BIDIR_SCHEMA, "bidir")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(judge, rows))

    flagged = both = 0
    checked: list[str] = []
    for row, v in results:
        verdict = v.get("verdict", "both")
        checked += [row["eid1"], row["eid2"]]
        if verdict == "both":
            both += 1
            continue
        # 근거 없다고 판정된 쪽에만 표시. 삭제는 하지 않는다.
        drop = ({"only_a2b": [row["eid2"]], "only_b2a": [row["eid1"]]}
                .get(verdict) or [row["eid1"], row["eid2"]])
        arrow = {"only_a2b": f"{row['b_name']}→{row['a_name']}",
                 "only_b2a": f"{row['a_name']}→{row['b_name']}"}.get(
                     verdict, "양쪽 모두")
        print(f"  ✗ {row['a_name'][:16]} ↔ {row['b_name'][:16]}  "
              f"근거없음: {arrow}")
        print(f"      {v.get('reason','')[:80]}")
        flagged += len(drop)
        if not dry_run:
            # ★예전 전문 판정을 **같이 지운다**(2026-08-03). 안 지웠더니
            #   「엔비디아 -SUPPLIES_TO-> 마이크론」이 `suspect=true` +
            #   `verdict=confirmed`인 모순 상태로 남았다. 전문 검증은 「관계가
            #   있나」만 보므로 방향이 반대여도 confirmed가 나온다 — 방향을
            #   본 이 검사가 더 나중이자 더 정확한 판정이다.
            #   `grounding.py::_MARK`도 같은 이유로 verdict를 지운다.
            session.run(
                "MATCH ()-[r]->() WHERE elementId(r) IN $eids "
                "SET r.grounding_suspect = true, "
                "    r.grounding_reason = $why, "
                "    r.grounding_stage1 = 'unfounded', "
                "    r.grounding_verdict = NULL, r.grounding_verdict_why = NULL",
                eids=drop,
                why=f"양방향 공급 검사: {v.get('reason','')[:160]}")

    if checked and not dry_run:
        session.run(_MARK_BIDIR, eids=checked)
    print(f"  → 정상(양방향 실재) {both}쌍 · 근거없음 표시 {flagged}건 "
          f"(**삭제 없음**)")
    return flagged, both


def check_events(session, dry_run: bool, full: bool = False) -> int:
    rows = [dict(r) for r in session.run(_FIND_EVENT, full=full)]
    print(f"\n[사건성] Event {len(rows)}건 점검"
          + ("" if full else " (미검사분만 — 전수는 --full)"))
    if not rows:
        print("  점검할 Event가 없습니다.")
        return 0

    def judge(row: dict) -> tuple[dict, dict]:
        dates = [d for d in (row.get("dates") or []) if d]
        linked = [c for c in (row.get("linked") or []) if c]
        user = (f"Event 이름: {row['name']}\n"
                f"기록된 발생일: {', '.join(dates) or '없음'}\n"
                f"연결된 기업: {', '.join(linked) or '없음'} (총 {row['deg']}개 연결)")
        return row, _ask(_EVENT_SYSTEM, user, _EVENT_SCHEMA, "eventness")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(judge, rows))

    # ★여기서 **지우지 않는다.** 걸린 것 중 상당수는 사건이 아닌 게 아니라
    #   **이름이 나쁠 뿐**이다. 실측: 「품질 미승인 외주업체」는 심텍의 실제 품질
    #   리스크인데 이름에 행위가 없어 걸렸고, 「베트남 박장 2공장」도 공장 관련
    #   사건인데 장소 이름만 남은 것이다. 지우면 그 관계를 통째로 잃는다.
    #   표시만 하고 `rename_leaked_events`가 근거를 다시 읽어 이름을 짓게 한다.
    #   거기서도 사건이라 부를 게 없으면 그때 삭제된다.
    flagged = 0
    for row, v in results:
        if v.get("is_event", True):
            continue
        print(f"  ✗ {row['name'][:46]:48} (연결 {row['deg']}) "
              f"{v.get('reason','')[:36]}")
        flagged += 1
        if not dry_run:
            session.run(_MARK_SUSPECT, eid=row["eid"],
                        why=v.get("reason", "")[:200])
    if not dry_run:
        # 판정이 끝난 것에 시점을 남긴다 — 다음 실행은 새로 들어온 것만 본다.
        kept = [r["eid"] for r, v in results if v.get("is_event", True)]
        if kept:
            session.run(_MARK_EVENTNESS, eids=kept)
    print(f"  → 사건성 의심 {flagged}건 · 유지 {len(rows)-flagged}건")
    if flagged:
        print(f"    ※ 지우지 않았습니다. `python -m batch.repair.event_names`가 "
              f"근거를 다시 읽어 이름을 짓고,\n"
              f"      사건이라 부를 게 없는 것만 삭제합니다.")
    return flagged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scope",
                    choices=["all", "direction", "supply", "symmetric",
                             "bidir", "event"],
                    default="all")
    ap.add_argument("--edge", help="특정 엣지 타입만 (예: ACQUIRES)")
    ap.add_argument("--full", action="store_true",
                    help="이미 검사한 엣지까지 전수 재검사 "
                         "(렉시콘·프롬프트를 고친 뒤에만 필요)")
    args = ap.parse_args()

    with neo4j_session() as session:
        if args.scope in ("all", "direction", "supply"):
            edges = ([args.edge] if args.edge else
                     ["SUPPLIES_TO"] if args.scope == "supply" else
                     list(DIRECTIONAL_EDGES))
            total_flip = total_unsure = 0
            for et in edges:
                f, u = check_supply(session, args.dry_run, et, full=args.full)
                total_flip += f
                total_unsure += u
            print(f"\n[방향 종합] 반전 {total_flip}건 · 판단불가 {total_unsure}건")
        if args.scope in ("all", "symmetric"):
            check_symmetric(session, args.dry_run, full=args.full)
        if args.scope in ("all", "bidir"):
            check_bidirectional(session, args.dry_run, full=args.full)
        if args.scope in ("all", "event"):
            check_events(session, args.dry_run, args.full)

    print(f"\n{'[dry-run] 실제 변경 없음' if args.dry_run else '✅ 적용 완료'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
