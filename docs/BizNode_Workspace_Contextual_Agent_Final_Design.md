# BizNode Workspace & Contextual Agent 최종 설계

## 1. 문서 목적

본 문서는 BizNode의 Workspace, Global Knowledge Graph, Query Anchor, Search, Ranking, Conversation 및 Agent의 최종적인 역할과 관계를 정의한다.

핵심 목표는 다음과 같다.

- Global Knowledge Graph를 플랫폼 전체의 공통 지식 기반으로 유지한다.
- Workspace를 별도의 지식 그래프나 검색 범위가 아닌 User Context로 정의한다.
- Query의 Anchor를 Company에 한정하지 않고 Global Graph의 Entity로 확장한다.
- Workspace의 Company membership을 검색 결과의 Contextual Ranking Signal로 활용한다.
- Workspace가 없어도 Global Search와 Agent가 정상적으로 동작하도록 한다.
- Conversation은 Workspace와 독립적으로 유지하여 플랫폼 전역에서 하나의 연속적인 채팅 경험을 제공한다.
- Workspace 전환이나 Context 변경 때문에 불필요한 Agent/Tool/Graph 호출이 발생하지 않도록 한다.

---

## 2. 핵심 설계 정의

### 2.1 Workspace

> Workspace는 사용자가 선택한 Company를 중심으로 Global Knowledge Graph에 존재하는 Entity와 Relationship을 사용자 관점에서 해석하기 위한 User Context이다.

Workspace는 Relationship을 직접 저장하거나 Neo4j의 관계를 복제하지 않는다.

구조적으로:

```text
Workspace
= User-defined Metadata
+ Company Membership
+ Derived Graph Context
```

여기서 Derived Graph Context는 영속적으로 별도 저장하는 Graph가 아니라, 필요할 때 Global Graph에서 파생되는 Context이다.

---

### 2.2 Global Knowledge Graph

Neo4j는 플랫폼 전체의 Global Knowledge Graph를 담당한다.

예:

```text
Company
Event
Person
Organization
Product
...
```

Entity 간 실제 Relationship도 Neo4j에 존재한다.

```text
Samsung ── COMPETES_WITH ── SK Hynix
Samsung ── SUPPLIES_TO ──── NVIDIA
Samsung ── HAS_EVENT ────── Event A
Event A ── INVOLVES ─────── Person B
```

Workspace를 위해 동일한 관계를 별도로 복제하지 않는다.

---

### 2.3 Query Anchor

Anchor는 Company가 아니라 다음과 같이 정의한다.

> Anchor는 사용자의 Query가 명시하거나 해석 과정에서 주요 대상으로 판단된 Global Graph Entity이다.

따라서 Anchor는 다음 Entity가 될 수 있다.

```text
Anchor
├── Company
├── Event
├── Person
├── Organization
└── Product
```

예:

```text
"삼성전자의 최근 리스크는?"
→ Anchor = Samsung [Company]

"이재용 관련 최근 소식은?"
→ Anchor = Person

"HBM 관련 최근 이슈는?"
→ Anchor = HBM [Product]

"최근 주요 투자 이벤트는?"
→ Anchor = 없음
```

Anchor가 없는 Query도 정상적인 Query이다.

---

### 2.4 Workspace Context와 Anchor의 분리

Workspace Company와 Query Anchor는 서로 다른 개념이다.

```text
Query
│
├── Query Anchor
│     └── 사용자가 질문에서 지정한 주요 대상
│
└── Workspace Context
      └── 사용자가 현재 관심 영역으로 지정한 Company 집합
```

예:

Workspace:

```text
Semiconductor
├── Samsung
├── SK Hynix
└── NVIDIA
```

Query:

```text
"TSMC의 최근 투자 동향은?"
```

이면:

```text
Query Anchor
└── TSMC

Workspace Context
├── Samsung
├── SK Hynix
└── NVIDIA
```

TSMC를 Workspace Company로 대체하지 않는다.

또한 Query에서 명시한 Entity를 찾지 못했다고 Workspace Company를 대신 Anchor로 사용하지 않는다.

---

## 3. 데이터 책임 분리

### 3.1 RDB

현재 Workspace 관련 RDB 구조를 활용한다.

```text
Workspace
├── id
├── user_id
├── title
└── description

WorkspaceCompany
├── workspace_id
└── company_id
```

RDB의 책임:

- User ↔ Workspace ownership
- Workspace metadata
- Workspace ↔ Company membership

기본적으로 저장하지 않는 것:

```text
Workspace-specific Relationship
Workspace-specific Event
Workspace-specific Person
Workspace-specific Product
```

---

### 3.2 Neo4j

Neo4j의 책임:

- Global Entity
- Global Relationship
- Global Event
- Global Knowledge Graph

Workspace별 Subgraph를 materialize하지 않는다.

```text
Workspace A Graph
Workspace B Graph
Workspace C Graph
```

같은 중복 Graph를 만들지 않는다.

---

## 4. Workspace Graph Context

Workspace가 다음 Company를 가진다고 가정한다.

```text
Workspace A
├── Samsung
├── SK Hynix
└── NVIDIA
```

이 Company 집합을 Global Graph의 Anchor Set으로 활용하여 관련 Entity와 Relationship을 해석할 수 있다.

```text
Workspace A
       │
       ▼
Company Membership
       │
       ▼
Global Neo4j Graph
       │
       ▼
Derived Graph Context
       │
       ├── Company ↔ Company Relationships
       ├── Company ↔ Event
       ├── Company ↔ Person
       ├── Company ↔ Product
       └── Other connected Entities
```

핵심은 새로운 관계를 생성하거나 저장하는 것이 아니라 기존 Global Graph를 Workspace 관점에서 해석하는 것이다.

---

## 5. Workspace의 제품적 역할

Workspace는 단순 Watchlist가 아니다.

단순 Watchlist:

```text
Workspace
├── Samsung
├── SK Hynix
└── NVIDIA
```

Workspace Context:

```text
Workspace
        │
        ▼
Company Membership
        │
        ▼
Global Graph
        │
        ▼
Relevant Relationships / Events / Entities
        │
        ▼
Workspace Context
```

따라서 Workspace는 다음과 같이 정의할 수 있다.

> 사용자가 선택한 Company들을 중심으로 Global Knowledge Graph에서 의미 있는 정보의 우선순위를 결정하는 관심 영역이다.

---

## 6. Workspace와 Search의 관계

### 6.1 Workspace는 검색 범위가 아니다

최종 정책:

```text
Workspace 있음
→ Global Search
→ Workspace Context 적용
→ Contextual Ranking

Workspace 없음
→ Global Search
→ Global Ranking
```

Workspace가 없다는 이유로 검색을 차단하지 않는다.

기존의:

```text
guard_workspace
= Workspace가 없으면 검색하지 않음
```

정책은 폐기하거나 의미를 재정의한다.

새로운 의미에서는 Workspace가 Search Gate가 아니다.

---

### 6.2 Workspace는 Ranking Signal이다

최종 Ranking 개념:

```text
Global Relevance
        +
Workspace Context Relevance
        ↓
Final Ranking
```

Workspace 밖의 결과를 삭제하지 않는다.

예:

```text
Query:
"최근 주요 투자 이벤트가 뭐야?"

Workspace:
Samsung
SK Hynix
NVIDIA
```

Global Search 결과가:

```text
Event A → NVIDIA 투자
Event B → 현대차 투자
Event C → Samsung 투자
Event D → OpenAI 투자
```

라면:

```text
Event A → 높은 Workspace Relevance
Event B → 낮은 Workspace Relevance
Event C → 높은 Workspace Relevance
Event D → 낮은 Workspace Relevance
```

가 될 수 있다.

Event B/D를 제거하는 것이 아니라 Ranking에서 Workspace Context를 반영한다.

---

## 7. Query 처리 구조

전체적인 처리 구조는 다음과 같다.

```text
User
 │
 ▼
Query
 │
 ▼
Query Understanding
 │
 ├───────────────┐
 ▼               ▼
Query Anchor   Workspace Context
 │               │
 │               └── Company Membership
 │
 └───────────────┬───────────────┘
                 ▼
         Global Knowledge Graph
                 │
                 ▼
              Search
                 │
                 ▼
        Candidate Materials
                 │
                 ▼
       Contextual Ranking
                 │
                 ▼
              Evidence
                 │
                 ▼
               Agent
                 │
                 ▼
                LLM
                 │
                 ▼
              Answer
```

---

## 8. Semantic Query 처리

Company가 Query에 직접 등장하지 않는 경우에도 Workspace Context를 사용할 수 있다.

예:

```text
"최근 주요 투자 이벤트가 뭐야?"
```

Anchor:

```text
없음
```

Workspace:

```text
Samsung
SK Hynix
NVIDIA
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
Event ↔ Company 연결 확인
 ↓
Workspace Company Membership과 비교
 ↓
Workspace Context Relevance
 ↓
Final Ranking
```

따라서 Workspace는 Company Anchor가 없는 Semantic Query에서도 의미가 있다.

---

## 9. Conversation과 Workspace의 관계

플랫폼의 Chatbot Agent는 특정 Workspace에 종속된 별도의 채팅방이 아니라, 플랫폼 전역에서 사용할 수 있는 하나의 연속적인 Conversation으로 정의한다.

```text
Conversation
├── Message 1
├── Message 2
├── Message 3
└── ...
```

Conversation은 Workspace와 독립적으로 유지한다.

현재 UI Context는 별도로 전달한다.

```text
Current Context
├── Home
├── Company Detail
└── Workspace
      └── workspace_id
```

따라서 사용자가:

```text
Workspace A
→ Workspace B
→ Company Detail
→ Home
```

으로 이동해도 Conversation 자체는 유지된다.

---

## 10. Frontend ↔ Backend Context 전달

Frontend는 모든 UI 상태를 계속 Backend에 전달하지 않는다.

Chat 요청 시 필요한 최소 Context만 전달한다.

예:

```json
{
  "conversation_id": "...",
  "message": "최근 투자 동향이 어때?",
  "workspace_id": 123
}
```

Workspace를 보고 있지 않은 경우:

```json
{
  "conversation_id": "...",
  "message": "최근 반도체 업계 동향이 어때?"
}
```

처럼 Workspace Context를 생략할 수 있다.

핵심은 Workspace 전환 자체가 Agent 실행을 발생시키지 않는 것이다.

```text
Workspace A → Workspace B
        ↓
Frontend current_workspace_id 변경
        ↓
Agent 실행 없음
Tool 호출 없음
Graph traversal 없음
```

실제 Chat 요청이 들어올 때만 Context를 사용한다.

---

## 11. Request Context 설계

Workspace Context는 Agent Tool이 아니라 Request Context로 전달하는 것을 원칙으로 한다.

권장 구조:

```text
Chat Request
├── conversation_id
├── message
└── workspace_id?
          │
          ▼
Workspace Context Resolution
          │
          ▼
workspace_company
          │
          ▼
Company Keys
          │
          ▼
Request Context
```

이후 Search와 Agent가 동일한 Context를 공유한다.

잘못된 방식:

```text
Agent
 ↓
get_workspace()
 ↓
get_workspace_companies()
 ↓
Search
```

권장 방식:

```text
Request
 ↓
Workspace Context Resolution (1회)
 ↓
Shared Request Context
 ├── Search
 ├── Ranker
 └── Agent
```

Workspace 조회를 여러 Tool이 반복하지 않는다.

---

## 12. Workspace Context와 성능

사용자가 Workspace를 여러 개 가지고 있더라도 모든 Workspace를 매 요청마다 처리하지 않는다.

```text
Query
 ↓
Current workspace_id
 ↓
해당 Workspace의 Company IDs
 ↓
필요한 Search / Ranking에만 사용
```

Workspace 전환 횟수 자체는 Agent 실행 비용이 아니다.

실제 비용은 다음과 같은 요청 처리에서 발생한다.

```text
Chat Request
 ↓
Search
 ↓
Graph traversal
 ↓
Reranking
 ↓
Evidence retrieval
 ↓
Agent tool calls
 ↓
LLM
```

따라서 Workspace 전환을 가볍게 유지하고, 실제 질문 시에만 Context를 적용한다.

---

## 13. Context Resolution 비용 최소화

Workspace Context는 가능한 한 요청 초기 단계에서 한 번만 resolve한다.

```text
workspace_id
    ↓
workspace_company
    ↓
company_ids
```

이 결과를 요청 State에 넣고 이후 노드가 공유한다.

개념적으로:

```text
Request Context
├── conversation_id
├── workspace_id
├── workspace_company_keys
└── query context
```

이렇게 하면 Agent Tool마다 Workspace를 다시 조회할 필요가 없다.

---

## 14. Anchor 처리 원칙

Anchor는 Global Graph Entity 기준으로 처리한다.

```text
Query
 ↓
Entity Detection / Resolution
 ↓
Anchor
 ├── Company
 ├── Event
 ├── Person
 ├── Organization
 └── Product
```

### Query에 명시적인 Anchor가 있는 경우

```text
"삼성전자의 최근 리스크"
→ Anchor = Samsung
```

검색 및 답변은 Samsung을 대상으로 한다.

### Query에 Anchor가 없는 경우

```text
"최근 주요 투자 이벤트"
→ Anchor = None
```

Global Search를 수행하고 Workspace Context가 있다면 Ranking Signal로 사용한다.

### Anchor Resolution 실패

```text
"존재하지 않는 기업 X의 리스크"
→ Anchor Resolution = unresolved
```

이 경우 Workspace Company를 대신 사용하지 않는다.

명시된 대상을 다른 Entity로 임의 대체하여 답변하지 않는다.

---

## 15. Workspace Context와 Anchor의 결합

Workspace와 Anchor는 우선순위가 다른 두 개의 Context다.

```text
                  Query
                    │
           ┌────────┴────────┐
           ▼                 ▼
     Query Anchor      Workspace Context
           │                 │
           │                 ▼
           │          Company Membership
           │                 │
           └────────┬────────┘
                    ▼
             Global Search
                    │
                    ▼
          Contextual Ranking
```

예:

```text
Workspace:
Samsung / SK Hynix / NVIDIA

Query:
"TSMC의 공급망 리스크는?"
```

결과:

```text
Anchor Relevance:
TSMC 관련 자료 우선

Workspace Relevance:
Samsung / SK Hynix / NVIDIA와 연결된 자료 추가 우선

Final Ranking:
Query Relevance + Workspace Context Relevance
```

Workspace Context가 Query Anchor를 덮어쓰지 않는다.

---

## 16. 기존 Ring 개념

기존:

```text
R0 = Workspace 내부
R1 = Workspace 한쪽
R3 = Workspace 외부
```

같은 Ring은 Workspace를 검색 경계로 보는 정책과 연결되어 있었다.

최종 설계에서는 Ring을 제품의 검색 범위 개념으로 사용하지 않는다.

기본 모델은:

```text
Global Relevance
+
Workspace Context Relevance
↓
Final Ranking
```

이다.

Ring이 내부적으로 필요한 경우 Ranking Feature 또는 설명 가능한 거리 신호로 재정의할 수 있지만, "Workspace 밖이면 검색하지 않는다"는 의미로 사용하지 않는다.

---

## 17. 기존 구현에서 재정의가 필요한 부분

현재 구현에는 이전 Workspace 정책의 흔적이 남아 있다.

### 17.1 guard_workspace

기존:

```text
Workspace가 없으면 검색 중단
```

최종:

```text
Workspace가 없어도 Global Search 수행
Workspace가 있으면 Contextual Ranking 적용
```

따라서 `guard_workspace`는 제거하거나 검색 차단 기능이 아닌 Context 처리 기능으로 재정의한다.

### 17.2 Anchor 구현

현재 구현은 Company 중심으로 되어 있는 부분이 있다.

최종 설계에서는 Anchor를 Global Graph Entity 기준으로 확장한다.

```text
Company
Event
Person
Organization
Product
```

### 17.3 Workspace를 Anchor로 사용하는 로직

기존:

```text
Query에 Anchor가 없으면
→ Workspace Company를 Anchor로 사용
```

최종:

```text
Query에 Anchor가 없음
→ Anchor = None
→ Workspace = Ranking Context
```

Workspace Company를 Query Anchor로 승격시키지 않는다.

---

## 18. Search Layer의 역할

Search Layer는 Global Knowledge에서 후보를 찾고 Workspace Context를 Ranking Signal로 활용한다.

```text
SearchRequest
├── query
├── entity / relation constraints
└── workspace context
        │
        ▼
Global Search
        │
        ▼
Candidate SearchHit
        │
        ▼
Result Ranking
        │
        ├── Global Relevance
        └── Workspace Relevance
        │
        ▼
SearchResult
```

Workspace는 Search Candidate를 무조건 제거하는 Hard Filter가 아니다.

---

## 19. 하지 않는 것

다음 구조는 기본 설계로 채택하지 않는다.

### 19.1 Workspace Relationship 복제

```text
workspace_relationship
```

같은 별도 저장 구조를 만들어 Neo4j Edge를 복제하지 않는다.

### 19.2 Workspace별 Neo4j Subgraph

```text
Workspace A Graph
Workspace B Graph
Workspace C Graph
```

같은 중복 Graph를 생성하지 않는다.

### 19.3 Workspace를 검색 Hard Filter로 사용

```text
Workspace 밖 결과 제거
```

하지 않는다.

### 19.4 Workspace Company를 무조건 Query Anchor로 사용

Query에 Anchor가 없다고 Workspace Company를 Query 대상이라고 간주하지 않는다.

### 19.5 모든 Workspace를 LLM에 주입

사용자의 모든 Workspace를 매 요청마다 LLM Context에 넣지 않는다.

### 19.6 Workspace 전환마다 Agent 실행

화면에서 Workspace를 바꾸는 것만으로 Search, Tool, Graph traversal, LLM을 실행하지 않는다.

### 19.7 Tool별 Workspace 재조회

여러 Agent Tool이 각각 Workspace를 조회하지 않는다.

---

## 20. 최종 전체 구조

```text
                              User
                                │
                                ▼
                              Query
                                │
                                ▼
                      Query Understanding
                                │
                 ┌──────────────┴──────────────┐
                 │                             │
                 ▼                             ▼
           Query Anchor                Workspace Context
                 │                             │
        ┌────────┼────────┐                    │
        │        │        │                    ▼
     Company   Event    Person          Company Membership
        │        │        │                    │
        └────────┼────────┘                    │
                 │                             │
                 └──────────────┬──────────────┘
                                ▼
                     Global Knowledge Graph
                                │
                                ▼
                           Global Search
                                │
                                ▼
                       Candidate Materials
                                │
                                ▼
                    Contextual Ranking
                                │
                     ┌──────────┴──────────┐
                     │                     │
                     ▼                     ▼
              Global Relevance     Workspace Relevance
                     │                     │
                     └──────────┬──────────┘
                                ▼
                             Evidence
                                │
                                ▼
                              Agent
                                │
                                ▼
                               LLM
                                │
                                ▼
                             Answer
```

---

## 21. 최종 설계 원칙

1. **Global Knowledge Graph는 플랫폼 전체의 지식이다.**
   Neo4j의 Company, Event, Person, Organization, Product 및 Relationship을 Global Knowledge로 유지한다.

2. **Workspace는 User Context다.**
   Workspace는 사용자가 선택한 Company들을 하나의 관심 영역으로 묶은 Context이다.

3. **Workspace는 Relationship을 소유하지 않는다.**
   Neo4j에 이미 존재하는 Relationship을 Workspace Company membership을 기준으로 해석한다.

4. **Workspace는 별도의 Graph가 아니다.**
   Workspace의 Graph Context는 Global Graph에서 필요할 때 파생한다.

5. **Anchor는 Company에 한정하지 않는다.**
   Company, Event, Person, Organization, Product 등 Global Graph Entity가 Anchor가 될 수 있다.

6. **Anchor와 Workspace Context는 분리한다.**
   Query Anchor는 사용자의 질문 대상이고, Workspace는 사용자의 관심 영역이다.

7. **Workspace는 Search Boundary가 아니다.**
   Workspace 밖의 Global Knowledge도 검색할 수 있어야 한다.

8. **Workspace는 Ranking Signal이다.**
   Global Relevance와 Workspace Context Relevance를 결합하여 최종 Ranking을 구성한다.

9. **Anchor가 없어도 검색한다.**
   Semantic Query는 Global Search를 수행하고 Workspace Context가 있다면 이를 Ranking에 활용한다.

10. **Anchor Resolution 실패 시 Workspace로 대체하지 않는다.**
    명시된 Query 대상과 다른 Entity를 임의로 선택하지 않는다.

11. **Conversation은 Workspace와 독립적이다.**
    플랫폼 전역에서 하나의 연속적인 Chat Conversation을 유지한다.

12. **Workspace 전환은 가벼운 UI Context 변경이다.**
    Workspace를 이동하는 것만으로 Agent나 Tool을 실행하지 않는다.

13. **Workspace Context는 Request Context로 전달한다.**
    Agent Tool이 Workspace를 반복 조회하지 않고 요청 초기 단계에서 한 번 resolve한 값을 공유한다.

14. **모든 Workspace를 매번 처리하지 않는다.**
    현재 요청에서 필요한 Workspace Context만 사용한다.

15. **성능 최적화는 실제 병목 이후에 도입한다.**
    Signature Cache, candidate index, traversal 최적화 등은 필요성이 확인된 이후 추가한다.

---

## 22. 한 문장 최종 정의

### Workspace

> **Workspace는 사용자가 선택한 Company를 중심으로 Global Knowledge Graph의 Entity와 Relationship을 사용자 관점에서 해석하기 위한 User Context이며, Relationship을 별도로 저장하거나 복제하지 않는다.**

### Anchor

> **Anchor는 사용자의 Query가 지정하거나 해석 과정에서 주요 대상으로 판단된 Global Graph Entity이며, Company·Event·Person·Organization·Product 등으로 확장될 수 있다.**

### Contextual Agent

> **Contextual Agent는 Global Knowledge를 기본 지식 기반으로 사용하면서 Query Anchor와 현재 Workspace Context를 함께 고려하여 검색, Ranking, Evidence Retrieval 및 답변 생성을 수행하는 플랫폼 전역 Agent이다.**

### 전체 개념

```text
Global Knowledge
      +
Query Anchor
      +
User Workspace Context
      ↓
Contextual Search / Ranking
      ↓
Evidence
      ↓
Contextual Agent
      ↓
Personalized Answer
```
