"""
第一層 AI 過濾：領域專屬術語萃取
=====================================
Supervisor 分類完後，針對各個 coding system 從臨床文字中
抽出對應類型的術語，再交給向量 DB 搜尋。

流程：
    臨床文字
        ↓
    Supervisor 分類（snomed / icd10 / loinc）
        ↓
    extract_terms()  ← 這個檔案
        ↓
    similarity_search（向量 DB）
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.logger import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------
# 各領域專屬 prompt
# ------------------------------------------------------------------

_SNOMED_SYSTEM = """你是臨床術語萃取助理，專門識別適合查詢 SNOMED CT 的概念。
從臨床描述中抽取「疾病、症狀、臨床發現、身體構造」類術語。

輸出規則：
- 只回傳 JSON 陣列，不要任何說明
- 每個元素為單一術語字串
- 範例：["急性胸痛", "ST 段上升", "心肌梗塞"]"""

_ICD10_SYSTEM = """你是臨床術語萃取助理，專門識別適合查詢 ICD-10-PCS 的概念。
從臨床描述中抽取「手術、治療處置、醫療介入」類術語。

輸出規則：
- 只回傳 JSON 陣列，不要任何說明
- 每個元素為單一術語字串
- 範例：["緊急心導管手術", "經皮冠狀動脈介入治療"]"""

_LOINC_SYSTEM = """你是臨床術語萃取助理，專門識別適合查詢 LOINC 的概念。
從臨床描述中抽取「實驗室檢驗、影像檢查、生命徵象量測」類術語。

輸出規則：
- 只回傳 JSON 陣列，不要任何說明
- 每個元素為單一術語字串
- 範例：["心肌酵素", "肌鈣蛋白", "心電圖"]"""

_DOMAIN_PROMPTS: dict[str, str] = {
    "snomed": _SNOMED_SYSTEM,
    "icd10": _ICD10_SYSTEM,
    "loinc": _LOINC_SYSTEM,
}

# ------------------------------------------------------------------
# LLM 單例
# ------------------------------------------------------------------

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        from app.agent.graph import build_chat_llm  # lazy import 避免 circular
        _llm = build_chat_llm(json_format=True)
    return _llm


# ------------------------------------------------------------------
# 萃取函式
# ------------------------------------------------------------------

def extract_terms(clinical_text: str, domain: str) -> list[str]:
    """從臨床文字萃取指定領域的術語，失敗時 fallback 到原始文字。

    Parameters
    ----------
    clinical_text : str
        原始臨床描述
    domain : str
        "snomed" | "icd10" | "loinc"

    Returns
    -------
    list[str]
        萃取出的術語列表；萃取失敗時回傳 [clinical_text]
    """
    if domain not in _DOMAIN_PROMPTS:
        raise ValueError(f"不支援的 domain: {domain!r}，可用：{list(_DOMAIN_PROMPTS)}")

    try:
        response = _get_llm().invoke([
            SystemMessage(content=_DOMAIN_PROMPTS[domain]),
            HumanMessage(content=clinical_text),
        ])

        raw = response.content.strip()
        parsed = json.loads(raw)

        # 相容 {"terms": [...]} 或直接 [...] 兩種格式
        if isinstance(parsed, dict):
            parsed = next((v for v in parsed.values() if isinstance(v, list)), [])

        terms = [str(t).strip() for t in parsed if str(t).strip()]
        if terms:
            logger.info(f"[extractor/{domain}] 萃取 {len(terms)} 個術語: {terms}")
            return terms

    except Exception as e:
        logger.warning(f"[extractor/{domain}] 萃取失敗，fallback 到原始文字: {e}")

    return [clinical_text]
