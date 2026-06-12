from pydantic import BaseModel

class AgentQueryRequest(BaseModel):
    query: str
    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "病患因急性胸痛就診，心電圖顯示 ST 上升，安排緊急心導管手術，術後抽血追蹤心肌酵素"
            }
        }
    }
