from sqlalchemy import Column, Integer, Text, DateTime
from datetime import datetime, timezone

from app.database import LOINCBase


class LoincMapping(LOINCBase):
    """健保碼 → LOINC 對照表（整併大平台 + 衛福部）"""
    __tablename__ = "loinc_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhi_code = Column(Text, nullable=False, index=True, comment="健保碼")
    nhi_name_eng = Column(Text, comment="健保項目英文名稱")
    nhi_name_cht = Column(Text, comment="健保項目中文名稱")
    loinc_code = Column(Text, nullable=False, index=True, comment="LOINC code")
    long_common_name = Column(Text, comment="LOINC 完整名稱")
    component = Column(Text, comment="軸1: 分析物")
    property = Column(Text, comment="軸2: 量測性質")
    time_aspect = Column(Text, comment="軸3: 時間")
    system = Column(Text, comment="軸4: 檢體來源")
    scale = Column(Text, comment="軸5: 尺度")
    method = Column(Text, comment="軸6: 方法")
    loinc_class = Column("class", Text, comment="LOINC 分類")
    example_units = Column(Text, comment="範例單位")
    relation = Column(Text, comment="關聯程度 (H/A/S/C/P/L/U)")
    source = Column(Text, comment="資料來源 (platform/mohw)")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
