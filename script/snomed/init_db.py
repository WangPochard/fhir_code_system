"""
初始化資料庫
- 啟用 pgvector extension
- 建立所有表格
- 建立向量索引
"""

import sys
from pathlib import Path
import logging

# 加入專案路徑
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.database import snomed_engine, SnomedBase
from app.snomed.models import Document, DocumentEmbedding
from app.config import get_settings
from app.logger import logger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
settings = get_settings()

def init_database():
    """初始化資料庫"""
    
    logger.info("=" * 60)
    logger.info("開始初始化資料庫")
    logger.info("=" * 60)
    
    try:
        # 1. 測試連線
        logger.info("📡 測試資料庫連線...")
        with snomed_engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            logger.info(f"✅ PostgreSQL 版本: {version[:50]}...")
        
        # 2. 啟用 pgvector
        logger.info("🔧 啟用 pgvector extension...")
        with snomed_engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            logger.info("✅ pgvector extension 已啟用")
        
        # 3. 建立表格
        logger.info("📊 建立資料表...")
        SnomedBase.metadata.create_all(bind=snomed_engine)
        logger.info("✅ 資料表建立完成:")
        for table in SnomedBase.metadata.sorted_tables:
            logger.info(f"   - {table.name}")
        
        # 4. 建立向量索引
        logger.info("🔍 建立向量索引...")
        with snomed_engine.connect() as conn:
            # 檢查索引是否存在
            result = conn.execute(text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'document_embeddings'
                  AND indexname = 'embeddings_hnsw_idx'
            """))
            
            if not result.fetchone():
                conn.execute(text("""
                    CREATE INDEX embeddings_hnsw_idx 
                    ON document_embeddings 
                    USING hnsw (embedding vector_cosine_ops)
                """))
                conn.commit()
                logger.info("✅ 向量索引已建立 (HNSW)")
            else:
                logger.info("ℹ️  向量索引已存在")
        
        # 5. 顯示配置
        logger.info("\n" + "=" * 60)
        logger.info("📋 當前配置:")
        logger.info(f"   Database: {settings.postgres_db}")
        logger.info(f"   Ollama: {settings.ollama_base_url}")
        logger.info(f"   Embedding: {settings.embedding_model}")
        logger.info(f"   Dimension: {settings.embedding_dimension}")
        logger.info(f"   LLM: {settings.ollama_model}")
        logger.info("=" * 60)
        
        logger.info("\n✅ 資料庫初始化完成!")
        return True
        
    except Exception as e:
        logger.error(f"\n❌ 初始化失敗: {e}")
        return False

def drop_all_tables():
    """刪除所有表格 (危險操作!)"""
    logger.warning("⚠️  準備刪除所有表格...")
    confirm = input("確定要刪除所有表格嗎? (yes/no): ")
    
    if confirm.lower() == 'yes':
        SnomedBase.metadata.drop_all(bind=snomed_engine)
        logger.info("✅ 所有表格已刪除")
    else:
        logger.info("❌ 取消操作")

if __name__ == "__main__":
    init_database()