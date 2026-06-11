import json

from langchain_core.tools import tool
from sqlalchemy import text

from app.snomed.routers import rag as snomed_rag
from app.icd10.routers import rag_eng as icd10_rag
from app.database import LOINCSession
from app.logger import get_logger
from app.errorbot.routers import get_service
from typing import Any, Dict


logger = get_logger(__name__)


@tool
def search_snomed_ct(query: str) -> str:
    """搜尋 SNOMED CT 臨床術語與概念代碼。適用於疾病、症狀、臨床發現的標準化查詢。"""
    try:
        results = snomed_rag.similarity_search(
            query, k=15, threshold=0.3,
            extra_params={"tw_valueset": None},
        )
        grouped = snomed_rag.group_results(results)[:3]
        output = [
            {
                "concept_id": c["concept_id"],
                "fsn": c.get("fsn"),
                "semantic_tag": c.get("semantic_tag"),
                "similarity": round(c.get("best_similarity", c.get("similarity", 0)), 3),
            }
            for c in grouped
        ]
        return json.dumps(output, ensure_ascii=False) if output else "未找到相關 SNOMED CT 概念"
    except Exception as e:
        logger.error(f"SNOMED tool 失敗: {e}")
        return f"SNOMED 搜尋失敗: {e}"


@tool
def search_icd10_pcs(query: str) -> str:
    """搜尋 ICD-10-PCS 處置代碼。適用於手術、治療、醫療處置的代碼查詢（英文查詢效果較佳）。"""
    try:
        results = icd10_rag.similarity_search(
            query, k=5, threshold=0.3,
            extra_params={"valueset": None},
        )
        output = [
            {
                "code": c["code"],
                "term_eng": c.get("term_eng"),
                "term_cht": c.get("term_cht"),
                "similarity": round(c.get("similarity", 0), 3),
            }
            for c in results[:3]
        ]
        return json.dumps(output, ensure_ascii=False) if output else "未找到相關 ICD-10-PCS 代碼"
    except Exception as e:
        logger.error(f"ICD-10 tool 失敗: {e}")
        return f"ICD-10 搜尋失敗: {e}"


@tool
def search_loinc(query: str) -> str:
    """搜尋 LOINC 檢驗代碼。可輸入健保碼（如 09001B）或檢驗名稱關鍵字。
    適用於實驗室檢查、影像檢查、生命徵象的代碼查詢。"""
    db = LOINCSession()
    try:
        rows = db.execute(
            text("""
                SELECT loinc_code, long_common_name, component, property, relation
                FROM loinc_mapping
                WHERE nhi_code = :q
                ORDER BY CASE relation WHEN 'H' THEN 1 WHEN 'A' THEN 2 ELSE 3 END
                LIMIT 5
            """),
            {"q": query},
        ).fetchall()

        if not rows:
            rows = db.execute(
                text("""
                    SELECT DISTINCT loinc_code, long_common_name, component, property, relation
                    FROM loinc_mapping
                    WHERE lower(long_common_name) LIKE lower(:q)
                       OR lower(nhi_name_eng) LIKE lower(:q)
                    LIMIT 5
                """),
                {"q": f"%{query}%"},
            ).fetchall()

        if not rows:
            return f"未找到 '{query}' 的 LOINC 對應"

        output = [
            {"loinc_code": r[0], "name": r[1], "component": r[2], "property": r[3]}
            for r in rows
        ]
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        logger.error(f"LOINC tool 失敗: {e}")
        return f"LOINC 搜尋失敗: {e}"
    finally:
        db.close()

@tool
def explain_validation_result(
    outcome: Dict[str, Any],
    include_warning: bool = False,
    include_cascading: bool = True,
) -> str:
    """
    將 FHIR OperationOutcome 驗證結果翻譯成醫療人員看得懂的中文說明。
    傳入 OperationOutcome JSON，回傳中文解釋。
    """
    if outcome.get("resourceType") != "OperationOutcome":
        return "錯誤：請傳入有效的 OperationOutcome 資源"  # ← 不能用 HTTPException

    if not include_warning:
        outcome = {
            **outcome,
            "issue": [i for i in outcome.get("issue", []) if i.get("severity") == "error"],
        }

    service = get_service()
    result = service.explain(outcome)

    issues = result.get("issues", [])
    if not include_cascading:
        issues = [i for i in issues if i.get("category") != "cascading"]

    return json.dumps(issues, ensure_ascii=False)

medical_tools = [search_snomed_ct, search_icd10_pcs, search_loinc, explain_validation_result]
