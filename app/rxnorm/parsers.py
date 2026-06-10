"""
健保用藥品項查詢檔解析器

格式說明：
- 固定寬度格式，無標題列
- 位置以 big5 byte position 為準（中文字佔 2 bytes）
- 官方提供 b5 及 txt (utf-8) 兩種編碼
- 欄位定義來源：健保用藥品項查詢檔欄位格式說明 (109.01.09 更新)
"""

import logging
from pathlib import Path
from typing import Iterator

import pandas as pd

from .models import NhiDrugPrice

logger = logging.getLogger(__name__)


class NhiDrugPriceParser:
    """
    解析健保用藥品項查詢檔 (.txt / .b5)

    自動偵測編碼，丟路徑就能跑。

    Usage:
        parser = NhiDrugPriceParser()
        df = parser.parse("sample_data/file.txt")
        parser.to_csv("sample_data/file.txt", "output/result.csv")
    """

    # 欄位定義：(欄位名, 起始位置, 結束位置)
    # 位置為 1-based，與官方文件一致，切割時再轉 0-based
    FIELD_SPEC: list[tuple[str, int, int]] = [
        ("new_mark",              1,    2),
        ("oral_tablet_note",      4,   13),
        ("single_compound_note", 15,   16),
        ("drug_code",            18,   27),
        ("reference_price",      29,   37),
        ("price_start_date",     39,   45),
        ("price_end_date",       47,   53),
        ("english_name",         55,  174),
        ("spec_amount",         176,  182),
        ("spec_unit",           184,  235),
        ("ingredient_name",     237,  292),
        ("ingredient_amount",   294,  305),
        ("ingredient_unit",     307,  357),
        ("dosage_form",         359,  444),
        ("manufacturer_name",   605,  624),
        ("drug_category",       768,  768),
        ("quality_code",        770,  770),
        ("chinese_name",        772,  899),
        ("group_name",          901, 1200),
        ("compound1_name",     1201, 1256),
        ("compound1_amount",   1259, 1269),
        ("compound1_unit",     1271, 1321),
        ("compound2_name",     1323, 1378),
        ("compound2_amount",   1380, 1390),
        ("compound2_unit",     1392, 1442),
        ("compound3_name",     1444, 1499),
        ("compound3_amount",   1501, 1511),
        ("compound3_unit",     1513, 1563),
        ("compound4_name",     1565, 1620),
        ("compound4_amount",   1622, 1632),
        ("compound4_unit",     1634, 1684),
        ("compound5_name",     1686, 1741),
        ("compound5_amount",   1743, 1753),
        ("compound5_unit",     1755, 1805),
        ("manufacturer_full_name", 1807, 1848),
        ("atc_code",           1850, 1857),
        ("not_produced_5yr",   1859, 1859),
    ]

    EXPECTED_LINE_WIDTH = 1859

    @staticmethod
    def _detect_encoding(raw_head: bytes) -> str:
        """從前幾 KB 判斷編碼"""
        if raw_head.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        try:
            raw_head.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "big5"

    def _read_as_big5_lines(self, filepath: Path) -> list[bytes]:
        """
        讀檔並統一轉成 big5 bytes 行列表。
        用 binary mode 讀，不會因為編碼問題炸掉。
        """
        raw = filepath.read_bytes()
        encoding = self._detect_encoding(raw[:4096])
        logger.info("偵測編碼: %s (%s)", encoding, filepath.name)

        if encoding.startswith("utf"):
            text = raw.decode(encoding, errors="replace")
            return [
                line.rstrip().encode("big5", errors="replace")
                for line in text.splitlines()
            ]
        else:
            # big5 原生，直接 split
            return [
                line.rstrip(b"\r\n").rstrip(b" ")
                for line in raw.split(b"\n")
            ]

    def _parse_line(self, b: bytes, line_no: int) -> dict | None:
        """解析一行 big5 bytes"""
        if not b or not b.strip():
            return None

        b = b.ljust(self.EXPECTED_LINE_WIDTH)

        row = {}
        for field_name, start, end in self.FIELD_SPEC:
            raw = b[start - 1 : end]
            row[field_name] = raw.decode("big5", errors="replace").strip()

        if not row.get("drug_code"):
            logger.warning("第 %d 行：藥品代碼為空，跳過", line_no)
            return None

        return row

    def _iter_records(self, filepath: Path) -> Iterator[dict]:
        """逐行迭代解析"""
        lines = self._read_as_big5_lines(filepath)
        for line_no, b in enumerate(lines, start=1):
            row = self._parse_line(b, line_no)
            if row is not None:
                yield row

    def parse(self, filepath: str | Path) -> pd.DataFrame:
        """解析檔案，回傳 DataFrame"""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"找不到檔案: {filepath}")

        records = list(self._iter_records(filepath))
        logger.info("共解析 %d 筆紀錄 (%s)", len(records), filepath.name)

        if not records:
            columns = [name for name, _, _ in self.FIELD_SPEC]
            return pd.DataFrame(columns=columns)

        return pd.DataFrame(records)

    def to_csv(
        self,
        input_path: str | Path,
        output_path: str | Path,
        encoding_out: str = "utf-8-sig",
    ) -> int:
        """將 txt 轉成 CSV"""
        df = self.parse(input_path)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False, encoding=encoding_out)

        logger.info("已寫入 %d 筆 → %s", len(df), output_path)
        return len(df)

    def to_model(self, filepath: str | Path) -> list[NhiDrugPrice]:
        """解析檔案，回傳 NhiDrugPrice dataclass list"""
        return [
            NhiDrugPrice(**row) for row in self._iter_records(Path(filepath))
        ]