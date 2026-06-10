"""
初始化 LOINC 資料庫
- 建立 loinc_mapping 表
- 從 loinc_merge.csv 匯入資料
"""

import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text
from app.database import loinc_engine, LOINCBase, LOINCSession
from app.loinc.models import LoincMapping
from app.config import get_settings
from app.logger import logger

settings = get_settings()

MERGE_CSV = Path(__file__).parent.parent.parent / "data" / "loinc-data" / "loinc_merge.csv"
BATCH_SIZE = 500


def init_database():
    """建立 loinc_mapping 表"""
    logger.info("=" * 60)
    logger.info("初始化 LOINC 資料庫")
    logger.info("=" * 60)

    try:
        # 1. 測試連線
        logger.info("測試資料庫連線...")
        with loinc_engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).fetchone()[0]
            logger.info(f"PostgreSQL: {version[:60]}...")

        # 2. 建立表
        logger.info("建立 loinc_mapping 表...")
        LoincMapping.__table__.create(bind=loinc_engine, checkfirst=True)
        logger.info("loinc_mapping 表建立完成")

        # 3. 建立複合索引
        logger.info("建立索引...")
        with loinc_engine.connect() as conn:
            idx_name = "loinc_mapping_nhi_loinc_idx"
            result = conn.execute(text(
                "SELECT 1 FROM pg_indexes "
                "WHERE tablename = 'loinc_mapping' AND indexname = :idx"
            ), {"idx": idx_name})

            if not result.fetchone():
                conn.execute(text(
                    f"CREATE INDEX {idx_name} ON loinc_mapping (nhi_code, loinc_code)"
                ))
                logger.info(f"  索引 {idx_name} 已建立")
            else:
                logger.info(f"  索引 {idx_name} 已存在，跳過")

            conn.commit()

        logger.info("=" * 60)
        logger.info(f"Database: {settings.loinc_db}")
        logger.info("=" * 60)
        logger.info("資料庫初始化完成!")
        return True

    except Exception as e:
        logger.error(f"初始化失敗: {e}")
        return False


def import_data():
    """從 loinc_merge.csv 匯入資料"""
    if not MERGE_CSV.exists():
        logger.error(f"找不到 {MERGE_CSV}")
        logger.error("請先執行: python script/loinc/merge_nhi_loinc.py")
        return False

    db = LOINCSession()

    try:
        # 檢查是否已有資料
        existing = db.query(LoincMapping).count()
        if existing > 0:
            logger.warning(f"loinc_mapping 已有 {existing} 筆資料")
            confirm = input("要清空後重新匯入嗎? (yes/no): ")
            if confirm.lower() != "yes":
                logger.info("取消匯入")
                return False
            db.execute(text("TRUNCATE TABLE loinc_mapping RESTART IDENTITY"))
            db.commit()
            logger.info("已清空 loinc_mapping")

        # 讀取 CSV 並匯入
        logger.info(f"讀取 {MERGE_CSV}...")
        with open(MERGE_CSV, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        logger.info(f"共 {len(rows)} 筆，開始匯入...")

        batch = []
        for i, row in enumerate(rows, 1):
            batch.append(LoincMapping(
                nhi_code=row["nhi_code"],
                nhi_name_eng=row["nhi_name_eng"],
                nhi_name_cht=row["nhi_name_cht"],
                loinc_code=row["loinc_code"],
                long_common_name=row["long_common_name"],
                component=row["component"],
                property=row["property"],
                time_aspect=row["time_aspect"],
                system=row["system"],
                scale=row["scale"],
                method=row["method"],
                loinc_class=row["class"],
                example_units=row["example_units"],
                relation=row["relation"],
                source=row["source"],
            ))

            if len(batch) >= BATCH_SIZE:
                db.bulk_save_objects(batch)
                db.commit()
                logger.info(f"  已匯入 {i}/{len(rows)} ({i/len(rows)*100:.1f}%)")
                batch = []

        if batch:
            db.bulk_save_objects(batch)
            db.commit()

        final_count = db.query(LoincMapping).count()
        logger.info(f"匯入完成！共 {final_count} 筆")
        return True

    except Exception as e:
        db.rollback()
        logger.error(f"匯入失敗: {e}")
        raise
    finally:
        db.close()


def drop_table():
    """刪除 loinc_mapping 表"""
    confirm = input("確定要刪除 loinc_mapping 表嗎? (yes/no): ")
    if confirm.lower() == "yes":
        LoincMapping.__table__.drop(bind=loinc_engine, checkfirst=True)
        logger.info("loinc_mapping 表已刪除")
    else:
        logger.info("取消操作")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--import":
            init_database()
            import_data()
        elif sys.argv[1] == "--drop":
            drop_table()
        else:
            print("用法:")
            print("  python script/loinc/init_db.py          建立表")
            print("  python script/loinc/init_db.py --import  建立表 + 匯入資料")
            print("  python script/loinc/init_db.py --drop    刪除表")
    else:
        init_database()
