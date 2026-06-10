import os
import pandas as pd

BASE_DIR = os.path.abspath(os.path.join(os.getcwd(), 'SnomedCT_InternationalRF2_PRODUCTION_20250701T120000Z'))
TERM_FILE = os.path.join(BASE_DIR, 'Snapshot', 'Terminology', 'sct2_Description_Snapshot-en_INT_20250701.txt')
CONCEPT_FILE = os.path.join(BASE_DIR, 'Snapshot', 'Terminology', 'sct2_Concept_Snapshot_INT_20250701.txt')


# 載入描述檔
desc = pd.read_csv(
    TERM_FILE, 
    sep='\t', dtype=str
)

# 載入概念檔，確認是 active 的概念
concepts = pd.read_csv(
    CONCEPT_FILE, 
    sep='\t', dtype=str
)
active_concepts = set(concepts[concepts['active'] == '1']['id'])

# 只保留 active 的描述，且該概念也是 active 的
desc = desc[(desc['active'] == '1') & (desc['conceptId'].isin(active_concepts))]

# typeId 區分 FSN 和 Synonym
# 900000000000003001 = FSN (Fully Specified Name)
# 900000000000013009 = Synonym
desc['type'] = desc['typeId'].map({
    '900000000000003001': 'FSN',
    '900000000000013009': 'Synonym'
})

# 這就是你要拿去建 embedding 的語料
# 每一行 = 一個 (term, conceptId, type) 的組合
print(f"共 {len(desc)} 筆描述，涵蓋 {desc['conceptId'].nunique()} 個概念")

print(desc)