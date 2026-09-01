# BizNode Contextual Agent 사용자 시나리오

## 1. 문서 목적

본 문서는 BizNode의 최종 설계를 실제 사용자의 행동 흐름으로 검증하기 위한 사용자 시나리오를 정의한다.

검증 대상:
- Workspace가 없어도 Global Knowledge 기반 Chat이 동작하는가?
- Workspace가 있을 때 실제 사용자 가치가 생기는가?
- Query Anchor와 Workspace Context가 충돌하지 않는가?
- Company 외에 Event, Person, Organization, Product 등이 Anchor가 될 수 있는가?
- Conversation이 Workspace와 독립적으로 유지되는가?
- 화면 이동과 Workspace 전환이 불필요한 Agent/Tool 실행을 발생시키지 않는가?
- Single-Agent 구조로 전체 경험을 처리할 수 있는가?

## 2. 핵심 사용자 경험

BizNode Chat은 특정 Workspace에 종속된 Agent가 아니라 플랫폼 전역에서 사용할 수 있는 하나의 연속적인 Conversation이다.

> Global Knowledge Graph를 기본 지식 기반으로 사용하고, 현재 UI Context와 Workspace Context가 있으면 이를 검색 및 Ranking에 활용한다.

```text
User
 ↓
Conversation
 ↓
Query
 ├── UI Context
 └── Workspace Context
 ↓
Query Understanding
 ↓
Query Anchor
 ↓
Global Search
 ↓
Workspace-aware Ranking
 ↓
Evidence
 ↓
Single Agent
 ↓
Answer
```

## 3. Scenario 1 — Home에서 질문하기

사용자가 Home에서 질문한다.

> 최근 반도체 업계 주요 이슈가 뭐야?

상태:

```text
UI Context = Home
Workspace Context = None
Query Anchor = None
```

처리:

```text
Query
 ↓
Intent = Semiconductor / Recent Issues
 ↓
Global Search
 ↓
Global Knowledge + News
 ↓
Ranking
 ↓
Evidence
 ↓
Single Agent
 ↓
Answer
```

Workspace가 없어도 검색을 중단하지 않는다.

**검증:** Workspace가 없는 사용자도 Global Knowledge Agent를 정상적으로 사용할 수 있어야 한다.

## 4. Scenario 2 — Workspace 생성

사용자가 관심 기업을 Workspace로 묶는다.

```text
Workspace
Title: AI 반도체 공급망
Description: HBM 및 AI GPU 공급망 관련 기업 추적

Companies:
- Samsung
- SK Hynix
- NVIDIA
```

RDB에는 `workspace`, `workspace_company`를 저장한다.

Neo4j에는 기존 Global Knowledge Graph를 유지한다.

```text
Company
Event
Person
Organization
Product
Relationships
```

Workspace 때문에 Relationship을 복제하지 않는다.

## 5. Scenario 3 — Workspace에서 Semantic Query

Workspace 화면에서:

> 최근 주요 투자 이벤트가 뭐야?

라고 질문한다.

```text
Query Anchor = None

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

처리:

```text
Query
 ↓
Intent = Investment Event
 ↓
Global Event Search
 ↓
Candidate Events
 ↓
Workspace Company와의 연결성 확인
 ↓
Workspace Relevance
 ↓
Final Ranking
```

예:

```text
Event A → NVIDIA 투자
Event B → 현대차 투자
Event C → Samsung 투자
Event D → OpenAI 투자
```

Workspace와 관련된 A/C의 Ranking을 높이되 B/D를 삭제하지 않는다.

```text
Global Relevance
+
Workspace Relevance
→
Final Ranking
```

## 6. Scenario 4 — Workspace에서 특정 Company 질문

> NVIDIA의 최근 투자 동향은 어때?

```text
Query Anchor
└── NVIDIA [Company]

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

NVIDIA가 Workspace에 있기 때문에 Anchor가 되는 것이 아니라, Query가 NVIDIA를 명시했기 때문에 Anchor가 된다.

Workspace는 별도의 Ranking Context다.

## 7. Scenario 5 — Workspace 밖의 Company 질문

현재 Workspace:

```text
Samsung
SK Hynix
NVIDIA
```

질문:

> TSMC의 최근 투자 동향은?

```text
Query Anchor = TSMC [Company]

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

TSMC가 Workspace에 없어도 Global Search를 수행한다.

TSMC와 Workspace Company 사이에 관련 Relationship이 있다면 Workspace Relevance를 Ranking Signal로 활용할 수 있다.

**Workspace는 검색 범위를 제한하지 않는다.**

## 8. Scenario 6 — Product가 Anchor인 질문

> HBM 관련해서 최근 무슨 일이 있었어?

```text
Query Anchor
└── HBM [Product]

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

Global Graph에서 HBM과 연결된 Company/Event 등을 찾고:

```text
HBM Query Relevance
+
Workspace Relevance
→
Final Ranking
```

을 적용한다.

**Anchor는 Company에 종속되지 않는다.**

## 9. Scenario 7 — Person이 Anchor인 질문

> 이재용 관련 최근 주요 이슈가 뭐야?

```text
Query Anchor
└── Person

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

Person과 연결된 Event/Company 관계를 Global Graph와 News에서 찾고 Workspace 관련성을 Ranking에 활용한다.

## 10. Scenario 8 — Company Detail에서 후속 질문

사용자가 Workspace에서 NVIDIA를 클릭한다.

```text
Company Detail
NVIDIA
```

기존 대화:

```text
User: NVIDIA의 최근 투자 동향은?
Assistant: ...
User: 그럼 공급망에는 어떤 영향을 줘?
```

Context:

```text
Conversation Context
→ 이전 NVIDIA 투자 대화

UI Context
→ Company Detail / NVIDIA

Workspace Context
→ AI 반도체 공급망
```

Query Understanding은 Conversation과 UI Context를 참고하여 "그럼"의 대상을 해석할 수 있다.

## 11. Scenario 9 — Workspace 전환

사용자가:

```text
AI 반도체
 ↓
자동차
 ↓
배터리
 ↓
AI 반도체
```

로 이동한다.

Workspace 전환 자체에서는:

```text
Agent 호출 X
LLM 호출 X
Neo4j traversal X
Search X
Tool 호출 X
```

실제 질문을 보낼 때만 현재 `workspace_id`를 Context로 사용한다.

따라서 Workspace를 여러 개 자주 이동하는 것 자체가 Agent latency를 발생시키지 않는다.

## 12. Scenario 10 — Workspace 전환 후 Conversation 유지

Workspace A에서:

> NVIDIA의 최근 투자 동향은?

이라고 질문한 뒤 Workspace B로 이동한다.

그리고:

> 아까 말한 투자와 비교하면 어떤 차이가 있어?

라고 질문한다.

Conversation은 유지된다.

```text
Conversation
├── NVIDIA 투자 질문
├── NVIDIA 투자 답변
└── 후속 질문
```

현재 Workspace Context만 B로 변경된다.

```text
Conversation Context
+
Current Workspace Context
+
Global Knowledge
```

을 사용한다.

**Conversation ≠ Workspace**

## 13. Scenario 11 — Query와 Workspace가 충돌

Workspace:

```text
Samsung
SK Hynix
NVIDIA
```

질문:

> 현대차의 최근 리스크는 뭐야?

```text
Query Anchor
└── Hyundai Motor [Company]

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

현대차가 Workspace에 없어도 검색한다.

Workspace Company를 Query Anchor로 대체하지 않는다.

**Workspace Context는 Query의 의미를 덮어쓰지 않는다.**

## 14. Scenario 12 — Anchor Resolution 실패

질문:

> XYZ라는 기업의 최근 리스크는?

Global Graph에서 XYZ를 해석하지 못한다.

```text
Query Anchor = UNRESOLVED
```

Workspace Company를 대신 사용하지 않는다.

잘못된 동작:

```text
XYZ → Samsung으로 추정
```

올바른 동작:

```text
XYZ
 ↓
Resolution 실패
 ↓
해당 Entity를 찾을 수 없다는 응답
```

## 15. Scenario 13 — Workspace와 무관한 질문

Workspace:

```text
Samsung
SK Hynix
NVIDIA
```

질문:

> 이번 주 원달러 환율은 어떻게 됐어?

Workspace와 무관하더라도 질문을 반도체로 억지로 해석하지 않는다.

```text
Query
 ↓
Market Data / Global Knowledge
 ↓
USD/KRW
```

Workspace Relevance가 낮거나 0이어도 Global Relevance가 높으면 정상적으로 결과를 반환한다.

## 16. Scenario 14 — 복합 질문

> 우리 반도체 Workspace에서 최근 NVIDIA 투자와 관련된 공급망 리스크를 정리해줘.

Context:

```text
Query Anchor
└── NVIDIA [Company]

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA

Intent
├── Investment
├── Supply Chain
└── Risk
```

처리:

```text
Query Understanding
 ↓
Anchor = NVIDIA
 ↓
Global Search
 ├── NVIDIA Investment
 ├── Supply Chain Relationships
 ├── Related Events
 └── Relevant News
 ↓
Workspace Context
 ↓
Contextual Ranking
 ↓
Evidence Validation
 ↓
Single Agent
 ↓
Answer
```

Single Agent는 하나의 Request State에서 Query, Anchor, Workspace Context, 검색 결과와 Evidence를 사용할 수 있다.

## 17. 전체 사용자 Journey

```text
① Home
   │
   │ "최근 반도체 이슈 뭐야?"
   ▼
Global Chat
   │
   ▼
② Workspace 생성
   │
   │ Samsung / SK Hynix / NVIDIA
   ▼
③ Workspace 화면
   │
   │ "최근 투자 이벤트는?"
   ▼
Global Search
   +
Workspace Ranking
   │
   ▼
④ NVIDIA 클릭
   │
   ▼
Company Detail
   │
   │ "그럼 공급망에는?"
   ▼
Conversation
+
UI Context
+
Workspace Context
   │
   ▼
⑤ 다른 Workspace 이동
   │
   ▼
Workspace B
   │
   │ "이전 투자와 비교하면?"
   ▼
Conversation
+
Workspace B Context
+
Global Knowledge
```

## 18. 최종 Request Context

사용자 요청 하나가 들어오면 개념적으로 다음 Context를 갖는다.

```text
Request Context
│
├── Conversation Context
│     └── 이전 대화 맥락
│
├── UI Context
│     ├── 현재 페이지
│     ├── 현재 Company
│     └── 현재 Workspace
│
├── Workspace Context
│     ├── workspace_id
│     └── company_keys
│
└── Query Context
      ├── intent
      └── anchors
```

책임:

```text
Conversation Context
→ Query Understanding / Answer

UI Context
→ Query Understanding

Workspace Context
→ Search / Ranking

Query Anchor
→ Retrieval / Tool Selection

Evidence
→ Answer Grounding

Single Agent
→ 필요한 Tool 선택 및 실행
```

## 19. 시나리오 기반 설계 검증 질문

다음 질문에 모두 Yes라고 답할 수 있어야 한다.

1. Workspace가 없어도 Global Search가 가능한가?
2. Workspace가 있어도 Workspace 밖의 Entity를 질문할 수 있는가?
3. Workspace Company가 Query Anchor를 임의로 대체하지 않는가?
4. Anchor가 Company 이외의 Entity로 확장 가능한가?
5. Anchor가 없는 Semantic Query도 정상 처리되는가?
6. Workspace가 검색 결과를 Hard Filter하지 않는가?
7. Workspace가 Ranking Signal로 활용되는가?
8. Conversation이 Workspace 전환에도 유지되는가?
9. Workspace 전환 자체가 Agent/Tool/LLM 호출을 발생시키지 않는가?
10. Workspace Context를 요청당 한 번 resolve하고 내부 컴포넌트가 공유할 수 있는가?
11. UI Context와 Workspace Context를 구분할 수 있는가?
12. Query Anchor와 UI Context가 충돌할 때 Query 의미를 보존하는가?
13. Anchor Resolution 실패 시 Workspace Entity로 임의 대체하지 않는가?
14. 현재 요구사항을 Single-Agent + Tool 구조로 처리할 수 있는가?
15. 실제로 여러 Agent가 필요한 독립적인 지능적 책임이 존재하는가?

15번의 답이 아직 No라면 기본 아키텍처는 Single-Agent로 유지하고, 실제 평가에서 분리 필요성이 확인될 때 Multi-Agent로 확장한다.
