"""
procedure_infer prompt 評估與調整輔助腳本

流程：
  1. 順向：6欄位 context → LLM → procedure_query
  2. 反向：ground_truth_code → DB → term_eng（正確的 procedure 描述）
  3. 請 LLM 比較兩者差異，給出 prompt 調整建議
  4. 輸出 CSV 供人工審閱

使用方式：
  python script/icd10/tune_procedure_prompt.py --input data.xlsx --output result.csv
"""

import sys
import csv
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
from sqlalchemy import text

from app.database import ICD10Session
from app.llm.llm import LLMService
from app.llm.prompts import prompt_service

# ------------------------------------------------------------------
# 6 欄位對應（Excel 欄位名稱 → 顯示標籤）
# ------------------------------------------------------------------
CONTEXT_FIELDS = {
    "imgResult":        "影像報告",
    "subjective":       "主觀描述",
    "objective":        "客觀描述",
    "assessment":       "評估",
    "plan":             "計畫",
    "diagCurrentStatus": "簡要病摘",
}


def build_context(row: pd.Series) -> str:
    """將 6 個欄位組合成 LLM 輸入 context"""
    parts = []
    for field, label in CONTEXT_FIELDS.items():
        val = row.get(field, "")
        if pd.notna(val) and str(val).strip():
            parts.append(f"[{label}]\n{str(val).strip()}")
    return "\n\n".join(parts)


def get_term_eng(db, code: str) -> str:
    """從 ICD-10 DB 反查 ground truth code 的 term_eng"""
    row = db.execute(
        text("SELECT term_eng FROM embed_icd10 WHERE code = :code LIMIT 1"),
        {"code": code},
    ).fetchone()
    return row[0] if row else f"（查無 {code}）"


def analyze_gap(llm: LLMService, procedure_query: str, term_eng: str, code: str) -> str:
    """分析單筆順向/反向差距，回傳純文字描述"""
    prompt = f"""分析以下兩個影像程序描述的差異：

【LLM 生成的 procedure_query】
{procedure_query}

【正確答案（ICD-10-PCS {code} 的 term_eng）】
{term_eng}

請用 1~2 句話說明兩者差距在哪裡。不需要建議，只需描述差異。"""
    try:
        return llm.llm.invoke(prompt).strip()
    except Exception as e:
        return f"分析失敗: {e}"


def rewrite_prompt(llm: LLMService, current_prompt: str, gap_analyses: list[dict]) -> str:
    """根據所有樣本的差距分析，直接改寫 prompt"""
    cases = "\n\n".join(
        f"案例 {i+1}（{g['code']}）\n  生成結果：{g['procedure_query']}\n  正確答案：{g['term_eng']}\n  差距分析：{g['gap']}"
        for i, g in enumerate(gap_analyses)
    )
    prompt = f"""你是 prompt engineering 專家。以下是一個用於從臨床文字推斷影像檢查程序的 prompt，以及多筆評估樣本的差距分析。

【目前的 Prompt】
{current_prompt}

【樣本差距分析】
{cases}

請根據上述差距，直接改寫 prompt，使其能更精準地生成正確的 procedure_query。
要求：
- 只輸出改寫後的完整 prompt 本文，不要加說明、不要加標題
- 保留原本的輸入變數格式（{{report_text}} 等）
- 改寫要針對觀察到的共同錯誤模式"""
    try:
        return llm.llm.invoke(prompt).strip()
    except Exception as e:
        return f"prompt 改寫失敗: {e}"


# ------------------------------------------------------------------
# 單輪迭代
# ------------------------------------------------------------------
def run_one_iteration(llm: LLMService, db, df: pd.DataFrame, prompt: str, iteration: int) -> tuple[str, list]:
    """用指定 prompt 跑一輪 gap 分析，回傳 (tuned_prompt, results)"""
    total = len(df)
    results = []
    gap_analyses = []

    for idx, row in df.iterrows():
        code = str(row.get("ICD-10-PCS", "")).strip()
        print(f"  [{idx + 1}/{total}] {code}")

        term_eng = get_term_eng(db, code)
        context  = build_context(row)

        # 用當前 prompt 直接呼叫 LLM（不走 prompt_service）
        # 用 replace 而非 format，避免 prompt 內的 JSON {} 被誤判為佔位符
        import re, json
        try:
            raw = llm.llm.invoke(prompt.replace("{report_text}", context))
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            inferred = json.loads(m.group()) if m else {}
        except Exception:
            inferred = {}

        procedure_query = inferred.get("procedure_query", "")
        confidence      = inferred.get("confidence", "")

        gap = analyze_gap(llm, procedure_query, term_eng, code)
        gap_analyses.append({"code": code, "procedure_query": procedure_query, "term_eng": term_eng, "gap": gap})

        results.append({
            "iteration":         iteration,
            "ground_truth_code": code,
            "term_eng":          term_eng,
            "procedure_query":   procedure_query,
            "confidence":        confidence,
            "gap_analysis":      gap,
            "prompt_used":       prompt,
        })

    print(f"  → 改寫 prompt...")
    tuned = rewrite_prompt(llm, prompt, gap_analyses)
    return tuned, results


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main(input_file: str, output_file: str, iterations: int):
    print(f"讀取資料: {input_file}")
    df = pd.read_excel(input_file, sheet_name="樣本")
    print(f"共 {len(df)} 筆，迭代 {iterations} 輪\n")

    llm = LLMService()
    db  = ICD10Session()
    current_prompt = prompt_service.get("procedure_infer")

    all_results = []

    try:
        for i in range(1, iterations + 1):
            print(f"=== 第 {i} 輪 ===")
            tuned_prompt, results = run_one_iteration(llm, db, df, current_prompt, i)
            for r in results:
                r["tuned_prompt"] = tuned_prompt
            all_results.extend(results)
            current_prompt = tuned_prompt   # 下一輪用改寫後的 prompt
    finally:
        db.close()

    # 輸出 CSV（所有輪次都在同一個檔）
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\n完成，結果寫入: {output_file}")
    print(f"\n【最終 Prompt（第 {iterations} 輪）】\n{current_prompt}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="procedure_infer prompt 評估腳本")
    parser.add_argument("--input",      required=True,             help="輸入 Excel 路徑")
    parser.add_argument("--output",     default="tune_result.csv", help="輸出 CSV 路徑")
    parser.add_argument("--iterations", default=3, type=int,       help="迭代輪數（預設 3）")
    args = parser.parse_args()
    main(args.input, args.output, args.iterations)
