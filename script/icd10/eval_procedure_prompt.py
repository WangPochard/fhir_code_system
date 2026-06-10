"""
eval_procedure_prompt.py

驗證 tuned_prompt 生成的 procedure_query 是否能在 RAG 搜尋 top-K 中找到正確答案。

流程：
  1. 讀取 tune_result.csv 取得 tuned_prompt 與 ground truth
  2. 讀取 code.xlsx（樣本 sheet）重建每筆 context
  3. 用 tuned_prompt 生成 new_procedure_query
  4. 用 new_procedure_query 實際打 RAG 搜尋，取 top-K 結果
  5. 檢查 ground_truth_code 是否出現在 top-K 中（pass / fail）
  6. 輸出 eval_result.csv

使用方式：
  python script/icd10/eval_procedure_prompt.py \
      --tune tune_result.csv \
      --data code.xlsx \
      --output eval_result.csv \
      --topk 5
"""

import sys
import re
import csv
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
from app.llm.llm import LLMService
from app.icd10.routers import rag_eng

CONTEXT_FIELDS = {
    "imgResult":         "影像報告",
    "subjective":        "主觀描述",
    "objective":         "客觀描述",
    "assessment":        "評估",
    "plan":              "計畫",
    "diagCurrentStatus": "簡要病摘",
}


def build_context(row: pd.Series) -> str:
    parts = []
    for field, label in CONTEXT_FIELDS.items():
        val = row.get(field, "")
        if pd.notna(val) and str(val).strip():
            parts.append(f"[{label}]\n{str(val).strip()}")
    return "\n\n".join(parts)


def run_tuned_prompt(llm: LLMService, tuned_prompt: str, report_text: str) -> dict:
    prompt = tuned_prompt.replace("{report_text}", report_text)
    try:
        response = llm.llm.invoke(prompt)
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass
    return {"procedure_query": "", "confidence": "low"}


def rag_search(query: str, k: int) -> list[dict]:
    """直接呼叫 RAGService，回傳 top-K 結果"""
    if not query:
        return []
    return rag_eng.similarity_search(query, k=k, threshold=0.0)


def main(tune_file: str, data_file: str, output_file: str, topk: int):
    # 讀取 tune_result.csv，取最後一輪（iteration 最大）的 tuned_prompt
    tune_df = pd.read_csv(tune_file)
    last_iter = tune_df["iteration"].max()
    last_df   = tune_df[tune_df["iteration"] == last_iter]
    tuned_prompt = last_df["tuned_prompt"].iloc[0]
    print(f"使用第 {last_iter} 輪 tuned_prompt（前 80 字）: {tuned_prompt[:80]}...")

    data_df = pd.read_excel(data_file, sheet_name="樣本")
    total   = len(data_df)
    print(f"樣本數: {total}，top-K: {topk}\n")

    llm = LLMService()
    results = []

    for idx, data_row in data_df.iterrows():
        code = str(data_row.get("ICD-10-PCS", "")).strip()
        print(f"[{idx + 1}/{total}] {code}")

        # 從 tune_result 取同 code 的舊版 procedure_query 與 term_eng（第1輪）
        first_row = tune_df[(tune_df["ground_truth_code"] == code) & (tune_df["iteration"] == 1)]
        old_query = first_row["procedure_query"].iloc[0] if not first_row.empty else ""
        term_eng  = first_row["term_eng"].iloc[0]        if not first_row.empty else ""

        # 生成新版 procedure_query
        context   = build_context(data_row)
        new_inf   = run_tuned_prompt(llm, tuned_prompt, context)
        new_query = new_inf.get("procedure_query", "")
        new_conf  = new_inf.get("confidence", "")

        # RAG 搜尋 top-K，舊版
        old_hits  = rag_search(old_query, topk)
        old_codes = [r["code"] for r in old_hits]
        old_pass  = code in old_codes

        # RAG 搜尋 top-K，新版
        new_hits  = rag_search(new_query, topk)
        new_codes = [r["code"] for r in new_hits]
        new_pass  = code in new_codes

        print(f"  old: {old_query[:60]} → {'PASS' if old_pass else 'FAIL'}")
        print(f"  new: {new_query[:60]} → {'PASS' if new_pass else 'FAIL'}")

        # top-K 結果整理成可讀格式
        def fmt_hits(hits):
            return " | ".join(f"{r['code']}({r.get('similarity', 0):.3f})" for r in hits)

        results.append({
            "ground_truth_code":   code,
            "term_eng":            term_eng,
            "old_procedure_query": old_query,
            "old_top5":            fmt_hits(old_hits),
            "old_pass":            "pass" if old_pass else "fail",
            "new_procedure_query": new_query,
            "new_confidence":      new_conf,
            "new_top5":            fmt_hits(new_hits),
            "new_pass":            "pass" if new_pass else "fail",
        })

    old_passed = sum(1 for r in results if r["old_pass"] == "pass")
    new_passed = sum(1 for r in results if r["new_pass"] == "pass")
    print(f"\n舊版 top-{topk} pass: {old_passed}/{total}")
    print(f"新版 top-{topk} pass: {new_passed}/{total}")

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    print(f"結果寫入: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tuned prompt RAG 搜尋驗證腳本")
    parser.add_argument("--tune",   required=True,               help="tune_result.csv 路徑")
    parser.add_argument("--data",   required=True,               help="原始 Excel 路徑（code.xlsx）")
    parser.add_argument("--output", default="eval_result.csv",   help="輸出 CSV 路徑")
    parser.add_argument("--topk",   default=5, type=int,         help="top-K（預設 5）")
    args = parser.parse_args()
    main(args.tune, args.data, args.output, args.topk)
