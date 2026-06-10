from sqlalchemy import Column, Integer, String, Text, Boolean
from sqlalchemy.dialects.postgresql import TIMESTAMPTZ
from sqlalchemy.sql import func

from app.database import SnomedBase


class PromptTemplate(SnomedBase):
    __tablename__ = "prompt_templates"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    content     = Column(Text, nullable=False)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(TIMESTAMPTZ, server_default=func.now(), nullable=False)
    updated_at  = Column(TIMESTAMPTZ, server_default=func.now(), onupdate=func.now(), nullable=False)
