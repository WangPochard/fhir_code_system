# ReAct Agent Summary 步驟 Token 爆炸問題

## 整體流程

`/api/ai/agent/query` 走的是 **ReAct Agent** 架構，每次請求的執行流程如下：

```
使用者 query
    │
    ▼
[ agent_node 第 1 次 ]
    │  LLM 讀取 query，決定要呼叫哪些工具、query 參數是什麼
    │  輸出：3 個 tool_call（帶各自的搜尋關鍵字）
    ▼
[ tools_node ]
    │  實際執行 3 個工具，查詢資料庫取得醫療代碼
    │  輸出：3 份 JSON 搜尋結果（ToolMessage）
    ▼
[ agent_node 第 2 次 ] ← summary 在這裡
    │  LLM 讀取全部工具結果，整理成繁體中文摘要
    │  輸出：最終回答（純文字）
    ▼
結束，回傳給使用者
```

---

## 各節點說明

### agent_node（`app/agent/graph.py`）

這個節點就是「呼叫 LLM」。它在整個流程中被執行兩次，用途不同：

- **第 1 次**（messages 裡還沒有工具結果）  
  使用 `_AGENT_SYSTEM_PROMPT`，讓 LLM 判斷臨床描述需要查哪些系統，產出 tool_call。

- **第 2 次**（messages 裡已有工具結果）  
  使用 `_AGENT_SUMMARY_PROMPT`，讓 LLM 把三份工具回傳的 JSON 整理成中文摘要。  
  **summary 步驟就在這裡。**

兩次的判斷邏輯在 `_agent_node` 裡：
```python
has_tool_results = any(isinstance(m, ToolMessage) for m in state["messages"])
prompt = _AGENT_SUMMARY_PROMPT if has_tool_results else _AGENT_SYSTEM_PROMPT
```

### tools_node（`app/agent/tools.py`）

執行三個工具函式，每個工具內部流程是：

1. `extract_terms(query, domain)`：用 LLM 從 query 萃取該領域的關鍵術語
2. 用萃取出的術語做向量搜尋或 SQL 查詢
3. 回傳 JSON 格式的代碼清單

所以 tools_node 裡也有 LLM call（extract_terms），LangSmith trace 裡可以看到每個工具內部都有一個 `qwen3.5:4b-mlx` span。

---

## 目前遇到的問題

### 問題現象

送出請求後，工具查詢正常完成，但最後整個 hang 住、永遠不回應，電腦 GPU 很燙。

### 問題發生在哪個節點

**agent_node 第 2 次（summary 步驟）**。

透過 LangSmith trace 確認：
- `agent` 第 1 次：15.85 秒，正常完成，輸出 3 個 tool_call
- `tools`：3 個工具都執行完，各有輸出
- `agent` 第 2 次：LLM call 進去之後不返回，服務被手動砍掉才結束

LangSmith 顯示整筆 run 為 `incomplete`，第 2 次 agent span 只有被殺前的極短 duration（0.01s），不是完成時間。

---

## 過度思考（Thinking Mode）問題的發現過程

### 什麼是 thinking mode

`qwen3.5:4b-mlx` 屬於 Qwen3 系列，預設啟用 thinking mode：回應前會先輸出 `<think>...</think>` 的內部推理過程，才給最終答案。這個推理過程沒有長度限制，context 越複雜可能越長。

### 如何發現這個問題導致 hang

summary 步驟的 context 比第 1 次大很多（多了 3 份工具 JSON 結果），模型面對大 context 進入 thinking mode 後無法停止 generate。

電腦很燙 + 請求 hang 住 = GPU 持續高負載在 generate token，但永遠不結束。這就是 **token 爆炸**：模型不斷輸出 thinking token，沒有產出最終回答。

如果是 Ollama 服務崩潰，會馬上回傳 connection error；hang 住且 GPU 燙代表服務正常，是模型本身在瘋狂生成。

### 之前的處置嘗試

**`bb2a956`｜2026-06-24 11:46｜分階段控制 thinking mode**

- routing 步驟（第 1 次 agent）加 `/no_think`，關掉推理加快速度
- summary 步驟（第 2 次 agent）改用 `/think`，期望深度推理提升整理品質
- `_agent_node` 新增 `has_tool_results` 判斷，依此切換 prompt

**結果：** 實測 summary 步驟使用 `/think` 後，回應延遲明顯拉長，品質無顯著提升。

---

**`fb0e415`｜2026-06-24 16:41｜關閉 summary thinking mode**

- `_AGENT_SUMMARY_PROMPT` 從 `/think` 改回 `/no_think`

**結果：** 回應延遲恢復正常，當時問題緩解。

---

**目前狀況（2026-06-29）：** 問題重新出現，原因尚待確認。
- 假說：Qwen3 的 `/no_think` 必須放在 **user message** 前綴才有效，放在 system message 可能被忽略，導致 thinking mode 仍然觸發。
- 尚未驗證：需在 LangSmith 的第 2 次 agent LLM span output 確認是否出現 `<think>` token。

---

## 處理方向

### 短期止血

在 `build_chat_llm()` 對 ChatOllama 加 timeout：

```python
kwargs = {"base_url": s.llm_base_url, "model": s.llm_model, "temperature": 0, "timeout": 60}
```

超過 60 秒丟 exception，讓 API 回 500，至少不讓 server 永遠 hang 死。

### 根本修法（待驗證後再動）

**假說：** `/no_think` 移到 user message 前綴才對 Qwen3 有效。

**驗證方式：**
1. 重現問題時執行 `ollama ps`，確認 GPU 確實高負載（排除 Ollama 崩潰）
2. 在 LangSmith incomplete trace 的第 2 次 agent LLM span 查看 output 有無 `<think>` token

確認後再修改並記錄結果。

### 備用方向

若 `/no_think` 修了還是過慢，考慮截斷工具輸出長度，限制每個工具 JSON 最多回傳幾筆結果，降低 summary 步驟的 context 大小。

---

## 相關檔案

| 檔案 | 說明 |
|------|------|
| `app/agent/graph.py` | `_agent_node`、`_route`、兩個 prompt |
| `app/agent/tools.py` | 三個工具函式 |
| `app/agent/extractor.py` | `extract_terms` 的 LLM call |
