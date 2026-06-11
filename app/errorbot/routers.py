from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from app.response import ok
from .service import ValidationExplainService

router = APIRouter()

_service: Optional[ValidationExplainService] = None

_OUTCOME_EXAMPLE = {
    "resourceType": "OperationOutcome",
    "issue": [
        {
            "severity": "error",
            "code": "processing",
            "diagnostics": "Constraint failed: ele-1: 'All FHIR elements must have a @value or children'",
            "location": ["Composition.category[0].coding[0].code", "Line[22] Col[18]"],
            "expression": ["Composition.category[0].coding[0].code"],
        }
    ],
}


def get_service() -> ValidationExplainService:
    global _service
    if _service is None:
        _service = ValidationExplainService()
    return _service


class ExplainResponse(BaseModel):
    summary: str
    total_errors: int
    total_warnings: int
    issues: List[Dict[str, Any]]


@router.post("/explain")
def explain_validation_result(
    outcome: Dict[str, Any] = Body(..., example=_OUTCOME_EXAMPLE),
    include_warning: bool = Query(False, description="是否包含 warning（預設只回傳 error）"),
    include_cascading: bool = Query(True, description="是否包含連帶錯誤（預設顯示，設 false 只回傳根因錯誤）"),
):
    """
    將 FHIR OperationOutcome 驗證結果翻譯成醫療人員看得懂的中文說明。

    直接傳入 OperationOutcome JSON body 即可。
    - ?include_warning=true：包含 warning
    - ?include_cascading=false：只顯示根因錯誤，隱藏連帶錯誤
    """
    if outcome.get("resourceType") != "OperationOutcome":
        raise HTTPException(status_code=400, detail="請傳入有效的 OperationOutcome 資源")

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

    return ok(ExplainResponse(
        summary=result["summary"],
        total_errors=result["total_errors"],
        total_warnings=result["total_warnings"],
        issues=issues,
    ), "說明完成")
