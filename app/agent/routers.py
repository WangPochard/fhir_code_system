from app.response import ok
from app.logger import get_logger
from app.agent.schemas import AgentQueryRequest
from app.agent.graph import MedicalCodingAgent
from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage

logger = get_logger(__name__)

graph = MedicalCodingAgent().graph
router = APIRouter(prefix="/agent", tags=["Medical Coding Agent"])

@router.post(
    "/query",
    summary="醫療代碼 AI Agent 查詢",
    description=(
        "輸入臨床自由文字，Agent 自動判斷需查詢哪些術語系統"
        "（SNOMED CT / ICD-10-PCS / LOINC），並回傳整合後的代碼建議。\n\n"
        "**前提**：LLM 後端需支援 tool calling（function calling）。"
    ),
)
async def agent_query(req: AgentQueryRequest):
    try:
        result = await graph.ainvoke({"messages": [HumanMessage(req.query)]})
        answer = result["messages"][-1].content
        return ok({"query": req.query, "answer": answer})
    except Exception as e:
        logger.error(f"Agent query 失敗 query={req.query!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent 查詢失敗: {e}")
