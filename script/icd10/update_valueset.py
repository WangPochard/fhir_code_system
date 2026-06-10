#!/usr/bin/env python
"""
從 ValueSet JSON 更新 embed_icd10.valueset 欄位。

使用方法：
  python script/icd10/update_valueset.py
  python script/icd10/update_valueset.py --dry-run
"""

import json
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from app.database import ICD10Session
from app.logger import logger

_VALUESET_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "icd10" / "valuesets"

VALUESETS = {
    "imaging":        _VALUESET_DIR / "ValueSet-icd-10-pcs-2023-image.json",
    "radiotherapy":   _VALUESET_DIR / "ValueSet-icd-10-pcs-2023-radiotherapy.json",
}


def load_codes(path: Path) -> set[str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    codes = set()
    for include in data.get("compose", {}).get("include", []):
        for concept in include.get("concept", []):
            codes.add(concept["code"])
    return codes


def main(dry_run: bool = False):
    db = ICD10Session()
    try:
        # 先清空現有 valueset（重跑時保持乾淨）
        if not dry_run:
            db.execute(text("UPDATE embed_icd10 SET valueset = NULL"))
            logger.info("已清空 valueset 欄位")

        for valueset_name, json_path in VALUESETS.items():
            codes = load_codes(json_path)
            logger.info(f"{valueset_name}：共 {len(codes):,} 個 code")

            if dry_run:
                result = db.execute(
                    text("SELECT COUNT(*) FROM embed_icd10 WHERE code = ANY(:codes)"),
                    {"codes": list(codes)},
                ).scalar()
                logger.info(f"[DRY RUN] {valueset_name}：embed_icd10 中有 {result:,} 筆會被更新")
                continue

            result = db.execute(
                text("UPDATE embed_icd10 SET valueset = :vs WHERE code = ANY(:codes)"),
                {"vs": valueset_name, "codes": list(codes)},
            )
            logger.info(f"{valueset_name}：更新 {result.rowcount:,} 筆")

        if not dry_run:
            db.commit()
            remaining = db.execute(
                text("SELECT COUNT(*) FROM embed_icd10 WHERE valueset IS NULL")
            ).scalar()
            logger.info(f"完成。valueset=NULL（一般）：{remaining:,} 筆")

    except Exception as e:
        db.rollback()
        logger.error(f"失敗: {e}", exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只統計不寫入")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
