"""
更新 ICD-10 PCS 英文 embedding
- 用 all-mpnet-base-v2（純英文最強）重建 embedding_eng
- 覆蓋既有的英文向量
- 分批處理，斷點續跑安全
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.logger import logger
from app.config import get_settings
from app.database import ICD10Session
from app.icd10.models import CodesPCS, EmbeddingPCSCodes
from app.embedding import EmbeddingService

settings = get_settings()

MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"
BATCH_SIZE = 200


def build_embeddings():
    """新建：從 codespcs 建立 embed_icd10 資料（尚無紀錄的 code）"""
    embed_service = EmbeddingService(model_name=MODEL_NAME)
    db = ICD10Session()

    try:
        existing = set(
            r[0] for r in db.execute(
                text("SELECT code FROM embed_icd10")
            ).fetchall()
        )
        logger.info(f"embed_icd10 已有 {len(existing)} 筆")

        total = db.query(CodesPCS).count()
        logger.info(f"codespcs 共 {total} 筆")

        remaining = total - len(existing)
        if remaining <= 0:
            logger.info("全部已建立完畢，無需處理")
            return

        logger.info(f"待處理: {remaining} 筆")

        offset = 0
        processed = 0

        while True:
            rows = (
                db.query(CodesPCS)
                .order_by(CodesPCS.code)
                .offset(offset)
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            offset += BATCH_SIZE

            batch_rows = [r for r in rows if r.code not in existing]
            if not batch_rows:
                continue

            eng_texts = []
            cht_texts = []
            codes = []
            for r in batch_rows:
                parts = r.longdes.split("\r\n", 1) if r.longdes else ["", ""]
                eng = parts[0].strip()
                cht = parts[1].strip() if len(parts) > 1 else ""
                eng_texts.append(eng)
                cht_texts.append(cht)
                codes.append(r.code)

            eng_vectors = embed_service.embed_documents(eng_texts, batch_size=128)

            records = []
            for code, eng, cht, vec in zip(codes, eng_texts, cht_texts, eng_vectors):
                records.append(EmbeddingPCSCodes(
                    code=code,
                    term_eng=eng,
                    term_cht=cht,
                    embedding_eng=vec,
                    embedding_cht=None,
                ))

            db.bulk_save_objects(records)
            db.commit()

            processed += len(records)
            logger.info(f"已處理 {processed}/{remaining} 筆")

        logger.info(f"完成！共建立 {processed} 筆 embedding")

    except Exception as e:
        db.rollback()
        logger.error(f"建立失敗: {e}")
        raise
    finally:
        db.close()


def rebuild_embeddings():
    """重建：用新模型覆蓋已有的 embedding_eng"""
    embed_service = EmbeddingService(model_name=MODEL_NAME)
    db = ICD10Session()

    try:
        total = db.query(EmbeddingPCSCodes).count()
        logger.info(f"embed_icd10 共 {total} 筆，將全部重建 embedding_eng")
        logger.info(f"模型: {MODEL_NAME}")

        offset = 0
        processed = 0

        while True:
            rows = (
                db.query(EmbeddingPCSCodes)
                .order_by(EmbeddingPCSCodes.id)
                .offset(offset)
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            eng_texts = [r.term_eng or "" for r in rows]
            vectors = embed_service.embed_documents(eng_texts, batch_size=128)

            for row, vec in zip(rows, vectors):
                row.embedding_eng = vec

            db.commit()

            processed += len(rows)
            offset += BATCH_SIZE
            logger.info(f"已更新 {processed}/{total} 筆 ({processed/total*100:.1f}%)")

        logger.info(f"完成！共更新 {processed} 筆 embedding_eng")

    except Exception as e:
        db.rollback()
        logger.error(f"更新失敗: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rebuild":
        rebuild_embeddings()
    else:
        build_embeddings()
