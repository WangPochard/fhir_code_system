"""
初始化資料庫
- 啟用 pgvector extension
- 建立 embed_icd10 表格（不動既有表）
- 建立向量索引 (HNSW)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.database import icd10_engine, ICD10Base
from app.icd10.models import EmbeddingPCSCodes
from app.config import get_settings
from app.logger import logger

settings = get_settings()


def init_database():
    """初始化資料庫：建立 embedding 表及向量索引"""

    logger.info("=" * 60)
    logger.info("開始初始化資料庫")
    logger.info("=" * 60)

    try:
        # 1. 測試連線
        logger.info("測試資料庫連線...")
        with icd10_engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).fetchone()[0]
            logger.info(f"PostgreSQL: {version[:60]}...")

        # 2. 啟用 pgvector
        logger.info("啟用 pgvector extension...")
        with icd10_engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            logger.info("pgvector extension 已啟用")

        # 3. 確保 codespcs.code 有 unique constraint（FK 需要）
        logger.info("檢查 codespcs.code unique constraint...")
        with icd10_engine.connect() as conn:
            result = conn.execute(text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'codespcs'::regclass AND contype IN ('p','u') "
                "AND conkey = ARRAY[(SELECT attnum FROM pg_attribute "
                "WHERE attrelid = 'codespcs'::regclass AND attname = 'code')]"
            ))
            if not result.fetchone():
                conn.execute(text(
                    "ALTER TABLE codespcs ADD CONSTRAINT codespcs_code_unique UNIQUE (code)"
                ))
                conn.commit()
                logger.info("  已加上 codespcs_code_unique constraint")
            else:
                logger.info("  constraint 已存在，跳過")

        # 4. 建立 embed_icd10 表
        logger.info("建立 embed_icd10 表...")
        EmbeddingPCSCodes.__table__.create(bind=icd10_engine, checkfirst=True)
        logger.info("embed_icd10 表建立完成")

        # 4. 建立向量索引 (HNSW)
        logger.info("建立向量索引...")
        with icd10_engine.connect() as conn:
            for col, idx_name in [
                ("embedding_eng", "embed_icd10_eng_hnsw_idx"),
                ("embedding_cht", "embed_icd10_cht_hnsw_idx"),
            ]:
                result = conn.execute(text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE tablename = 'embed_icd10' AND indexname = :idx"
                ), {"idx": idx_name})

                if not result.fetchone():
                    conn.execute(text(
                        f"CREATE INDEX {idx_name} "
                        f"ON embed_icd10 "
                        f"USING hnsw ({col} vector_cosine_ops)"
                    ))
                    logger.info(f"  索引 {idx_name} 已建立")
                else:
                    logger.info(f"  索引 {idx_name} 已存在，跳過")

            conn.commit()

        # 5. 顯示配置
        logger.info("=" * 60)
        logger.info("當前配置:")
        logger.info(f"  Database: {settings.icd10_db}")
        logger.info(f"  Embedding model: {settings.sentence_transformer_model}")
        logger.info(f"  Embedding dimension: {settings.embedding_dimension}")
        logger.info("=" * 60)
        logger.info("資料庫初始化完成!")
        return True

    except Exception as e:
        logger.error(f"初始化失敗: {e}")
        return False


def drop_embedding_table():
    """刪除 embed_icd10 表（危險操作）"""
    confirm = input("確定要刪除 embed_icd10 表嗎? (yes/no): ")
    if confirm.lower() == "yes":
        EmbeddingPCSCodes.__table__.drop(bind=icd10_engine, checkfirst=True)
        logger.info("embed_icd10 表已刪除")
    else:
        logger.info("取消操作")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--drop":
        drop_embedding_table()
    else:
        init_database()
