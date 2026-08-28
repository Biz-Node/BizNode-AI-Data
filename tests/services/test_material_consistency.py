"""⑥.5 Material Consistency — ③ 그래프 라벨과 ⑥ 근거 원문을 **답변 전에** 대조한다.

★왜 이 파일이 있나 (설계서 §10 · 현황서 §5-12·§5-14)

대조 지점이 ⑩ 하나뿐이었고 **⑩ 은 답변을 쓴 뒤**다. ③ 이 만든 라벨과 ⑥ 이
만든 근거 원문이 어긋나는지 볼 자리가 답변 **앞에** 없었다.

    답변  「SK하이닉스는 최근 HBM3E 대량 양산에 차질이 발생했다고 보도되었습니다」
    근거  「… SK하이닉스는 이미 HBM3E 12단의 양산을 세계 최초로 시작했다.」

LLM 이 지어낸 것이 아니다 — **Event 라벨에 이미 「차질」이 있었다**
(`evt_news_153cd0debaae`). 낱말 겹침은 0.60 으로 「의심」 임계 0.34 를 한참
넘어 이걸 못 잡는다(현황서 §5-12).

★**이 모듈은 flag 만 낸다** — 설계서 §10 의 ⑥.5 금지사항이 「사건·관계·근거를
  **버리지 않는다** · LLM 을 호출하지 않는다 · 새 조회를 하지 않는다」다.
  격리는 ⑦ Context Builder 의 일이다.

★**규칙은 실측으로 정했다** (2026-08-26 · 위험사건 327건 전수)

    후보 A  라벨의 부정 어휘가 근거에 없다              26/327 (8.0%)  precision ~15%
    후보 B  A 이면서 근거에 그 부정 어휘의 반의어가 있다  4/327 (1.2%)  precision 50%   ★채택

  후보 A 는 **과다 제외**가 확인됐다 — 26건 중 대부분이 동의어 오탐이었다
  (「지연」↔「늦어지고」 · 「파손」↔「깨지는」 · 「사망사고」↔「숨진채 발견」).
"""

from __future__ import annotations

from app.api.schemas import Event, Evidence
from app.services import material_consistency as mc


def _event(name, *, is_risk=True, event_id="evt_1", evidence_ids=("ev_a",),
           occurred_at="2024-10-29"):
    return Event(event_id=event_id, name=name, event_type="공급망", is_risk=is_risk,
                 role="subject", occurred_at=occurred_at,
                 evidence_ids=list(evidence_ids))


def _evidence(text, *, evidence_id="ev_a", missing=False):
    return Evidence(evidence_id=evidence_id, text=text, source_doc="doc",
                    source_type="news", missing=missing)


# ── 후보 B — 극성 반전 ───────────────────────────────────────────────────

def test_flags_the_measured_hbm3e_case():
    """★실측 사례 그 자체(현황서 §5-12) — 라벨은 「차질」인데 근거는 「시작」이다."""
    events = [_event("HBM3E 대량 양산 차질")]
    evidence = [_evidence("2025년 HBM3E 12단 중심의 재편이 유력시되는 시장 수요 "
                          "변화에 발맞춰 SK하이닉스는 이미 HBM3E 12단의 양산을 "
                          "세계 최초로 시작했다.")]

    flags = mc.check_polarity(events, evidence)

    assert "evt_1" in flags


def test_does_not_flag_when_the_label_word_is_in_the_evidence():
    """부정 어휘가 근거에도 있으면 어긋난 것이 아니다 — 327건 중 272건(83.2%)이 여기다."""
    events = [_event("이천 공장 화재")]
    evidence = [_evidence("이천 공장에서 화재가 발생해 직원들이 대피했다.")]

    assert mc.check_polarity(events, evidence) == {}


def test_does_not_flag_on_a_missing_word_alone():
    """★후보 A 를 쓰지 않는 이유 — 낱말 부재만으로는 동의어가 전부 걸린다.
    실측 26건 중 대부분이 이 부류였다(precision ~15%)."""
    events = [_event("용인시 첨단 팹 지연")]
    evidence = [_evidence("첨단 팹 조성이 늦어지고 있어 투자에 발목을 잡히는 게 "
                          "아니냐는 우려가 있다.")]

    assert mc.check_polarity(events, evidence) == {}


def test_does_not_flag_a_non_risk_event():
    """`is_risk=False` 는 애초에 부정 라벨이 아니다 — 대조 대상이 아니다."""
    events = [_event("HBM3E 대량 양산 차질", is_risk=False)]
    evidence = [_evidence("SK하이닉스는 HBM3E 12단의 양산을 세계 최초로 시작했다.")]

    assert mc.check_polarity(events, evidence) == {}


def test_only_reads_the_events_own_evidence():
    """★남의 근거로 판정하면 안 된다 — 기업별 evidence scope(설계서 §6-2)와
    같은 원칙이다. 사건에 안 달린 근거는 이 사건의 극성을 말하지 않는다."""
    events = [_event("HBM3E 대량 양산 차질", evidence_ids=["ev_own"])]
    evidence = [_evidence("양산을 세계 최초로 시작했다.", evidence_id="ev_other")]

    assert mc.check_polarity(events, evidence) == {}


def test_ignores_missing_evidence():
    """`missing=true` 는 원문을 못 찾은 것이다 — 없는 원문으로 판정하지 않는다."""
    events = [_event("HBM3E 대량 양산 차질")]
    evidence = [_evidence("양산을 세계 최초로 시작했다.", missing=True)]

    assert mc.check_polarity(events, evidence) == {}


def test_the_flag_says_why():
    """★조용히 flag 하지 않는다([규칙 2]) — 어떤 낱말이 어떤 반의어와 부딪혔나."""
    events = [_event("HBM3E 대량 양산 차질")]
    evidence = [_evidence("양산을 세계 최초로 시작했다.")]

    flag = mc.check_polarity(events, evidence)["evt_1"]

    assert "차질" in flag.label_words
    assert "시작" in flag.evidence_words


def test_check_never_mutates_the_material():
    """★⑥.5 금지사항 — 사건·관계·근거를 **버리지 않는다**(설계서 §10).
    버리면 「그 사건이 없다」로 읽힌다."""
    events = [_event("HBM3E 대량 양산 차질")]
    evidence = [_evidence("양산을 세계 최초로 시작했다.")]

    mc.check_polarity(events, evidence)

    assert len(events) == 1
    assert len(evidence) == 1


# ── 시간 맥락 대조 (§5-14) ───────────────────────────────────────────────
# ★**오류 확정이 아니라 flag 후보다.** 문서 실측(§5-14)은 층 A 37건 후보 중
#   확정 24 · 배경절 아님 13 이었다 — 규칙 자체가 확정을 낼 수 없다.

def test_flags_the_measured_nitrogen_leak_case():
    """★실측 사례 그 자체(현황서 §5-14) — 기사의 주 사건은 **2024년 손해배상
    판결**이고 2015년 질소 누출은 그것을 설명하는 **배경절**이다. 그런데
    `occurred_at` 에는 **보도일**이 들어가 있다.

    실제 답변: 「2024년 2월 16일에 질소 누출 사고가 발생하여 …」"""
    events = [_event("이천 공장 질소 누출 사고", occurred_at="2024-02-16")]
    evidence = [_evidence(
        "2015년 SK하이닉스 이천 공장에서 발생한 질소가스 누출 사고로 인해 "
        "근로자 3명이 사망한 사건과 관련, SK하이닉스가 하청업체에 손해배상 "
        "청구 소송을 제기한 지 8년 만에 약 8억 원을 배상받게 됐다.")]

    flags = mc.check_temporal(events, evidence)

    assert "evt_1" in flags


def test_temporal_flag_records_the_year_found_in_the_evidence():
    """★조용히 flag 하지 않는다 — 원문이 말한 연도와 `occurred_at` 을 둘 다 남긴다."""
    events = [_event("이천 공장 질소 누출 사고", occurred_at="2024-02-16")]
    evidence = [_evidence("2015년 이천 공장에서 발생한 질소가스 누출 사고와 관련,")]

    flag = mc.check_temporal(events, evidence)["evt_1"]

    assert 2015 in flag.evidence_years
    assert flag.occurred_year == 2024


def test_temporal_flag_records_the_background_marker_as_a_signal():
    """★배경절 표지어는 **요구 조건이 아니라 함께 남기는 신호**다 — 실측 층 A
    39건 중 표지어가 같이 있는 것은 9건뿐이다."""
    events = [_event("이천 공장 질소 누출 사고", occurred_at="2024-02-16")]
    evidence = [_evidence("2015년 발생한 질소가스 누출 사고와 관련, 8년 만에 배상받게 됐다.")]

    flag = mc.check_temporal(events, evidence)["evt_1"]

    assert flag.markers


def test_no_temporal_flag_without_a_background_marker_is_still_a_flag():
    """★표지어가 없어도 연도 불일치만으로 후보가 된다 — 둘을 **곱하지** 않는다.
    실측 층 A 39건 중 30건이 표지어 없이 연도만 어긋난 경우다."""
    events = [_event("삼성전자-브로드컴 MOU", occurred_at="2023-09-22")]
    evidence = [_evidence("브로드컴은 2020년 3월 삼성전자와 RFFE 부품 공급에 대한 "
                          "장기계약(LTA)을 체결했다.")]

    flag = mc.check_temporal(events, evidence)["evt_1"]

    assert flag.markers == ()


def test_does_not_flag_when_the_evidence_year_matches_the_report_year():
    """같은 해에 일어나고 같은 해에 보도된 사건 — 대다수가 여기다."""
    events = [_event("이천 공장 화재", occurred_at="2024-02-16")]
    evidence = [_evidence("2024년 2월 이천 공장에서 화재가 발생했다.")]

    assert mc.check_temporal(events, evidence) == {}


def test_allows_a_one_year_gap_because_year_end_events_are_reported_next_year():
    """★연말 사건이 이듬해 보도되는 것은 정상이다 — 문서 실측도 `−1` 을 허용했다."""
    events = [_event("이천 공장 화재", occurred_at="2024-02-16")]
    evidence = [_evidence("2023년 12월 이천 공장에서 화재가 발생했다.")]

    assert mc.check_temporal(events, evidence) == {}


def test_does_not_flag_when_the_evidence_has_no_absolute_year():
    """★「지난해」·「앞서」만 있고 절대 시점이 없으면 **원문으로 확정할 수 없다**
    (문서 층 B). 92건이 여기 걸리는데 판독 불가가 많아 후보로 삼지 않는다."""
    events = [_event("이천 공장 화재", occurred_at="2024-02-16")]
    evidence = [_evidence("앞서 지난해 이천 공장에서 화재가 발생한 바 있다.")]

    assert mc.check_temporal(events, evidence) == {}


def test_temporal_check_reads_only_the_events_own_evidence():
    """극성 대조와 같은 원칙 — 남의 근거로 날짜를 판정하지 않는다."""
    events = [_event("이천 공장 질소 누출 사고", occurred_at="2024-02-16",
                     evidence_ids=["ev_own"])]
    evidence = [_evidence("2015년 질소가스 누출 사고", evidence_id="ev_other")]

    assert mc.check_temporal(events, evidence) == {}


def test_temporal_check_applies_to_non_risk_events_too():
    """★극성 대조와 **범위가 다르다.** 배경절 오라벨은 위험사건에만 생기지
    않는다 — 실측 층 A 39건에 사업확장·자본거래가 섞여 있다."""
    events = [_event("삼성전자-브로드컴 MOU", occurred_at="2023-09-22", is_risk=False)]
    evidence = [_evidence("브로드컴은 2020년 3월 삼성전자와 장기계약을 체결했다.")]

    assert "evt_1" in mc.check_temporal(events, evidence)


def test_temporal_check_never_mutates_the_material():
    """★⑥.5 금지사항 — 버리지 않는다."""
    events = [_event("이천 공장 질소 누출 사고", occurred_at="2024-02-16")]
    evidence = [_evidence("2015년 질소가스 누출 사고와 관련,")]

    mc.check_temporal(events, evidence)

    assert len(events) == 1
    assert len(evidence) == 1
