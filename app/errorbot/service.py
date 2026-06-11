import re
import json
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

from app.llm.llm import get_llm_service
from app.logger import get_logger

logger = get_logger("rag.errorbot")


CODESYSTEM_LOOKUP_URLS = {
    "http://loinc.org": "https://loinc.org/search/?t=1&s=",
    "http://snomed.info/sct": "https://browser.ihtsdotools.org/?perspective=full&conceptId1=",
    "http://hl7.org/fhir/sid/icd-10": "https://icd.who.int/browse10/2019/en#/",
    "http://hl7.org/fhir/sid/icd-10-cm": "https://icd.who.int/browse10/2019/en#/",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=",
    "https://twcore.mohw.gov.tw/ig/twcore/CodeSystem/icd-10-cm-2023-tw": "https://twcore.mohw.gov.tw/ig/twcore/CodeSystem/icd-10-cm-2023-tw",
    "http://hl7.org/fhir/sid/ndc": "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query=",
}

VALUESET_BROWSE_URL = "https://twcore.mohw.gov.tw/ig/twcore/"
FHIR_TERMINOLOGY_URL = "https://terminology.hl7.org/"

CODESYSTEM_DISPLAY_NAMES = {
    "http://loinc.org": "LOINC（醫學檢驗代碼）",
    "http://snomed.info/sct": "SNOMED CT（臨床術語代碼）",
    "http://hl7.org/fhir/sid/icd-10": "ICD-10（國際疾病分類）",
    "http://hl7.org/fhir/sid/icd-10-cm": "ICD-10-CM（國際疾病分類）",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm（藥物代碼）",
}

# FHIR resource type → 中文名稱
RESOURCE_DISPLAY_NAMES = {
    "Claim": "申請單",
    "Encounter": "就診記錄",
    "Encounter-opd": "門診就診記錄",
    "Patient": "病患資料",
    "Practitioner": "醫事人員",
    "Organization": "醫療機構",
    "Condition": "診斷",
    "Observation": "觀察/檢查結果",
    "MedicationRequest": "藥物處方",
    "Coverage": "保險資料",
    "DiagnosticReport": "診斷報告",
    "ImagingStudy": "影像檢查",
    "Composition": "病歷摘要",
    "ClinicalImpression": "臨床評估",
    "CarePlan": "照護計畫",
    "DocumentReference": "文件參考",
    "AllergyIntolerance": "過敏記錄",
    "Specimen": "檢體",
    "Procedure": "處置",
}


def _get_resource_display(resource_type: str) -> str:
    for key, name in RESOURCE_DISPLAY_NAMES.items():
        if resource_type.startswith(key):
            return name
    return resource_type


def _get_system_display_name(system: str) -> str:
    for prefix, name in CODESYSTEM_DISPLAY_NAMES.items():
        if system.startswith(prefix):
            return name
    if "twcore" in system or "nhi" in system:
        return "健保署代碼系統"
    return system


def _lookup_url_for_system(system: str, code: str = "") -> str:
    for sys_prefix, base_url in CODESYSTEM_LOOKUP_URLS.items():
        if system.startswith(sys_prefix):
            return f"{base_url}{code}"
    if "twcore.mohw.gov.tw" in system:
        return system
    return ""


def _lookup_url_for_valueset(diagnostics: str) -> str:
    vs_url_match = re.search(r"\((https?://\S+?)(?:\|[\d.]+)?\)", diagnostics)
    if vs_url_match:
        return vs_url_match.group(1)
    if "twcore" in diagnostics:
        return VALUESET_BROWSE_URL
    return FHIR_TERMINOLOGY_URL


class ValidationExplainService:
    """將 FHIR OperationOutcome 驗證結果翻譯成醫療人員看得懂的說明。

    子類別只需覆寫 _call_llm()，其餘邏輯完全共用。
    """

    def _call_llm(self, prompt: str) -> str:
        return get_llm_service()._call_llm(prompt)

    # ------------------------------------------------------------------
    # 主要入口
    # ------------------------------------------------------------------

    def explain(self, operation_outcome: Dict[str, Any]) -> Dict[str, Any]:
        issues = operation_outcome.get("issue", [])
        if not issues:
            return {
                "summary": "驗證通過，沒有發現任何問題。",
                "total_errors": 0,
                "total_warnings": 0,
                "issues": [],
            }

        # 先建立 UUID → 資源類型對應表，供後續說明使用
        uuid_to_resource = self._build_uuid_resource_map(issues)

        # 找出「哪些 UUID 本身有根因錯誤」（即該資源自己有欄位問題）
        problematic_uuids = self._find_problematic_uuids(issues)

        # 分類並解釋所有 issue
        explained = self._explain_all_issues(issues, uuid_to_resource, problematic_uuids)

        # 根因排前、連帶排後
        root_causes = [i for i in explained if i["category"] == "root_cause"]
        cascading = [i for i in explained if i["category"] == "cascading"]

        error_count = sum(1 for i in issues if i.get("severity") == "error")
        warning_count = sum(1 for i in issues if i.get("severity") == "warning")
        root_count = len(root_causes)
        cascade_count = len(cascading)

        summary = f"驗證發現 {error_count} 個錯誤、{warning_count} 個警告。"
        if root_count and cascade_count:
            summary += (
                f"其中 {root_count} 個為資料本身的錯誤（需優先修正），"
                f"{cascade_count} 個為連帶錯誤（修正根因後通常會自動消失）。"
                f"建議先集中修正「根因錯誤」部分。"
            )
        elif root_count:
            summary += f"全部 {root_count} 個均為資料本身錯誤，請逐一修正。"
        elif cascade_count:
            summary += f"全部 {cascade_count} 個均為連帶錯誤，請先確認被引用的資料是否正確。"

        return {
            "summary": summary,
            "total_errors": error_count,
            "total_warnings": warning_count,
            "issues": root_causes + cascading,
        }

    # ------------------------------------------------------------------
    # UUID 分析工具
    # ------------------------------------------------------------------

    def _build_uuid_resource_map(self, issues: List[Dict]) -> Dict[str, str]:
        """
        從 issues 的 location 欄位中，建立 urn:uuid:xxx → 資源類型 的對應表。
        例如：location = "Bundle.entry[17].resource/*Encounter/null*/.diagnosis[0].condition"
        → entry index 17 的資源類型是 Encounter
        """
        uuid_map: Dict[str, str] = {}
        # 也從 diagnostics 中抽取被引用的 uuid 和 profile 名稱
        for issue in issues:
            diagnostics = issue.get("diagnostics", "")
            # 從 "Details for urn:uuid:xxx matching against profile .../Encounter-opd-twpas" 抽取
            for match in re.finditer(
                r"Details for (urn:uuid:\S+) matching against profile [^\s,]*/(\w[\w-]*?)(?:\|[^,\s]+)?(?:[,\s]|$)",
                diagnostics,
            ):
                uuid = match.group(1).rstrip(")")
                profile_name = match.group(2)
                # profile name 如 "Encounter-opd-twpas" → 取第一段作為資源類型
                resource_type = profile_name.split("-")[0].capitalize()
                if resource_type and uuid not in uuid_map:
                    uuid_map[uuid] = resource_type

            # 從 "Unable to find a profile match for urn:uuid:xxx among choices: .../Encounter-opd-twpas"
            for match in re.finditer(
                r"Unable to find a profile match for (urn:uuid:\S+) among choices: [^\s,]*/(\w[\w-]*?)(?:\|[^,\s]+)?(?:[,\s]|$)",
                diagnostics,
            ):
                uuid = match.group(1).rstrip(")")
                profile_name = match.group(2)
                resource_type = profile_name.split("-")[0].capitalize()
                if resource_type and uuid not in uuid_map:
                    uuid_map[uuid] = resource_type

        return uuid_map

    def _find_problematic_uuids(self, issues: List[Dict]) -> Dict[str, List[str]]:
        """
        找出「本身有欄位問題」的 UUID，並記錄原因。
        判斷依據：某 issue 的 location 包含 Bundle.entry[N].resource，
        且錯誤是資料欄位問題（代碼找不到、slice 不符、constraint 失敗等），
        而不是「引用了別人」。
        回傳：{uuid: [問題描述, ...]}
        """
        problematic: Dict[str, List[str]] = defaultdict(list)

        for issue in issues:
            diagnostics = issue.get("diagnostics", "")
            locations = issue.get("location", [])
            severity = issue.get("severity", "")

            if severity not in ("error", "warning"):
                continue

            # 跳過「無法找到 profile 對應」類型（這是連帶錯誤的特徵）
            if "Unable to find a profile match for urn:uuid" in diagnostics:
                continue
            if "Details for urn:uuid" in diagnostics and "matching against profile" in diagnostics:
                continue
            if "matching slice is required, but not found" in diagnostics:
                continue

            # 這個 issue 是某個實際資源本身的問題
            for loc in locations:
                entry_match = re.search(r"Bundle\.entry\[(\d+)\]\.resource", loc)
                if entry_match:
                    entry_idx = int(entry_match.group(1))
                    short_reason = self._short_reason(diagnostics)
                    problematic[str(entry_idx)].append(short_reason)

        return dict(problematic)

    def _short_reason(self, diagnostics: str) -> str:
        """把一個 diagnostics 訊息濃縮成一句話"""
        if "Code is not found in CodeSystem" in diagnostics:
            m = re.search(r"(\S+)#(\S+)", diagnostics)
            if m:
                return f"代碼 {m.group(2)} 在代碼系統中查不到"
        if "None of the codings provided are in the value set" in diagnostics:
            m = re.search(r"codes = (\S+)#(\S+)", diagnostics)
            code_info = f"（代碼 {m.group(2)}）" if m else ""
            return f"使用的代碼{code_info}不在允許的選項清單中"
        if "No codes in ValueSet belong to CodeSystem" in diagnostics:
            m = re.search(r"code (\S+)#(\S+)", diagnostics, re.IGNORECASE)
            code_info = f"（代碼 {m.group(2)}）" if m else ""
            return f"使用的代碼{code_info}所在的代碼系統在驗證器中找不到"
        if "Concept Display" in diagnostics and "does not match expected" in diagnostics:
            return "代碼顯示名稱與標準不符"
        if "does not match any known slice" in diagnostics:
            return "資料格式不符合規範定義的結構"
        if "Constraint failed" in diagnostics:
            m = re.search(r"Constraint failed: (\S+)", diagnostics)
            return f"未通過規範規則 {m.group(1)}" if m else "未通過規範規則"
        if "UCUM Codes that contain human readable annotations" in diagnostics:
            return "計量單位代碼含有非標準的人類可讀標註"
        return diagnostics[:80] + ("..." if len(diagnostics) > 80 else "")

    # ------------------------------------------------------------------
    # 解釋所有 issues
    # ------------------------------------------------------------------

    def _explain_all_issues(
        self,
        issues: List[Dict],
        uuid_to_resource: Dict[str, str],
        problematic_uuids: Dict[str, List[str]],
    ) -> List[Dict]:

        MAX_DIAGNOSTICS_LEN = 500
        short_issues = [i for i in issues if len(i.get("diagnostics", "")) <= MAX_DIAGNOSTICS_LEN]
        long_issues = [i for i in issues if len(i.get("diagnostics", "")) > MAX_DIAGNOSTICS_LEN]

        # 短訊息嘗試 LLM
        llm_explained: List[Dict] = []
        logger.info(f"issues 分流 | 短訊息（送 LLM）: {len(short_issues)} 筆，長訊息（規則式）: {len(long_issues)} 筆")
        if short_issues:
            issues_text = self._format_issues_for_prompt(short_issues)
            prompt = self._build_prompt(issues_text)
            try:
                logger.info(f"呼叫 LLM | backend={self.__class__.__name__}")
                response = self._call_llm(prompt)
                logger.info("LLM 回應成功")
                llm_explained = self._parse_issues_from_response(
                    response, short_issues, uuid_to_resource, problematic_uuids
                )
            except Exception as e:
                logger.error(f"LLM 解釋失敗，改用規則式: {e}")
                llm_explained = self._explain_with_rules(short_issues, uuid_to_resource, problematic_uuids)

        rule_explained = self._explain_with_rules(long_issues, uuid_to_resource, problematic_uuids)

        return llm_explained + rule_explained

    # ------------------------------------------------------------------
    # 規則式解釋
    # ------------------------------------------------------------------

    def _explain_with_rules(
        self,
        issues: List[Dict],
        uuid_to_resource: Dict[str, str],
        problematic_uuids: Dict[str, List[str]],
    ) -> List[Dict]:
        result = []
        for issue in issues:
            diagnostics = issue.get("diagnostics", "")
            locations = issue.get("location", [])
            location_str = locations[0] if locations else "未知位置"
            category, explanation, suggestion = self._explain_single_issue(
                diagnostics, location_str, uuid_to_resource, problematic_uuids
            )
            ref_url = self._extract_reference_url(diagnostics)
            if ref_url and ref_url not in suggestion:
                suggestion += f" 參考連結：{ref_url}"
            result.append({
                "severity": issue.get("severity", "unknown"),
                "location": location_str,
                "original_message": diagnostics,
                "explanation": explanation,
                "suggestion": suggestion,
                "category": category,
            })
        return result

    def _explain_single_issue(
        self,
        diagnostics: str,
        location_str: str,
        uuid_to_resource: Dict[str, str],
        problematic_uuids: Dict[str, List[str]],
    ) -> Tuple[str, str, str]:
        """
        回傳 (category, explanation, suggestion)
        category: 'root_cause' | 'cascading'
        """
        readable_location = self._humanize_location(location_str)

        # ================================================================
        # 連帶錯誤：「引用了一筆驗不過的資料」
        # ================================================================

        # 模式 A：Unable to find a profile match for urn:uuid:xxx among choices: ...Profile
        ref_mismatch = re.search(
            r"Unable to find a profile match for (urn:uuid:\S+) among choices: [^\s,]*/(\w[\w-]*?)(?:\|[^,\s]+)?$",
            diagnostics,
        )
        if ref_mismatch:
            bad_uuid = ref_mismatch.group(1).rstrip(")")
            expected_profile = ref_mismatch.group(2)
            resource_type = uuid_to_resource.get(bad_uuid, "")
            resource_display = _get_resource_display(resource_type) if resource_type else "某筆資料"
            expected_display = _get_resource_display(expected_profile.split("-")[0].capitalize())

            # 找出被引用資料本身的問題原因
            entry_idx = self._uuid_to_entry_idx(bad_uuid, problematic_uuids, uuid_to_resource)
            if entry_idx is not None and str(entry_idx) in problematic_uuids:
                root_reasons = "、".join(set(problematic_uuids[str(entry_idx)]))
                explanation = (
                    f"{readable_location}引用了一筆「{resource_display}」資料，"
                    f"但該筆資料因為本身有問題（{root_reasons}），"
                    f"無法通過「{expected_display}」格式的驗證，"
                    f"導致這裡的引用也跟著失敗。"
                )
                suggestion = (
                    f"這是連帶錯誤。請先修正被引用的「{resource_display}」資料的欄位問題，"
                    f"此錯誤通常會自動消失。"
                )
            else:
                explanation = (
                    f"{readable_location}引用的「{resource_display}」資料（{bad_uuid[:20]}...）"
                    f"未能通過「{expected_display}」格式的驗證。"
                    f"通常是因為被引用的資料本身有欄位填寫錯誤。"
                )
                suggestion = (
                    f"這是連帶錯誤。請先找出並修正被引用的「{resource_display}」資料的欄位問題，"
                    f"此錯誤通常會自動消失。"
                )
            return "cascading", explanation, suggestion

        # 模式 B：Slice 'xxx': a matching slice is required, but not found
        slice_required = re.search(
            r"Slice '([^']+)'.*matching slice is required, but not found", diagnostics
        )
        if slice_required:
            slice_name = slice_required.group(1)
            resource_hint = slice_name.split(":")[-1] if ":" in slice_name else slice_name
            resource_display = _get_resource_display(resource_hint.capitalize())
            explanation = (
                f"整包資料中應該要有「{resource_display}」，"
                f"但驗證器找不到符合格式要求的「{resource_display}」資料。"
                f"這通常是因為「{resource_display}」本身有欄位錯誤，導致驗證器無法辨識它。"
            )
            suggestion = (
                f"這是連帶錯誤。請先修正「{resource_display}」資料的欄位問題，"
                f"此錯誤通常會自動消失。若「{resource_display}」資料確實缺少，請補充。"
            )
            return "cascading", explanation, suggestion

        # 模式 C：does not match any known slice（Bundle.entry 整筆不符，超長版本）
        if "does not match any known slice" in diagnostics and "slicing is CLOSED" in diagnostics:
            entry_match = re.search(r"Bundle\.entry\[(\d+)\]", location_str)
            entry_desc = f"第 {int(entry_match.group(1)) + 1} 筆" if entry_match else "某筆"
            resource_match = re.search(r"resource/\*(\w+)", location_str)
            resource_type = resource_match.group(1) if resource_match else ""
            resource_display = _get_resource_display(resource_type) if resource_type else "資料"

            # 判斷是欄位層級（根因）還是 entry 整筆層級（連帶）
            # entry 整筆層級：location 僅為 Bundle.entry[N]，不含更深的路徑
            is_entry_level = bool(re.match(r"^Bundle\.entry\[\d+\]$", location_str.strip()))

            if not is_entry_level:
                # 欄位層級：某個具體欄位不符合 slice 規範 → 根因錯誤
                field_part = location_str.split(".")[-1]
                field = re.sub(r"\[\d+\]", "", field_part)
                explanation = (
                    f"{entry_desc}「{resource_display}」資料中的「{field}」欄位"
                    f"填寫的內容不符合健保署 PAS 規範所定義的格式或選項，"
                    f"驗證器無法將該欄位的值歸類到任何允許的 slice。"
                )
                suggestion = (
                    f"請確認「{field}」欄位的填寫格式與代碼是否符合健保署 PAS IG 規範的定義。"
                    f"如需確認允許的值域，可參考健保署 PAS IG 規範（https://nhicore.nhi.gov.tw/pas/）。"
                )
                return "root_cause", explanation, suggestion

            # entry 整筆層級：整筆資料不符合任何 slice → 連帶錯誤
            entry_idx = entry_match.group(1) if entry_match else None
            if entry_idx and entry_idx in problematic_uuids:
                root_reasons = "、".join(set(problematic_uuids[entry_idx]))
                explanation = (
                    f"{entry_desc}「{resource_display}」資料因為本身有問題（{root_reasons}），"
                    f"未能通過健保署規範格式的驗證，驗證器無法將它歸類到任何允許的資料類型。"
                )
                suggestion = (
                    f"請先修正這筆「{resource_display}」資料的欄位問題（{root_reasons}），"
                    f"修正後此錯誤通常會自動消失。"
                )
                return "cascading", explanation, suggestion
            else:
                # 嘗試從 diagnostics 中推測可能的資源類型
                failed_slices = re.findall(r"Does not match slice '(\w+)'", diagnostics)
                hint = ""
                if failed_slices:
                    sample = "、".join(f"「{s}」" for s in failed_slices[:3])
                    hint = f"驗證器嘗試比對的格式包括 {sample} 等，均未通過。"
                explanation = (
                    f"{entry_desc}「{resource_display}」資料未能通過健保署 PAS 規範格式驗證。"
                    f"{hint}"
                    f"通常是因為必填欄位缺漏、代碼錯誤，或資料結構不符合規範定義。"
                )
                suggestion = (
                    "這是連帶錯誤。請先修正這筆資料本身的欄位問題（必填項目缺漏或代碼錯誤），"
                    "修正後此錯誤通常會自動消失。如需確認欄位定義，可參考健保署 PAS IG 規範（https://nhicore.nhi.gov.tw/pas/）。"
                )
                return "cascading", explanation, suggestion

        # ================================================================
        # 根因錯誤：資料欄位本身有問題
        # ================================================================

        # 1) 代碼在 CodeSystem 中找不到
        if "Code is not found in CodeSystem" in diagnostics:
            m = re.search(r"(\S+)#(\S+)", diagnostics)
            system = m.group(1) if m else "未知系統"
            code = m.group(2) if m else "未知代碼"
            system_name = _get_system_display_name(system)
            lookup_url = _lookup_url_for_system(system, code)
            explanation = (
                f"{readable_location}填寫的代碼「{code}」在{system_name}中查詢不到。"
                f"可能是代碼填寫有誤，或使用了舊版代碼。"
            )
            suggestion = f"請確認「{code}」是否為正確有效的代碼。"
            if lookup_url:
                suggestion += f" 可至此查詢：{lookup_url}"
            return "root_cause", explanation, suggestion

        # 2) 代碼所在的 CodeSystem 整個找不到（驗證器沒有這個 CodeSystem 的資料）
        if "No codes in ValueSet belong to CodeSystem with URL" in diagnostics:
            cs_match = re.search(r"CodeSystem with URL (\S+)", diagnostics)
            code_match = re.search(r"code (\S+)#(\S+)", diagnostics, re.IGNORECASE)
            cs_url = cs_match.group(1) if cs_match else "未知代碼系統"
            code = code_match.group(2) if code_match else "填寫的代碼"
            system_name = _get_system_display_name(cs_url)
            explanation = (
                f"{readable_location}使用的代碼「{code}」所屬的代碼系統（{system_name}）"
                f"在驗證器中找不到對應資料，無法驗證代碼是否有效。"
            )
            suggestion = (
                f"請確認代碼「{code}」是否正確。"
                f"此錯誤也可能是驗證環境尚未載入此代碼系統，代碼本身不一定有誤。"
                f" 代碼系統網址：{cs_url}"
            )
            return "root_cause", explanation, suggestion

        # 3) 代碼不在允許的選項清單中
        if "None of the codings provided are in the value set" in diagnostics:
            vs_match = re.search(r"value set '([^']+)'", diagnostics)
            vs_name = vs_match.group(1) if vs_match else "指定選項清單"
            codes_match = re.search(r"codes = (\S+)#(\S+)", diagnostics)
            code_info = f"（目前填寫的代碼：{codes_match.group(2)}）" if codes_match else ""
            vs_url = _lookup_url_for_valueset(diagnostics)
            explanation = (
                f"{readable_location}填寫的代碼{code_info}不在系統允許的選項清單中。"
                f"允許的選項清單為「{vs_name}」。"
            )
            suggestion = f"請從「{vs_name}」中選擇一個有效的代碼。"
            if vs_url:
                suggestion += f" 可至此查看允許的選項：{vs_url}"
            return "root_cause", explanation, suggestion

        # 4) SNOMED 代碼顯示名稱不符
        if "Concept Display" in diagnostics and "does not match expected" in diagnostics:
            m = re.search(r'Concept Display "([^"]+)" does not match expected "([^"]+)"', diagnostics)
            if m:
                used = m.group(1)
                expected = m.group(2)
                explanation = (
                    f"{readable_location}使用的代碼顯示名稱「{used}」與標準名稱「{expected}」不符。"
                    f"代碼本身可能正確，但顯示文字需要修正。"
                )
                suggestion = f"請將顯示名稱改為標準名稱「{expected}」。"
                return "root_cause", explanation, suggestion

        # 5) UCUM 單位含有人類可讀標註
        if "UCUM Codes that contain human readable annotations" in diagnostics:
            explanation = (
                f"{readable_location}的計量單位代碼含有人類可讀的標註（如 {{vial}}），"
                f"這類標註在系統比對時會被忽略，可能造成計算錯誤。"
            )
            suggestion = "建議移除單位代碼中的大括號標註部分，使用純 UCUM 代碼。"
            return "root_cause", explanation, suggestion

        # 6) Constraint 失敗（dom-6 等）
        constraint = re.search(r"Constraint failed: (\S+):\s*'([^']+)'", diagnostics)
        if constraint:
            rule_id = constraint.group(1)
            rule_desc = constraint.group(2)
            if "narrative" in rule_desc.lower() or "text" in rule_desc.lower():
                explanation = (
                    f"{readable_location}缺少人類可讀的文字摘要（narrative）。"
                    f"這是 FHIR 的最佳實踐建議，建議加上但非強制要求。"
                )
                suggestion = "此為建議性規則，不影響資料上傳，可暫時忽略。若要完全符合規範，請在資源中加上 text 欄位。"
            else:
                explanation = f"{readable_location}未通過資料規則 {rule_id} 的檢查：{rule_desc}"
                suggestion = f"請依照規則 {rule_id} 的描述補充或修正資料。規則說明：{rule_desc}"
            return "root_cause", explanation, suggestion

        # 7) 欄位值為空
        if "value cannot be empty" in diagnostics or "Primitive_NotEmpty" in diagnostics:
            explanation = f"{readable_location}的值不得為空，必須填入有效內容。"
            suggestion = "請確認該欄位是否漏填，或填入了 null / 空字串。"
            return "root_cause", explanation, suggestion

        # 8) 未知的擴充欄位
        if "Unknown extension" in diagnostics:
            ext_match = re.search(r"Unknown extension (\S+)", diagnostics)
            ext_url = ext_match.group(1) if ext_match else ""
            explanation = f"{readable_location}使用了系統無法辨識的擴充欄位：{ext_url}"
            suggestion = "請確認擴充欄位的 URL 是否正確，並對照健保署 PAS IG 規範確認是否支援此擴充欄位。"
            return "root_cause", explanation, suggestion

        # 9) ValueSet 找不到
        if "ValueSet" in diagnostics and "not found" in diagnostics:
            vs_match = re.search(r"ValueSet '([^']+)' not found", diagnostics)
            vs_url = vs_match.group(1) if vs_match else ""
            explanation = (
                f"{readable_location}使用的代碼所對應的選項清單在驗證器中找不到（{vs_url}）。"
                f"驗證器無法確認代碼是否有效。"
            )
            suggestion = "請確認使用的代碼是否符合健保署規範。此問題可能是驗證環境尚未載入對應選項清單，代碼本身不一定有誤。"
            return "root_cause", explanation, suggestion

        # 10) 程式碼無法展開 ValueSet（LOINC 等）
        if "Unable to expand ValueSet" in diagnostics:
            explanation = (
                f"{readable_location}的代碼無法驗證，因為驗證器缺少必要的代碼系統資料（可能是 LOINC）。"
            )
            suggestion = "此問題為驗證環境缺少對應代碼系統資料，代碼本身可能是正確的。請確認代碼是否符合健保署規範，若確認正確可暫時忽略此提示。"
            return "root_cause", explanation, suggestion

        # 未知類型
        explanation = f"{readable_location}有驗證問題：{diagnostics[:150]}{'...' if len(diagnostics) > 150 else ''}"
        suggestion = "請仔細確認此欄位的填寫是否符合健保署 PAS IG 規範，並確認必填項目是否完整。"
        return "root_cause", explanation, suggestion

    # ------------------------------------------------------------------
    # 輔助工具
    # ------------------------------------------------------------------

    def _uuid_to_entry_idx(
        self,
        uuid: str,
        problematic_uuids: Dict[str, List[str]],
        uuid_to_resource: Dict[str, str],
    ) -> Optional[int]:
        """嘗試從 uuid 找出對應的 entry index（目前無法直接對應，回傳 None）"""
        # 未來可透過 fullUrl 欄位建立對應，目前先回傳 None
        return None

    def _extract_reference_url(self, diagnostics: str) -> str:
        code_match = re.search(r"code (\S+)#(\S+)", diagnostics, re.IGNORECASE)
        if code_match:
            system, code = code_match.group(1), code_match.group(2)
            return _lookup_url_for_system(system, code)
        if "value set" in diagnostics.lower():
            return _lookup_url_for_valueset(diagnostics)
        return ""

    def _humanize_location(self, location: str) -> str:
        if not location or location == "未知位置":
            return "某個欄位"

        entry_match = re.search(r"Bundle\.entry\[(\d+)\]", location)
        entry_desc = f"第 {int(entry_match.group(1)) + 1} 筆資料" if entry_match else ""

        resource_match = re.search(r"resource/\*(\w+)", location)
        resource_type = resource_match.group(1) if resource_match else ""
        resource_display = _get_resource_display(resource_type) if resource_type else ""
        resource_desc = f"（{resource_display}）" if resource_display else ""

        if not entry_desc and not resource_desc:
            first_seg = location.split(".")[0]
            if first_seg and first_seg not in ("Bundle", "resource"):
                resource_desc = f"{first_seg} 資源"

        field = location.split(".")[-1] if "." in location else location
        field = re.sub(r"\[\d+\]", "", field)
        field_desc = f"的「{field}」欄位" if field and field not in ("Bundle", "resource", "entry") else ""

        parts = [p for p in [entry_desc, resource_desc, field_desc] if p]
        return "".join(parts) if parts else location

    # ------------------------------------------------------------------
    # LLM 相關（短訊息）
    # ------------------------------------------------------------------

    def _format_issues_for_prompt(self, issues: List[Dict]) -> str:
        lines = []
        for i, issue in enumerate(issues, 1):
            severity = issue.get("severity", "unknown")
            diagnostics = issue.get("diagnostics", "")
            locations = issue.get("location", [])
            location_str = locations[0] if locations else "未知位置"
            lines.append(f"{i}. [{severity}] 位置: {location_str}\n   訊息: {diagnostics}")
        return "\n".join(lines)

    def _build_prompt(self, issues_text: str) -> str:
        return f"""你是 FHIR 醫療資料標準的專家，能用白話中文向醫療院所人員解釋技術問題。

以下是 FHIR Validator 對一份健保署 PAS 申請資料產生的驗證錯誤：

{issues_text}

請用繁體中文回覆，格式為 JSON：
{{
  "summary": "用一兩句話總結這些問題",
  "issues": [
    {{
      "index": 1,
      "explanation": "用醫院行政人員或護理師能理解的白話文說明這個問題是什麼",
      "suggestion": "具體說明如何修正"
    }}
  ]
}}

注意事項：
- explanation 避免使用 FHIR 技術術語，說「代碼不正確」而不是「coding system validation failed」
- 如果是代碼查不到，說明「哪個欄位的代碼在哪個系統查不到」
- 如果是選項不對，說明「填的值不在允許的選項清單裡」
- suggestion 要具體可操作
- 每一筆 issue 的 explanation 和 suggestion 必須是完整獨立的句子，不得出現「同上」、「同前」、「如上」、「見上方」等參照其他項目的說法
- 即使多筆錯誤類型相似，每一筆都必須寫出完整內容，不可省略
- 只回傳 JSON，不要加其他文字"""

    def _parse_issues_from_response(
        self,
        response: str,
        original_issues: List[Dict],
        uuid_to_resource: Dict[str, str],
        problematic_uuids: Dict[str, List[str]],
    ) -> List[Dict]:
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if not json_match:
            return self._explain_with_rules(original_issues, uuid_to_resource, problematic_uuids)

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return self._explain_with_rules(original_issues, uuid_to_resource, problematic_uuids)

        llm_items = {item["index"]: item for item in data.get("issues", []) if "index" in item}
        result = []

        for idx, issue in enumerate(original_issues, 1):
            diagnostics = issue.get("diagnostics", "")
            locations = issue.get("location", [])
            location_str = locations[0] if locations else "未知位置"

            # 用規則式取得分類和基礎說明
            category, rule_explanation, rule_suggestion = self._explain_single_issue(
                diagnostics, location_str, uuid_to_resource, problematic_uuids
            )

            llm_item = llm_items.get(idx, {})
            explanation = llm_item.get("explanation") or rule_explanation
            suggestion = llm_item.get("suggestion") or rule_suggestion

            ref_url = self._extract_reference_url(diagnostics)
            if ref_url and ref_url not in suggestion:
                suggestion += f" 參考連結：{ref_url}"

            result.append({
                "severity": issue.get("severity", "unknown"),
                "location": location_str,
                "original_message": diagnostics,
                "explanation": explanation,
                "suggestion": suggestion,
                "category": category,
            })

        return result
