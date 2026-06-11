from fastapi import APIRouter, HTTPException

from app.database import SnomedSession
from app.config import get_settings
from app.rag import RAGService
from app.llm.llm import get_llm_service
from app.logger import get_logger
from app.response import ok
from .schemas import (
    IdentifyRequest, IdentifyResponse, IdentifiedConcept, IdentifyCandidate,
    StatsResponse,
)

settings = get_settings()
logger = get_logger(__name__)

router = APIRouter(prefix="/snomed", tags=["SNOMED CT"])

# ------------------------------------------------------------------
# SQL & Mapper
# ------------------------------------------------------------------
SEARCH_SQL = """
    SELECT
        d.snomed_concept_id, d.fsn, d.semantic_tag, d.icd10_codes,
        d.source, de.term,
        1 - (de.embedding <=> CAST(:qvec AS vector)) AS similarity
    FROM document_embeddings de
    JOIN documents d ON de.document_id = d.id
    WHERE 1 - (de.embedding <=> CAST(:qvec AS vector)) > :threshold
      AND (:tw_valueset IS NULL OR d.tw_valueset = :tw_valueset)
    ORDER BY de.embedding <=> CAST(:qvec AS vector)
    LIMIT :k
"""

STATS_SQL = """
    SELECT
        COUNT(DISTINCT d.id)     AS total_documents,
        COUNT(de.id)             AS total_embeddings,
        COUNT(DISTINCT d.source) AS unique_sources
    FROM documents d
    LEFT JOIN document_embeddings de ON d.id = de.document_id
"""


def snomed_mapper(row):
    return {
        "concept_id": row[0],
        "fsn": row[1],
        "semantic_tag": row[2],
        "icd10_codes": row[3] or [],
        "source": row[4],
        "term": row[5],
        "similarity": float(row[6]),
    }


# ------------------------------------------------------------------
# RAG Service（啟動時初始化一次）
# ------------------------------------------------------------------
rag = RAGService(
    db_session=SnomedSession,
    search_sql=SEARCH_SQL,
    result_mapper=snomed_mapper,
    stats_sql=STATS_SQL,
    model_name=settings.sentence_transformer_model,
    group_key="concept_id",
    llm_service=get_llm_service(),
)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
# 此為純 RAG 向量搜尋實作，未經 NER 萃取與 LLM reranking，可信度不高。
# 完整識別流程請參考 /identify。
#
# @router.post(
#     "/search",
#     summary="向量搜尋 SNOMED CT 概念",
#     description="輸入藥品成分或臨床術語，回傳 Top-K 最相似的 SNOMED CT 概念",
# )
# async def search(req: SearchRequest):
#     try:
#         raw = rag.similarity_search(
#             query=req.query, k=req.top_k * 3, threshold=req.threshold,
#         )
#         grouped = rag.group_results(raw)[:req.top_k]
#
#         candidates = [
#             CandidateResponse(
#                 rank=i,
#                 concept_id=c["concept_id"],
#                 fsn=c.get("fsn"),
#                 semantic_tag=c.get("semantic_tag"),
#                 icd10_codes=c.get("icd10_codes", []),
#                 term=c.get("term"),
#                 similarity=c.get("best_similarity", c.get("similarity", 0)),
#             )
#             for i, c in enumerate(grouped, 1)
#         ]
#
#         return ok(SearchResponse(
#             query=req.query,
#             total=len(candidates),
#             embedding_model=rag.embedding.model_name,
#             candidates=candidates,
#         ))
#     except Exception as e:
#         logger.error(f"SNOMED search 失敗 query={req.query!r}: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail="搜尋失敗，請稍後再試")


_FIELD_TYPE_VALUESET = {
    "diagnosis":        "condition",
    "medication_route": "medication-path",
    "department":       "medical-department",
    "profession":       "health-professional",
}


@router.post(
    "/identify",
    summary="臨床文字 → SNOMED CT 多術語識別",
    description=(
        "輸入臨床自由文字，LLM 自動萃取術語後逐一搜尋 SNOMED CT 概念並評分排序。\n"
        "每個術語獨立回傳候選清單與第一順位判斷說明。\n\n"
        "**範例 1：診斷欄位 + ICD-10-CM 輔助**\n"
        "```json\n"
        "{\n"
        '  "clinical_text": "T-colon ca for surgical consultation",\n'
        '  "icd10_cm_code": "C18.4",\n'
        '  "icd10_cm_label": "Malignant neoplasm of transverse colon",\n'
        '  "field_type": "diagnosis",\n'
        '  "top_k": 3,\n'
        '  "threshold": 0\n'
        "}\n"
        "```\n\n"
        "**範例 2：臨床印象（多概念，不帶 CM 碼）**\n"
        "```json\n"
        "{\n"
        '  "clinical_text": "HTN-, body weight loss, back pain, nocturia 3-4x",\n'
        '  "field_type": "diagnosis",\n'
        '  "top_k": 3,\n'
        '  "threshold": 0\n'
        "}\n"
        "```\n\n"
        "**範例 3：用藥途徑**\n"
        "```json\n"
        "{\n"
        '  "clinical_text": "intravenous injection",\n'
        '  "field_type": "medication_route",\n'
        '  "top_k": 3,\n'
        '  "threshold": 0\n'
        "}\n"
        "```"
    ),
)
async def identify(req: IdentifyRequest):
    try:
        tw_valueset = _FIELD_TYPE_VALUESET.get(req.field_type) if req.field_type else None
        raw = rag.identify(
            clinical_text=req.clinical_text,
            icd10_cm_code=req.icd10_cm_code or "",
            icd10_cm_label=req.icd10_cm_label or "",
            top_k=req.top_k,
            threshold=req.threshold,
            tw_valueset=tw_valueset,
        )
        concepts = [
            IdentifiedConcept(
                term=c["term"],
                reason=c.get("reason", ""),
                candidates=[IdentifyCandidate(**cand) for cand in c["candidates"]],
            )
            for c in raw.get("identified_concepts", [])
        ]
        return ok(IdentifyResponse(
            clinical_text=req.clinical_text,
            identified_concepts=concepts,
        ))
    except Exception as e:
        logger.error(f"SNOMED identify 失敗 text={req.clinical_text!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="識別失敗，請稍後再試")


@router.get(
    "/stats",
    summary="知識庫統計",
)
async def stats():
    try:
        raw = rag.get_stats()
        return ok(StatsResponse(
            total_documents=raw.get("col_0", 0),
            total_embeddings=raw.get("col_1", 0),
            unique_sources=raw.get("col_2", 0),
            embedding_model=raw.get("embedding_model", ""),
        ))
    except Exception as e:
        logger.error(f"SNOMED stats 失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
