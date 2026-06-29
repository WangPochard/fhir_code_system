import json

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage, ToolMessage

from app.agent.graph import MedicalCodingAgent
from app.agent.supervisor import MedicalCodingSupervisor
from app.agent.schemas import AgentQueryRequest
from app.response import ok
from app.logger import get_logger

logger = get_logger(__name__)

graph = MedicalCodingAgent().graph
supervisor_graph = MedicalCodingSupervisor().graph
router = APIRouter(prefix="/agent", tags=["Medical Coding Agent"])

_TOOL_KEY_MAP = {
    "search_snomed_ct": "snomed",
    "search_icd10_pcs": "icd10",
    "search_loinc": "loinc",
}


@router.post(
    "/query",
    summary="醫療代碼 AI Agent 查詢（ReAct）",
    description=(
        "輸入臨床自由文字（症狀、診斷、處置、檢驗等），Agent 自動判斷需要查哪些術語系統並回傳對應代碼。\n\n"
        "**前提**：LLM 後端需支援 tool calling（function calling）。"
    ),
)
async def agent_query(req: AgentQueryRequest):
    try:
        result = await graph.ainvoke({"messages": [HumanMessage(req.query)]}, {"recursion_limit": 10})

        # 從 ToolMessage 解析結構化代碼結果
        results: dict[str, list] = {"snomed": [], "icd10": [], "loinc": []}
        for m in result["messages"]:
            if isinstance(m, ToolMessage):
                key = _TOOL_KEY_MAP.get(m.name)
                if key:
                    try:
                        results[key] = json.loads(m.content)
                    except (json.JSONDecodeError, TypeError):
                        results[key] = []

        # LLM 生成的純文字說明
        explain = result["messages"][-1].content

        return ok({"query": req.query, "results": results, "explain": explain})
    except Exception as e:
        logger.error(f"Agent query 失敗 query={req.query!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent 查詢失敗: {e}")


@router.post(
    "/supervisor/query",
    summary="醫療代碼 Supervisor Agent 查詢",
    description=(
        "輸入臨床自由文字（症狀、診斷、處置、檢驗等），Supervisor 明確決定依序呼叫哪些 worker，每個 worker 獨立回傳結果。\n\n"
        "與 /query 的差別：路由決策明確可見，適合需要追蹤推理過程的場景。"
    ),
)
async def supervisor_query(req: AgentQueryRequest):
    try:
        result = await supervisor_graph.ainvoke({
            "messages": [HumanMessage(req.query)],
            "next": "",
        })
        # 收集所有 worker 回傳的結果
        worker_messages = [
            {"worker": m.name, "result": m.content}
            for m in result["messages"]
            if hasattr(m, "name") and m.name in ("snomed", "icd10", "loinc")
        ]
        return ok({"query": req.query, "results": worker_messages})
    except Exception as e:
        logger.error(f"Supervisor query 失敗 query={req.query!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Supervisor 查詢失敗: {e}")
