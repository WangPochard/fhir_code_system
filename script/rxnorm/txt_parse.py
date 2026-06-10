"""
健保用藥品項查詢檔 → CSV

Usage:
    python script/txt_parse.py sample_data/all1_11503_1.TXT
    python script/txt_parse.py sample_data/all1_11503_1.TXT output/result.csv
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.rxnorm.parsers import NhiDrugPriceParser
from app.logger import logger


def main():
    input_path = os.path.abspath(os.path.join(os.getcwd(), "sample_data", "all1_11503_1.TXT"))
    output_path = os.path.abspath(os.path.join(os.getcwd(), "sample_data", "output", "data.csv"))

    logger.info(input_path)
    logger.info(output_path)

    parser = NhiDrugPriceParser()
    df = parser.parse(input_path)

    # 摘要
    logger.info(f"檔案: {input_path}")
    logger.info(f"筆數: {len(df)}")
    logger.info(f"欄位: {len(df.columns)}")

    # 前 3 筆重點欄位
    key_cols = [
        "drug_code", "reference_price", "price_start_date", "price_end_date",
        "english_name", "chinese_name", "ingredient_name", "dosage_form",
        "manufacturer_full_name", "atc_code",
    ]
    for idx, row in df.head(3).iterrows():
        logger.info(f"[{idx + 1}] {row['drug_code']}  ${row['reference_price']}  {row['chinese_name']}  ({row['atc_code']})")
    if len(df) > 3:
        logger.info(f"... 共 {len(df)} 筆")

    # 輸出
    parser.to_csv(input_path, output_path)
    logger.info(f"CSV → {output_path}")


if __name__ == "__main__":
    main()
