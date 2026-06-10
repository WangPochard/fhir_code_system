# build_vectorstore.py
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.database import get_snomed_context, SnomedSession
from app.snomed.models import Document, DocumentEmbedding
from app.config import get_settings
from app.logger import logger
from app.embedding import EmbeddingService

data_path = os.path.abspath(os.path.join(os.getcwd(), "data", "rag_document_v3"))
all_files = sorted(Path(data_path).glob("**/*.txt"))

settings = get_settings()
rag_service = EmbeddingService(model_name=settings.sentence_transformer_model)
total = len(all_files)

db = SnomedSession()
rows = db.execute(text("SELECT DISTINCT snomed_concept_id FROM documents")).fetchall()
existing_ids = {row[0] for row in rows}
db.close()
logger.info(f"DB 已有 {len(existing_ids):,} 筆，將跳過")


def parse_concept_file(raw: str):
    """解析 .txt，回傳 (fsn, synonyms, snomed_id, semantic_tag, icd10_codes)"""
    content = raw[:raw.find("[")].strip() if "[" in raw else raw.strip()

    # content 是 "FSN. 同義詞1. 同義詞2. ..." 用句號切開
    parts = [p.strip() for p in content.split(".") if p.strip()]
    fsn = parts[0] if parts else None
    synonyms = parts[1:] if len(parts) > 1 else []

    snomed_id = None
    semantic_tag = None
    icd10_codes = []

    m = re.search(r"\[SNOMED:(\d+)\]", raw)
    if m:
        snomed_id = m.group(1)

    m = re.search(r"\[Type:([^\]]+)\]", raw)
    if m:
        semantic_tag = m.group(1).strip()

    m = re.search(r"\[ICD10:([^\]]+)\]", raw)
    if m:
        icd10_codes = [c.strip() for c in m.group(1).split(",") if c.strip()]

    return fsn, synonyms, snomed_id, semantic_tag, icd10_codes


for i, filepath in enumerate(all_files, 1):
    raw = filepath.read_text(encoding="utf-8").strip()
    if not raw:
        continue

    fsn, synonyms, snomed_id, semantic_tag, icd10_codes = parse_concept_file(raw)

    if not fsn or not snomed_id:
        logger.warning(f"⚠️ 跳過（缺 fsn 或 snomed_id）: {filepath.name}")
        continue

    if snomed_id in existing_ids:
        continue

    source = str(filepath.relative_to(Path.cwd()))

    # 要 embed 的詞：FSN + 所有同義詞
    terms = [fsn] + synonyms

    vectors = rag_service.embed_documents(terms, batch_size=100)

    with get_snomed_context() as db:
        doc = Document(
            snomed_concept_id=snomed_id,
            fsn=fsn,
            synonyms=synonyms if synonyms else None,
            semantic_tag=semantic_tag,
            icd10_codes=icd10_codes if icd10_codes else None,
            source=source,
        )
        db.add(doc)
        db.flush()

        for term, vector in zip(terms, vectors):
            emb = DocumentEmbedding(
                document_id=doc.id,
                term=term,
                embedding=vector,
            )
            db.add(emb)

    if i % 1000 == 0:
        logger.info(f"   進度: {i:,}/{total:,} ({i/total*100:.1f}%)")

logger.info("✅ 完成")