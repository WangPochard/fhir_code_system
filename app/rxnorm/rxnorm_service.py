"""
RxNorm API 查詢服務

透過 NLM RxNav REST API 將健保藥品成分對應到 RxCUI。
免費、不需 API key。

API 文件: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

try:
    from ..config import get_settings
except ImportError:
    from app.config import get_settings

logger = logging.getLogger(__name__)

# 括號內容，查詢時要去掉
SALT_SUFFIXES = re.compile(
    r"\s*\("
    r"(?:HCL|HCl|HYDROCHLORIDE|SULFATE|SODIUM|POTASSIUM|CALCIUM|"
    r"MALEATE|MESYLATE|FUMARATE|TARTRATE|PHOSPHATE|ACETATE|BROMIDE|"
    r"CITRATE|SUCCINATE|BESYLATE|NITRATE|BASE)"
    r"\)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RxNormMatch:
    """單筆 RxNorm 比對結果"""

    rxcui: str
    name: str
    tty: str  # term type: IN, SCD, SBD, etc.
    source: str = ""  # 哪個 API 找到的


@dataclass
class RxNormRelated:
    """getAllRelatedInfo 回傳的完整關係樹"""

    rxcui: str
    groups: dict[str, list[RxNormMatch]] = field(default_factory=dict)

    def get_by_tty(self, *ttys: str) -> list[RxNormMatch]:
        result = []
        for tty in ttys:
            result.extend(self.groups.get(tty, []))
        return result

    @property
    def ingredients(self) -> list[RxNormMatch]:
        return self.get_by_tty("IN", "MIN", "PIN")

    @property
    def clinical_drugs(self) -> list[RxNormMatch]:
        return self.get_by_tty("SCD")

    @property
    def branded_drugs(self) -> list[RxNormMatch]:
        return self.get_by_tty("SBD")

    @property
    def dose_forms(self) -> list[RxNormMatch]:
        return self.get_by_tty("DF")

    @property
    def all_ttys(self) -> list[str]:
        return list(self.groups.keys())


@dataclass
class RxNormLookupResult:
    """一筆健保藥品的 RxNorm 查詢結果"""

    drug_code: str
    ingredient_name: str
    query_used: str = ""
    matches: list[RxNormMatch] = field(default_factory=list)
    error: str = ""

    @property
    def best_match(self) -> Optional[RxNormMatch]:
        priority = {
            "IN": 0, "PIN": 1, "MIN": 2,
            "SCD": 3, "SBD": 4,
            "GPCK": 5, "BPCK": 6,
        }
        if not self.matches:
            return None
        return min(self.matches, key=lambda m: priority.get(m.tty, 99))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RxNormService:
    """
    RxNorm API 查詢服務

    【查 RxCUI】建 mapping 用
      - approximate_match(term)                模糊比對，容錯最高
      - find_rxcui_by_name(name)               精確藥名查 RxCUI
      - find_rxcui_by_id(id_type, id_value)    用其他代碼查（NDC, ATC 等）
      - get_drugs(ingredient)                  用成分名列出所有相關藥品

    【查詳細資訊】驗證 & 補充資料
      - get_all_related(rxcui)                 查完整關係樹
      - get_related_by_type(rxcui, ttys)       只查特定 TTY 類型
      - get_properties(rxcui)                  查 RxCUI 的名稱、TTY
      - get_all_properties(rxcui)              查所有屬性（含 ATC、SNOMED 等跨碼）

    【NDC 相關】
      - get_ndcs(rxcui)                        用 RxCUI 查 NDC 碼

    【輔助工具】
      - get_spelling_suggestions(name)         拼字建議

    【整合查詢】
      - lookup(ingredient_name, drug_code)     依序嘗試多種策略查 RxCUI
      - lookup_batch(df, ...)                  批次查詢 DataFrame
    """

    def __init__(self, delay: float = 0.1):
        settings = get_settings()
        self.base_url = settings.nlm_url.rstrip("/") + "/REST"
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # 內部 HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{path}"
        time.sleep(self.delay)
        try:
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("API 請求失敗: %s %s → %s", path, params, e)
            return {}

    @staticmethod
    def _clean_ingredient(name: str) -> str:
        cleaned = SALT_SUFFIXES.sub("", name).strip()
        return cleaned if cleaned else name.strip()

    # ==================================================================
    # 第一類: 查 RxCUI（建 mapping 的核心）
    # ==================================================================

    def approximate_match(
        self, term: str, max_entries: int = 5
    ) -> list[RxNormMatch]:
        """
        模糊比對查 RxCUI — 建 mapping 時最常用

        容錯度高，即使藥名拼寫不完全正確也能找到。
        內部會自動去除鹽類後綴。

        Args:
            term: 藥品名稱或成分（如 "metformin 500mg tablet"）
            max_entries: 最多回傳幾筆候選

        Returns:
            RxNormMatch 列表，依相關度排序
        """
        cleaned = self._clean_ingredient(term)
        data = self._get(
            "approximateTerm.json",
            {"term": cleaned, "maxEntries": max_entries},
        )
        matches = []
        group = data.get("approximateGroup", {})
        for candidate in group.get("candidate", []):
            matches.append(
                RxNormMatch(
                    rxcui=candidate.get("rxcui", ""),
                    name=candidate.get("name", ""),
                    tty=candidate.get("tty", ""),
                    source="approximateMatch",
                )
            )
        return matches

    def find_rxcui_by_name(self, name: str) -> list[RxNormMatch]:
        """用精確藥名查 RxCUI（search=2 為 normalized match）"""
        data = self._get("rxcui.json", {"name": name, "search": 2})
        matches = []
        id_group = data.get("idGroup", {})
        for rxcui in id_group.get("rxnormId", []):
            props = self.get_properties(rxcui)
            if props:
                matches.append(
                    RxNormMatch(
                        rxcui=rxcui,
                        name=props.get("name", ""),
                        tty=props.get("tty", ""),
                        source="findRxcuiByString",
                    )
                )
        return matches

    def find_rxcui_by_id(
        self, id_type: str, id_value: str
    ) -> list[RxNormMatch]:
        """
        用其他代碼系統查 RxCUI

        Args:
            id_type: "NDC", "ATC", "SNOMEDCT", "MESH" 等
            id_value: 代碼值
        """
        data = self._get("rxcui.json", {"idtype": id_type, "id": id_value})
        matches = []
        id_group = data.get("idGroup", {})
        for rxcui in id_group.get("rxnormId", []):
            props = self.get_properties(rxcui)
            if props:
                matches.append(
                    RxNormMatch(
                        rxcui=rxcui,
                        name=props.get("name", ""),
                        tty=props.get("tty", ""),
                        source=f"findRxcuiById:{id_type}",
                    )
                )
        return matches

    def get_drugs(self, ingredient: str) -> list[RxNormMatch]:
        """用成分名查出所有相關藥品（不同劑量、劑型都列出來）"""
        cleaned = self._clean_ingredient(ingredient)
        data = self._get("drugs.json", {"name": cleaned})
        matches = []
        drug_group = data.get("drugGroup", {})
        for group in drug_group.get("conceptGroup", []):
            tty = group.get("tty", "")
            for prop in group.get("conceptProperties", []):
                matches.append(
                    RxNormMatch(
                        rxcui=prop.get("rxcui", ""),
                        name=prop.get("name", ""),
                        tty=tty,
                        source="getDrugs",
                    )
                )
        return matches

    # ==================================================================
    # 第二類: 查詳細資訊（驗證 & 補充資料）
    # ==================================================================

    def get_all_related(self, rxcui: str) -> RxNormRelated:
        """
        查完整關係樹 — 一次拿到成分、劑型、劑量、品牌等所有關聯概念

        回傳 RxNormRelated 物件，可用:
          .ingredients      成分 (IN, MIN, PIN)
          .clinical_drugs   臨床藥物 (SCD)
          .branded_drugs    品牌藥 (SBD)
          .dose_forms       劑型 (DF)
          .all_ttys         所有 TTY 類型清單
          .get_by_tty(...)  取得指定 TTY 類型
        """
        data = self._get(f"rxcui/{rxcui}/allrelated.json")
        result = RxNormRelated(rxcui=rxcui)
        all_related = data.get("allRelatedGroup", {})
        for group in all_related.get("conceptGroup", []):
            tty = group.get("tty", "")
            concepts = []
            for prop in group.get("conceptProperties", []):
                concepts.append(
                    RxNormMatch(
                        rxcui=prop.get("rxcui", ""),
                        name=prop.get("name", ""),
                        tty=tty,
                        source="getAllRelatedInfo",
                    )
                )
            if concepts:
                result.groups[tty] = concepts
        return result

    def get_related_by_type(
        self, rxcui: str, ttys: list[str]
    ) -> list[RxNormMatch]:
        """只查特定 TTY 類型的關聯概念"""
        tty_param = "+".join(ttys)
        data = self._get(
            f"rxcui/{rxcui}/related.json", {"tty": tty_param}
        )
        matches = []
        related_group = data.get("relatedGroup", {})
        for group in related_group.get("conceptGroup", []):
            tty = group.get("tty", "")
            for prop in group.get("conceptProperties", []):
                matches.append(
                    RxNormMatch(
                        rxcui=prop.get("rxcui", ""),
                        name=prop.get("name", ""),
                        tty=tty,
                        source="getRelatedByType",
                    )
                )
        return matches

    def get_properties(self, rxcui: str) -> dict:
        """查 RxCUI 的基本屬性（名稱、TTY、同義詞）"""
        data = self._get(f"rxcui/{rxcui}/properties.json")
        return data.get("properties", {})

    def get_all_properties(self, rxcui: str) -> dict[str, list[dict]]:
        """查所有屬性，包括 ATC、SNOMED CT、DrugBank 等跨碼對應"""
        data = self._get(f"rxcui/{rxcui}/allProperties.json", {"prop": "all"})
        result: dict[str, list[dict]] = {}
        prop_group = data.get("propConceptGroup", {})
        for prop in prop_group.get("propConcept", []):
            category = prop.get("propCategory", "OTHER")
            entry = {
                "propName": prop.get("propName", ""),
                "propValue": prop.get("propValue", ""),
            }
            result.setdefault(category, []).append(entry)
        return result

    # ==================================================================
    # 第三類: NDC 相關
    # ==================================================================

    def get_ndcs(self, rxcui: str) -> list[str]:
        """用 RxCUI 查對應的 NDC 碼"""
        data = self._get(f"rxcui/{rxcui}/ndcs.json")
        ndc_group = data.get("ndcGroup", {})
        return ndc_group.get("ndcList", {}).get("ndc", [])

    # ==================================================================
    # 第四類: 輔助工具
    # ==================================================================

    def get_spelling_suggestions(self, name: str) -> list[str]:
        """拼字建議 — 藥名打錯時用"""
        data = self._get("spellingsuggestions.json", {"name": name})
        suggestion_group = data.get("suggestionGroup", {})
        return suggestion_group.get("suggestionList", {}).get("suggestion", [])

    # ==================================================================
    # 整合查詢（建 mapping 流程）
    # ==================================================================

    def lookup(
        self,
        ingredient_name: str,
        drug_code: str = "",
    ) -> RxNormLookupResult:
        """
        查詢單筆藥品的 RxCUI（建 mapping 用）

        依序嘗試:
        1. getDrugs（用去鹽類的成分名，最精準）
        2. approximateMatch（模糊比對，容錯高）
        3. findRxcuiByString（正規化比對）
        """
        result = RxNormLookupResult(
            drug_code=drug_code,
            ingredient_name=ingredient_name,
        )

        if not ingredient_name or not ingredient_name.strip():
            result.error = "成分名稱為空"
            return result

        cleaned = self._clean_ingredient(ingredient_name)
        result.query_used = cleaned

        # 策略 1: getDrugs
        matches = self.get_drugs(cleaned)
        if matches:
            result.matches = matches
            return result

        # 策略 2: approximateMatch
        matches = self.approximate_match(cleaned)
        if matches:
            result.matches = matches
            return result

        # 策略 3: findRxcuiByString
        matches = self.find_rxcui_by_name(cleaned)
        if matches:
            result.matches = matches
            return result

        result.error = "找不到匹配的 RxCUI"
        return result

    def lookup_batch(
        self,
        df,
        ingredient_col: str = "ingredient_name",
        drug_code_col: str = "drug_code",
        limit: int | None = None,
    ) -> list[RxNormLookupResult]:
        """
        批次查詢 DataFrame 裡的藥品

        同一個成分只查一次 API（自動去重），節省時間。
        """
        seen: dict[str, RxNormLookupResult] = {}
        results = []
        rows = df.head(limit) if limit else df

        for _, row in rows.iterrows():
            ingredient = row.get(ingredient_col, "").strip()
            drug_code = row.get(drug_code_col, "").strip()

            if not ingredient:
                continue

            cleaned = self._clean_ingredient(ingredient)
            if cleaned in seen:
                prev = seen[cleaned]
                result = RxNormLookupResult(
                    drug_code=drug_code,
                    ingredient_name=ingredient,
                    query_used=prev.query_used,
                    matches=prev.matches,
                    error=prev.error,
                )
            else:
                result = self.lookup(ingredient, drug_code)
                seen[cleaned] = result

            results.append(result)

        logger.info(
            "批次查詢完成: %d 筆, %d 筆有匹配",
            len(results),
            sum(1 for r in results if r.matches),
        )
        return results
    

if __name__ == "__main__":
    name = "ethinyl estradiol 0.03 MG"
    service = RxNormService(delay=0.15)
    matches = service.approximate_match(name, max_entries=3)

    if matches:
        best = matches[0]
        print(f"    → RxCUI: {best.rxcui}  {best.name}  ({best.tty})")
        if len(matches) > 1:
            print(f"    其他候選:")
            for m in matches[1:]:
                print(f"      RxCUI={m.rxcui}  {m.name}  ({m.tty})")
    else:
        print(f"    → 查無結果")
    print()

    print("=" * 30)
    matches = service.get_drugs("ethinyl estradiol")
    related = service.get_all_related(matches[0].rxcui)
    tw_core_ttys = ["SCD", "SBD", "GPCK", "BPCK", "SCDG", "SBDG", "SCDF", "SBDF"]
    valid_matches = related.get_by_tty(*tw_core_ttys)
    for m in valid_matches:
        print(f"      RxCUI={m.rxcui}  {m.name}  ({m.tty})")

    print("=" * 30)
    print("")

    related = service.get_all_related("748865")
    for tty in related.all_ttys:
        concepts = related.get_by_tty(tty)
        print(f"\n  [{tty}] ({len(concepts)} 筆)")
        for c in concepts[:5]:
            print(f"    RxCUI={c.rxcui:<10s}  {c.name}")
        if len(concepts) > 5:
            print(f"    ... 還有 {len(concepts) - 5} 筆")