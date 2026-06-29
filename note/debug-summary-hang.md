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

**tools_node 內的 extractor LLM call**（後來加 log 才確認，不是 summary 步驟）。

透過加入詳細 log 後確認的執行序：
```
[agent_node/routing] LLM call 開始
[agent_node/routing] LLM call 完成，has_tool_calls=True
[extractor/snomed] LLM call 開始
[extractor/icd10] LLM call 開始
[extractor/loinc] LLM call 開始
HTTP 200 OK ← 其中一個 extractor 開始收到 response stream
（之後無任何 log）← 該 extractor generate 不停止
```

Ollama server log 確認：prompt 處理完（115/116 tokens），進入 generate 階段後不結束。

三個 extractor 同時啟動，Ollama 一次只能跑一個，第一個拿到 response 的 extractor 進入 thinking mode，generate 無限 token。

---

## 過度思考（Thinking Mode）問題的發現過程

### 什麼是 thinking mode

`qwen3.5:4b-mlx` 屬於 Qwen3 系列，預設啟用 thinking mode：回應前會先輸出 `<think>...</think>` 的內部推理過程，才給最終答案。這個推理過程沒有長度限制，context 越複雜可能越長。

**Think 是用來「推導」的，不是用來「判斷」或「認出」的。**

- 需要 think：多步驟邏輯推理、數學演算、鑑別診斷（答案需要一步一步導出）
- 不需要 think：NER 術語抽取、格式化輸出、分類決策（模型直接從訓練記憶認出，不需要推導過程）

本專案所有 LLM call 都屬於後者：extractor 是辨識醫療術語，agent routing 是分類決策，summary 是格式化整理。全部用 `/no_think`。

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

---

**`2026-06-29 上午`｜問題重新出現，開始排查**

症狀：請求 hang 住、電腦 GPU 很燙、LangSmith 顯示 `incomplete`。

排查步驟：
1. LangSmith 看到 `incomplete` 但無法從 UI 判斷卡在哪個節點（服務殺掉後才出現第 2 個 agent node）
2. 在 `_agent_node` 和 `extract_terms` 加入 log，重跑
3. Log 顯示卡在 extractor，不是 summary 步驟
4. Ollama server log 確認：prompt 處理完（115/116 tokens）後進入 generate，generate 不結束
5. 電腦燙 + hang + GPU 高負載 → token 爆炸（thinking mode 未被關閉）

**結論：** `/no_think` 放在 system message 對 Qwen3 不穩定，模型有機率忽略，進入 thinking mode 無限 generate。

---

**`2026-06-29 下午`｜修正 `/no_think` 位置**

**原因：** Qwen3 官方指定 `/no_think` 必須放在 **user message 前綴** 才有效，放在 system message 是錯的。

**做法：**
- `extractor.py`：`_SNOMED_SYSTEM`、`_ICD10_SYSTEM`、`_LOINC_SYSTEM` 移除 `/no_think`，改在 `extract_terms()` 的 `HumanMessage(content=f"/no_think\n{clinical_text}")` 加入
- `graph.py`：`_AGENT_SYSTEM_PROMPT`、`_AGENT_SUMMARY_PROMPT` 移除 `/no_think`，改在 `_agent_node` 對第一個 `HumanMessage` 前加 `/no_think\n`

**成效：** 不再無限 hang，但 thinking mode 仍有觸發，整體耗時約 10 分 44 秒（見下方實測記錄）。

**實測 log（pid 27654）：**
```
16:54:32 - routing 完成，三個 extractor 同時啟動
16:57:12 - 第 1 個 extractor 開始收到 stream（等了 2m40s）
16:58:47 - 第 2 個 extractor 開始收到 stream（再等 1m35s）
17:02:58 - icd10 extractor 完成（共 8m26s）→ 萃取 2 個術語
17:03:10 - snomed extractor 完成 → 萃取 3 個術語
17:03:38 - loinc extractor 完成 → 萃取 3 個術語
17:03:38 - summary 開始
17:05:01 - summary 完成（1m23s）
總耗時：約 10 分 44 秒
```

**分析：** `/no_think` 在 user message 沒有完全阻止 thinking mode，模型還是進入了 thinking，每個 extractor 花了 2-8 分鐘生成 thinking token，最終還是輸出正確結果，但速度無法接受。

---

**`2026-06-29 下午`｜加入 `num_predict` 限制最大輸出 token 數**

**原因：** `/no_think` 無法可靠阻止 thinking mode；thinking mode 一旦觸發就會生成大量 token，導致回應時間過長甚至無限 hang。需要在 Ollama 層設定硬上限，強制截斷。

**做法：**
- `graph.py`：`build_chat_llm()` 新增 `num_predict` 參數（預設 1024），Ollama kwargs 加入 `num_predict`
- `extractor.py`：`_get_llm()` 改用 `build_chat_llm(json_format=True, num_predict=300)`

**參數設計理由：**
- extractor 只需輸出短 JSON 陣列（約 50-100 token），300 已足夠；萬一 thinking 塞滿 300 token 導致 JSON 解析失敗，`extract_terms()` 有 fallback 機制會改用原始文字，不會 crash
- agent routing / summary 需要更多空間（tool call + 中文摘要），給 1024

**預期成效：** extractor 每次最多生成 300 token（M2 約 6 秒），三個排隊共約 18 秒；summary 最多 1024 token，約 20 秒。總耗時預計從 10 分鐘降至 1 分鐘以內。

**成效：** 待測試

---

## 備用方向（尚未嘗試）

若速度仍不理想，考慮截斷工具輸出長度，限制每個工具 JSON 最多回傳幾筆結果，降低 summary 步驟的 context 大小。

---

## 相關檔案

| 檔案 | 說明 |
|------|------|
| `app/agent/graph.py` | `_agent_node`、`_route`、兩個 prompt |
| `app/agent/tools.py` | 三個工具函式 |
| `app/agent/extractor.py` | `extract_terms` 的 LLM call |
