from pydantic import BaseModel, Field
from typing import List, Optional


class StatsResponse(BaseModel):
    total_codes: int
    total_embeddings: int
    embedding_model: str


class CandidateResponse(BaseModel):
    rank: int
    code: str
    term_eng: Optional[str] = None
    term_cht: Optional[str] = None
    confidence_pct: float = Field(..., description="AI 信心分數（最高分 = top_k × 5，rank 1 滿分，等差遞減）")
    reason: str = Field("", description="此代碼與報告的對應說明（繁體中文）")
    imaging_modality: Optional[str] = Field(None, description="成像模態（影像類代碼專用）")
    imaging_body_part: Optional[str] = Field(None, description="掃描部位（影像類代碼專用）")


# ------------------------------------------------------------------
# PCS 建議（單份報告）
# ------------------------------------------------------------------
class PcsSuggestRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "report_text": (
                    "右肺上葉穿刺切片，穿刺位點右上葉後段，"
                    "組織學診斷為非小細胞肺癌（腺癌），"
                    "PD-L1（22C3）TPS = 75%。免疫組化在穿刺切片組織上執行。"
                ),
                "cm_code": "C34.12",
                "cm_label": "右上葉惡性腫瘤",
                "top_k": 5,
                "threshold": 0.0,
            }
        }
    }

    report_text: str = Field(..., description="診斷報告文字（findings / impression / 病理描述）")
    cm_code: Optional[str] = Field(None, description="ICD-10-CM 診斷碼（來自 HIS，有助縮小搜尋範圍）")
    cm_label: Optional[str] = Field(None, description="CM 診斷碼說明（中英文皆可）")
    top_k: int = Field(5, ge=1, le=20, description="回傳候選代碼數")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="向量相似度門檻")


class PcsExtractedFacts(BaseModel):
    modality: str = Field(..., description="影像模態或程序類型")
    body_part: str = Field(..., description="解剖部位")
    laterality: str = Field(..., description="側性")
    approach: str = Field(..., description="執行路徑")
    contrast: str = Field(..., description="對比劑使用")
    procedure_description: str = Field(..., description="組合後的程序描述字串（用於 RAG 查詢）")
    extraction_confidence: str = Field(..., description="萃取信心 (high / medium / low)")
    not_found_reason: str = Field("", description="若 confidence 為 low，說明原因")
    pcs_section: str = Field(..., description="ICD-10-PCS Section（0/B/C/D/3/unknown）")
    pcs_body_system: str = Field(..., description="ICD-10-PCS Body System 代碼（單一字元或 unknown）")
    pcs_body_system_confidence: str = Field(..., description="Body system 判斷信心 (high / medium / low)")
    pcs_body_system_reason: str = Field("", description="Body system 判斷依據")


class PcsSuggestResponse(BaseModel):
    candidates: List[CandidateResponse] = Field(..., description="PCS 候選代碼清單（含中文說明）")
    top_recommendation_summary: str = Field("", description="Rank 1 優先推薦的整體說明")
    degradation_decision: str = Field(..., description="use_pcs | loinc_fallback | needs_human_review")
    degradation_reason: str = Field(..., description="降級決策說明")


# ------------------------------------------------------------------
# PCS 建議（批次）
# ------------------------------------------------------------------
class PcsBatchItem(BaseModel):
    report_text: str = Field(..., description="診斷報告文字")
    cm_code: Optional[str] = Field(None, description="ICD-10-CM 診斷碼")
    cm_label: Optional[str] = Field(None, description="CM 診斷碼說明")


class PcsBatchRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "report_text": "右肺上葉穿刺切片，組織學診斷為腺癌，PD-L1 TPS = 75%。",
                        "cm_code": "C34.12",
                        "cm_label": "右上葉惡性腫瘤",
                    },
                    {
                        "report_text": "CT scan of the abdomen with contrast. Liver mass 3.2 cm in segment VI.",
                        "cm_code": "C22.0",
                        "cm_label": "肝細胞癌",
                    },
                ],
                "top_k": 5,
                "threshold": 0.0,
            }
        }
    }

    items: List[PcsBatchItem] = Field(..., min_length=1, max_length=20, description="報告清單（最多 20 筆）")
    top_k: int = Field(5, ge=1, le=20, description="每筆回傳候選代碼數")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="向量相似度門檻")


class PcsBatchResultItem(BaseModel):
    index: int = Field(..., description="對應輸入的索引（0-based）")
    report_text_preview: str = Field(..., description="報告文字前 80 字（供對照識別）")
    result: Optional[PcsSuggestResponse] = Field(None, description="PCS 建議結果")
    error: Optional[str] = Field(None, description="若該筆失敗，說明原因")


class PcsBatchResponse(BaseModel):
    total: int = Field(..., description="送入的總筆數")
    success: int = Field(..., description="成功筆數")
    failed: int = Field(..., description="失敗筆數")
    results: List[PcsBatchResultItem]
