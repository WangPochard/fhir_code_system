import json

from langchain_core.tools import tool
from sqlalchemy import text

from app.snomed.routers import rag as snomed_rag
from app.icd10.routers import rag_eng as icd10_rag
from app.database import LOINCSession
from app.agent.extractor import extract_terms
from app.logger import get_logger


logger = get_logger(__name__)


@tool
def search_snomed_ct(query: str) -> str:
    """搜尋 SNOMED CT 臨床術語與概念代碼。適用於疾病、症狀、臨床發現的標準化查詢。"""
    try:
        raw = snomed_rag.identify(clinical_text=query, top_k=3, threshold=0.0)
        seen: set[str] = set()
        output = []
        for concept in raw.get("identified_concepts", []):
            term = concept.get("term", "")
            for cand in concept.get("candidates", []):
                cid = cand.get("concept_id", "")
                if cid and cid not in seen:
                    seen.add(cid)
                    output.append({
                        "concept_id": cid,
                        "fsn": cand.get("fsn"),
                        "confidence_pct": cand.get("confidence_pct"),
                        "matched_term": term,
                    })
        return json.dumps(output[:6], ensure_ascii=False) if output else "未找到相關 SNOMED CT 概念"
    except Exception as e:
        logger.error(f"SNOMED tool 失敗: {e}")
        return f"SNOMED 搜尋失敗: {e}"


@tool
def search_icd10_pcs(query: str) -> str:
    """搜尋 ICD-10-PCS 處置代碼。適用於手術、治療、醫療處置的代碼查詢。"""
    try:
        from app.icd10.routers import llm as icd10_llm, _score_pcs_candidate

        facts = icd10_llm.extract_pcs_facts_from_report(report_text=query)
        section = facts.get("pcs_section", "unknown")
        _SECTION_VALUESET = {"B": "imaging", "D": "radiotherapy"}
        valueset = _SECTION_VALUESET.get(section)  # None → 不過濾（手術等其他 section）

        procedure_query = facts.get("procedure_description") or query

        raw = icd10_rag.similarity_search(
            procedure_query, k=10, threshold=0.3,
            extra_params={"valueset": valueset},
        )

        scored = []
        for rag_rank, c in enumerate(raw, 1):
            total, _ = _score_pcs_candidate(c["code"], facts, c, rag_rank)
            scored.append((total, c))
        scored.sort(key=lambda x: x[0], reverse=True)

        seen: set[str] = set()
        output = []
        for total, c in scored:
            code = c["code"]
            if code not in seen:
                seen.add(code)
                output.append({
                    "code": code,
                    "term_eng": c.get("term_eng"),
                    "term_cht": c.get("term_cht"),
                    "confidence_pct": total,
                })
        return json.dumps(output[:6], ensure_ascii=False) if output else "未找到相關 ICD-10-PCS 代碼"
    except Exception as e:
        logger.error(f"ICD-10 tool 失敗: {e}")
        return f"ICD-10 搜尋失敗: {e}"


@tool
def search_loinc(query: str) -> str:
    """搜尋 LOINC 檢驗代碼。可輸入健保碼（如 09001B）或檢驗名稱關鍵字。
    適用於實驗室檢查、影像檢查、生命徵象的代碼查詢。"""
    try:
        terms = extract_terms(query, "loinc")
        db = LOINCSession()
        try:
            seen: set[str] = set()
            output = []
            for term in terms:
                rows = db.execute(
                    text("""
                        SELECT loinc_code, long_common_name, component, property, relation
                        FROM loinc_mapping
                        WHERE nhi_code = :q
                        ORDER BY CASE relation WHEN 'H' THEN 1 WHEN 'A' THEN 2 ELSE 3 END
                        LIMIT 3
                    """),
                    {"q": term},
                ).fetchall()

                if not rows:
                    rows = db.execute(
                        text("""
                            SELECT DISTINCT loinc_code, long_common_name, component, property, relation
                            FROM loinc_mapping
                            WHERE lower(long_common_name) LIKE lower(:q)
                               OR lower(nhi_name_eng) LIKE lower(:q)
                            LIMIT 3
                        """),
                        {"q": f"%{term}%"},
                    ).fetchall()

                for r in rows:
                    if r[0] not in seen:
                        seen.add(r[0])
                        output.append({
                            "loinc_code": r[0],
                            "name": r[1],
                            "component": r[2],
                            "property": r[3],
                            "matched_term": term,
                        })
            return json.dumps(output[:6], ensure_ascii=False) if output else "未找到相關 LOINC 代碼"
        finally:
            db.close()
    except Exception as e:
        logger.error(f"LOINC tool 失敗: {e}")
        return f"LOINC 搜尋失敗: {e}"

medical_tools = [search_snomed_ct, search_icd10_pcs, search_loinc]
