"""evidence_selector — 질문 의도로 사건을 골라내는 순수 함수들.

★왜 이 파일이 있나 (2026-08-23)

Step1 에서 기업별 evidence scope 를 분리했지만, 그 뒤로도 **질문이 무엇이든
같은 재료**가 나갔다. 실측: 「SK하이닉스」와 「SK하이닉스 노조 관련 리스크
알려줘」가 바이트 단위로 동일한 82건(13,916자)을 반환했고, 「삼성전자와
SK하이닉스의 담합 소송」은 205건(34,430자)까지 갔다. 사건 수에 상한이 없었다.

설계 실험 3회로 확정한 순위 규칙:

  ① 근거 **원문**을 질문과 비교 → 실패. 「안전사고」 질의에서 실제 사고 근거
     (TMAH·인산 노출)가 82건 중 최하위(0.155)로 밀렸다. 근거 단편에 기업명이
     없어 임베딩이 빗나간다.
  ② 사건 **라벨**(name + event_type)로 비교 → 나아졌지만 여전히 기업명이 든
     라벨이 상위를 먹었다.
  ③ 질문과 라벨 **양쪽에서 앵커 기업명을 제거** → 정확해졌다.
     '안전사고' → 사고재해 5건 / '소송 상황' → 분쟁소송 5건.

규칙 매칭은 **hard filter 가 아니라 티어**다 — 규칙에 없는 표현은 임베딩이
받는다. 그리고 **다른 기업이라는 이유로는 아무것도 버리지 않는다**: 선택은
기업 scope 안에서만 일어나고, scope 를 정하는 것은 이 모듈의 일이 아니다.
"""

from __future__ import annotations

from datetime import date

from app.api.schemas import Event
from app.core import clock
from app.services import evidence_selector as sel


def _event(event_id, name, event_type, *, is_risk=False, occurred_at="2026-06-01",
           evidence_ids=("ev_x",)):
    return Event(event_id=event_id, name=name, event_type=event_type,
                 is_risk=is_risk, role="subject", occurred_at=occurred_at,
                 article_count=1, timeline=[], evidence_ids=list(evidence_ids))


# ── 의도 추출 ────────────────────────────────────────────────────────────

def test_anchor_name_is_stripped_from_the_question():
    """★기업명이 남으면 임베딩을 지배한다 — 실험 ②의 실패 원인."""
    assert sel.intent_of("SK하이닉스 노조 관련 리스크 알려줘", ["SK하이닉스"]) \
        == "노조 관련 리스크 알려줘"


def test_every_resolved_name_is_stripped():
    got = sel.intent_of("삼성전자와 SK하이닉스의 담합 소송",
                        ["삼성전자", "SK하이닉스"])
    assert "삼성전자" not in got and "SK하이닉스" not in got
    assert "담합 소송" in got


def test_question_with_only_a_company_name_has_no_intent():
    """★「SK하이닉스」만 물으면 **의도가 없다.** 원문을 그대로 쓰면 「SK하이닉스」
    와의 유사도로 줄을 세우게 되는데 그건 잡음이다 — 실측으로 「행복 도시락
    사업」이 상위에 올라왔다. 빈 문자열을 돌려주고 유사도를 아예 건너뛴다."""
    assert sel.intent_of("SK하이닉스", ["SK하이닉스"]) == ""


def test_no_intent_skips_the_embedder_entirely():
    """의도가 없으면 임베딩을 부르지 않는다 — 잡음일 뿐인데 돈과 시간을 쓴다."""
    called = []

    def fake_embed(texts):
        called.append(texts)
        return [[1.0, 0.0] for _ in texts]

    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="", embed=fake_embed, anchor_names=[])

    assert got == {} and called == []


def test_no_intent_falls_back_to_risk_then_recency():
    """의도가 없으면 위험사건·최신순이 답이다 — 임의 유사도보다 낫다."""
    events = [_event("e1", "옛 일반", "기타", is_risk=False, occurred_at="2020-01-01"),
              _event("e2", "최근 위험", "기타", is_risk=True, occurred_at="2026-08-01")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=1)
    assert [e.event_id for e in kept] == ["e2"]


def test_no_anchor_leaves_the_question_alone():
    assert sel.intent_of("반도체 업계 소송", []) == "반도체 업계 소송"


# ── 규칙 신호 ────────────────────────────────────────────────────────────

def test_rule_matches_labour_event_type():
    assert "노무" in sel.matched_event_types("노조 관련 리스크 알려줘")


def test_rule_matches_accident_event_type():
    assert "사고재해" in sel.matched_event_types("안전사고 있었어?")


def test_rule_matches_litigation_event_type():
    assert "분쟁소송" in sel.matched_event_types("소송 상황")


def test_unmatched_wording_yields_no_rule_signal():
    """★규칙이 못 잡는 표현이 있다는 걸 인정한다 — 그건 임베딩이 받는다."""
    assert sel.matched_event_types("요즘 어때?") == frozenset()


# ── 형태소 경계 (2026-09-02) ────────────────────────────────────────────
#
# ★**오탐은 누락보다 훨씬 나쁘다.** 규칙 티어는 `select()` 의 최상위 정렬 키라
#   틀린 type 이 켜지면 상한 10 을 다 먹고 정답이 통째로 밀려난다 — 임베딩이
#   그 아래에 깔려 구제하지 못한다. 실측: 「공장에서 인명 피해 난 곳 있어?」가
#   `사업확장` 을 켜서 상위 10건이 전부 증설 사건이 됐다(적중 0/10).
#
#   경계 없는 부분일치로 되돌리면 아래가 전부 깨진다.

def test_a_keyword_inside_a_longer_word_does_not_fire():
    """★「사고팔다」의 `사고` 는 사고재해가 아니다."""
    assert sel.matched_event_types("회사를 사고판 사례 있어?") == frozenset()


def test_a_keyword_followed_by_a_particle_still_fires():
    """★조사가 붙은 것은 **낱말의 끝**이다 — 여기까지 막으면 한국어를 못 읽는다."""
    assert "사고재해" in sel.matched_event_types("사고가 났다")
    assert "사고재해" in sel.matched_event_types("사고를 냈다")
    assert "자본거래" in sel.matched_event_types("보안업체 인수한 곳 있어?")


def test_the_head_of_a_compound_still_fires():
    """★**앞은 보지 않는다.** 한국어 복합명사는 뒤가 머리다.

        설비 + 투자   투자가 머리다        ← 정당한 질의
        투자 + 증권   투자는 수식일 뿐이다   ← 다른 낱말

    앞뒤를 다 막아 봤더니 이 질의가 죽어 정답이 18 → 17 로 떨어졌다(실측).
    """
    assert "사업확장" in sel.matched_event_types("최근 대규모 설비투자 사례 알려줘")


def test_a_high_precision_keyword_is_not_guarded():
    """★정밀도가 높은 낱말에는 경계를 걸지 않는다.

    한때 임금(정밀도 100%)·품질(100%)·지분(92%)까지 걸었다가 「임금협약」·
    「임금교섭」이 죽었다. 목록의 근거는 정밀도이지 낱말의 길이가 아니다.
    """
    assert "노무" in sel.matched_event_types("임금협약 잠정합의안 가결")


def test_one_clean_match_is_enough():
    """★`search` 가 아니라 `finditer` 인 이유 — 첫 매치가 막혀도 뒤를 마저 본다.

    「투자증권」의 `투자` 는 막히고 「설비투자한」의 `투자` 는 통과한다.
    """
    assert "사업확장" in sel.matched_event_types("투자증권이 설비투자한 곳")


def test_the_measured_probes_stay_shut():
    """★실측 probe(2026-09-02) — 경계 검사 전에는 **12건 전부** 오검출이었다."""
    for question in ("회사를 사고판 사례 있어?", "매출채권 회수 문제 있는 곳?",
                     "투자증권 계열사 뭐 있어?", "기술보증기금 지원받은 곳?",
                     "안전상비의약품 판매 기업?", "조사료 사업하는 기업?",
                     "주식회사 형태로 바꾼 곳?", "노사연 콘서트 후원 기업?"):
        assert sel.matched_event_types(question) == frozenset(), question

    # 나머지 둘은 **정당한 쪽만** 남아야 한다 — 통째로 끄는 것이 목적이 아니다.
    assert sel.matched_event_types("보안업체 인수한 곳 있어?") == frozenset({"자본거래"})
    assert sel.matched_event_types("연구소 신설한 회사?") == frozenset({"사업확장"})


# ── 어휘 3낱말 (2026-09-02) ──────────────────────────────────────────────
#
# 경계 수정으로 오탐은 껐지만 **누락 둘**이 남았다. 실측으로 고른 것은 세 낱말이고,
# 함께 검토했던 나머지 넷(`피해`·`추락`·`중상`·`법적`)은 **일부러 뺐다** — 아래
# 테스트들이 그 「빼기로 한 결정」까지 같이 고정한다. 다시 넣으면 깨진다.

def test_a_casualty_question_fires_the_accident_type():
    """★「인명」이 없으면 `공장` 때문에 사업확장만 켜져 적중이 0/10 이었다."""
    assert "사고재해" in sel.matched_event_types("공장에서 인명 피해 난 곳 있어?")


def test_the_compound_form_fires_too():
    """★`인명피해` 가 `인명` **앞에** 있어야 한다 — 교대는 왼쪽부터 시도한다.

    순서가 뒤집히면 붙여 쓴 「인명피해」가 `인명` 으로 걸리고, `인명` 은 뒤 경계를
    거는 낱말이라 뒤따르는 「피해」에 막혀 **통째로 꺼진다.**
    """
    assert "사고재해" in sel.matched_event_types("공장에서 인명피해 난 곳 있어?")


def test_a_rescue_product_is_not_an_accident():
    """★`인명` 을 경계 대상에 넣은 이유 — 「인명구조」는 사건이 아니다."""
    assert "사고재해" not in sel.matched_event_types("인명구조 장비 만드는 회사?")


def test_a_dispute_question_fires_the_litigation_type():
    assert "분쟁소송" in sel.matched_event_types("요즘 법적 다툼 있는 곳?")


def test_the_rejected_words_stay_out():
    """★검토했지만 **넣지 않기로** 한 넷. 근거는 전부 실측이다.

        법적  「합법적」·「불법적」에 걸린다. 이건 **앞** 충돌이라 뒤 경계로 못 막고,
              앞 경계는 복합명사를 죽여 이미 금지했다
        추락  「주가 추락」은 실적이다 — 형태소가 아니라 의미 충돌이라 못 막는다
        피해  「해킹 피해」·「유출 피해」는 정보유출이다 (사건명 4건 중 2건이 정보유출)
        중상  「중상모략」에 걸린다. 그러면서 33건 세트에 기여가 **0** 이었다

    셋(`법적`·`추락`·`피해`)이 고치려던 질의는 `인명`·`다툼` 이 이미 고친다.
    """
    assert "분쟁소송" not in sel.matched_event_types("합법적으로 처리한 곳 있어?")
    assert "분쟁소송" not in sel.matched_event_types("불법적 거래 적발된 기업?")
    assert "사고재해" not in sel.matched_event_types("주가 추락한 기업 알려줘")
    assert "사고재해" not in sel.matched_event_types("최근 해킹 피해 기업?")
    assert "사고재해" not in sel.matched_event_types("중상모략 논란 있는 곳?")


def test_words_absent_from_event_names_are_not_dead():
    """★`제소`·`출자`·`조달`·`불량` 은 **사건명에 0건**이지만 지우면 안 된다.

    이 표는 질의에도 걸린다. 사건명만 보고 「죽었다」고 판정하면 방향 하나를
    통째로 못 본 것이다 — 지워 봤더니 아래 넷이 전부 `∅` 가 되고, 그 대가로
    얻는 것은 리콜·오탐률·정방향 어디에도 없었다(실측).
    """
    assert "분쟁소송" in sel.matched_event_types("최근 제소당한 기업 알려줘")
    assert "자본거래" in sel.matched_event_types("최근 출자한 곳 있어?")
    assert "공급망" in sel.matched_event_types("부품 조달 문제 있는 곳?")
    assert "품질" in sel.matched_event_types("불량률 높은 제품 있어?")


# ── 위험 축 (event_type 과 별개) ─────────────────────────────────────────

def test_risk_wording_is_detected():
    for intent in ("최근 리스크 어때?", "위험한 일 있었어?", "악재 있나",
                   "우려되는 점", "부정적인 이슈", "무슨 문제 있어?", "논란"):
        assert sel.risk_intent(intent), intent


def test_non_risk_wording_is_not_detected():
    for intent in ("최근 실적 어때?", "안전사고", "요즘 어때?", "신제품 개발"):
        assert not sel.risk_intent(intent), intent


def test_risk_is_a_separate_axis_from_event_type():
    """★「리스크」는 `_EVENT_TYPE_KEYWORDS` 를 **건드리지 않는다.**

    거기에 넣으면 `matched_event_types()` 의 뜻이 「질문이 지목한 사건 종류」에서
    벗어나고, 같은 값을 읽는 `claim_check._intent_linked` 의 판정까지 조용히
    움직인다(`answer.py:168` · `answer_service.py:720`).
    """
    assert sel.matched_event_types("최근 리스크 어때?") == frozenset()
    assert sel.risk_intent("최근 리스크 어때?")


def test_the_two_axes_can_overlap():
    """「노조 관련 리스크」는 종류(`노무`)이면서 동시에 위험 질의다."""
    assert "노무" in sel.matched_event_types("노조 관련 리스크 알려줘")
    assert sel.risk_intent("노조 관련 리스크 알려줘")


def test_ambiguous_wording_is_left_to_the_embedding():
    """★「이슈」는 일부러 뺐다 — 「HBM 이슈」는 위험이 아니라 주제다.
    가르지 못하는 말에 티어를 켜면 위험 아닌 사건이 통째로 밀려난다."""
    assert not sel.risk_intent("HBM 이슈")


# ── 시간 축 (event_type·is_risk 와 또 별개) ──────────────────────────────

def test_recent_wording_is_detected():
    for intent in ("최근 리스크 어때?", "요즘 어때?", "요새 뭐 있나", "최신 동향",
                   "근래 소식", "올해 실적", "이번 분기"):
        assert sel.recent_intent(intent), intent


def test_non_recent_wording_is_not_detected():
    for intent in ("안전사고", "소송 상황", "노조 관련 리스크"):
        assert not sel.recent_intent(intent), intent


def test_past_pointing_wording_is_not_a_recency_signal():
    """★「작년」·「2024년」은 **필터**지 티어가 아니다 — 「최근을 우선하라」가
    아니라 「그 시점만 보라」는 뜻이고, 필터는 이 모듈의 일이 아니다."""
    assert not sel.recent_intent("작년 실적")
    assert not sel.recent_intent("2024년 사고")


def test_recent_window_is_a_year_back_and_string_comparable(monkeypatch):
    """★`occurred_at` 은 Neo4j date 가 **아니라 문자열**이다. `date()` 로 캐스팅해
    비교하면 null 이 되어 조용히 0건이 된다(현황서 §8-20 에서 실제로 밟았다)."""
    monkeypatch.setattr(clock, "today", lambda: date(2026, 8, 30))
    assert sel.recent_window() == "2025-08"
    assert "2026-07-28" >= sel.recent_window()
    assert not ("2025-07-31" >= sel.recent_window())


def test_recent_window_crosses_the_year_boundary(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 1, 15))
    assert sel.recent_window() == "2025-01"
    monkeypatch.setattr(clock, "today", lambda: date(2026, 12, 31))
    assert sel.recent_window() == "2025-12"


# ── 선택 ────────────────────────────────────────────────────────────────

def test_rule_matched_types_come_first():
    events = [_event("e1", "HBM 증산", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    kept, _ = sel.select(events, matched=frozenset({"노무"}), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_rule_is_a_boost_not_a_hard_filter():
    """★규칙에 안 걸린 사건도 자리가 남으면 살아남는다."""
    events = [_event("e1", "HBM 증산", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    kept, dropped = sel.select(events, matched=frozenset({"노무"}), sims={}, limit=2)
    assert len(kept) == 2 and dropped == []


def test_a_risk_question_lifts_risk_events_above_similarity():
    """★**이 테스트는 `risk_wanted` 가 없던 동안 빨간불이었다**(2026-08-30).

    아래 `test_risk_events_win_ties_when_no_similarity_is_available` 이 같은
    의도를 적어 두고도 못 잡았다 — 거기는 `sims={}` 라 **진짜 동점**을 만들지만,
    실제 질의에서는 유사도가 늘 채워져 위험 정렬이 **닿지 않는 죽은 키**였다.

    실측(2026-08-30 · 삼성전자 128건 · 「이 회사 최근 리스크 어때?」)에서 상위
    15건의 유사도 폭이 **0.06**(0.3680~0.3088)인데도 동점이 하나도 없어, 뽑힌
    10건 중 위험사건이 3건뿐이었다. 아래 값은 그 분포에서 가져왔다.
    """
    events = [_event("e1", "채용박람회", "기타", is_risk=False),
              _event("e2", "본사 압수수색", "규제수사", is_risk=True)]
    sims = {"e1": 0.3593, "e2": 0.2384}      # 실측 분포 — 동점이 아니다
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2,
                         risk_wanted=True)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_without_a_risk_question_nothing_changes():
    """★위험을 안 물었으면 **기본값이 그대로여야 한다** — 유사도가 이긴다."""
    events = [_event("e1", "채용박람회", "기타", is_risk=False),
              _event("e2", "본사 압수수색", "규제수사", is_risk=True)]
    sims = {"e1": 0.3593, "e2": 0.2384}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e1", "e2"]


def test_the_rule_tier_still_outranks_the_risk_tier():
    """★「안전사고 리스크」에서 사고재해가 아닌 위험사건이 사고재해를 밀어내면
    안 된다. 규칙은 「무엇을」이고 위험은 「어떤 성격을」이라 좁은 쪽이 먼저다."""
    events = [_event("e1", "인산 누출", "사고재해", is_risk=False),
              _event("e2", "본사 압수수색", "규제수사", is_risk=True)]
    kept, _ = sel.select(events, matched=frozenset({"사고재해"}), sims={}, limit=2,
                         risk_wanted=True)
    assert [e.event_id for e in kept] == ["e1", "e2"]


def test_the_risk_tier_is_a_boost_not_a_hard_filter():
    """★위험 아닌 사건도 자리가 남으면 살아남는다(`matched` 와 같은 규약)."""
    events = [_event("e1", "HBM 증산", "사업확장", is_risk=False),
              _event("e2", "본사 압수수색", "규제수사", is_risk=True)]
    kept, dropped = sel.select(events, matched=frozenset(), sims={}, limit=2,
                               risk_wanted=True)
    assert len(kept) == 2 and dropped == []


def test_near_identical_similarity_wakes_the_recency_key():
    """★**이 테스트도 `_SIM_BUCKET` 이전에는 빨간불이었다**(2026-08-30).

    유사도가 0.003 차이인데 옛 코드는 그걸 「순위가 다르다」로 읽어 최신순을
    영원히 못 보게 했다. 실측에서 인접 gap 중앙값이 **0.0034** 다 — 이웃끼리는
    사실상 붙어 있고, 그 붙은 것들 사이에서 무엇을 앞에 둘지가 진짜 질문이다.
    """
    events = [_event("e1", "옛 사건", "사업확장", occurred_at="2021-10-12"),
              _event("e2", "최근 사건", "사업확장", occurred_at="2026-07-28")]
    sims = {"e1": 0.3593, "e2": 0.3560}      # 실측 분포 — 같은 덩어리다
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_near_identical_similarity_wakes_the_risk_key():
    """★같은 이유로 `위험사건` 기본값도 깨어난다 — 질문이 위험을 안 물었어도
    다른 신호가 같으면 위험을 앞에 두는 것이 원래 의도였다."""
    events = [_event("e1", "일반", "사업확장", is_risk=False),
              _event("e2", "위험", "사업확장", is_risk=True)]
    sims = {"e1": 0.3593, "e2": 0.3560}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_a_real_similarity_gap_still_wins():
    """★**뭉개기만 하면 안 된다** — 유사도가 진짜로 갈리는 질의에서는 계속
    줄을 세워야 한다. 실측: 「노조 관련 리스크」의 상위 20건 폭이 0.191 로
    「최근 리스크」(0.072)의 2.7배다."""
    events = [_event("e1", "무관", "사업확장", occurred_at="2026-07-28"),
              _event("e2", "질문과 가까움", "사업확장", occurred_at="2021-10-12")]
    sims = {"e1": 0.20, "e2": 0.38}          # 덩어리가 다르다
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_the_recency_window_lifts_events_inside_it():
    """★유사도가 **다른 덩어리**여도 창이 이긴다 — 창은 유사도 위에 있다."""
    events = [_event("e1", "옛 사건", "사업확장", occurred_at="2021-10-12"),
              _event("e2", "최근 사건", "사업확장", occurred_at="2026-07-28")]
    sims = {"e1": 0.38, "e2": 0.20}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2,
                         recent_since="2025-08")
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_the_risk_tier_outranks_the_recency_window():
    """★「최근 리스크」가 묻는 것은 **「위험한 것 중 최근인 것」**이지 그 반대가
    아니다. 위험사건이 없으면 옛 위험사건이라도 내놓아야지, 위험하지 않은
    최근 사건을 내놓을 일이 아니다."""
    events = [_event("e1", "최근이지만 안전", "사업확장",
                     is_risk=False, occurred_at="2026-07-28"),
              _event("e2", "옛 위험사건", "규제수사",
                     is_risk=True, occurred_at="2022-01-05")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2,
                         risk_wanted=True, recent_since="2025-08")
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_the_recency_window_is_a_boost_not_a_hard_filter():
    """★창을 필터로 쓰면 사건이 뜸한 기업이 통째로 빈다."""
    events = [_event("e1", "옛 사건 하나", "사업확장", occurred_at="2021-10-12"),
              _event("e2", "옛 사건 둘", "사업확장", occurred_at="2022-03-01")]
    kept, dropped = sel.select(events, matched=frozenset(), sims={}, limit=2,
                               recent_since="2025-08")
    assert len(kept) == 2 and dropped == []


def test_an_event_without_a_date_is_treated_as_outside_the_window():
    """★날짜를 모르는 사건을 창 안으로 넣으면 **모르는 것을 아는 척**하는 것이다."""
    events = [_event("e1", "날짜 없음", "사업확장", occurred_at=None),
              _event("e2", "창 안", "사업확장", occurred_at="2026-07-28")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2,
                         recent_since="2025-08")
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_without_a_recency_question_the_window_is_not_applied():
    events = [_event("e1", "옛 사건", "사업확장", occurred_at="2021-10-12"),
              _event("e2", "최근 사건", "사업확장", occurred_at="2026-07-28")]
    sims = {"e1": 0.38, "e2": 0.20}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e1", "e2"]


def test_similarity_orders_within_a_tier():
    events = [_event("e1", "낮은 유사도", "사업확장"),
              _event("e2", "높은 유사도", "사업확장")]
    sims = {"e1": 0.10, "e2": 0.90}
    kept, _ = sel.select(events, matched=frozenset(), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_rule_tier_outranks_a_higher_similarity():
    """규칙이 **우선** 신호다 — 유사도가 높아도 티어를 못 넘는다."""
    events = [_event("e1", "무관하지만 유사도 높음", "사업확장"),
              _event("e2", "노조 설립", "노무")]
    sims = {"e1": 0.95, "e2": 0.10}
    kept, _ = sel.select(events, matched=frozenset({"노무"}), sims=sims, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_limit_caps_and_reports_what_was_dropped():
    """★조용히 자르면 「그게 전부」로 읽힌다 — 잘린 것을 돌려준다."""
    events = [_event(f"e{i}", f"사건{i}", "사업확장") for i in range(5)]
    kept, dropped = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert len(kept) == 2 and len(dropped) == 3


def test_risk_events_win_ties_when_no_similarity_is_available():
    """★임베딩이 없거나 실패했을 때의 폴백 — 위험사건이 앞에 온다."""
    events = [_event("e1", "일반", "사업확장", is_risk=False),
              _event("e2", "위험", "사업확장", is_risk=True)]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_recent_events_win_when_risk_and_similarity_tie():
    events = [_event("e1", "옛날", "사업확장", occurred_at="2020-01-01"),
              _event("e2", "최근", "사업확장", occurred_at="2026-08-01")]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e2", "e1"]


def test_selection_is_stable_for_identical_events():
    """★동점이면 **`event_id` 사전순**으로 확정한다(2026-08-28 변경).

    전에는 「입력 순서를 지킨다」였다. 그런데 입력 순서는
    `company_service.events_of()` 가 준 Neo4j 행 순서이고, 그 `ORDER BY
    coalesce(h.occurred_at, e.last_seen)` 에는 **동점 해소가 없다** — 실측에서는
    안정적이었지만 계약이 아니라 관측일 뿐이라, 언제 바뀌어도 이상하지 않은
    것에 결정성을 기대고 있었다.
    """
    events = [_event(f"e{i}", "같은 사건", "사업확장") for i in range(4)]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["e0", "e1"]


def test_full_ties_no_longer_depend_on_the_input_order():
    """★**입력 순서를 뒤집어도 같은 결과**여야 한다 — 그게 전과 달라진 점이다."""
    ids = ["e3", "e1", "e0", "e2"]
    events = [_event(i, "같은 사건", "사업확장") for i in ids]
    kept, _ = sel.select(events, matched=frozenset(), sims={}, limit=2)

    assert [e.event_id for e in kept] == ["e0", "e1"]

    reversed_input = [_event(i, "같은 사건", "사업확장") for i in reversed(ids)]
    kept2, _ = sel.select(reversed_input, matched=frozenset(), sims={}, limit=2)
    assert [e.event_id for e in kept2] == ["e0", "e1"]


def test_the_tiebreak_never_outranks_a_real_signal():
    """★**정렬 기준을 바꾼 것이 아니다.** `event_id` 는 위 네 신호가 **전부
    같을 때만** 보인다 — 유사도·위험·최신·규칙 티어를 이기면 안 된다."""
    # 사전순으로는 z9 가 뒤인데, 유사도가 높으므로 앞에 서야 한다
    events = [_event("a1", "낮은 유사도", "사업확장"),
              _event("z9", "높은 유사도", "사업확장")]
    kept, _ = sel.select(events, matched=frozenset(),
                         sims={"a1": 0.10, "z9": 0.90}, limit=2)
    assert [e.event_id for e in kept] == ["z9", "a1"]

    # 규칙 티어도 마찬가지
    events = [_event("a1", "티어 없음", "사업확장"),
              _event("z9", "티어 있음", "분쟁소송")]
    kept, _ = sel.select(events, matched=frozenset({"분쟁소송"}), sims={}, limit=2)
    assert [e.event_id for e in kept] == ["z9", "a1"]


# ── 유사도 계산 ──────────────────────────────────────────────────────────

def test_similarities_uses_the_injected_embedder():
    events = [_event("e1", "노조 설립", "노무")]
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    got = sel.similarities(events, intent="노조", embed=fake_embed, anchor_names=[])

    assert got["e1"] == 1.0
    assert calls, "임베더가 호출돼야 한다"


def test_anchor_name_is_stripped_from_event_labels_too():
    """★질문에서만 빼면 소용없다 — 라벨에서도 같은 항을 지워야 비교가 된다."""
    events = [_event("e1", "SK하이닉스 압수수색", "규제수사")]
    seen = []

    def fake_embed(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    sel.similarities(events, intent="압수수색", embed=fake_embed,
                     anchor_names=["SK하이닉스"])

    assert not any("SK하이닉스" in t for t in seen), seen


def test_similarities_returns_empty_when_the_embedder_fails():
    """★OpenAI 가 죽어도 /ask 는 죽지 않는다 — 유사도만 포기하고 규칙으로 간다."""
    def broken_embed(texts):
        raise RuntimeError("openai down")

    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="노조", embed=broken_embed, anchor_names=[])

    assert got == {}


def test_similarities_without_an_embedder_is_empty():
    got = sel.similarities([_event("e1", "노조 설립", "노무")],
                           intent="노조", embed=None, anchor_names=[])
    assert got == {}
