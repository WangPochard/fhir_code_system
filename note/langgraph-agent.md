# LangGraph 醫療代碼 Agent 筆記

> 記錄我把現有的 RAG 醫療代碼系統改造成 LangGraph AI Agent 的過程與設計構想。

---

## 背景：我原本有什麼

我的系統是一個 FastAPI 服務，整合了四個醫療代碼系統。每個系統都是獨立的 RAG pipeline，使用者要自己知道去打哪個 API：

| 系統 | 端點 | 怎麼搜尋 |
|---|---|---|
| SNOMED CT | `/api/ai/snomed/identify` | 文字 → embedding → pgvector |
| ICD-10-PCS | `/api/ai/icd10/suggest` | 文字 → embedding → pgvector |
| LOINC | `/api/ai/loinc/lookup` | 健保碼 → SQL |
| RxNorm | `/api/ai/rxnorm/lookup` | 呼叫外部 NLM API |

問題是：**使用者必須自己判斷要查哪個系統**，而且無法跨系統問問題。

---

## 目標：Agent 自己決定要查什麼

我想要的是：輸入一段臨床文字，Agent 自己判斷要查 SNOMED、ICD-10 還是 LOINC，然後把結果整合起來回答。

```mermaid
graph LR
    User["使用者<br/>輸入臨床文字"] --> Agent["LangGraph Agent"]
    Agent -->|自主判斷| SNOMED["SNOMED CT Tool"]
    Agent -->|自主判斷| ICD10["ICD-10-PCS Tool"]
    Agent -->|自主判斷| LOINC["LOINC Tool"]
    SNOMED --> Agent
    ICD10 --> Agent
    LOINC --> Agent
    Agent --> Answer["整合回答"]
```

---

## 架構設計

### 整體系統架構

我沒有動原本的 RAG 系統，只是在外面加了一層 Agent。

```mermaid
graph TD
    subgraph FastAPI服務
        subgraph 原有RAG系統
            SR["snomed/routers.py<br/>RAGService 實例 rag"]
            IR["icd10/routers.py<br/>RAGService 實例 rag_eng"]
            LR["loinc/routers.py<br/>直接 SQL"]
        end

        subgraph 新增 app/agent/
            T["tools.py<br/>@tool 裝飾器包裝"]
            G["graph.py<br/>StateGraph"]
            R["routers.py<br/>POST /agent/query"]
        end

        T -->|直接 import，不走 HTTP| SR
        T -->|直接 import，不走 HTTP| IR
        T -->|LOINCSession| LR
        G --> T
        R --> G
    end

    User["使用者"] --> R
```

### LangGraph StateGraph

這是 Agent 的核心，一個簡單的兩節點狀態機：

```mermaid
stateDiagram-v2
    [*] --> agent : 使用者訊息進來
    agent --> tools : LLM 決定呼叫 Tool
    agent --> [*] : LLM 直接回答（不需要 Tool）
    tools --> agent : Tool 結果塞回 messages
```

用 LangGraph 程式碼表達：

```python
graph = (
    StateGraph(MessagesState)
    .add_node("agent", _agent)          # LLM 思考節點
    .add_node("tools", ToolNode(...))   # 執行 Tool 節點
    .add_edge("__start__", "agent")
    .add_conditional_edges("agent", _route)
    .add_edge("tools", "agent")         # Tool 完成 → 回 LLM
    .compile()
)
```

---

## 一次查詢的完整流程

以「第二型糖尿病需要 SNOMED 和血糖 LOINC 代碼」為例：

```mermaid
sequenceDiagram
    participant U as 使用者
    participant R as FastAPI Router
    participant G as LangGraph Graph
    participant LLM as ChatOllama / ChatOpenAI
    participant ST as search_snomed_ct()
    participant LT as search_loinc()
    participant DB as PostgreSQL / LOINCSession

    U->>R: POST /api/ai/agent/query
    R->>G: graph.ainvoke({messages})

    G->>LLM: 第一輪：分析使用者需求
    LLM-->>G: tool_calls: [search_snomed_ct("type 2 diabetes")]

    G->>ST: 呼叫 Tool
    ST->>DB: snomed_rag.similarity_search()
    DB-->>ST: Top-3 SNOMED 概念
    ST-->>G: JSON 結果

    G->>LLM: 第二輪：帶著 SNOMED 結果繼續
    LLM-->>G: tool_calls: [search_loinc("blood glucose")]

    G->>LT: 呼叫 Tool
    LT->>DB: SQL LIKE 搜尋
    DB-->>LT: LOINC 候選清單
    LT-->>G: JSON 結果

    G->>LLM: 第三輪：整合兩份結果
    LLM-->>G: 自然語言回答（無 tool_calls）

    G-->>R: messages[-1].content
    R-->>U: ok({answer})
```

---

## Tool 的設計細節

### 為什麼 Tool 不呼叫 identify()？

原本的 `/snomed/identify` 端點內部流程是：

```
identify() = NER萃取術語 → similarity_search → LLM reranking
```

Agent 本身就是「思考要查什麼」的角色，所以 Tool 只需要做 `similarity_search()`，不需要再跑一次 NER。

```mermaid
graph LR
    subgraph 原本 identify 端點
        N["NER 萃取術語<br/>(LLM)"] --> S["similarity_search"] --> Rank["LLM Reranking"]
    end

    subgraph Agent Tool
        S2["similarity_search<br/>直接呼叫"]
    end

    style N fill:#ffcccc
    style Rank fill:#ffcccc
    style S2 fill:#ccffcc
```

紅色的部分在 Agent 架構下不需要，Agent 的 LLM 本身就在做這件事。

### LOINC Tool 的特殊處理

LOINC 沒有向量搜尋，只有 `健保碼 → LOINC` 的對照表。所以 LOINC tool 做了兩段式查詢：

```mermaid
flowchart TD
    Q["輸入 query"] --> Try["嘗試精確健保碼查詢<br/>WHERE nhi_code = query"]
    Try -->|有結果| R["回傳結果"]
    Try -->|無結果| Fuzzy["文字模糊搜尋<br/>WHERE long_common_name LIKE %query%"]
    Fuzzy -->|有結果| R
    Fuzzy -->|無結果| Empty["回傳『未找到』"]
```

---

## LangSmith 監控

打開 LangSmith 之後，每次 `/agent/query` 都會出現完整的 trace：

```mermaid
graph TD
    Trace["LangSmith Trace"] --> Round1["第1輪 LLM 呼叫<br/>input: 使用者問題<br/>output: tool_calls"]
    Trace --> Tool1["Tool 執行<br/>search_snomed_ct / search_loinc 等"]
    Trace --> Round2["第2輪 LLM 呼叫<br/>input: 使用者問題 + tool結果<br/>output: tool_calls 或最終回答"]
    Trace --> FinalLLM["最終 LLM 呼叫<br/>input: 所有 messages<br/>output: 自然語言整合回答"]
```

設定方式：在 `.env` 加入：

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_xxxxxx
LANGCHAIN_PROJECT=medical-coding-agent
```

---

## 未來可以擴充的方向

```mermaid
mindmap
  root((醫療代碼 Agent))
    現有 Tools
      SNOMED CT
      ICD-10-PCS
      LOINC
    可加入
      RxNorm Tool
        藥品查詢
        交互作用檢查
      ErrorBot Tool
        FHIR OperationOutcome 解釋
      跨系統對應
        SNOMED → ICD-10 mapping
        LOINC → 健保碼 mapping
    Agent 升級
      多輪對話記憶
        加入 checkpointer
        保留病患 context
      Human-in-the-loop
        高風險代碼要人工確認
      批次處理
        一次處理整份病歷
```

---

## 檔案位置速查

```
app/
├── agent/
│   ├── tools.py     ← @tool 定義，docstring 決定 Agent 路由邏輯
│   ├── graph.py     ← StateGraph + LLM 初始化 + LangSmith 橋接
│   └── routers.py   ← POST /api/ai/agent/query
├── snomed/routers.py  ← rag 實例（被 tools.py import）
├── icd10/routers.py   ← rag_eng 實例（被 tools.py import）
└── config.py          ← 新增 langchain_* 設定欄位
```
