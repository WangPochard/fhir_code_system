from pydantic import BaseModel

class AgentQueryRequest(BaseModel):
    query: str
    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "第二型糖尿病合併高血壓，需要 SNOMED CT 代碼和相關血糖 LOINC 代碼"
            }
        }
    }