"""
建立 prompt_templates 資料表並寫入預設 prompts。

使用方式：
  python script/init_prompts.py           # 只建表，已存在的 prompt 不覆蓋
  python script/init_prompts.py --force   # 強制覆蓋所有 prompt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import SnomedSession

# ------------------------------------------------------------------
# 預設 prompts
# template 變數用 {variable}，literal 大括號用 {{ 和 }}
# ------------------------------------------------------------------
PROMPTS = [
    {
        "name": "ner_extract",
        "description": "基礎 NER：從臨床文字抽取醫學術語（不含原因）",
        "content": """\
你是醫學命名實體辨識 (NER) 系統。
從以下臨床文字中抽取所有醫學術語（診斷、症狀、檢驗、藥物、手術）。

規則：
- 只回傳 JSON，格式為 {{"terms": ["術語1", "術語2"]}}
- 保留原文用語，不要翻譯或改寫
- 每個術語應該是一個完整的醫學概念，不要拆太細

臨床文字：
{clinical_text}""",
    },
    {
        "name": "ner_with_reason",
        "description": "進階 NER：抽取完整臨床概念，處理否定詞與頻率修飾詞，附上擷取原因",
        "content": """\
你是醫學概念提取系統，專門從臨床文字中識別可用於醫療編碼（ICD、SNOMED）的完整臨床概念。

任務：從以下臨床文字中提取所有有意義的臨床概念。

核心原則：
1. 提取「完整概念」，不是單字。保留所有修飾語（部位、側別、大小、程度、性質）。
   ✓ 正確: "right breast malignancy measuring 3.6 cm"
   ✗ 錯誤: "malignancy"、"right breast"（分開）
2. 一個臨床發現是一個概念，不要拆解成多個單字。
   ✓ 正確: "invasive carcinoma with ductal and lobular differentiation"
   ✗ 錯誤: "carcinoma"、"ductal"、"lobular"（分開）
3. 解剖位置本身不是概念，要和病症合在一起。
   ✓ 正確: "juxtapleural micronodule in the right lower lobe"
   ✗ 錯誤: "right lower lobe"（單獨列出）
4. 保留原文語言與用詞，不要翻譯或改寫。

否定詞處理（重要）：
- 否定符號：- 結尾（例如 HTN-）、no、without、denied、negative for、(-)
- 遇到否定，term 改寫為 SNOMED 否定形式，讓向量搜尋找到正確的否定概念
  - "HTN-"          → "no hypertension"
  - "no fever"      → "no fever"（保留 no）
  - "(-) DM"        → "no diabetes mellitus"
  - "denied chest pain" → "no chest pain"
- reason 標明「否定症狀」

頻率與數量修飾詞處理：
- 3-4x、BID、QD、Q8H、daily、PRN、mg、ml 等是頻率/劑量資訊，不是臨床概念
- 從 term 中剝離這些修飾詞，只保留臨床概念本身
  - "nocturia 3-4x"   → "nocturia"
  - "metformin 500mg" → "metformin"
  - "fever for 3 days" → "fever"

輸出格式（只回傳 JSON，不要有其他文字）：
{{"terms": [{{"term": "完整臨床概念（已處理否定與頻率）", "reason": "概念類型說明，否定症狀請標明"}}]}}

臨床文字：
{clinical_text}""",
    },
    {
        "name": "procedure_infer",
        "description": "從影像報告 findings/impression 推斷執行的影像檢查程序（modality、body region、contrast）",
        "content": """\
你是醫療影像編碼專家。請從以下影像報告的 findings/impression 內容，推斷這份報告是由哪種影像檢查所產生的。

判斷依據：
- 報告中提到的影像技術線索（例如 "CT imaging"、"MRI"、"PET-CT"、"sonography"）
- 報告涵蓋的解剖範圍（胸部、腹部、骨盆等）
- 報告建議的後續追蹤方式（例如建議做 PET-CT，代表本次可能是 CT）
- 影像特性描述（例如 FDG-avid 代表 PET scan）

輸出格式（只回傳 JSON，不要有其他文字）：
{{
  "modality": "CT / MRI / PET / Ultrasound / X-Ray / 無法判斷",
  "body_regions": ["chest", "abdomen"],
  "contrast": "with low osmolar contrast / without contrast / unknown",
  "procedure_query": "用來查詢 ICD-10-PCS 的英文描述，例如 CT scan of chest and abdomen with contrast",
  "reasoning": "為什麼這樣判斷（引用報告中的線索）",
  "confidence": "high / medium / low"
}}

影像報告內容：
{report_text}""",
    },
    {
        "name": "rerank",
        "description": "用 LLM 對向量搜尋候選結果重新排序，選出最相關的 top_k 個",
        "content": """\
你是醫療術語排序專家。請根據查詢文字，從候選結果中選出最相關的 {top_k} 個。

【查詢】
{query}

【候選結果】
{candidate_text}

【要求】
- 選出最相關的 {top_k} 個，按相關性從高到低排列
- 只回傳 JSON，格式為 [{{"index": 1, "reason": "理由"}}, ...]
- index 對應候選結果的編號（從 1 開始）""",
    },
    {
        "name": "explain_match",
        "description": "解釋向量搜尋結果與查詢的相關性，以繁體中文說明",
        "content": """\
你是醫療編碼專家。以下是使用者的臨床敘述，以及向量搜尋找到最相近的結果。

請用繁體中文說明：
1. 每個結果為什麼與查詢相關（或不相關）
2. 你認為哪個最合適，為什麼

【使用者敘述】
{query}

【搜尋結果】
{candidate_text}

請直接回覆分析，不需要 JSON 格式。""",
    },
]

# ------------------------------------------------------------------
# 建表 + 寫入
# ------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    content     TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def main(force: bool = False):
    db = SnomedSession()
    try:
        db.execute(text(CREATE_TABLE_SQL))
        db.commit()
        print("✓ prompt_templates 資料表已就緒")

        inserted = 0
        updated = 0
        skipped = 0

        for p in PROMPTS:
            existing = db.execute(
                text("SELECT id FROM prompt_templates WHERE name = :name"),
                {"name": p["name"]},
            ).fetchone()

            if existing:
                if force:
                    db.execute(
                        text("""
                            UPDATE prompt_templates
                            SET content = :content, description = :description, updated_at = NOW()
                            WHERE name = :name
                        """),
                        {"name": p["name"], "content": p["content"], "description": p["description"]},
                    )
                    updated += 1
                    print(f"  ↻ 更新: {p['name']}")
                else:
                    skipped += 1
                    print(f"  - 跳過 (已存在): {p['name']}")
            else:
                db.execute(
                    text("""
                        INSERT INTO prompt_templates (name, description, content)
                        VALUES (:name, :description, :content)
                    """),
                    {"name": p["name"], "content": p["content"], "description": p["description"]},
                )
                inserted += 1
                print(f"  + 新增: {p['name']}")

        db.commit()
        print(f"\n完成：新增 {inserted}，更新 {updated}，跳過 {skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="強制覆蓋已存在的 prompt")
    args = parser.parse_args()
    main(force=args.force)
