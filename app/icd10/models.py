from sqlalchemy import Column, Integer, BigInteger, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone

from app.database import ICD10Base
from app.config import get_settings

settings = get_settings()


class CodesPCS(ICD10Base):
    __tablename__ = "codespcs"

    code = Column(Text, primary_key=True)
    section = Column(Text)
    bodysystem = Column(Text)
    operation = Column(Text)
    bodypart = Column(Text)
    approach = Column(Text)
    device = Column(Text)
    qualifer = Column(Text)
    longdes = Column(Text)
    chtdes1 = Column(Text)
    chtdes2 = Column(Text)
    chtdes3 = Column(Text)

    embeddings = relationship("EmbeddingPCSCodes", back_populates="icd", cascade="all, delete-orphan")


class CodesPCSHospital(ICD10Base):
    """住院碼"""
    __tablename__ = "codespcs_hospital"

    id = Column("識別碼", BigInteger, primary_key=True)
    code = Column(Text)
    dept = Column(Text)
    sub_dept = Column(Text)
    eng = Column(Text)
    engdes = Column(Text)


class CodesPCSOutpatient(ICD10Base):
    """門診碼"""
    __tablename__ = "codespcs_outpatient"

    id = Column("識別碼", BigInteger, primary_key=True)
    code = Column(Text)
    dept = Column(Text)
    sub_dept = Column(Text)
    icd9 = Column(Text)
    eng = Column(Text)


class EmbeddingPCSCodes(ICD10Base):
    __tablename__ = "embed_icd10"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(Text, ForeignKey("codespcs.code", ondelete="CASCADE"), nullable=False)
    term_eng = Column(Text, comment="英文原始文字")
    term_cht = Column(Text, comment="中文原始文字")
    embedding_eng = Column(Vector(settings.embedding_dimension), comment="英文向量")
    embedding_cht = Column(Vector(settings.embedding_dimension), comment="中文向量")
    valueset = Column(Text, nullable=True, comment="值集分類：imaging / radiotherapy / NULL")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    icd = relationship("CodesPCS", back_populates="embeddings")
