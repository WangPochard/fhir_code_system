import os

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode

from app.config import get_settings
from app.agent.tools import medical_tools
from app.logger import get_logger

_AGENT_SYSTEM_PROMPT = """/no_think
你是醫療代碼查詢助理，可使用以下工具查詢標準代碼：
- search_snomed_ct：查詢疾病、症狀、臨床發現
- search_icd10_pcs：查詢手術、治療處置
- search_loinc：查詢實驗室檢驗、影像檢查

規則：
1. 根據臨床描述判斷需要查詢哪些工具，每個工具只呼叫一次。
2. 取得所有需要的結果後，立即整理成繁體中文摘要回傳，不再呼叫任何工具。
3. 回傳格式：列出每個找到的代碼、名稱與相似度分數。"""

_AGENT_SUMMARY_PROMPT = """/no_think
你是醫療代碼查詢助理。工具已完成搜尋，請整理所有結果。

輸出規則：
1. 用繁體中文撰寫摘要。
2. 依系統分組（SNOMED CT / ICD-10-PCS / LOINC），列出代碼、名稱與相似度。
3. 若某系統無結果，簡短說明即可。
4. 不再呼叫任何工具。"""

logger = get_logger(__name__)


def build_chat_llm(json_format: bool = False):
    """建立支援 tool calling / structured output 的 Chat LLM。

    json_format=True 僅供 Ollama 後端：強制回傳 JSON，用於 supervisor 的結構化輸出。
    ReAct agent（bind_tools）不應開啟此選項。
    """
    s = get_settings()
    if s.llm_backend == "vllm":
        from langchain_openai import ChatOpenAI
        base_url = s.vllm_base_url.replace("/chat/completions", "")
        return ChatOpenAI(base_url=base_url, model=s.vllm_model, temperature=0, api_key="none")
    elif s.llm_backend == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = {"base_url": s.llm_base_url, "model": s.llm_model, "temperature": 0}
        if json_format:
            kwargs["format"] = "json"
        return ChatOllama(**kwargs)
    else:
        raise ValueError(f"不支援的 LLM backend: {s.llm_backend!r}，目前支援 vllm / ollama")


class MedicalCodingAgent:

    def __init__(self):
        s = get_settings()
        self._setup_langsmith(s)
        self._llm = build_chat_llm().bind_tools(medical_tools)
        self.graph = self._build_graph()
        logger.info("Medical Coding Agent graph 初始化完成")

    def _setup_langsmith(self, s):
        # pydantic-settings 不寫入 os.environ，需手動橋接給 LangChain SDK
        if s.langchain_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = s.langchain_tracing_v2
            os.environ["LANGCHAIN_API_KEY"] = s.langchain_api_key
            os.environ["LANGCHAIN_PROJECT"] = s.langchain_project
            logger.info(f"LangSmith tracing 啟用，project={s.langchain_project}")

    def _agent_node(self, state: MessagesState):
        has_tool_results = any(isinstance(m, ToolMessage) for m in state["messages"])
        prompt = _AGENT_SUMMARY_PROMPT if has_tool_results else _AGENT_SYSTEM_PROMPT
        messages = [SystemMessage(content=prompt)] + state["messages"]
        return {"messages": [self._llm.invoke(messages)]}

    @staticmethod
    def _route(state: MessagesState):
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        tool_rounds = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
        if tool_rounds >= len(medical_tools):
            return END
        return "tools"

    def _build_graph(self):
        return (
            StateGraph(MessagesState)
            .add_node("agent", self._agent_node)
            .add_node("tools", ToolNode(medical_tools))
            .add_edge("__start__", "agent")
            .add_conditional_edges("agent", self._route, {"tools": "tools", END: END})
            .add_edge("tools", "agent")
            .compile()
        )


