from fastapi import APIRouter, HTTPException

from app.database import ICD10Session
from app.config import get_settings
from app.rag import RAGService
from app.logger import get_logger
from app.response import ok
from app.llm.llm import LLMService
from .schemas import (
    StatsResponse, CandidateResponse,
    PcsSuggestRequest, PcsSuggestResponse,
    PcsBatchRequest, PcsBatchResponse, PcsBatchResultItem,
)

settings = get_settings()
logger = get_logger(__name__)

router = APIRouter(prefix="/icd10", tags=["ICD-10 PCS"])

ICD10_MODEL_ENG = "sentence-transformers/all-mpnet-base-v2"

SEARCH_SQL_ENG = """
    SELECT
        e.code, e.term_eng, e.term_cht,
        1 - (e.embedding_eng <=> CAST(:qvec AS vector)) AS similarity
    FROM embed_icd10 e
    WHERE e.embedding_eng IS NOT NULL
      AND 1 - (e.embedding_eng <=> CAST(:qvec AS vector)) > :threshold
      AND (:valueset IS NULL OR e.valueset = :valueset)
    ORDER BY e.embedding_eng <=> CAST(:qvec AS vector)
    LIMIT :k
"""

STATS_SQL = """
    SELECT
        COUNT(DISTINCT c.code) AS total_codes,
        COUNT(e.id)            AS total_embeddings
    FROM codespcs c
    LEFT JOIN embed_icd10 e ON c.code = e.code
"""


def icd10_mapper(row):
    return {
        "code": row[0],
        "term_eng": row[1],
        "term_cht": row[2],
        "similarity": float(row[3]),
    }


rag_eng = RAGService(
    db_session=ICD10Session,
    search_sql=SEARCH_SQL_ENG,
    result_mapper=icd10_mapper,
    stats_sql=STATS_SQL,
    model_name=ICD10_MODEL_ENG,
)

llm = LLMService()


# ------------------------------------------------------------------
# PCS 評分對照表（Rule-based，不依賴 LLM）
# ------------------------------------------------------------------
_IMG_ROOT_TYPE: dict[str, str] = {
    "CT": "2", "MRI": "3", "Ultrasound": "4",
    "X-Ray": "0", "Fluoroscopy": "1",
}

_SURG_ROOT_OP: dict[str, set[str]] = {
    "Biopsy":   {"B"},
    "Surgery":  {"B", "T", "E", "G", "Q", "R", "S", "N", "P"},
    "Lab":      {"J"},
    "Radiation": {"D"},
}

_APPROACH_CODE: dict[str, str] = {
    "percutaneous": "3",
    "open":         "0",
    "endoscopic":   "4",
    "natural_opening": "7",
}


# ------------------------------------------------------------------
# 7-axis PCS 評分常數（各 axis 獨立給分，滿分合計 100，不分餅）
# Axis 1 Section:15  Axis2 BodySystem:20  Axis3 RootOp:25
# Axis 4 BodyPart:25(rank-based,step=5)  Axis5 Approach:15
# ------------------------------------------------------------------
_AXIS1_MAX = 15
_AXIS2_MAX = 20
_AXIS3_MAX = 25
_AXIS4_MAX = 25
_AXIS5_MAX = 15

# Axis 4 body_part rank → 分數（等差 5，最多 9 名後固定 1 分）
_BODY_PART_RANK_SCORE = [25, 20, 15, 10, 5, 4, 3, 2, 1]
_WRONG_LATERALITY_PENALTY = 8


def _score_body_part(rag_rank: int, extracted: dict, candidate: dict) -> int:
    base = _BODY_PART_RANK_SCORE[min(rag_rank - 1, len(_BODY_PART_RANK_SCORE) - 1)]
    laterality = (extracted.get("laterality") or "unspecified").lower()
    if laterality in ("left", "right"):
        term_eng = (candidate.get("term_eng") or "").lower()
        opposite = "right" if laterality == "left" else "left"
        if opposite in term_eng:
            base = max(1, base - _WRONG_LATERALITY_PENALTY)
    return base


def _score_pcs_candidate(
    code: str,
    extracted: dict,
    candidate: dict,
    rag_rank: int,
) -> tuple[int, dict[str, str]]:
    """7-axis 獨立評分，滿分 100；rubric 僅供 Log，不進 response。"""
    if len(code) < 7:
        return 0, {}

    section           = code[0]
    body_system_char  = code[1]
    root_char         = code[2]
    approach_contrast = code[4]

    # Axis 1: Section
    expected_sec = extracted.get("pcs_section", "unknown")
    a1 = _AXIS1_MAX if (expected_sec not in ("unknown", "") and section == expected_sec) else 0

    # Axis 2: Body System
    expected_bs = extracted.get("pcs_body_system", "unknown")
    a2 = _AXIS2_MAX if (expected_bs not in ("unknown", "") and body_system_char == expected_bs) else 0

    # Axis 4: Body Part（rank-based，先算方便 axis3/5 共用 section 判斷）
    a4 = _score_body_part(rag_rank, extracted, candidate)

    # Axis 3 & 5：影像 vs 非影像邏輯不同
    if section == "B":
        expected_root = _IMG_ROOT_TYPE.get(extracted.get("modality", ""), None)
        a3 = _AXIS3_MAX if (expected_root and root_char == expected_root) else 0

        contrast = extracted.get("contrast", "unknown")
        if contrast == "yes":
            a5 = _AXIS5_MAX if approach_contrast != "Z" else 0
        elif contrast == "no":
            a5 = _AXIS5_MAX if approach_contrast == "Z" else 0
        else:
            a5 = _AXIS5_MAX // 2  # 不確定 → 給一半
    else:
        modality = extracted.get("modality", "unknown")
        expected_roots = _SURG_ROOT_OP.get(modality, set())
        if expected_roots and root_char in expected_roots:
            a3 = _AXIS3_MAX
        elif not expected_roots:
            a3 = _AXIS3_MAX // 2  # modality 不明 → 給一半
        else:
            a3 = 0

        approach = extracted.get("approach", "unknown")
        expected_approach = _APPROACH_CODE.get(approach)
        if expected_approach and approach_contrast == expected_approach:
            a5 = _AXIS5_MAX
        elif approach == "unknown":
            a5 = _AXIS5_MAX // 2
        else:
            a5 = 0

    rubric = {
        f"Axis1 Section({_AXIS1_MAX})":       f"{a1}",
        f"Axis2 BodySystem({_AXIS2_MAX})":    f"{a2}",
        f"Axis3 RootOp({_AXIS3_MAX})":        f"{a3}",
        f"Axis4 BodyPart({_AXIS4_MAX})":      f"{a4}",
        f"Axis5 Approach({_AXIS5_MAX})":      f"{a5}",
    }
    return a1 + a2 + a3 + a4 + a5, rubric


# ------------------------------------------------------------------
# 內部工具函式
# ------------------------------------------------------------------


def _decide_degradation(extraction_confidence: str, candidate_count: int) -> tuple[str, str]:
    if candidate_count == 0:
        return "needs_human_review", "RAG 未找到符合的代碼，需人工確認"
    if extraction_confidence == "high":
        return "use_pcs", "報告明確記載程序資訊，信心高"
    if extraction_confidence == "medium":
        return "loinc_fallback", "程序資訊部分模糊，建議先以 LOINC 18748-4 填入並標記待補填"
    return "needs_human_review", "報告程序資訊不足或萃取失敗，需人工確認"


async def _run_pcs_pipeline(
    report_text: str,
    cm_code: str,
    cm_label: str,
    top_k: int,
    threshold: float,
) -> PcsSuggestResponse:
    """單份報告的完整 PCS pipeline，供單筆與批次 endpoint 共用。"""
    facts = llm.extract_pcs_facts_from_report(
        report_text=report_text,
        cm_code=cm_code,
        cm_label=cm_label,
    )

    section = facts.get("pcs_section", "unknown")
    body_system = facts.get("pcs_body_system", "unknown")
    bs_confidence = facts.get("pcs_body_system_confidence", "low")
    extraction_confidence = facts.get("extraction_confidence", "low")

    procedure_query = facts.get("procedure_description") or report_text[:200]

    # 根據 LLM 萃取的 pcs_section 決定 valueset filter
    # 僅支援影像（B）與放射治療（D），無法識別者回傳 needs_human_review
    _SECTION_VALUESET = {"B": "imaging", "D": "radiotherapy"}
    valueset = _SECTION_VALUESET.get(section)
    if valueset is None:
        logger.warning(f"PCS section 無法識別: {section!r}，無法對應值集")
        return PcsSuggestResponse(
            candidates=[],
            top_recommendation_summary="",
            degradation_decision="needs_human_review",
            degradation_reason="報告所描述的程序類型無法對應至已知的 ICD-10-PCS 值集，建議確認報告內容是否包含明確的程序描述，或交由人工編碼。",
        )
    logger.info(f"PCS valueset filter: {valueset}")

    raw = rag_eng.similarity_search(
        query=procedure_query, k=top_k, threshold=threshold,
        extra_params={"valueset": valueset},
    )

    logger.info(
        f"PCS 萃取結果 | modality={facts.get('modality')} body_part={facts.get('body_part')} "
        f"laterality={facts.get('laterality')} approach={facts.get('approach')} "
        f"contrast={facts.get('contrast')} extraction_confidence={extraction_confidence} "
        f"not_found_reason={facts.get('not_found_reason')} "
        f"pcs_section={section} pcs_body_system={body_system} "
        f"pcs_body_system_confidence={bs_confidence} "
        f"pcs_body_system_reason={facts.get('pcs_body_system_reason')}"
    )

    # Rule-based 評分並依分數重新排序
    scored = []
    for rag_rank, c in enumerate(raw, 1):
        total, rubric = _score_pcs_candidate(c["code"], facts, c, rag_rank)
        scored.append((total, rubric, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    # confidence_pct = 各 axis 獨立加總（滿分 100），不分餅
    # rubric 細項只寫 Log，不進 response
    for total, rubric, c in scored:
        logger.debug(
            f"PCS rubric | {c['code']} total={total} | "
            + " ".join(f"{k}={v}" for k, v in rubric.items())
        )

    logger.info(
        "PCS 評分結果 | "
        + " | ".join(
            f"{c['code']} score={total}"
            for total, _, c in scored
        )
    )

    # 為每個候選產生中文說明（LLM 只負責文字，不打分）
    explanations_data: dict = {}
    top_summary = ""
    if scored:
        explain_result = llm.explain_pcs_candidates(
            procedure_query=procedure_query,
            report_preview=report_text[:300],
            candidates=[c for _, _, c in scored],
        )
        explanations_data = {
            e["code"]: e for e in explain_result.get("explanations", [])
        }
        top_summary = explain_result.get("top_recommendation_summary", "")

    candidates = [
        CandidateResponse(
            rank=i,
            code=c["code"],
            term_eng=c.get("term_eng"),
            term_cht=c.get("term_cht"),
            confidence_pct=float(total),
            reason=explanations_data.get(c["code"], {}).get("reason", ""),
            imaging_modality=explanations_data.get(c["code"], {}).get("imaging_modality") or None,
            imaging_body_part=explanations_data.get(c["code"], {}).get("imaging_body_part") or None,
        )
        for i, (total, _, c) in enumerate(scored, 1)
    ]

    degradation, degradation_reason = _decide_degradation(extraction_confidence, len(candidates))

    return PcsSuggestResponse(
        candidates=candidates,
        top_recommendation_summary=top_summary,
        degradation_decision=degradation,
        degradation_reason=degradation_reason,
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
@router.get("/stats", summary="ICD-10 知識庫統計")
async def stats():
    try:
        raw = rag_eng.get_stats()
        return ok(StatsResponse(
            total_codes=raw.get("col_0", 0),
            total_embeddings=raw.get("col_1", 0),
            embedding_model=ICD10_MODEL_ENG,
        ))
    except Exception as e:
        logger.error(f"ICD-10 stats 失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


@router.post(
    "/pcs-suggest",
    summary="診斷報告 → ICD-10-PCS 代碼建議",
    description=(
        "輸入診斷報告文字（必填）與 ICD-10-CM 診斷碼（選填）。\n"
        "LLM 從報告萃取程序事實 → 全庫 RAG 搜尋 → 7-axis 評分排序 → 信心分層降級決策。\n\n"
        "---\n\n"
        "**範例 1：肺癌穿刺切片（病理報告）**\n"
        "```json\n"
        "{\n"
        '  "cm_code": "C34.12",\n'
        '  "cm_label": "右上葉惡性腫瘤",\n'
        '  "report_text": "右肺上葉穿刺切片，穿刺位點右上葉後段，組織學診斷為非小細胞肺癌（腺癌），PD-L1（22C3）TPS = 75%。免疫組化在穿刺切片組織上執行。",\n'
        '  "threshold": 0,\n'
        '  "top_k": 5\n'
        "}\n"
        "```\n\n"
        "**範例 2：腹部骨盆 CT（影像報告）**\n"
        "```json\n"
        "{\n"
        '  "cm_code": "C18.4",\n'
        '  "report_text": "CT of abdomen and pelvis without and with IV contrast enhancement show:\\n1. Circumferential wall thickening with pericolonic infiltrates about 7.0cm in length at transverse colon, associated with several enlarged lymph nodes. Advanced transverse colon cancer with regional lymphadenopathy should be considered.\\n2. Multiple enlarged lymph nodes at mesenteric root, paraaortic & paracaval retroperitoneum, bilateral iliac chain, and lower mediastinum. Metastatic lymphadenopathy should be considered.",\n'
        '  "threshold": 0,\n'
        '  "top_k": 5\n'
        "}\n"
        "```"
    ),
)
async def pcs_suggest(req: PcsSuggestRequest):
    try:
        result = await _run_pcs_pipeline(
            report_text=req.report_text,
            cm_code=req.cm_code or "",
            cm_label=req.cm_label or "",
            top_k=req.top_k,
            threshold=req.threshold,
        )
        return ok(result)
    except Exception as e:
        logger.error(f"ICD-10 pcs-suggest 失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="PCS 建議失敗，請稍後再試")


@router.post(
    "/pcs-suggest/batch",
    summary="診斷報告 → ICD-10-PCS 代碼建議（批次）",
    description=(
        "批次版本，每筆各自執行完整 pipeline，結果互相獨立。\n"
        "單筆失敗不影響其他筆，失敗原因記錄在 error 欄位。"
    ),
)
async def pcs_suggest_batch(req: PcsBatchRequest):
    results: list[PcsBatchResultItem] = []
    success = 0
    failed = 0

    for i, item in enumerate(req.items):
        try:
            result = await _run_pcs_pipeline(
                report_text=item.report_text,
                cm_code=item.cm_code or "",
                cm_label=item.cm_label or "",
                top_k=req.top_k,
                threshold=req.threshold,
            )
            results.append(PcsBatchResultItem(
                index=i,
                report_text_preview=item.report_text[:80],
                result=result,
            ))
            success += 1
        except Exception as e:
            logger.error(f"ICD-10 pcs-suggest/batch 第 {i} 筆失敗: {e}", exc_info=True)
            results.append(PcsBatchResultItem(
                index=i,
                report_text_preview=item.report_text[:80],
                error=str(e),
            ))
            failed += 1

    return ok(PcsBatchResponse(
        total=len(req.items),
        success=success,
        failed=failed,
        results=results,
    ))
