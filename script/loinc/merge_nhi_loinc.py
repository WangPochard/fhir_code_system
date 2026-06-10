"""
整併健保碼 → LOINC 對照表

用法：
  python script/loinc/merge_nhi_loinc.py

資料來源：
  - reference/NHI-Code–LOINC對應清單.xlsx（醫療資訊大平台，503 個健保碼，含 6 軸）
  - reference/ConceptMap-nhi-loinc.json（衛福部 FHIR，53 個健保碼，無 6 軸）
  - reference/Loinc.csv（LOINC 官方資料庫，10 萬筆，用來補衛福部缺少的 6 軸）

產出：
  - data/loinc-data/loinc_merge.csv
    後續用於 init.py 匯入資料庫，以及建立 embedding 向量表
"""

import json
import csv
import openpyxl
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLATFORM_FILE = ROOT / "reference" / "NHI-Code–LOINC對應清單.xlsx"
MOHW_FILE = ROOT / "reference" / "ConceptMap-nhi-loinc.json"
LOINC_CSV = ROOT / "reference" / "Loinc.csv"
MERGE_OUTPUT = ROOT / "data" / "loinc-data" / "loinc_merge.csv"

MERGE_FIELDS = [
    "nhi_code", "nhi_name_eng", "nhi_name_cht",
    "loinc_code", "long_common_name",
    "component", "property", "time_aspect", "system", "scale", "method",
    "class", "example_units", "relation", "source",
]


def load_loinc_db():
    """載入 Loinc.csv 建立 loinc_code → 6 軸 的 dict"""
    db = {}
    with open(LOINC_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            db[row["LOINC_NUM"]] = row
    return db


def load_platform():
    """載入大平台對照表"""
    wb = openpyxl.load_workbook(PLATFORM_FILE)
    ws = wb["CODEBOOK"]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        nhi_code = str(row[1]).strip() if row[1] else ""
        if not nhi_code:
            continue
        rows.append({
            "nhi_code": nhi_code,
            "nhi_name_eng": str(row[2]).strip() if row[2] else "",
            "nhi_name_cht": str(row[3]).strip() if row[3] else "",
            "loinc_code": str(row[7]).strip() if row[7] else "",
            "component": str(row[8]).strip() if row[8] else "",
            "property": str(row[9]).strip() if row[9] else "",
            "time_aspect": str(row[10]).strip() if row[10] else "",
            "system": str(row[11]).strip() if row[11] else "",
            "scale": str(row[12]).strip() if row[12] else "",
            "method": str(row[13]).strip() if row[13] else "",
            "class": str(row[14]).strip() if row[14] else "",
            "example_units": str(row[16]).strip() if row[16] else "",
            "relation": str(row[6]).strip() if row[6] else "",
        })
    return rows


def load_mohw():
    """載入衛福部 ConceptMap"""
    with open(MOHW_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    rows = []
    for element in raw["group"][0]["element"]:
        nhi_code = element["code"]
        display = element.get("display", "")
        for t in element.get("target", []):
            rows.append({
                "nhi_code": nhi_code,
                "nhi_name_cht": display,
                "loinc_code": t["code"],
            })
    return rows


# ------------------------------------------------------------------
# merge: 整併大平台 + 衛福部（補 6 軸）→ loinc_merge.csv
# ------------------------------------------------------------------
def cmd_merge():
    print("載入 Loinc.csv...")
    loinc_db = load_loinc_db()
    print(f"  LOINC 資料庫: {len(loinc_db)} 筆")

    print("載入大平台...")
    platform_rows = load_platform()
    platform_pairs = {(r["nhi_code"], r["loinc_code"]) for r in platform_rows}
    print(f"  大平台: {len(platform_rows)} 筆")

    print("載入衛福部...")
    mohw_rows = load_mohw()
    print(f"  衛福部: {len(mohw_rows)} 筆")

    output_rows = []

    # 大平台的直接寫入（已有 6 軸）
    for r in platform_rows:
        loinc_info = loinc_db.get(r["loinc_code"], {})
        output_rows.append({
            "nhi_code": r["nhi_code"],
            "nhi_name_eng": r["nhi_name_eng"],
            "nhi_name_cht": r["nhi_name_cht"],
            "loinc_code": r["loinc_code"],
            "long_common_name": loinc_info.get("LONG_COMMON_NAME", ""),
            "component": r["component"],
            "property": r["property"],
            "time_aspect": r["time_aspect"],
            "system": r["system"],
            "scale": r["scale"],
            "method": r["method"],
            "class": r["class"],
            "example_units": r["example_units"],
            "relation": r["relation"],
            "source": "platform",
        })

    # 衛福部的：補 6 軸，且排除大平台已有的
    mohw_added = 0
    mohw_skipped = 0
    for r in mohw_rows:
        pair = (r["nhi_code"], r["loinc_code"])
        if pair in platform_pairs:
            mohw_skipped += 1
            continue

        loinc_info = loinc_db.get(r["loinc_code"], {})
        output_rows.append({
            "nhi_code": r["nhi_code"],
            "nhi_name_eng": loinc_info.get("COMPONENT", ""),
            "nhi_name_cht": r["nhi_name_cht"],
            "loinc_code": r["loinc_code"],
            "long_common_name": loinc_info.get("LONG_COMMON_NAME", ""),
            "component": loinc_info.get("COMPONENT", ""),
            "property": loinc_info.get("PROPERTY", ""),
            "time_aspect": loinc_info.get("TIME_ASPCT", ""),
            "system": loinc_info.get("SYSTEM", ""),
            "scale": loinc_info.get("SCALE_TYP", ""),
            "method": loinc_info.get("METHOD_TYP", ""),
            "class": loinc_info.get("CLASS", ""),
            "example_units": loinc_info.get("EXAMPLE_UNITS", ""),
            "relation": "H",
            "source": "mohw",
        })
        mohw_added += 1

    # 寫入 CSV
    with open(MERGE_OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MERGE_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    # 統計
    unique_nhi = set(r["nhi_code"] for r in output_rows)
    unique_loinc = set(r["loinc_code"] for r in output_rows)

    print(f"\n===== 整併結果 =====")
    print(f"輸出: {MERGE_OUTPUT}")
    print(f"總筆數: {len(output_rows)}")
    print(f"  大平台: {len(platform_rows)} 筆")
    print(f"  衛福部補充: {mohw_added} 筆（跳過重複 {mohw_skipped} 筆）")
    print(f"健保碼（去重）: {len(unique_nhi)} 個")
    print(f"LOINC碼（去重）: {len(unique_loinc)} 個")


if __name__ == "__main__":
    cmd_merge()
