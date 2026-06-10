from fastapi import APIRouter, HTTPException

from sqlalchemy import text
from app.database import LOINCSession
from app.logger import get_logger
from app.llm.llm import LLMService
from app.response import ok
from .schemas import (
    LookupRequest, LookupResponse,
    ResolveRequest, ResolveResponse,
    LoincCandidate, StatsResponse,
    ReportSuggestRequest, ReportSuggestResponse, ReportSuggestCandidate,
)

router = APIRouter(prefix="/loinc", tags=["LOINC"])
logger = get_logger(__name__)
llm = LLMService()


# ------------------------------------------------------------------
# SQL
# ------------------------------------------------------------------
LOOKUP_SQL = """
    SELECT loinc_code, long_common_name,
           component, property, time_aspect, system, scale, method,
           class, example_units, relation,
           nhi_name_eng, nhi_name_cht
    FROM loinc_mapping
    WHERE nhi_code = :nhi_code
    ORDER BY
        CASE relation WHEN 'H' THEN 1 WHEN 'A' THEN 2 WHEN 'S' THEN 3
                      WHEN 'C' THEN 4 WHEN 'P' THEN 5 ELSE 6 END,
        loinc_code
"""

STATS_SQL = """
    SELECT
        COUNT(*) AS total_mappings,
        COUNT(DISTINCT nhi_code) AS total_nhi_codes,
        COUNT(DISTINCT loinc_code) AS total_loinc_codes
    FROM loinc_mapping
"""


def _row_to_candidate(row) -> LoincCandidate:
    return LoincCandidate(
        loinc_code=row[0],
        long_common_name=row[1],
        component=row[2],
        property=row[3],
        time_aspect=row[4],
        system=row[5],
        scale=row[6],
        method=row[7],
        loinc_class=row[8],
        example_units=row[9],
        relation=row[10],
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
@router.post(
    "/lookup",
    summary="查詢健保碼對應的 LOINC 候選",
    description="輸入健保碼，回傳所有候選 LOINC code（含 6 軸），依關聯程度排序",
)
async def lookup(req: LookupRequest):
    db = LOINCSession()
    try:
        rows = db.execute(text(LOOKUP_SQL), {"nhi_code": req.nhi_code}).fetchall()

        candidates = [_row_to_candidate(r) for r in rows]

        nhi_name_eng = rows[0][11] if rows else None
        nhi_name_cht = rows[0][12] if rows else None

        return ok(LookupResponse(
            nhi_code=req.nhi_code,
            nhi_name_eng=nhi_name_eng,
            nhi_name_cht=nhi_name_cht,
            total=len(candidates),
            candidates=candidates,
        ))
    except Exception as e:
        logger.error(f"LOINC lookup 失敗 nhi_code={req.nhi_code!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
    finally:
        db.close()


@router.post(
    "/resolve",
    summary="精確篩選 LOINC code",
    description="輸入健保碼 + 6 軸條件，篩選出符合的 LOINC code",
)
async def resolve(req: ResolveRequest):
    db = LOINCSession()
    try:
        conditions = ["nhi_code = :nhi_code"]
        params = {"nhi_code": req.nhi_code}

        for field, col in [
            ("component", "component"),
            ("property", "property"),
            ("time_aspect", "time_aspect"),
            ("system", "system"),
            ("scale", "scale"),
            ("method", "method"),
        ]:
            val = getattr(req, field)
            if val:
                conditions.append(f"{col} = :{field}")
                params[field] = val

        sql = f"""
            SELECT loinc_code, long_common_name,
                   component, property, time_aspect, system, scale, method,
                   class, example_units, relation,
                   nhi_name_eng, nhi_name_cht
            FROM loinc_mapping
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE relation WHEN 'H' THEN 1 WHEN 'A' THEN 2 WHEN 'S' THEN 3
                              WHEN 'C' THEN 4 WHEN 'P' THEN 5 ELSE 6 END,
                loinc_code
        """

        rows = db.execute(text(sql), params).fetchall()
        candidates = [_row_to_candidate(r) for r in rows]

        return ok(ResolveResponse(
            nhi_code=req.nhi_code,
            total=len(candidates),
            candidates=candidates,
        ))
    except Exception as e:
        logger.error(f"LOINC resolve 失敗 nhi_code={req.nhi_code!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
    finally:
        db.close()


@router.post(
    "/report-suggest",
    summary="報告文字推估 LOINC 代碼",
    description=(
        "輸入健保碼（必填）與檢驗/病理/基因報告文字，"
        "由 AI 從健保碼對應的 LOINC 候選清單中挑選最吻合的代碼並說明原因。\n\n"
        "**支援報告類型**\n"
        "- 檢驗報告：血液、尿液生化（血糖、肌酐酸、CBC 等）\n"
        "- 病理報告：組織切片、細胞學（含染色法與判讀）\n"
        "- 基因檢測報告：KRAS/NRAS/BRAF 突變分析等次世代定序結果\n\n"
        "**不適用**：純診斷印象（Clinical impression）或 ICD-10 診斷摘要\n\n"
        "候選清單由健保碼規則過濾產生，AI 只在清單內選擇，不會生成不存在的代碼。\n\n"
        "---\n\n"
        "**範例 1：僅提供健保碼與報告文字（6 大指標未知）**\n"
        "```json\n"
        "{\n"
        '  "nhi_code": "09015C",\n'
        '  "report_text": "Fasting blood glucose, venous serum, enzymatic method. Result: 5.2 mmol/L.",\n'
        '  "top_k": 3\n'
        "}\n"
        "```\n\n"
        "**範例 2：健保碼 + 報告文字 + 部分已知 6 大指標（可進一步縮小候選）**\n"
        "```json\n"
        "{\n"
        '  "nhi_code": "09015C",\n'
        '  "report_text": "Fasting blood glucose, venous serum, enzymatic method. Result: 5.2 mmol/L.",\n'
        '  "time_aspect": "Pt",\n'
        '  "system": "Ser/Plas",\n'
        '  "scale": "Qn",\n'
        '  "top_k": 3\n'
        "}\n"
        "```"
    ),
)
async def report_suggest(req: ReportSuggestRequest):
    db = LOINCSession()
    try:
        rows = db.execute(text(LOOKUP_SQL), {"nhi_code": req.nhi_code}).fetchall()
    except Exception as e:
        logger.error(f"LOINC report-suggest lookup 失敗 nhi_code={req.nhi_code!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
    finally:
        db.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"健保碼 {req.nhi_code!r} 無對應 LOINC 資料")

    nhi_name_eng = rows[0][11]
    nhi_name_cht = rows[0][12]
    all_candidates = [_row_to_candidate(r).model_dump() for r in rows]

    # 6 大指標若有提供，先做規則過濾縮小 LLM 評估範圍
    axis_filters = {
        "component":   req.component,
        "property":    req.property,
        "time_aspect": req.time_aspect,
        "system":      req.system,
        "scale":       req.scale,
        "method":      req.method,
    }
    candidates = [
        c for c in all_candidates
        if all(
            c.get(k) == v
            for k, v in axis_filters.items()
            if v is not None
        )
    ] or all_candidates  # 若過濾後為空（指標不匹配），退回全部候選

    logger.info(
        f"LOINC report-suggest | nhi={req.nhi_code} "
        f"total={len(all_candidates)} after_filter={len(candidates)}"
    )

    ranked = llm.rank_loinc_candidates_from_report(
        report_text=req.report_text,
        candidates=candidates,
        nhi_name=nhi_name_cht or nhi_name_eng or "",
    )

    results = [
        ReportSuggestCandidate(
            rank=item["rank"],
            loinc_code=item["loinc_code"],
            long_common_name=item.get("long_common_name"),
            component=item.get("component"),
            property=item.get("property"),
            time_aspect=item.get("time_aspect"),
            system=item.get("system"),
            scale=item.get("scale"),
            method=item.get("method"),
            relation=item.get("relation"),
            confidence_pct=item["confidence_pct"],
            reason=item.get("reason", ""),
        )
        for item in ranked[: req.top_k]
    ]

    return ok(ReportSuggestResponse(
        nhi_code=req.nhi_code,
        nhi_name_eng=nhi_name_eng,
        nhi_name_cht=nhi_name_cht,
        total_candidates_evaluated=len(candidates),
        results=results,
    ))


@router.get(
    "/stats",
    summary="LOINC 知識庫統計",
)
async def stats():
    db = LOINCSession()
    try:
        row = db.execute(text(STATS_SQL)).fetchone()
        return ok(StatsResponse(
            total_mappings=row[0],
            total_nhi_codes=row[1],
            total_loinc_codes=row[2],
        ))
    except Exception as e:
        logger.error(f"LOINC stats 失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
    finally:
        db.close()
