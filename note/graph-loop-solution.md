# LangGraph Multi-Agent Loop 問題解法整理

## 問題背景

在 Supervisor + Worker 架構中，Supervisor（qwen2.5:7b）會在每個 worker 執行完後重新被呼叫，
決定下一步要交給哪個 worker 或回傳 FINISH。

小模型常見的 loop 成因：
- 看到 worker 回傳「未找到相關代碼」，判斷「任務未完成」，繼續呼叫 worker
- 從 message history 推斷「哪些 worker 已跑過」不夠可靠
- 沒有明確的終止條件，不知道何時該說 FINISH

---

## 方案一：平行 fan-out（放棄 LLM routing）

不使用 Supervisor 決定分流，直接三個 worker 同時執行，結果合併回傳。

```python
import asyncio

results = await asyncio.gather(
    run_snomed(query),
    run_icd10(query),
    run_loinc(query),
)
```

**優點**
- 沒有 loop 可能
- 速度最快（平行執行）

**缺點**
- 放棄 AI 分流邏輯，不管查詢內容一律跑三個
- 浪費資源（純診斷描述不需要跑 LOINC）
- 不符合 AI 多因子結構化路由的設計理念

---

## 方案二：單次結構化輸出 → 直接搜尋（放棄 graph loop）

一次 LLM call 產出結構化計畫（要查哪些系統 + 各自術語），直接執行搜尋，完全不進入 graph loop。

```python
class SearchPlan(BaseModel):
    snomed_terms: list[str]
    icd10_terms: list[str]
    loinc_terms: list[str]

plan = llm.with_structured_output(SearchPlan).invoke(query)
# 直接搜，不走 graph
```

**優點**
- 只有一次 LLM call，不可能 loop
- 架構最清晰
- 對小模型而言，結構化 JSON 輸出比多輪 tool calling 穩定

**缺點**
- 把「分類」和「NER 萃取」合成一步，對小模型要求較高
- 失去 graph 的可觀測性與彈性擴充

---

## 方案三：固定順序 Pipeline（放棄 LLM routing）

Supervisor 改為不依賴 LLM 決定，hardcode 執行順序：snomed → icd10 → loinc。

**優點**
- 絕對不 loop
- 行為完全可預期，容易 debug

**缺點**
- 失去「只查相關系統」的能力
- 不符合 AI 驅動多 agent 的設計理念

---

## 方案四：外掛 Guardrail（保留現有架構，截斷 loop）

在 graph routing 邏輯或 Supervisor node 裡加截斷判斷，當 LLM 試圖重複呼叫已完成的 worker 時強制 FINISH。

### ReAct agent — 計數 ToolMessage

```python
@staticmethod
def _route(state: MessagesState):
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return END
    tool_rounds = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
    if tool_rounds >= len(medical_tools):
        return END
    return "tools"
```

### Supervisor — 追蹤已執行 worker

```python
called = {
    m.name for m in state["messages"]
    if isinstance(m, AIMessage) and getattr(m, "name", None) in ("snomed", "icd10", "loinc")
}
if decision.next != "FINISH" and decision.next in called:
    logger.warning(f"Supervisor 試圖重複呼叫 {decision.next}，強制 FINISH")
    decision = RouteDecision(next="FINISH", reason=f"{decision.next} 已執行，結束")
```

**優點**
- 保留完整架構不動
- 實作簡單，改動最小

**缺點**
- 治標不治本：loop 仍然發生，只是被截斷
- LLM 還是跑到截斷點才停，浪費推理時間

---

## 方案五：進度顯式注入 Supervisor（推薦，現有架構下最佳解）

保留 Supervisor + Worker 架構，但在每次 Supervisor 被呼叫時，
把「已完成哪些 worker、還剩哪些」明確寫進 system prompt，
讓 LLM 不需要從 message history 自行推斷。

```python
def _supervisor_node(self, state: SupervisorState):
    called = {
        m.name for m in state["messages"]
        if isinstance(m, AIMessage) and getattr(m, "name", None) in ("snomed", "icd10", "loinc")
    }
    remaining = {"snomed", "icd10", "loinc"} - called

    progress = (
        f"\n\n【當前進度】"
        f"已完成：{', '.join(called) if called else '無'} ｜ "
        f"可用：{', '.join(remaining) if remaining else '無，請回傳 FINISH'}"
    )
    messages = [SystemMessage(content=_SUPERVISOR_PROMPT + progress)] + state["messages"]
    decision = self._llm.invoke(messages)
    ...
```

**原理**

小模型推斷 message history 不可靠，但能可靠地遵循白話指令。
當 `remaining` 為空時，system prompt 直接出現「可用：無，請回傳 FINISH」，
模型面對明確指令比推斷對話歷史穩定非常多。

**優點**
- 保留完整 AI 分流架構
- 治本：讓 Supervisor 真正「知道自己的進度」
- 方案四的 guardrail 可同時保留當保險

**缺點**
- system prompt 動態拼接，每次呼叫略有不同（可接受）

---

## 總結比較

| 方案 | 保留 AI routing | 保留 graph loop | 解決根本原因 | 改動幅度 |
|------|:-:|:-:|:-:|------|
| 一：平行 fan-out | ✗ | ✗ | ✓ | 大（重寫） |
| 二：單次結構化輸出 | △ | ✗ | ✓ | 大（重寫） |
| 三：固定 pipeline | ✗ | ✗ | ✓ | 中 |
| 四：外掛 guardrail | ✓ | ✓ | ✗ | 小 |
| 五：進度顯式注入 | ✓ | ✓ | ✓ | 小 |

**建議組合**：方案五作為主要修法 + 方案四 guardrail 保留當保險。
