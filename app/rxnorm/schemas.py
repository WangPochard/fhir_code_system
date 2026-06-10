from pydantic import BaseModel, Field
from typing import List, Optional


class RxNormMatchResponse(BaseModel):
    rxcui: str
    name: str
    tty: str
    source: str = ""


class LookupRequest(BaseModel):
    ingredient_name: str = Field(..., description="藥品成分名稱", examples=["metformin"])
    drug_code: str = Field("", description="健保藥品代碼（選填）")


class LookupResponse(BaseModel):
    drug_code: str
    ingredient_name: str
    query_used: str
    best_match: Optional[RxNormMatchResponse] = None
    matches: List[RxNormMatchResponse]
    error: str = ""


class ApproximateRequest(BaseModel):
    term: str = Field(..., description="藥品名稱或成分", examples=["aspirin 500mg tablet"])
    max_entries: int = Field(5, ge=1, le=20, description="最多回傳筆數")


class DrugSearchRequest(BaseModel):
    ingredient: str = Field(..., description="成分名稱", examples=["ethinyl estradiol"])


class RelatedRequest(BaseModel):
    rxcui: str = Field(..., description="RxCUI 代碼", examples=["748865"])


class RelatedResponse(BaseModel):
    rxcui: str
    groups: dict[str, List[RxNormMatchResponse]]


class PropertiesResponse(BaseModel):
    rxcui: str
    properties: dict


class SpellingSuggestionsResponse(BaseModel):
    name: str
    suggestions: List[str]
