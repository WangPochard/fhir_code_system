import os

from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode

from app.config import get_settings
from app.agent.tools import medical_tools
from app.logger import get_logger

logger = get_logger(__name__)


class MedicalCodingAgent:

    def __init__(self):
        s = get_settings()
        self._setup_langsmith(s)
        self._llm = self._build_llm(s)
        self.graph = self._build_graph()
        logger.info("Medical Coding Agent graph 初始化完成")

    def _setup_langsmith(self, s):
        # pydantic-settings 不寫入 os.environ，需手動橋接給 LangChain SDK
        if s.langchain_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = s.langchain_tracing_v2
            os.environ["LANGCHAIN_API_KEY"] = s.langchain_api_key
            os.environ["LANGCHAIN_PROJECT"] = s.langchain_project
            logger.info(f"LangSmith tracing 啟用，project={s.langchain_project}")

    def _build_llm(self, s):
        if s.llm_backend == "vllm":
            from langchain_openai import ChatOpenAI
            base_url = s.vllm_base_url.replace("/chat/completions", "")
            llm = ChatOpenAI(
                base_url=base_url,
                model=s.vllm_model,
                temperature=0,
                api_key="none",
            )
        elif s.llm_backend == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(
                base_url=s.llm_base_url,
                model=s.llm_model,
                temperature=0,
            )
        else:
            raise ValueError(f"不支援的 LLM backend: {s.llm_backend!r}，目前支援 vllm / ollama")

        logger.info(f"LLM backend: {s.llm_backend}")
        return llm.bind_tools(medical_tools)

    def _agent_node(self, state: MessagesState):
        return {"messages": [self._llm.invoke(state["messages"])]}

    @staticmethod
    def _route(state: MessagesState):
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

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


