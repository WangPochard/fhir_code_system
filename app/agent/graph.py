import os

from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.agent.tools import medical_tools
from app.logger import get_logger

logger = get_logger(__name__)

# LangSmith tracing：pydantic-settings 不寫入 os.environ，需手動橋接
_s = get_settings()
if _s.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = _s.langchain_tracing_v2
    os.environ["LANGCHAIN_API_KEY"] = _s.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = _s.langchain_project
    logger.info(f"LangSmith tracing 啟用，project={_s.langchain_project}")


def _build_llm():
    s = get_settings()
    if s.llm_backend == "vllm":
        base_url = s.vllm_base_url.replace("/chat/completions", "")
        llm = ChatOpenAI(
            base_url=base_url,
            model=s.vllm_model,
            temperature=0,
            api_key="none",
        )
    else:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            base_url=s.llm_base_url,
            model=s.llm_model,
            temperature=0,
        )
    return llm.bind_tools(medical_tools)


_llm = _build_llm()


def _agent(state: MessagesState):
    return {"messages": [_llm.invoke(state["messages"])]}


def _route(state: MessagesState):
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


graph = (
    StateGraph(MessagesState)
    .add_node("agent", _agent)
    .add_node("tools", ToolNode(medical_tools))
    .add_edge("__start__", "agent")
    .add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    .add_edge("tools", "agent")
    .compile()
)

logger.info("Medical Coding Agent graph 初始化完成")
