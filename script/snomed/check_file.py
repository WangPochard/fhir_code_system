#!/usr/bin/env python
"""
診斷 SNOMED CSV 資料結構
找出為什麼會有 37 萬筆但概念只有 15 萬個
"""

import csv
import sys
from collections import defaultdict, Counter
from pathlib import Path

csv.field_size_limit(sys.maxsize)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SNOMED_DIR = PROJECT_ROOT / "data" / "snomed_processed"

TERMS_CSV = SNOMED_DIR / "snomed_terms.csv"


def analyze_terms_csv():
    """分析 snomed_terms.csv 的結構"""
    
    print("🔍 分析 snomed_terms.csv...")
    
    total_rows = 0
    concepts_with_fsn = set()
    concepts_with_synonym = set()
    all_concepts = set()
    
    concept_term_counts = Counter()  # 每個 concept 有幾個 term
    term_type_counts = Counter()     # FSN, synonym 數量
    
    with TERMS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        
        print(f"📋 欄位: {reader.fieldnames}\n")
        
        for row in reader:
            total_rows += 1
            
            cid = row.get("conceptId", "").strip()
            if not cid:
                continue
            
            all_concepts.add(cid)
            concept_term_counts[cid] += 1
            
            term_type = (row.get("termType") or "").strip()
            term_type_counts[term_type] += 1
            
            if term_type.upper() == "FSN":
                concepts_with_fsn.add(cid)
            elif term_type.lower() == "synonym":
                concepts_with_synonym.add(cid)
            
            # 抽樣前 5 筆
            if total_rows <= 5:
                print(f"範例 {total_rows}:")
                print(f"  conceptId:   {cid}")
                print(f"  term:        {row.get('term', '')[:60]}")
                print(f"  termType:    {term_type}")
                print(f"  semanticTag: {row.get('semanticTag', '')}")
                print()
    
    print(f"📊 統計結果:")
    print(f"   總行數:                    {total_rows:>8,}")
    print(f"   唯一概念數:                {len(all_concepts):>8,}")
    print(f"   有 FSN 的概念:             {len(concepts_with_fsn):>8,}")
    print(f"   有同義詞的概念:            {len(concepts_with_synonym):>8,}")
    print(f"   平均每個概念有幾個 term:   {total_rows/len(all_concepts):>8,.1f}")
    
    print(f"\n📊 Term Type 分布:")
    for term_type, count in term_type_counts.most_common():
        print(f"   {term_type or '(空白)':.<30} {count:>8,}")
    
    # 找出 term 最多的概念
    print(f"\n📊 Term 數量最多的概念 (Top 10):")
    for cid, count in concept_term_counts.most_common(10):
        print(f"   {cid}: {count} terms")
    
    # 統計分布
    term_count_distribution = Counter(concept_term_counts.values())
    print(f"\n📊 每個概念的 Term 數量分布:")
    for count, num_concepts in sorted(term_count_distribution.items())[:20]:
        print(f"   {count} terms: {num_concepts:>8,} concepts")
    
    # 檢查沒有 FSN 的概念
    no_fsn = all_concepts - concepts_with_fsn
    print(f"\n❌ 沒有 FSN 的概念: {len(no_fsn):,} 個")
    if no_fsn:
        print(f"   範例 (前 10 個): {sorted(list(no_fsn))[:10]}")


def main():
    analyze_terms_csv()


if __name__ == "__main__":
    main()
