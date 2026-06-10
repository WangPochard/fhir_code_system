"""
建立 ICD-10 PCS 中文 embedding
- 讀取 embed_icd10 中 embedding_cht = NULL 的資料
- 用 sentence-transformers/paraphrase-multilingual-mpnet-base-v2 對 term_cht 轉向量
- 更新 embedding_cht 欄位
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.logger import logger
from app.config import get_settings
from app.database import ICD10Session
from app.icd10.models import EmbeddingPCSCodes
from app.embedding import EmbeddingService

settings = get_settings()
BATCH_SIZE = 200



def build_chinese_embeddings():
    # 使用多語言模型
    embed_service = EmbeddingService(model_name="sentence"
    "-transformers/paraphrase-multilingual-mpnet-base-v2")
    db = ICD10Session()

    try:
        # 統計需要處理的筆數
        need_update = db.query(EmbeddingPCSCodes).filter(
            EmbeddingPCSCodes.embedding_cht.is_(None),
            EmbeddingPCSCodes.term_cht.isnot(None),
            EmbeddingPCSCodes.term_cht != ""
        ).count()

        logger.info(f"需要建立中文向量的資料: {need_update:,} 筆")

        if need_update == 0:
            logger.info("沒有需要處理的資料")
            return

        processed = 0

        while True:
            # 分批查詢需要更新的資料
            rows = (
                db.query(EmbeddingPCSCodes)
                .filter(
                    EmbeddingPCSCodes.embedding_cht.is_(None),
                    EmbeddingPCSCodes.term_cht.isnot(None),
                    EmbeddingPCSCodes.term_cht != ""
                )
                .order_by(EmbeddingPCSCodes.id)
                .limit(BATCH_SIZE)
                .all()
            )

            if not rows:
                break

            # 準備中文文字清單
            cht_texts = [row.term_cht for row in rows]
            
            # 批次轉向量
            logger.info(f"正在處理第 {processed+1}-{processed+len(rows)} 筆...")
            cht_vectors = embed_service.embed_documents(cht_texts, batch_size=128)

            # 逐筆更新
            for row, vector in zip(rows, cht_vectors):
                row.embedding_cht = vector

            db.commit()
            processed += len(rows)
            
            logger.info(f"✅ 已處理 {processed:,}/{need_update:,} 筆 ({processed/need_update*100:.1f}%)")

        logger.info(f"🎉 完成！共更新 {processed:,} 筆中文向量")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ 處理失敗: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    build_chinese_embeddings()