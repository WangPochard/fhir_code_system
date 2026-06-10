from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, ARRAY
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone

from app.database import SnomedBase
from app.config import get_settings

settings = get_settings()


class Document(SnomedBase):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snomed_concept_id = Column(String(20), nullable=False, comment="SNOMED CT Code")
    fsn = Column(Text, nullable=False, comment="Full Specified Name")
    synonyms = Column(ARRAY(String), comment="同義詞列表")
    semantic_tag = Column(String(100), comment="finding/disorder/procedure...")
    icd10_codes = Column(ARRAY(String), comment="ICD-10 編碼列表")
    source = Column(String(500), comment="來源檔案路徑")
    tw_valueset = Column(String(50), nullable=True, comment="台灣 TWCore 值集分類：condition / medical-department / medication-path / health-professional")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    embeddings = relationship("DocumentEmbedding", back_populates="document", cascade="all, delete-orphan")


class DocumentEmbedding(SnomedBase):
    __tablename__ = "document_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    term = Column(Text, nullable=False, comment="這筆向量對應的詞")
    embedding = Column(Vector(settings.embedding_dimension), comment="向量")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    document = relationship("Document", back_populates="embeddings")
