"""
SNOMED CT RF2 資料前處理腳本
================================
將 RF2 原始 tab-delimited txt 檔整理成乾淨的 CSV / Parquet，
產出兩組可直接使用的資料：
  1. concept_map.csv       → SNOMED CT ↔ ICD-10 對照表
  2. snomed_terms.csv      → 供 RAG embedding 用的術語庫
  3. snomed_hierarchy.csv  → 概念階層關係（Is-a）
  4. snomed_summary.json   → 資料集統計摘要
"""

import os
import json
import pandas as pd
from pathlib import Path

# ============================================================
# 1. 路徑設定 — 請依你的實際路徑修改
# ============================================================
BASE_DIR = os.path.abspath(
    os.path.join(os.getcwd(), 'SnomedCT_InternationalRF2_PRODUCTION_20250701T120000Z')
)

SNAPSHOT_TERM = os.path.join(BASE_DIR, 'Snapshot', 'Terminology')
SNAPSHOT_REF  = os.path.join(BASE_DIR, 'Snapshot', 'Refset')

# 輸出目錄
OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'snomed_processed')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 原始檔案路徑
FILES = {
    'concept':      os.path.join(SNAPSHOT_TERM, 'sct2_Concept_Snapshot_INT_20250701.txt'),
    'description':  os.path.join(SNAPSHOT_TERM, 'sct2_Description_Snapshot-en_INT_20250701.txt'),
    'relationship': os.path.join(SNAPSHOT_TERM, 'sct2_Relationship_Snapshot_INT_20250701.txt'),
    'text_def':     os.path.join(SNAPSHOT_TERM, 'sct2_TextDefinition_Snapshot-en_INT_20250701.txt'),
    'extended_map': os.path.join(SNAPSHOT_REF, 'Map', 'der2_iisssccRefset_ExtendedMapSnapshot_INT_20250701.txt'),
    'simple_map':   os.path.join(SNAPSHOT_REF, 'Map', 'der2_sRefset_SimpleMapSnapshot_INT_20250701.txt'),
}


def load_rf2(filepath: str, usecols=None) -> pd.DataFrame:
    """讀取 RF2 tab-delimited txt 檔"""
    if not os.path.exists(filepath):
        print(f"  ⚠ 檔案不存在，跳過: {filepath}")
        return pd.DataFrame()
    
    print(f"  📂 讀取: {os.path.basename(filepath)}")
    df = pd.read_csv(filepath, sep='\t', dtype=str, usecols=usecols, low_memory=False)
    print(f"     → {len(df):,} 筆, {len(df.columns)} 欄")
    return df


# ============================================================
# 2. 載入原始資料
# ============================================================
print("=" * 60)
print("步驟 1: 載入 RF2 原始檔案")
print("=" * 60)

concepts     = load_rf2(FILES['concept'])
descriptions = load_rf2(FILES['description'])
relationships = load_rf2(FILES['relationship'])
text_defs    = load_rf2(FILES['text_def'])
extended_map = load_rf2(FILES['extended_map'])
simple_map   = load_rf2(FILES['simple_map'])


# ============================================================
# 3. 整理概念 + 描述 → snomed_terms.csv (RAG 用)
# ============================================================
print("\n" + "=" * 60)
print("步驟 2: 整理術語庫 (snomed_terms.csv)")
print("=" * 60)

# 只留 active 概念
active_concept_ids = set(concepts[concepts['active'] == '1']['id'])
print(f"  Active 概念數: {len(active_concept_ids):,}")

# 過濾描述：active 且對應的概念也 active
desc = descriptions[
    (descriptions['active'] == '1') &
    (descriptions['conceptId'].isin(active_concept_ids))
].copy()

# 標記描述類型
TYPE_MAP = {
    '900000000000003001': 'FSN',       # Fully Specified Name
    '900000000000013009': 'Synonym',   # 同義詞
}
desc['termType'] = desc['typeId'].map(TYPE_MAP).fillna('Other')

# 從 FSN 中提取語義標籤 (semantic tag)，例如 "Diabetes mellitus (disorder)" → "disorder"
fsn_mask = desc['termType'] == 'FSN'
desc.loc[fsn_mask, 'semanticTag'] = (
    desc.loc[fsn_mask, 'term']
    .str.extract(r'\(([^)]+)\)$', expand=False)
)

# 對 Synonym 行，從同概念的 FSN 補上 semanticTag
fsn_tags = desc.loc[fsn_mask, ['conceptId', 'semanticTag']].drop_duplicates('conceptId')
desc = desc.merge(
    fsn_tags.rename(columns={'semanticTag': '_fsnTag'}),
    on='conceptId', how='left'
)
desc['semanticTag'] = desc['semanticTag'].fillna(desc['_fsnTag'])
desc.drop(columns=['_fsnTag'], inplace=True)

# 加入文字定義 (如果有的話)
if not text_defs.empty:
    active_defs = text_defs[
        (text_defs['active'] == '1') &
        (text_defs['conceptId'].isin(active_concept_ids))
    ][['conceptId', 'term']].rename(columns={'term': 'textDefinition'})
    # 同一概念可能有多個定義，取第一個
    active_defs = active_defs.drop_duplicates('conceptId')
    desc = desc.merge(active_defs, on='conceptId', how='left')
else:
    desc['textDefinition'] = None

# 精簡欄位，只保留需要的
terms = desc[[
    'conceptId', 'term', 'termType', 'semanticTag',
    'languageCode', 'textDefinition'
]].copy()

# 排序：先按 conceptId，再按 termType (FSN 在前)
terms = terms.sort_values(['conceptId', 'termType']).reset_index(drop=True)

print(f"  整理後: {len(terms):,} 筆術語, 涵蓋 {terms['conceptId'].nunique():,} 個概念")
print(f"  語義標籤分布 (top 15):")
tag_counts = terms.drop_duplicates('conceptId')['semanticTag'].value_counts().head(15)
for tag, count in tag_counts.items():
    print(f"    {tag}: {count:,}")

# 存檔
terms_path = os.path.join(OUTPUT_DIR, 'snomed_terms.csv')
terms.to_csv(terms_path, index=False, encoding='utf-8-sig')
print(f"\n  ✅ 已存檔: {terms_path}")


# ============================================================
# 4. 整理階層關係 → snomed_hierarchy.csv
# ============================================================
print("\n" + "=" * 60)
print("步驟 3: 整理階層關係 (snomed_hierarchy.csv)")
print("=" * 60)

if not relationships.empty:
    rels = relationships[relationships['active'] == '1'].copy()
    
    # typeId = 116680003 代表 "Is a" 關係
    IS_A = '116680003'
    hierarchy = rels[rels['typeId'] == IS_A][['sourceId', 'destinationId', 'typeId']].copy()
    hierarchy.columns = ['childId', 'parentId', 'relationshipType']
    hierarchy['relationshipType'] = 'Is_a'
    
    # 也保留其他常用關係
    OTHER_RELS = {
        '363698007': 'Finding_site',
        '116676008': 'Associated_morphology',
        '246075003': 'Causative_agent',
        '127489000': 'Has_active_ingredient',
        '411116001': 'Has_dose_form',
    }
    
    other = rels[rels['typeId'].isin(OTHER_RELS.keys())][['sourceId', 'destinationId', 'typeId']].copy()
    other.columns = ['childId', 'parentId', 'relationshipType']
    other['relationshipType'] = other['relationshipType'].map(
        {k: v for k, v in OTHER_RELS.items()}  # typeId was renamed to relationshipType
    )
    # 修正：other 的 relationshipType 欄位此時還是原始 typeId，需要重新 map
    other_raw = rels[rels['typeId'].isin(OTHER_RELS.keys())][['sourceId', 'destinationId', 'typeId']].copy()
    other_raw['relationshipType'] = other_raw['typeId'].map(OTHER_RELS)
    other_raw = other_raw.rename(columns={'sourceId': 'childId', 'destinationId': 'parentId'})
    other_raw = other_raw[['childId', 'parentId', 'relationshipType']]
    
    hierarchy = pd.concat([hierarchy, other_raw], ignore_index=True)
    
    print(f"  Is-a 關係: {len(hierarchy[hierarchy['relationshipType'] == 'Is_a']):,} 筆")
    print(f"  其他關係: {len(hierarchy[hierarchy['relationshipType'] != 'Is_a']):,} 筆")
    
    hier_path = os.path.join(OUTPUT_DIR, 'snomed_hierarchy.csv')
    hierarchy.to_csv(hier_path, index=False, encoding='utf-8-sig')
    print(f"  ✅ 已存檔: {hier_path}")
else:
    print("  ⚠ Relationship 檔案為空，跳過")


# ============================================================
# 5. 整理 ConceptMap → concept_map.csv
# ============================================================
print("\n" + "=" * 60)
print("步驟 4: 整理 SNOMED CT → ICD-10 對照表 (concept_map.csv)")
print("=" * 60)

if not extended_map.empty:
    cmap = extended_map[extended_map['active'] == '1'].copy()
    
    # 精簡欄位
    cmap = cmap[[
        'referencedComponentId',  # SNOMED CT concept ID
        'mapGroup',               # 對應群組
        'mapPriority',            # 群組內優先序
        'mapRule',                # 對應規則
        'mapAdvice',              # 建議
        'mapTarget',              # ICD-10 code
        'correlationId',          # 對應精確度
        'mapCategoryId',          # 對應類別
    ]].copy()
    
    cmap.columns = [
        'snomedConceptId', 'mapGroup', 'mapPriority',
        'mapRule', 'mapAdvice', 'icd10Code',
        'correlationId', 'mapCategoryId'
    ]
    
    # 加入 SNOMED CT 概念的 FSN 名稱，方便人讀
    fsn_lookup = terms[terms['termType'] == 'FSN'][['conceptId', 'term']].drop_duplicates('conceptId')
    fsn_lookup.columns = ['snomedConceptId', 'snomedFSN']
    cmap = cmap.merge(fsn_lookup, on='snomedConceptId', how='left')
    
    # correlationId 轉人讀標籤
    CORRELATION_MAP = {
        '447561005': 'EXACT_MATCH',
        '447559001': 'BROAD_TO_NARROW',
        '447557004': 'NARROW_TO_BROAD',
        '447558009': 'PARTIAL_OVERLAP',
        '447560006': 'NOT_MAPPABLE',
    }
    cmap['correlationType'] = cmap['correlationId'].map(CORRELATION_MAP).fillna('UNKNOWN')
    
    # 排序
    cmap = cmap.sort_values(
        ['snomedConceptId', 'mapGroup', 'mapPriority']
    ).reset_index(drop=True)
    
    print(f"  有效對照: {len(cmap):,} 筆")
    print(f"  涵蓋 SNOMED 概念: {cmap['snomedConceptId'].nunique():,} 個")
    print(f"  涵蓋 ICD-10 碼: {cmap['icd10Code'].nunique():,} 個")
    print(f"  對應精確度分布:")
    for ct, cnt in cmap['correlationType'].value_counts().items():
        print(f"    {ct}: {cnt:,}")
    
    cmap_path = os.path.join(OUTPUT_DIR, 'concept_map.csv')
    cmap.to_csv(cmap_path, index=False, encoding='utf-8-sig')
    print(f"\n  ✅ 已存檔: {cmap_path}")
else:
    print("  ⚠ ExtendedMap 檔案為空，跳過")


# ============================================================
# 6. 產出快速查詢用的 lookup dict (JSON)
# ============================================================
print("\n" + "=" * 60)
print("步驟 5: 產出 lookup JSON + 統計摘要")
print("=" * 60)

# --- 用向量化方式建 lookup，不用 groupby.apply ---

# 取 FSN lookup: conceptId → fsn term
fsn_df = terms[terms['termType'] == 'FSN'].drop_duplicates('conceptId')[['conceptId', 'term', 'semanticTag']]
fsn_dict = fsn_df.set_index('conceptId')[['term', 'semanticTag']].to_dict('index')

# 取 Synonym lookup: conceptId → list of synonyms
syn_df = terms[terms['termType'] == 'Synonym'][['conceptId', 'term']]
syn_dict = syn_df.groupby('conceptId')['term'].apply(list).to_dict()

# 組合，只取前 1000 筆當範例（完整版幾十萬筆太大）
sample_ids = list(fsn_dict.keys())[:1000]
sample_lookup = {}
for cid in sample_ids:
    info = fsn_dict.get(cid, {})
    sample_lookup[cid] = {
        'fsn': info.get('term'),
        'synonyms': syn_dict.get(cid, []),
        'semanticTag': info.get('semanticTag'),
    }

lookup_path = os.path.join(OUTPUT_DIR, 'snomed_lookup_sample.json')
with open(lookup_path, 'w', encoding='utf-8') as f:
    json.dump(sample_lookup, f, ensure_ascii=False, indent=2)
print(f"  ✅ 範例 lookup: {lookup_path} ({len(sample_lookup)} 筆)")

# 統計摘要
summary = {
    'total_active_concepts': len(active_concept_ids),
    'total_terms': len(terms),
    'total_fsn': len(terms[terms['termType'] == 'FSN']),
    'total_synonyms': len(terms[terms['termType'] == 'Synonym']),
    'semantic_tag_distribution': terms.drop_duplicates('conceptId')['semanticTag'].value_counts().head(20).to_dict(),
    'concept_map_entries': len(cmap) if not extended_map.empty else 0,
    'hierarchy_entries': len(hierarchy) if not relationships.empty else 0,
    'output_files': [
        'snomed_terms.csv        — RAG embedding 用的術語庫',
        'snomed_hierarchy.csv    — 概念階層與語義關係',
        'concept_map.csv         — SNOMED CT → ICD-10 對照表',
        'snomed_lookup_sample.json — 快速查表範例 (前1000筆)',
        'snomed_summary.json     — 本檔（統計摘要）',
    ]
}

summary_path = os.path.join(OUTPUT_DIR, 'snomed_summary.json')
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"  ✅ 統計摘要: {summary_path}")


# ============================================================
# 完成
# ============================================================
print("\n" + "=" * 60)
print("✅ 全部完成！產出檔案在: " + OUTPUT_DIR)
print("=" * 60)
print("""
產出檔案說明:
┌─────────────────────────────┬──────────────────────────────────┐
│ 檔案                         │ 用途                              │
├─────────────────────────────┼──────────────────────────────────┤
│ snomed_terms.csv            │ RAG embedding 語料庫              │
│                             │ 欄位: conceptId, term, termType, │
│                             │       semanticTag, textDefinition│
├─────────────────────────────┼──────────────────────────────────┤
│ snomed_hierarchy.csv        │ 概念階層 (Is-a) + 語義關係        │
│                             │ 欄位: childId, parentId,         │
│                             │       relationshipType           │
├─────────────────────────────┼──────────────────────────────────┤
│ concept_map.csv             │ SNOMED CT → ICD-10 對照表         │
│                             │ 欄位: snomedConceptId, icd10Code,│
│                             │       snomedFSN, correlationType │
├─────────────────────────────┼──────────────────────────────────┤
│ snomed_lookup_sample.json   │ ID→名稱 快速查表 (範例)            │
├─────────────────────────────┼──────────────────────────────────┤
│ snomed_summary.json         │ 資料集統計摘要                     │
└─────────────────────────────┴──────────────────────────────────┘

下一步:
  1. ConceptMap 查詢 → 直接讀 concept_map.csv
  2. RAG 建索引     → 讀 snomed_terms.csv，對 term 欄位做 embedding
  3. Ollama rerank  → 用候選結果餵給 local LLM 做排序驗證
""")