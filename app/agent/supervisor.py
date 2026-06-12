import operator
from typing import TypedDict, Annotated, Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel

from app.agent.graph import build_chat_llm
from app.agent.tools import search_snomed_ct, search_icd10_pcs, search_loinc
from app.logger import get_logger

logger = get_logger(__name__)

_SUPERVISOR_PROMPT = """你是醫療代碼查詢系統的路由助理，管理三個專門的 worker：

- snomed：查詢 SNOMED CT 臨床術語代碼（疾病、症狀、臨床發現、用藥途徑）
- icd10：查詢 ICD-10-PCS 處置代碼（手術、影像檢查、治療處置）
- loinc：查詢 LOINC 檢驗代碼（血液、尿液、病理、基因檢測）

根據對話內容決定下一步要交給哪個 worker。
若所有需要的代碼已查詢完畢，回傳 FINISH。
同一個任務可依序呼叫多個 worker。"""


class SupervisorState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    next: str


class RouteDecision(BaseModel):
    next: Literal["snomed", "icd10", "loinc", "FINISH"]
    reason: str


class MedicalCodingSupervisor:

    def __init__(self):
        self._llm = build_chat_llm(json_format=True).with_structured_output(RouteDecision)
        self.graph = self._build_graph()
        logger.info("Medical Coding Supervisor graph 初始化完成")

    # ------------------------------------------------------------------
    # Supervisor node
    # ------------------------------------------------------------------
    def _supervisor_node(self, state: SupervisorState):
        messages = [SystemMessage(content=_SUPERVISOR_PROMPT)] + state["messages"]
        try:
            decision = self._llm.invoke(messages)
        except OutputParserException as e:
            logger.warning(f"Supervisor 結構化輸出解析失敗，預設 FINISH：{e}")
            decision = RouteDecision(next="FINISH", reason="LLM 輸出解析失敗")
        logger.info(f"Supervisor → {decision.next}（{decision.reason}）")
        return {"next": decision.next}

    # ------------------------------------------------------------------
    # Worker nodes（重用 tools.py 已有的搜尋邏輯）
    # ------------------------------------------------------------------
    @staticmethod
    def _get_original_query(state: SupervisorState) -> str:
        """取得使用者的原始輸入（第一條 HumanMessage）。"""
        return next(
            (m.content for m in state["messages"] if isinstance(m, HumanMessage)), ""
        )

    @staticmethod
    def _snomed_worker(state: SupervisorState):
        query = MedicalCodingSupervisor._get_original_query(state)
        result = search_snomed_ct.invoke({"query": query})
        logger.info("SNOMED worker 完成")
        return {"messages": [AIMessage(content=result, name="snomed")]}

    @staticmethod
    def _icd10_worker(state: SupervisorState):
        query = MedicalCodingSupervisor._get_original_query(state)
        result = search_icd10_pcs.invoke({"query": query})
        logger.info("ICD-10 worker 完成")
        return {"messages": [AIMessage(content=result, name="icd10")]}

    @staticmethod
    def _loinc_worker(state: SupervisorState):
        query = MedicalCodingSupervisor._get_original_query(state)
        result = search_loinc.invoke({"query": query})
        logger.info("LOINC worker 完成")
        return {"messages": [AIMessage(content=result, name="loinc")]}

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    @staticmethod
    def _route(state: SupervisorState) -> str:
        return "__end__" if state["next"] == "FINISH" else state["next"]

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------
    def _build_graph(self):
        graph = StateGraph(SupervisorState)

        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("snomed", self._snomed_worker)
        graph.add_node("icd10", self._icd10_worker)
        graph.add_node("loinc", self._loinc_worker)

        graph.add_edge("__start__", "supervisor")
        graph.add_conditional_edges(
            "supervisor", self._route,
            {"snomed": "snomed", "icd10": "icd10", "loinc": "loinc", "__end__": END},
        )
        graph.add_edge("snomed", "supervisor")
        graph.add_edge("icd10", "supervisor")
        graph.add_edge("loinc", "supervisor")

        return graph.compile()
