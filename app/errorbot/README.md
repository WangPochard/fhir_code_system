# ErrorBot

將 FHIR Validator 產出的 `OperationOutcome` 驗證結果，翻譯成醫療人員能理解的繁體中文說明，並自動區分**根因錯誤**與**連帶錯誤**。

---

## API

```
POST /api/v1/errorbot/explain
```

| 參數 | 類型 | 預設 | 說明 |
|---|---|---|---|
| `include_warning` | query bool | `false` | 是否包含 warning（預設只回傳 error） |
| `include_cascading` | query bool | `true` | 是否顯示連帶錯誤（設 false 只回傳根因） |

Body 直接傳入 `OperationOutcome` JSON。

---

## LLM 後端設定

透過 `.env` 的 `LLM_BACKEND` 切換，無需改程式碼。

### 使用 Ollama（預設）

```env
LLM_BACKEND=ollama
LLM_IP=172.16.2.113
LLM_PORT=11434
LLM_MODEL=gpt-oss:20b

# vLLM 留空即可
VLLM_BASE_URL=
VLLM_MODEL=
VLLM_TEMPERATURE=0.1
```

### 使用 vLLM

```env
LLM_BACKEND=vllm

# VLLM_BASE_URL 需填完整的 endpoint URL（含路徑）
VLLM_BASE_URL=http://10.13.64.32:8006/v1/chat/completions
VLLM_TEMPERATURE=0.1
VLLM_CONNECT_TIMEOUT=5
VLLM_READ_TIMEOUT=180

# vLLM 的 model 名稱需加 /models/ 前綴
# 格式：/models/{model-folder-name}
VLLM_MODEL=/models/medical-reasoning-gpt-oss-20b-merged

# LLM_IP / LLM_PORT / LLM_MODEL 在 vllm 模式下不使用，可留著不動
LLM_IP=172.16.2.113
LLM_PORT=11434
LLM_MODEL=gpt-oss:20b
```

> **vLLM model 名稱說明**
> vLLM serve 啟動時會用資料夾名稱作為 model ID，前面固定加 `/models/`。
> 可用以下指令確認目前可用的 model 名稱：
> ```bash
> curl http://10.13.64.32:8006/v1/models | python3 -m json.tool
> ```

---

## 修改後重啟服務

```bash
sudo systemctl restart code_rag_system.service
sudo systemctl status code_rag_system.service
```
