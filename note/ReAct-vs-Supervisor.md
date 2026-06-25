## ReAct：LLM 看到上一個工具的結果，才決定下一步
### ReAct /api/ai/agent/query
```mermaid
sequenceDiagram
    participant Client
    participant Router as routers.py<br/>agent_query()
    participant Graph as graph.py<br/>MedicalCodingAgent
    participant AgentNode as _agent_node()
    participant LLM as ChatOllama<br/>bind_tools
    participant ToolNode as ToolNode
    participant Extractor as extractor.py<br/>extract_terms()
    participant ExtLLM as ChatOllama<br/>json_format
    participant DB as pgvector / SQL

    Client->>Router: POST /api/ai/agent/query<br/>{"query": "心肌梗塞手術後心肌酵素上升"}

    Router->>Graph: graph.ainvoke({messages: [HumanMessage]})

    Note over Graph: __start__ → agent node

    Graph->>AgentNode: state.messages = [HumanMessage("心肌梗塞...")]
    AgentNode->>LLM: [SystemMessage(SYSTEM_PROMPT), HumanMessage("心肌梗塞...")]
    LLM-->>AgentNode: AIMessage(tool_calls=[search_snomed_ct, search_icd10_pcs, search_loinc])

    Note over Graph: _route() → 有 tool_calls → 去 tools node

    Graph->>ToolNode: 執行 3 個 tool calls

    ToolNode->>Extractor: extract_terms("心肌梗塞手術後心肌酵素上升", "snomed")
    Extractor->>ExtLLM: [SystemMessage(SNOMED_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["心肌梗塞", "急性心肌損傷"]
    Extractor-->>ToolNode: ["心肌梗塞", "急性心肌損傷"]
    ToolNode->>DB: similarity_search("心肌梗塞") → snomed DB
    DB-->>ToolNode: SNOMED 結果

    ToolNode->>Extractor: extract_terms("心肌梗塞手術後心肌酵素上升", "icd10")
    Extractor->>ExtLLM: [SystemMessage(ICD10_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["冠狀動脈繞道手術"]
    Extractor-->>ToolNode: ["冠狀動脈繞道手術"]
    ToolNode->>DB: similarity_search("冠狀動脈繞道手術") → icd10 DB
    DB-->>ToolNode: ICD-10 結果

    ToolNode->>Extractor: extract_terms("心肌梗塞手術後心肌酵素上升", "loinc")
    Extractor->>ExtLLM: [SystemMessage(LOINC_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["心肌酵素", "肌鈣蛋白"]
    Extractor-->>ToolNode: ["心肌酵素", "肌鈣蛋白"]
    ToolNode->>DB: SQL WHERE long_common_name LIKE → loinc DB
    DB-->>ToolNode: LOINC 結果

    ToolNode-->>Graph: [ToolMessage(snomed), ToolMessage(icd10), ToolMessage(loinc)]

    Note over Graph: _route() → ToolMessage 數量=3 >= 3 → END

    Graph->>AgentNode: state.messages = [HumanMessage, AIMessage(tool_calls), ToolMessage×3]
    Note over AgentNode: has_tool_results=True → 用 SUMMARY_PROMPT
    AgentNode->>LLM: [SystemMessage(SUMMARY_PROMPT), HumanMessage, AIMessage, ToolMessage×3]
    LLM-->>AgentNode: AIMessage("SNOMED: 22298006...\nICD-10: ...\nLOINC: ...")

    Note over Graph: _route() → 無 tool_calls → END

    Graph-->>Router: messages[-1].content = 自然語言整合回答
    Router-->>Client: {"answer": "SNOMED CT 代碼為..."}
```
## Supervisor：Worker 全部只看原始輸入，互相不知道彼此的結果

### Supervisor /api/ai/agent/supervisor/query
```mermaid
sequenceDiagram
    participant Client
    participant Router as routers.py<br/>supervisor_query()
    participant Graph as supervisor.py<br/>MedicalCodingSupervisor
    participant SupNode as _supervisor_node()
    participant SupLLM as ChatOllama<br/>json_format + structured_output
    participant Worker as snomed/icd10/loinc worker
    participant Extractor as extractor.py<br/>extract_terms()
    participant ExtLLM as ChatOllama<br/>json_format
    participant DB as pgvector / SQL

    Client->>Router: POST /api/ai/agent/supervisor/query<br/>{"query": "心肌梗塞手術後心肌酵素上升"}

    Router->>Graph: supervisor_graph.ainvoke({messages: [HumanMessage], next: ""})

    Note over Graph: __start__ → supervisor node

    Graph->>SupNode: called={} → 還有 worker 未執行
    SupNode->>SupLLM: [SystemMessage(SUPERVISOR_PROMPT), HumanMessage("心肌梗塞...")]
    SupLLM-->>SupNode: RouteDecision(next="snomed", reason="需要臨床術語")
    SupNode-->>Graph: {next: "snomed"}

    Note over Graph: → snomed worker

    Graph->>Worker: _snomed_worker(state)
    Worker->>Worker: _get_original_query() → "心肌梗塞手術後心肌酵素上升"
    Worker->>Extractor: extract_terms("心肌梗塞...", "snomed")
    Extractor->>ExtLLM: [SystemMessage(SNOMED_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["心肌梗塞", "急性心肌損傷"]
    Worker->>DB: similarity_search → snomed DB
    DB-->>Worker: SNOMED 結果
    Worker-->>Graph: AIMessage(content=結果, name="snomed")

    Note over Graph: → supervisor node

    Graph->>SupNode: called={"snomed"} → 還有 worker 未執行
    SupNode->>SupLLM: [SystemMessage, HumanMessage, AIMessage(snomed結果)]
    SupLLM-->>SupNode: RouteDecision(next="icd10")
    SupNode-->>Graph: {next: "icd10"}

    Note over Graph: → icd10 worker

    Graph->>Worker: _icd10_worker(state)
    Worker->>Worker: _get_original_query() → "心肌梗塞手術後心肌酵素上升"
    Note over Worker: 永遠拿原始 query，不管 snomed 找到什麼
    Worker->>Extractor: extract_terms("心肌梗塞...", "icd10")
    Extractor->>ExtLLM: [SystemMessage(ICD10_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["冠狀動脈繞道手術"]
    Worker->>DB: similarity_search → icd10 DB
    DB-->>Worker: ICD-10 結果
    Worker-->>Graph: AIMessage(content=結果, name="icd10")

    Note over Graph: → supervisor node

    Graph->>SupNode: called={"snomed","icd10"} → 還有 worker 未執行
    SupNode->>SupLLM: [SystemMessage, HumanMessage, AIMessage(snomed), AIMessage(icd10)]
    SupLLM-->>SupNode: RouteDecision(next="loinc")
    SupNode-->>Graph: {next: "loinc"}

    Note over Graph: → loinc worker

    Graph->>Worker: _loinc_worker(state)
    Worker->>Worker: _get_original_query() → "心肌梗塞手術後心肌酵素上升"
    Worker->>Extractor: extract_terms("心肌梗塞...", "loinc")
    Extractor->>ExtLLM: [SystemMessage(LOINC_SYSTEM), HumanMessage("心肌梗塞...")]
    ExtLLM-->>Extractor: ["心肌酵素", "肌鈣蛋白"]
    Worker->>DB: SQL LIKE → loinc DB
    DB-->>Worker: LOINC 結果
    Worker-->>Graph: AIMessage(content=結果, name="loinc")

    Note over Graph: → supervisor node

    Graph->>SupNode: called={"snomed","icd10","loinc"} → 全部完成，跳過 LLM
    SupNode-->>Graph: {next: "FINISH"}

    Note over Graph: → __end__

    Graph-->>Router: messages 中的 name="snomed/icd10/loinc" AIMessages
    Router->>Router: 收集 worker_messages
    Router-->>Client: {"results": [{worker: "snomed", result:...}, {worker: "icd10",...}, {worker: "loinc",...}]}
```
---
### 兩個架構做的事幾乎一樣，差異只有：
1. ReAct 最後多一次 LLM call 把結果整理成自然語言，Supervisor 直接回 JSON
2. ReAct 的 LLM 理論上可以根據前一個工具的結果調整下一個查詢，但現在程式碼裡 extractor 收到的都是原始 query，所以實際上也沒有做到

#### 這個專案裡：

|名詞|是什麼|例子|
|---|---|---|
|Tool|一個 Python 函式，@tool 裝飾| search_snomed_ct()|
|Agent|有 LLM 在做決策的 node  |ReAct 的 agent node、Supervisor node|
|Worker|Supervisor 裡的執行 node，沒有 LLM，直接呼叫 tool|_snomed_worker()|

1. ReAct 只有一個 Agent（一個 LLM）加上三個 tool。不是三個 agent。
2. Supervisor 有一個 Agent（Supervisor LLM）加上三個 worker，worker 裡面呼叫 tool，worker 本身不是 agent。