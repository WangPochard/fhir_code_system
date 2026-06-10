"""
健保用藥品項資料模型
"""

from dataclasses import dataclass


@dataclass
class NhiDrugPrice:
    """健保用藥品項單筆紀錄"""

    new_mark: str                   # 1  New_mark
    oral_tablet_note: str           # 2  口服錠註記
    single_compound_note: str       # 3  單/複方註記
    drug_code: str                  # 4  藥品代碼
    reference_price: str            # 5  藥價參考金額
    price_start_date: str           # 6  藥價參考日期 (民國年 YYYMMDD)
    price_end_date: str             # 7  藥價參考截止日期
    english_name: str               # 8  藥品英文名稱
    spec_amount: str                # 9  藥品規格量
    spec_unit: str                  # 10 藥品規格單位
    ingredient_name: str            # 11 成份名稱
    ingredient_amount: str          # 12 成份含量
    ingredient_unit: str            # 13 成份含量單位
    dosage_form: str                # 14 藥品劑型
    manufacturer_name: str          # 16 藥商名稱
    drug_category: str              # 18 藥品分類
    quality_code: str               # 19 品質分類碼
    chinese_name: str               # 20 藥品中文名稱
    group_name: str                 # 21 分類分組名稱
    compound1_name: str             # 22 （複方一）成份名稱
    compound1_amount: str           # 23 （複方一）藥品成份含量
    compound1_unit: str             # 24 （複方一）藥品成份含量單位
    compound2_name: str             # 25 （複方二）成份名稱
    compound2_amount: str           # 26 （複方二）藥品成份含量
    compound2_unit: str             # 27 （複方二）藥品成份含量單位
    compound3_name: str             # 28 （複方三）成份名稱
    compound3_amount: str           # 29 （複方三）藥品成份含量
    compound3_unit: str             # 30 （複方三）藥品成份含量單位
    compound4_name: str             # 31 （複方四）成份名稱
    compound4_amount: str           # 32 （複方四）藥品成份含量
    compound4_unit: str             # 33 （複方四）藥品成份含量單位
    compound5_name: str             # 34 （複方五）成份名稱
    compound5_amount: str           # 35 （複方五）藥品成份含量
    compound5_unit: str             # 36 （複方五）藥品成份含量單位
    manufacturer_full_name: str     # 37 製造廠名稱
    atc_code: str                   # 38 ATC CODE
    not_produced_5yr: str           # 39 未生產或未輸入達五年