from pydantic import BaseModel, Field
from typing import List, Optional


class LookupRequest(BaseModel):
    nhi_code: str = Field(..., description="健保碼", examples=["09015C"])


class LoincCandidate(BaseModel):
    loinc_code: str
    long_common_name: Optional[str] = None
    component: Optional[str] = None
    property: Optional[str] = None
    time_aspect: Optional[str] = None
    system: Optional[str] = None
    scale: Optional[str] = None
    method: Optional[str] = None
    loinc_class: Optional[str] = None
    example_units: Optional[str] = None
    relation: Optional[str] = Field(None, description="關聯程度 (H/A/S/C/P/L/U)")


class LookupResponse(BaseModel):
    nhi_code: str
    nhi_name_eng: Optional[str] = None
    nhi_name_cht: Optional[str] = None
    total: int
    candidates: List[LoincCandidate]


class ResolveRequest(BaseModel):
    nhi_code: str = Field(..., description="健保碼", examples=["09015C"])
    component: Optional[str] = Field(None, description="軸1: 分析物")
    property: Optional[str] = Field(None, description="軸2: 量測性質")
    time_aspect: Optional[str] = Field(None, description="軸3: 時間")
    system: Optional[str] = Field(None, description="軸4: 檢體來源")
    scale: Optional[str] = Field(None, description="軸5: 尺度")
    method: Optional[str] = Field(None, description="軸6: 方法")


class ResolveResponse(BaseModel):
    nhi_code: str
    total: int
    candidates: List[LoincCandidate]


class StatsResponse(BaseModel):
    total_mappings: int
    total_nhi_codes: int
    total_loinc_codes: int


# ------------------------------------------------------------------
# 報告文字推估 LOINC
# ------------------------------------------------------------------
class ReportSuggestRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "nhi_code": "09015C",
                "report_text": (
                    "Fasting blood glucose measurement. Specimen: venous serum. "
                    "Method: enzymatic (hexokinase). Result: 5.2 mmol/L. "
                    "Reference range: 3.9–6.1 mmol/L. Interpretation: normal."
                ),
                "top_k": 3,
            }
        }
    }

    nhi_code: str = Field(..., description="健保碼（必填，用於縮小候選範圍）")
    report_text: str = Field(..., description=(
        "檢驗／病理／基因報告原文（中英文皆可）\n"
        "支援：檢驗報告、病理報告、基因檢測報告\n"
        "不適用：純診斷印象或 ICD-10 診斷摘要"
    ))
    component: Optional[str] = Field(None, description="軸1 分析物（選填），例如 Glucose")
    property: Optional[str] = Field(None, description="軸2 量測性質（選填），例如 MCnc / ACnc")
    time_aspect: Optional[str] = Field(None, description="軸3 時間（選填），例如 Pt / 24H")
    system: Optional[str] = Field(None, description="軸4 檢體來源（選填），例如 Ser/Plas / Urine / Tiss")
    scale: Optional[str] = Field(None, description="軸5 尺度（選填），例如 Qn / Ord / Nom")
    method: Optional[str] = Field(None, description="軸6 方法（選填），例如 Enzymatic / PCR")
    top_k: int = Field(3, ge=1, le=10, description="回傳候選數（預設 3）")


class ReportSuggestCandidate(BaseModel):
    rank: int
    loinc_code: str
    long_common_name: Optional[str] = None
    component: Optional[str] = None
    property: Optional[str] = None
    time_aspect: Optional[str] = None
    system: Optional[str] = None
    scale: Optional[str] = None
    method: Optional[str] = None
    relation: Optional[str] = Field(None, description="關聯程度 (H/A/S/C/P/L/U)")
    confidence_pct: float = Field(..., description="AI 信心分數（0–100，各候選獨立評估）")
    reason: str = Field("", description="對應說明（繁體中文）")


class ReportSuggestResponse(BaseModel):
    nhi_code: str
    nhi_name_eng: Optional[str] = None
    nhi_name_cht: Optional[str] = None
    total_candidates_evaluated: int = Field(..., description="健保碼對應的候選總數（LLM 評估範圍）")
    results: List[ReportSuggestCandidate]
