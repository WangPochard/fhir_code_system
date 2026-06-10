from pydantic import BaseModel, Field
from typing import List, Optional


class SearchRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "Malignant neoplasm of transverse colon",
                "top_k": 5,
                "threshold": 0.0,
            }
        }
    }

    query: str = Field(..., description="臨床術語、診斷名稱、解剖部位或藥品成分")
    top_k: int = Field(5, ge=1, le=50, description="回傳筆數")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="最低相似度門檻")


class CandidateResponse(BaseModel):
    rank: int
    concept_id: str
    fsn: Optional[str] = None
    semantic_tag: Optional[str] = None
    icd10_codes: List[str] = []
    term: Optional[str] = None
    similarity: float


class SearchResponse(BaseModel):
    query: str
    total: int
    embedding_model: str
    candidates: List[CandidateResponse]


# ------------------------------------------------------------------
# /identify 回應結構
# ------------------------------------------------------------------
class IdentifyCandidate(BaseModel):
    rank: int
    concept_id: str
    fsn: Optional[str] = None
    confidence_pct: float = Field(..., description="AI 信心分數（0–100）")


class IdentifiedConcept(BaseModel):
    term: str = Field(..., description="從臨床文字萃取出的術語")
    reason: str = Field("", description="第一順位判斷說明（繁體中文）")
    candidates: List[IdentifyCandidate]


class IdentifyRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "clinical_text": "T-colon ca for surgical consultation",
                "icd10_cm_code": "C18.4",
                "icd10_cm_label": "Malignant neoplasm of transverse colon",
                "field_type": "diagnosis",
                "top_k": 3,
                "threshold": 0.0,
            }
        }
    }

    clinical_text: str = Field(..., description=(
        "【必填】臨床自由文字（中英文皆可）\n"
        "適用：主訴、臨床印象、短篇臨床摘要\n"
        "長文本亦支援（自動切 term），但長篇影像或檢驗報告建議改用 ICD-10-PCS / LOINC API"
    ))
    icd10_cm_code: Optional[str] = Field(None, description="【選填】ICD-10-CM 診斷碼，有助提升 SNOMED 對照準確度")
    icd10_cm_label: Optional[str] = Field(None, description="【選填】ICD-10-CM 診斷說明")
    field_type: Optional[str] = Field(None, description=(
        "【選填】欄位類型，依畫面上的欄位傳入對應值，後端自動縮小搜尋範圍。不填則全庫搜尋。\n"
        "- `diagnosis`：臨床診斷／疾病／症狀\n"
        "- `medication_route`：用藥途徑（口服、靜脈注射...）\n"
        "- `department`：醫療科別／專科\n"
        "- `profession`：醫事人員職類（醫師、護理師...）"
    ))
    top_k: int = Field(3, ge=1, le=10, description="【選填】每個術語回傳的候選數，預設 3")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="【選填】向量相似度門檻，預設 0.0")


class IdentifyResponse(BaseModel):
    clinical_text: str
    identified_concepts: List[IdentifiedConcept]


class StatsResponse(BaseModel):
    total_documents: int
    total_embeddings: int
    unique_sources: int
    embedding_model: str
