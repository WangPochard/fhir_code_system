#!/usr/bin/env python
"""
從 TWCore IG ValueSet JSON 更新 documents.tw_valueset 欄位。

使用方法：
  python script/snomed/update_valueset.py
  python script/snomed/update_valueset.py --dry-run
"""

import json
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from app.database import SnomedSession
from app.logger import logger

_VS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "snomed" / "valuesets"


def _load_snomed_codes(filename: str) -> list[str]:
    data = json.loads((_VS_DIR / filename).read_text())
    codes = []
    for inc in data.get("compose", {}).get("include", []):
        if "snomed.info/sct" not in inc.get("system", ""):
            continue
        for c in inc.get("concept", []):
            codes.append(c["code"])
    return codes


def main(dry_run: bool = False):
    medical_dept_codes = _load_snomed_codes("ValueSet-medical-department-sct-tw.json")
    medication_path_codes = _load_snomed_codes("ValueSet-medication-path-sct-tw.json")
    health_prof_codes = _load_snomed_codes("ValueSet-health-professional-sct-tw.json")

    logger.info(f"medical-department 明確 codes: {len(medical_dept_codes)}")
    logger.info(f"medication-path 明確 codes: {len(medication_path_codes)}")
    logger.info(f"health-professional 明確 codes: {len(health_prof_codes)}")

    # (valueset名稱, WHERE條件, 額外參數)
    # 標記順序不影響結果，因各 valueset 的 semantic_tag 互不重疊
    updates = [
        (
            "condition",
            "semantic_tag IN ('finding', 'disorder', 'situation', 'event')",
            {},
        ),
        (
            "medical-department",
            "snomed_concept_id = ANY(:codes)",
            {"codes": medical_dept_codes},
        ),
        (
            "medication-path",
            "snomed_concept_id = ANY(:codes) OR (semantic_tag = 'qualifier value' AND fsn ILIKE '%route%')",
            {"codes": medication_path_codes},
        ),
        (
            "health-professional",
            "semantic_tag = 'occupation' OR snomed_concept_id = ANY(:codes)",
            {"codes": health_prof_codes},
        ),
    ]

    db = SnomedSession()
    try:
        if not dry_run:
            db.execute(text("UPDATE documents SET tw_valueset = NULL"))
            logger.info("已清空 tw_valueset 欄位")

        for vs_name, where_clause, params in updates:
            count = db.execute(
                text(f"SELECT COUNT(*) FROM documents WHERE {where_clause}"),
                params,
            ).scalar()

            if dry_run:
                logger.info(f"[DRY RUN] {vs_name}: {count:,} 筆會被標記")
                continue

            result = db.execute(
                text(f"UPDATE documents SET tw_valueset = :vs WHERE {where_clause}"),
                {"vs": vs_name, **params},
            )
            logger.info(f"{vs_name}: 更新 {result.rowcount:,} 筆")

        if not dry_run:
            db.commit()
            null_count = db.execute(
                text("SELECT COUNT(*) FROM documents WHERE tw_valueset IS NULL")
            ).scalar()
            logger.info(f"完成。tw_valueset=NULL（未分類）：{null_count:,} 筆")

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
