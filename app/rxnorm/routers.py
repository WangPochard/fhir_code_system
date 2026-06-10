from fastapi import APIRouter, HTTPException
from typing import List
from app.logger import get_logger
from app.response import ok

from .rxnorm_service import RxNormService
from .schemas import (
    RxNormMatchResponse,
    LookupRequest, LookupResponse,
    ApproximateRequest, DrugSearchRequest,
    RelatedRequest, RelatedResponse,
    PropertiesResponse, SpellingSuggestionsResponse,
)

router = APIRouter(prefix="/rxnorm", tags=["RxNorm"])
logger = get_logger(__name__)

service = RxNormService(delay=0.1)


def _to_match_response(m) -> RxNormMatchResponse:
    return RxNormMatchResponse(rxcui=m.rxcui, name=m.name, tty=m.tty, source=m.source)


# ------------------------------------------------------------------
# 整合查詢（最常用）
# ------------------------------------------------------------------
@router.post(
    "/lookup",
    summary="藥品成分查 RxCUI",
    description="依序嘗試 getDrugs → approximateMatch → findRxcuiByString，回傳最佳匹配",
)
async def lookup(req: LookupRequest):
    try:
        result = service.lookup(
            ingredient_name=req.ingredient_name,
            drug_code=req.drug_code,
        )
        if result.error:
            logger.error(f"RxNorm lookup 錯誤 ingredient={req.ingredient_name!r}: {result.error}")
        best = None
        if result.best_match:
            best = _to_match_response(result.best_match)

        return ok(LookupResponse(
            drug_code=result.drug_code,
            ingredient_name=result.ingredient_name,
            query_used=result.query_used,
            best_match=best,
            matches=[_to_match_response(m) for m in result.matches],
            error=result.error,
        ))
    except Exception as e:
        logger.error(f"RxNorm lookup 失敗 ingredient={req.ingredient_name!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# 模糊比對
# ------------------------------------------------------------------
@router.post(
    "/approximate",
    summary="模糊比對藥名",
    description="容錯度高，拼寫不完全也能找到",
)
async def approximate_match(req: ApproximateRequest):
    try:
        matches = service.approximate_match(term=req.term, max_entries=req.max_entries)
        return ok([_to_match_response(m) for m in matches])
    except Exception as e:
        logger.error(f"RxNorm approximate_match 失敗 term={req.term!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# 成分查藥品
# ------------------------------------------------------------------
@router.post(
    "/drugs",
    summary="用成分名查所有相關藥品",
    description="列出該成分的所有劑量、劑型藥品",
)
async def get_drugs(req: DrugSearchRequest):
    try:
        matches = service.get_drugs(ingredient=req.ingredient)
        return ok([_to_match_response(m) for m in matches])
    except Exception as e:
        logger.error(f"RxNorm get_drugs 失敗 ingredient={req.ingredient!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# 完整關係樹
# ------------------------------------------------------------------
@router.post(
    "/related",
    summary="查 RxCUI 完整關係樹",
    description="一次拿到成分、劑型、品牌等所有關聯概念",
)
async def get_all_related(req: RelatedRequest):
    try:
        related = service.get_all_related(rxcui=req.rxcui)
        groups = {
            tty: [_to_match_response(m) for m in concepts]
            for tty, concepts in related.groups.items()
        }
        return ok(RelatedResponse(rxcui=related.rxcui, groups=groups))
    except Exception as e:
        logger.error(f"RxNorm get_all_related 失敗 rxcui={req.rxcui!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# 屬性查詢
# ------------------------------------------------------------------
@router.get(
    "/{rxcui}/properties",
    summary="查 RxCUI 基本屬性",
)
async def get_properties(rxcui: str):
    try:
        props = service.get_properties(rxcui)
        return ok(PropertiesResponse(rxcui=rxcui, properties=props))
    except Exception as e:
        logger.error(f"RxNorm get_properties 失敗 rxcui={rxcui!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


@router.get(
    "/{rxcui}/all-properties",
    summary="查 RxCUI 所有屬性（含 ATC、SNOMED 跨碼）",
)
async def get_all_properties(rxcui: str):
    try:
        return ok({"rxcui": rxcui, "properties": service.get_all_properties(rxcui)})
    except Exception as e:
        logger.error(f"RxNorm get_all_properties 失敗 rxcui={rxcui!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# NDC
# ------------------------------------------------------------------
@router.get(
    "/{rxcui}/ndcs",
    summary="用 RxCUI 查 NDC 碼",
)
async def get_ndcs(rxcui: str):
    try:
        return ok({"rxcui": rxcui, "ndcs": service.get_ndcs(rxcui)})
    except Exception as e:
        logger.error(f"RxNorm get_ndcs 失敗 rxcui={rxcui!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")


# ------------------------------------------------------------------
# 拼字建議
# ------------------------------------------------------------------
@router.get(
    "/spelling/{name}",
    summary="藥名拼字建議",
)
async def spelling_suggestions(name: str):
    try:
        suggestions = service.get_spelling_suggestions(name)
        return ok(SpellingSuggestionsResponse(name=name, suggestions=suggestions))
    except Exception as e:
        logger.error(f"RxNorm spelling_suggestions 失敗 name={name!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查詢失敗，請稍後再試")
