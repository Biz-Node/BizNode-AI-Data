"""Search Layer 회귀 평가셋 — 케이스 정의.

`tests/search/test_example_queries.py`는 DTO가 **모양을 담을 수 있는가**만 본다
(Searcher를 실행하지 않고 사람이 손으로 값을 채운다). 이 파일은 반대로 실제
PostgreSQL·Neo4j·ChromaDB를 대상으로 `SearchOrchestrator.search()`를 **끝까지
돌려** 검색 경로가 설계대로 갈리는지 본다.

고정값과 구조 조건을 가른다(`kind`).

    kind="fixed"       결과 기업을 이름·corp_code로 못 박는다. 이름 해소가
                       답 그 자체인 케이스(NAME 분기)와, 랭킹 정책을 증명하려면
                       특정 기업이 특정 자리에 와야 하는 케이스(워크스페이스)뿐이다.
    kind="structural"  기업명을 못 박지 않는다. mode·direction·edge_type·source·
                       엔티티 타입·건수 같은 **구조 조건**만 본다. 관계 점수와
                       임베딩 유사도는 데이터가 늘면 순위가 바뀌므로, 여기서
                       특정 기업의 순위를 고정하면 데이터 갱신마다 빨간불이 뜬다.

★알려진 결함(`known_issue`)은 **고치지 않고 표시만 한다.** `expected_*`에는
  「옳은 값」을 적고 `known_issue`에 사유를 남긴다 — 테스트는
  `xfail(strict=True)`로 돌아 지금은 실패로 집계되고, 결함이 고쳐지면
  XPASS로 뒤집혀 **평가셋을 갱신하라고 알린다.**

상대 엔티티 타입의 기본 허용 집합은 `pipeline/validators/matrix.py`의
`EDGE_MATRIX`에서 끌어온다 — 적재 시점에 이미 강제된 규칙이라 검색 결과도
반드시 그 안에 든다. 그보다 좁게 볼 수 있는 케이스만 `allowed_entity_types`로
따로 적는다(예: 앵커가 Company인 `IS_EXECUTIVE_OF` 질의는 상대가 Person뿐이다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pipeline.validators.matrix import EDGE_MATRIX
from search.model.enums import Direction, EntityType, SearchMode


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    verifies: str                       # 이 케이스가 무엇을 검증하는가
    coverage: tuple[str, ...]           # 커버하는 검색 분기
    kind: str                           # "fixed" | "structural"

    expected_mode: SearchMode
    expected_anchor: Optional[str]      # AnchorExtractor.extract() 기대값
    expected_direction: Optional[Direction]
    expected_edge_types: tuple[str, ...]
    expected_sources: frozenset[str]    # 히트마다 sources가 정확히 이 집합이어야 한다

    # 요청에 실어 보내는 값
    request_edge_types: Optional[tuple[str, ...]] = None
    workspace_keys: tuple[str, ...] = ()
    top_k: int = 10

    # 결과 특성
    exact_total: Optional[int] = None
    min_hits: int = 1
    must_include: tuple[tuple[str, str], ...] = ()      # (name, entity_id) — kind="fixed" 전용
    must_contain_entity_types: tuple[EntityType, ...] = ()
    allowed_entity_types: Optional[tuple[EntityType, ...]] = None

    known_issue: Optional[str] = None

    def allowed_types(self) -> Optional[frozenset[str]]:
        """상대 엔티티로 나올 수 있는 라벨. None이면 검사하지 않는다."""
        if self.allowed_entity_types is not None:
            return frozenset(e.value for e in self.allowed_entity_types)
        if not self.expected_edge_types:
            return None
        allowed: set[str] = set()
        for edge_type in self.expected_edge_types:
            rule = EDGE_MATRIX[edge_type]
            if self.expected_direction is Direction.OUTGOING:
                allowed |= set(rule.targets)
            elif self.expected_direction is Direction.INCOMING:
                allowed |= set(rule.sources)
            else:
                allowed |= set(rule.sources) | set(rule.targets)
        return frozenset(allowed)


_PG = frozenset({"postgres"})
_NEO = frozenset({"neo4j"})
_CHROMA = frozenset({"chroma"})


CASES: tuple[EvalCase, ...] = (
    # ── NAME 분기 ────────────────────────────────────────────────────
    EvalCase(
        id="name-exact-dart",
        query="삼성전자",
        verifies="기업명만 던졌을 때 DART 1차 정확 일치로 NAME 분기에 들어가고, "
                 "ResultRanker를 건너뛰어 rrf_score가 비어 있는가",
        coverage=("mode:NAME", "anchor:DART 1차", "ranker:NAME 분기 건너뜀"),
        kind="fixed",
        expected_mode=SearchMode.NAME,
        expected_anchor="삼성전자",
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_PG,
        exact_total=1,
        must_include=(("삼성전자", "00126380"),),
    ),
    EvalCase(
        id="name-two-char-company",
        query="농심 최근 실적",
        verifies="2글자 실존 상장사가 _MIN_CANDIDATE_LEN 필터에 탈락하지 않는가 "
                 "(상수를 2에서 올리면 이 케이스가 죽는다)",
        coverage=("mode:NAME", "anchor:Kiwi 문맥 분석", "negative:2글자 기업명"),
        kind="fixed",
        expected_mode=SearchMode.NAME,
        expected_anchor="농심",
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_PG,
        exact_total=1,
        must_include=(("농심", "00108241"),),
    ),
    EvalCase(
        id="name-josa-noise-ilii",
        query="SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?",
        verifies="Kiwi가 「SK하이닉스에」에서 조사 「에」를 떼고, 조사 잔여물 "
                 "「일이」를 실존 법인(01355031)으로 오인하지 않는가 (현황서 §4-1)",
        coverage=("mode:NAME", "anchor:Kiwi 조사 분리", "negative:일반명사 오인 방지"),
        kind="fixed",
        expected_mode=SearchMode.NAME,
        expected_anchor="SK하이닉스",
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_PG,
        exact_total=1,
        must_include=(("SK하이닉스", "00164779"),),
    ),
    EvalCase(
        id="name-english-alias",
        query="NAVER",
        verifies="corp_code_master에 영문으로 등재된 법인은 영문 질의로 NAME 분기에 "
                 "들어간다 — 한글 질의(known-alias-naver)와의 비대칭을 드러내는 대조군",
        coverage=("mode:NAME", "anchor:DART 1차", "negative:한글/영문 alias"),
        kind="fixed",
        expected_mode=SearchMode.NAME,
        expected_anchor="NAVER",
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_PG,
        exact_total=1,
        must_include=(("NAVER", "00266961"),),
    ),

    # ── RELATIONSHIP 분기 · 깊은 규칙(방향 판정) ──────────────────────
    EvalCase(
        id="rel-supplies-outgoing",
        query="삼성전자가 납품하는 기업은?",
        verifies="주체 조사 「가」로 direction=OUTGOING을 잡고, 결과가 전부 "
                 "삼성전자를 source로 하는 관계인가. 겸해서 freshness가 순위에 "
                 "반영되는가(expired 배제 · stale 감점)를 본다",
        coverage=("mode:RELATIONSHIP", "direction:OUTGOING", "router:깊은 규칙",
                  "graph:anchored", "entity:Company", "ranking:freshness"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=Direction.OUTGOING,
        expected_edge_types=("SUPPLIES_TO",),
        expected_sources=_NEO,
        min_hits=5,
        must_contain_entity_types=(EntityType.COMPANY,),
    ),
    EvalCase(
        id="rel-supplies-incoming",
        query="삼성전자에 납품하는 기업은?",
        verifies="대상 조사 「에」 하나로 같은 edge_type의 방향이 뒤집히는가. "
                 "겸해서 단일 소스 RRF 값이 1/(60+rank)로 매겨지는가를 본다",
        coverage=("mode:RELATIONSHIP", "direction:INCOMING", "router:깊은 규칙",
                  "graph:anchored", "entity:Company", "ranking:RRF",
                  "negative:조사에 따른 방향 반전"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=Direction.INCOMING,
        expected_edge_types=("SUPPLIES_TO",),
        expected_sources=_NEO,
        min_hits=5,
        must_contain_entity_types=(EntityType.COMPANY,),
    ),
    EvalCase(
        id="rel-stake-outgoing",
        query="삼성전자가 투자한 기업은?",
        verifies="OWNS_STAKE_IN에서도 주체 조사가 OUTGOING을 만드는가 "
                 "(투자자 → 피투자사)",
        coverage=("mode:RELATIONSHIP", "direction:OUTGOING", "router:깊은 규칙",
                  "graph:anchored"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=Direction.OUTGOING,
        expected_edge_types=("OWNS_STAKE_IN",),
        expected_sources=_NEO,
        min_hits=5,
    ),
    EvalCase(
        id="rel-stake-incoming",
        query="삼성전자에 투자한 기업은?",
        verifies="같은 edge_type이 대상 조사에서 INCOMING으로 뒤집히는가 "
                 "(피투자사 ← 투자자). 상대가 Person일 수도 있다(EDGE_MATRIX)",
        coverage=("mode:RELATIONSHIP", "direction:INCOMING", "router:깊은 규칙",
                  "graph:anchored", "negative:조사에 따른 방향 반전"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=Direction.INCOMING,
        expected_edge_types=("OWNS_STAKE_IN",),
        expected_sources=_NEO,
        min_hits=5,
    ),
    EvalCase(
        id="rel-stake-bidirectional",
        query="삼성전자 최근 투자 기업",
        verifies="조사가 없으면 방향을 강제하지 않고(direction=None) 양방향 관계가 "
                 "모두 후보에 남는가",
        coverage=("mode:RELATIONSHIP", "direction:없음(양방향)", "router:깊은 규칙",
                  "graph:anchored"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=None,
        expected_edge_types=("OWNS_STAKE_IN",),
        expected_sources=_NEO,
        min_hits=5,
    ),
    EvalCase(
        id="rel-sues-incoming",
        query="SK하이닉스를 제소한 기업",
        verifies="목적격 조사 「를」+제소가 INCOMING(피고가 앵커)으로 읽히는가",
        coverage=("mode:RELATIONSHIP", "direction:INCOMING", "router:깊은 규칙",
                  "graph:anchored", "entity:Company", "entity:Organization"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="SK하이닉스",
        expected_direction=Direction.INCOMING,
        expected_edge_types=("SUES",),
        expected_sources=_NEO,
        min_hits=3,
        must_contain_entity_types=(EntityType.COMPANY,),
    ),

    # ── RELATIONSHIP 분기 · 얕은 키워드(방향 없음) ────────────────────
    EvalCase(
        id="rel-shallow-partners",
        query="삼성전자와 협력한 기업",
        verifies="대표 키워드 1개만 등록된 얕은 규칙은 edge_type만 잡고 direction은 "
                 "None으로 둔다 — 방향을 지어내지 않는가",
        coverage=("mode:RELATIONSHIP", "direction:없음(양방향)", "router:얕은 키워드",
                  "graph:anchored"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=None,
        expected_edge_types=("PARTNERS_WITH",),
        expected_sources=_NEO,
        min_hits=5,
    ),
    EvalCase(
        id="rel-person-executive",
        query="삼성전자 임원",
        verifies="상대 엔티티가 Person인 관계도 라벨을 지어내지 않고 그대로 싣는가. "
                 "앵커가 Company인 IS_EXECUTIVE_OF는 EDGE_MATRIX상 상대가 Person뿐이다",
        coverage=("mode:RELATIONSHIP", "router:얕은 키워드", "graph:anchored",
                  "entity:Person"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=None,
        expected_edge_types=("IS_EXECUTIVE_OF",),
        expected_sources=_NEO,
        min_hits=3,
        must_contain_entity_types=(EntityType.PERSON,),
        allowed_entity_types=(EntityType.PERSON,),
    ),
    EvalCase(
        id="rel-organization-regulates",
        query="삼성전자를 규제한 기관",
        verifies="상대 엔티티가 Organization인 관계. REGULATES의 source는 "
                 "EDGE_MATRIX상 Organization뿐이다",
        coverage=("mode:RELATIONSHIP", "router:얕은 키워드", "graph:anchored",
                  "entity:Organization"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=None,
        expected_edge_types=("REGULATES",),
        expected_sources=_NEO,
        min_hits=3,
        must_contain_entity_types=(EntityType.ORGANIZATION,),
        allowed_entity_types=(EntityType.ORGANIZATION,),
    ),

    # ── RELATIONSHIP 분기 · anchor 없음 ───────────────────────────────
    EvalCase(
        id="rel-anchorless-sues",
        query="최근 소송 관련 기업",
        verifies="기업명이 없으면 anchorless 경로로 빠져 source/target 슬롯을 따로 "
                 "채우는가. 앵커가 없으므로 관계의 direction은 지어내지 않고 None이며, "
                 "결과가 있어도 VectorSearcher를 섞지 않는다",
        coverage=("mode:RELATIONSHIP", "direction:없음(양방향)", "anchor:추출 실패",
                  "graph:anchorless"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor=None,
        expected_direction=None,
        expected_edge_types=("SUES",),
        expected_sources=_NEO,
        min_hits=2,
    ),

    # ── 요청 edge_types 우선 ──────────────────────────────────────────
    EvalCase(
        id="rel-request-edge-override",
        query="SK하이닉스에 생산 차질을 일으킬 만한 일이 있었나?",
        request_edge_types=("SUPPLIES_TO", "DEPENDS_ON"),
        verifies="QueryRouter가 아무 키워드도 못 잡는 질의라도 요청이 edge_types를 "
                 "실으면 RELATIONSHIP으로 간다 — 같은 질의가 name-josa-noise-ilii "
                 "에서는 NAME이었다(챗봇 탐색 프로파일 배선)",
        coverage=("mode:RELATIONSHIP", "direction:없음(양방향)", "graph:anchored",
                  "router:요청 edge_types 우선"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="SK하이닉스",
        expected_direction=None,
        expected_edge_types=("SUPPLIES_TO", "DEPENDS_ON"),
        expected_sources=_NEO,
        min_hits=5,
    ),

    # ── 워크스페이스 랭킹 ─────────────────────────────────────────────
    EvalCase(
        id="rank-workspace-relationship",
        query="삼성전자에 납품하는 기업은?",
        workspace_keys=("00164779",),
        verifies="워크스페이스는 필터가 아니라 랭킹 문맥이다 — 워크스페이스에 닿는 "
                 "관계가 점수를 이기고 먼저 오되, 바깥 기업이 후보에서 사라지지는 "
                 "않는가. SK하이닉스는 점수순으로는 271번째라 워크스페이스 없이는 "
                 "top-10에 못 든다(현황서 §5)",
        coverage=("mode:RELATIONSHIP", "direction:INCOMING", "graph:anchored",
                  "ranking:workspace_keys"),
        kind="fixed",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor="삼성전자",
        expected_direction=Direction.INCOMING,
        expected_edge_types=("SUPPLIES_TO",),
        expected_sources=_NEO,
        min_hits=5,
        must_include=(("SK하이닉스", "00164779"),),
    ),

    # ── SEMANTIC 분기 ────────────────────────────────────────────────
    EvalCase(
        id="sem-hbm-anchorless",
        query="HBM을 만드는 기업",
        verifies="기업명도 관계 키워드도 없으면 VectorSearcher로 빠지는가. "
                 "company 컬렉션만 보므로 결과는 전부 Company다",
        coverage=("mode:SEMANTIC", "anchor:추출 실패", "vector:company 컬렉션",
                  "ranking:RRF"),
        kind="structural",
        expected_mode=SearchMode.SEMANTIC,
        expected_anchor=None,
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_CHROMA,
        exact_total=10,
        allowed_entity_types=(EntityType.COMPANY,),
    ),
    EvalCase(
        id="sem-unknown-company",
        query="존재하지않는기업 관련 뉴스",
        verifies="없는 기업명을 실존 기업으로 잘못 해소하지 않는가 — anchor는 None, "
                 "EntityResolver도 None이어야 하고, 의미검색 결과를 이름 해소로 "
                 "둔갑시키지 않는다(mode는 SEMANTIC이지 NAME이 아니다)",
        coverage=("mode:SEMANTIC", "anchor:추출 실패", "vector:company 컬렉션",
                  "negative:존재하지 않는 기업"),
        kind="structural",
        expected_mode=SearchMode.SEMANTIC,
        expected_anchor=None,
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_CHROMA,
        exact_total=10,
        allowed_entity_types=(EntityType.COMPANY,),
    ),

    # ── 알려진 결함 (고치지 않는다 · xfail로 표시만) ───────────────────
    EvalCase(
        id="known-alias-naver",
        query="네이버",
        verifies="corp_code_master에 'NAVER'로만 있는 회사를 한글 「네이버」로 물어도 "
                 "이름 해소로 답한다. company_aliases 2차 창구가 **정식 법인명**을 "
                 "돌려주므로 EntityResolver의 fuzzy 경로가 normalize로 'naver'를 만들어 "
                 "1.000으로 붙는다 — 두 컴포넌트가 같은 창구를 쓴다(2026-08-23 해소)",
        coverage=("mode:NAME", "anchor:company_aliases fallback",
                  "negative:한글/영문 alias"),
        kind="fixed",
        expected_mode=SearchMode.NAME,
        expected_anchor="NAVER Corporation",
        expected_direction=None,
        expected_edge_types=(),
        expected_sources=_PG,
        exact_total=1,
        must_include=(("NAVER", "00266961"),),
    ),
    EvalCase(
        id="known-generic-noun-daesang",
        query="이 사건의 대상 기업은?",
        verifies="일상어 「대상」이 동명 실존 법인(00121941 대상)으로 잡히면 안 된다. "
                 "지금은 corp_code_master 1차에서 1.000 정확 일치라 앵커가 붙고, "
                 "그 결과 HAS_EVENT anchored 검색이 0건을 낸다",
        coverage=("mode:RELATIONSHIP", "anchor:추출 실패", "graph:anchorless",
                  "negative:일반명사 오인 방지"),
        kind="structural",
        expected_mode=SearchMode.RELATIONSHIP,
        expected_anchor=None,
        expected_direction=None,
        expected_edge_types=("HAS_EVENT",),
        expected_sources=_NEO,
        min_hits=0,
        known_issue="동음이의 사명은 형태소 분석으로 못 가른다(현황서 §4-5). "
                    "질의 의도를 봐야 갈린다. 이번 작업 범위 밖(수정 금지)",
    ),
)


CASES_BY_ID: dict[str, EvalCase] = {case.id: case for case in CASES}

assert len(CASES_BY_ID) == len(CASES), "EvalCase.id가 중복됐다"
