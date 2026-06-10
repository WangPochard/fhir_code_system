import re
import json
from typing import List, Dict
from langchain_community.llms import Ollama

from app.config import get_settings
from app.logger import get_logger
from app.llm.prompts import prompt_service

settings = get_settings()
logger = get_logger(__name__)


class LLMService:
    """LLM 服務，提供 chunk 分段、NER 辨識、reranking 等功能"""

    def __init__(
        self,
        model: str = None,
        base_url: str = None,
        temperature: float = 0.1,
    ):
        self.model_name = model or settings.llm_model
        self.base_url = base_url or settings.llm_base_url
        self.llm = Ollama(
            model=self.model_name,
            base_url=self.base_url,
            temperature=temperature,
        )
        # 打分 / reranking 專用：temperature=0 確保同輸入同輸出
        self.llm_score = Ollama(
            model=self.model_name,
            base_url=self.base_url,
            temperature=0,
        )
        logger.info(f"LLM 服務初始化: {self.model_name} @ {self.base_url}")

    # ------------------------------------------------------------------
    # Chunk 分段（純文字切分，不依賴 LLM）
    # ------------------------------------------------------------------
    def chunk_text(self, text: str, max_chars: int = 1500) -> List[str]:
        """將長文本切成段落，每段不超過 max_chars"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        if len(paragraphs) <= 1 and len(text) > max_chars:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        chunks = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) > max_chars and current:
                chunks.append(current.strip())
                current = p
            else:
                current += "\n\n" + p if current else p
        if current:
            chunks.append(current.strip())

        logger.info(f"文本分段: {len(text)} 字 -> {len(chunks)} 段")
        return chunks

    # ------------------------------------------------------------------
    # NER 醫學術語辨識
    # ------------------------------------------------------------------
    def extract_ner(self, clinical_text: str) -> List[str]:
        """從臨床文字中抽取醫學術語"""
        prompt = prompt_service.render("ner_extract", clinical_text=clinical_text)

        try:
            response = self.llm.invoke(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                terms = data.get("terms", [])
                if terms:
                    logger.info(f"NER 抽取到 {len(terms)} 個術語: {terms}")
                    return terms
        except Exception as e:
            logger.warning(f"NER 抽取失敗: {e}")

        logger.warning("NER fallback: 使用原始文字")
        return [clinical_text]

    def extract_ner_with_reason(self, clinical_text: str) -> List[Dict]:
        """從臨床文字中抽取完整臨床概念，並附上擷取原因"""
        prompt = prompt_service.render("ner_with_reason", clinical_text=clinical_text)

        try:
            response = self.llm_score.invoke(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                terms = data.get("terms", [])
                if terms and isinstance(terms[0], dict):
                    logger.info(f"NER with reason 抽取到 {len(terms)} 個術語")
                    return terms
        except Exception as e:
            logger.warning(f"NER with reason 抽取失敗: {e}")

        logger.warning("NER with reason fallback: 使用原始文字")
        return [{"term": clinical_text[:200], "reason": "原始輸入（NER 失敗）"}]

    def extract_ner_with_reason_chunked(self, clinical_text: str, max_chars: int = 1500) -> List[Dict]:
        """長文本先 chunk 再逐段做 NER with reason，合併去重"""
        chunks = self.chunk_text(clinical_text, max_chars=max_chars)

        all_terms: List[Dict] = []
        seen: set[str] = set()
        for i, chunk in enumerate(chunks, 1):
            logger.info(f"NER with reason chunk {i}/{len(chunks)} ({len(chunk)} 字)...")
            chunk_terms = self.extract_ner_with_reason(chunk)
            for t in chunk_terms:
                term_str = t.get("term", "")
                if term_str and len(term_str) < 200 and term_str not in seen:
                    all_terms.append(t)
                    seen.add(term_str)

        if not all_terms:
            logger.warning("所有 chunk NER with reason 都失敗，fallback 到前 200 字")
            all_terms = [{"term": clinical_text[:200], "reason": "原始輸入（NER 失敗）"}]

        logger.info(f"NER with reason 總計抽取 {len(all_terms)} 個不重複術語")
        return all_terms

    # ------------------------------------------------------------------
    # NER + Chunk：長文本先分段再逐段 NER
    # ------------------------------------------------------------------
    def extract_ner_chunked(self, clinical_text: str, max_chars: int = 1500) -> List[str]:
        """長文本先 chunk 再逐段做 NER，合併去重"""
        chunks = self.chunk_text(clinical_text, max_chars=max_chars)

        all_terms = []
        seen = set()
        for i, chunk in enumerate(chunks, 1):
            logger.info(f"NER chunk {i}/{len(chunks)} ({len(chunk)} 字)...")
            chunk_terms = self.extract_ner(chunk)
            for t in chunk_terms:
                if len(t) < 200 and t not in seen:
                    all_terms.append(t)
                    seen.add(t)

        if not all_terms:
            logger.warning("所有 chunk NER 都失敗，fallback 到前 500 字")
            all_terms = [clinical_text[:500]]

        logger.info(f"NER 總計抽取 {len(all_terms)} 個不重複術語")
        return all_terms

    def extract_pcs_facts_from_report(
        self, report_text: str, cm_code: str = "", cm_label: str = ""
    ) -> Dict:
        """從診斷報告萃取 PCS 相關事實，並判斷對應的 ICD-10-PCS body system。

        body system 由 LLM 根據醫學知識直接判斷，避免維護硬寫的 CM 碼對照表。
        pcs_body_system_confidence 決定後續是否套用 SQL prefix filter：
          high   → 套用 filter（縮小搜尋範圍）
          medium → 套用 filter，同時合併無 filter 結果
          low    → 不套用 filter，全庫搜尋
        """
        cm_context = ""
        if cm_code or cm_label:
            cm_context = (
                f"\n補充資訊（來自 HIS）：\n"
                f"  ICD-10-CM 診斷碼：{cm_code}\n"
                f"  診斷說明：{cm_label}\n"
            )

        prompt = (
            "你是一位 ICD-10-PCS 臨床編碼助理。\n\n"
            "任務一：從以下診斷報告中，萃取「執行了什麼程序」的事實資訊。\n"
            "規則：只萃取報告中明確記載的事實，不要推斷或假設。"
            "若報告未提及某項資訊，填入 unknown。\n\n"
            "任務二：根據報告內容判斷此程序對應的 ICD-10-PCS Section 與 Body System。\n"
            "重要：ICD-10-CM 診斷碼描述的是疾病原因，不代表 PCS 的 body system。\n"
            "例如：為大腸癌做的腹部骨盆 CT，body system 是 W（Anatomical Regions），不是 D（Gastrointestinal）。\n\n"
            "ICD-10-PCS Imaging Body System 判斷規則（最優先依照掃描範圍）：\n"
            "  掃描範圍跨整個體腔 → W（Anatomical Regions）：\n"
            "    - chest / thorax / abdomen / pelvis / head / whole body / neck\n"
            "    - 例如：CT abdomen and pelvis、MRI brain、Chest X-Ray\n"
            "  掃描單一特定器官 → 對應 body system：\n"
            "    - colon only → D（Gastrointestinal）\n"
            "    - liver only / bile duct → F（Hepatobiliary）\n"
            "    - heart → 2（Heart）\n"
            "    - breast → H（Skin/Breast）\n\n"
            "ICD-10-PCS Section 常用值：\n"
            "  0 = Medical and Surgical（外科手術、切片）\n"
            "  B = Imaging（CT、MRI、超音波、X-Ray）\n"
            "  C = Nuclear Medicine（核醫、PET）\n"
            "  D = Radiation Therapy\n"
            "  3 = Administration（輸血、注射）\n\n"
            "ICD-10-PCS Body System 常用值：\n"
            "  0=CNS, 1=Peripheral Nervous, 2=Heart, 3=Upper Arteries,\n"
            "  4=Lower Arteries, 5=Upper Veins, 6=Lower Veins, 7=Lymphatic,\n"
            "  8=Eye, 9=Ear/Nose/Sinus, B=Respiratory, C=Mouth/Throat,\n"
            "  D=Gastrointestinal, F=Hepatobiliary/Pancreas, G=Endocrine,\n"
            "  H=Skin/Breast, J=Subcutaneous, K=Muscles, L=Tendons,\n"
            "  M=Bursae/Ligaments, N=Head/Facial Bones, P=Upper Bones,\n"
            "  Q=Lower Bones, R=Upper Joints, S=Lower Joints,\n"
            "  T=Urinary, U=Female Reproductive, V=Male Reproductive,\n"
            "  W=Anatomical Regions（跨區域或整體腔掃描）\n\n"
            f"診斷報告：\n{report_text}\n"
            f"{cm_context}\n"
            "請輸出 JSON：\n"
            "{{\n"
            '  "modality": "CT|MRI|Ultrasound|X-Ray|Biopsy|Surgery|Lab|Nuclear|Radiation|unknown",\n'
            '  "body_part": "掃描或處置的解剖部位（英文），若未提及填 unknown",\n'
            '  "laterality": "left|right|bilateral|unspecified",\n'
            '  "approach": "percutaneous|open|endoscopic|natural_opening|unknown",\n'
            '  "contrast": "yes|no|unknown",\n'
            '  "procedure_description": "一句英文描述程序，例如 CT abdomen and pelvis with contrast",\n'
            '  "extraction_confidence": "high|medium|low",\n'
            '  "not_found_reason": "若 extraction_confidence 為 low，說明缺乏哪些資訊",\n'
            '  "pcs_section": "0|B|C|D|3|unknown",\n'
            '  "pcs_body_system": "單一英文字母或數字，依上方規則填入，若無法判斷填 unknown",\n'
            '  "pcs_body_system_confidence": "high|medium|low",\n'
            '  "pcs_body_system_reason": "判斷依據，說明掃描範圍，一句話"\n'
            "}}"
        )

        try:
            response = self.llm.invoke(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                bs_confidence = data.get("pcs_body_system_confidence", "low")
                bs_value = data.get("pcs_body_system", "unknown")
                section_value = data.get("pcs_section", "unknown")
                logger.info(
                    f"PCS 事實萃取完成 | procedure={data.get('procedure_description')} | "
                    f"extraction_confidence={data.get('extraction_confidence')} | "
                    f"body_system={section_value}{bs_value} "
                    f"(confidence={bs_confidence}) | "
                    f"reason={data.get('pcs_body_system_reason')}"
                )
                return data
        except Exception as e:
            logger.warning(f"PCS 事實萃取失敗: {e}")

        logger.warning("PCS 事實萃取 fallback：所有欄位設為 unknown，將執行全庫搜尋")
        return {
            "modality": "unknown",
            "body_part": "unknown",
            "laterality": "unspecified",
            "approach": "unknown",
            "contrast": "unknown",
            "procedure_description": report_text[:200],
            "extraction_confidence": "low",
            "not_found_reason": "LLM 萃取失敗",
            "pcs_section": "unknown",
            "pcs_body_system": "unknown",
            "pcs_body_system_confidence": "low",
            "pcs_body_system_reason": "LLM 萃取失敗",
        }

    def extract_procedure_from_report(self, report_text: str) -> Dict:
        """從影像報告推斷執行的影像檢查程序"""
        prompt = prompt_service.render("procedure_infer", report_text=report_text)

        try:
            response = self.llm.invoke(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                logger.info(f"程序推斷完成: modality={data.get('modality')}, confidence={data.get('confidence')}")
                return data
        except Exception as e:
            logger.warning(f"程序推斷失敗: {e}")

        return {
            "modality": "無法判斷",
            "body_regions": [],
            "contrast": "unknown",
            "procedure_query": report_text[:200],
            "reasoning": "LLM 推斷失敗，使用原始文字",
            "confidence": "low",
        }

    def explain_pcs_candidates(
        self, procedure_query: str, report_preview: str, candidates: List[Dict]
    ) -> Dict:
        """為 PCS 候選代碼產生中文說明，並輸出 rank 1 的優先推薦理由。
        影像類代碼（code 開頭為 B）額外輸出成像模態與掃描部位。"""
        candidate_lines = "\n".join(
            f"{i+1}. [{c['code']}] {c.get('term_eng', '')} / {c.get('term_cht', '')} "
            f"（相似度 {c.get('similarity', 0):.3f}）"
            for i, c in enumerate(candidates)
        )

        prompt = f"""你是 ICD-10-PCS 臨床編碼助理，請以繁體中文、流暢自然的專業口吻撰寫說明。

診斷報告程序描述：{procedure_query}
報告摘要：{report_preview}

候選代碼（依評分由高至低排列）：
{candidate_lines}

撰寫要求：

1. 每個代碼的 reason（2～3 句）：
   - 說明此代碼代表的程序與涵蓋的解剖部位
   - 指出與報告的符合之處（引用報告中的具體描述）
   - 若有不符之處，用「雖然⋯⋯，但⋯⋯」方式說明，避免生硬否定

2. top_recommendation_summary（3～5 句）：
   - 說明優先考慮的程序類型或成像方式，以及報告涵蓋的範圍
   - 比較 Rank 1 與 Rank 2 的差異，點出關鍵區別
   - 引用原始報告的具體文字作為佐證
   - 以「故 [code] 為較高機率選項」作為結尾

代碼第一個字元為 B 者為影像類，需填入 imaging_modality 與 imaging_body_part；其他類別填 null。

請以 JSON 回應：
{{
  "explanations": [
    {{
      "rank": 1,
      "code": "XXXXXXX",
      "reason": "...",
      "imaging_modality": null,
      "imaging_body_part": null
    }}
  ],
  "top_recommendation_summary": "..."
}}"""

        try:
            response = self.llm.invoke(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                logger.info(f"PCS 候選說明產生完成，共 {len(data.get('explanations', []))} 筆")
                return data
        except Exception as e:
            logger.warning(f"PCS 候選說明產生失敗: {e}")

        return {"explanations": [], "top_recommendation_summary": ""}

    def rank_loinc_candidates_from_report(
        self,
        report_text: str,
        candidates: List[Dict],
        nhi_name: str = "",
    ) -> List[Dict]:
        """用報告文字對已篩選的 LOINC 候選清單進行 rerank。
        candidates 來自健保碼規則過濾，LLM 只做閱讀理解選擇，不生成新代碼。"""
        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            axes = " | ".join(filter(None, [
                f"Component: {c['component']}"   if c.get("component")   else None,
                f"Property: {c['property']}"     if c.get("property")    else None,
                f"Time: {c['time_aspect']}"      if c.get("time_aspect") else None,
                f"System: {c['system']}"         if c.get("system")      else None,
                f"Scale: {c['scale']}"           if c.get("scale")       else None,
                f"Method: {c['method']}"         if c.get("method")      else None,
            ]))
            candidate_lines.append(
                f"[{i}] {c['loinc_code']} {c.get('long_common_name', '')} | {axes}"
            )

        candidates_text = "\n".join(candidate_lines)
        nhi_context = f"健保項目：{nhi_name}\n" if nhi_name else ""

        prompt = f"""你是 LOINC 臨床編碼助理，請以繁體中文回應。

{nhi_context}以下是根據健保碼預先篩選出的 LOINC 候選清單（共 {len(candidates)} 筆）：
{candidates_text}

檢驗／病理報告文字：
{report_text}

任務：將上方所有候選依照與報告的吻合程度由高至低排列，全部都要列出，不可省略。
confidence_pct 為 0–100，代表此代碼與報告的吻合程度（即使不符合也要給分並說明）。

輸出完整 JSON 陣列（共 {len(candidates)} 筆）：
[
  {{
    "rank": 1,
    "index": 候選清單編號（整數，從1開始）,
    "confidence_pct": 0到100的數字,
    "reason": "2～3句：說明報告中哪些描述對應此代碼的 LOINC 軸（Component/System/Method 等）"
  }}
]"""

        try:
            response = self.llm.invoke(prompt)
            logger.debug(f"LOINC rerank LLM raw: {response[:300]}")
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                logger.warning(f"LOINC rerank：LLM 回應無 JSON 陣列，raw={response[:200]}")
                return []
            ranked = json.loads(json_match.group())
            result = []
            for item in ranked:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(candidates):
                    result.append({
                        **candidates[idx],
                        "rank": item.get("rank"),
                        "confidence_pct": float(item.get("confidence_pct", 0)),
                        "reason": item.get("reason", ""),
                    })
            logger.info(f"LOINC rerank 完成，共 {len(result)} 筆結果")
            return result
        except Exception as e:
            logger.warning(f"LOINC rerank 失敗: {e}")

        return []

    def rank_snomed_candidates(
        self,
        term: str,
        candidates: List[Dict],
        icd10_cm_code: str = "",
        icd10_cm_label: str = "",
    ) -> List[Dict]:
        """對 SNOMED CT 向量搜尋候選進行 LLM 評分與排序。
        回傳每筆附 confidence_pct，第一筆附 reason（解釋為何是第一順位）。"""
        if not candidates:
            return []

        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            candidate_lines.append(
                f"[{i}] concept_id={c['concept_id']} | "
                f"fsn={c.get('fsn', '')} | "
                f"semantic_tag={c.get('semantic_tag', '')} | "
                f"similarity={c.get('best_similarity', c.get('similarity', 0)):.3f}"
            )

        candidates_text = "\n".join(candidate_lines)

        cm_context = ""
        if icd10_cm_code or icd10_cm_label:
            cm_context = (
                f"\n補充資訊（來自 HIS）：\n"
                f"  ICD-10-CM 診斷碼：{icd10_cm_code}\n"
                f"  診斷說明：{icd10_cm_label}\n"
                "此診斷碼可作為輔助判斷依據，但 SNOMED CT 概念應以術語本身的語義為主。\n"
            )

        prompt = f"""你是 SNOMED CT 臨床編碼助理，請以繁體中文回應。

從臨床文字萃取出的術語：{term}
{cm_context}
向量搜尋到的 SNOMED CT 候選（共 {len(candidates)} 筆）：
{candidates_text}

任務：
1. 將所有候選依照與術語的吻合程度由高至低排列，全部都要列出，不可省略。
2. confidence_pct 依下列標準給分，每筆必須唯一，不得有任何兩筆相同：
   - 95–100：術語與 FSN 完全一致
   - 85–94 ：語意完全符合，但顆粒度有細微差異（如更細的 stage、subtype）
   - 70–84 ：語意大致符合，但顆粒度明顯不同（過廣或過細）
   - 50–69 ：部分語意符合，有明顯落差
   - 0–49  ：不吻合
   第一順位與第二順位的分數差距至少 3 分，確保最佳選項清楚可辨。
3. reason 只填在第一順位（rank=1），說明為何這個概念最符合該術語（1～2句）。
   - 若 semantic_tag 有助於理解（例如 disorder / procedure / body structure），可自然帶入文字說明，例如「此概念屬於臨床疾病（disorder）類型」，但不要直接把英文 tag 單獨羅列。
   - 若有提供 ICD-10-CM 碼，可在說明中引用其與 SNOMED 概念的對應關係。
   - 其他排名填空字串。

輸出完整 JSON 陣列（共 {len(candidates)} 筆）：
[
  {{
    "rank": 1,
    "index": 候選清單編號（整數，從1開始）,
    "confidence_pct": 0到100的數字,
    "reason": "rank=1 時填寫，其他填空字串"
  }}
]"""

        try:
            response = self.llm_score.invoke(prompt)
            logger.debug(f"SNOMED rank LLM raw: {response[:300]}")
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                logger.warning("SNOMED rank：LLM 回應無 JSON 陣列")
                return []
            ranked = json.loads(json_match.group())
            result = []
            for item in ranked:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(candidates):
                    result.append({
                        **candidates[idx],
                        "rank": item.get("rank"),
                        "confidence_pct": float(item.get("confidence_pct", 0)),
                        "reason": item.get("reason", ""),
                    })
            logger.info(f"SNOMED rank 完成，共 {len(result)} 筆結果")
            return result
        except Exception as e:
            logger.warning(f"SNOMED rank 失敗: {e}")

        return []

    # ------------------------------------------------------------------
    # Reranking
    # ------------------------------------------------------------------
    def rerank(
        self, query: str, candidates: List[Dict], top_k: int = 5
    ) -> List[Dict]:
        """用 LLM 對候選結果重新排序"""
        if len(candidates) <= top_k:
            return candidates

        candidate_text = ""
        for i, c in enumerate(candidates[:15], 1):
            candidate_text += f"  {i}. {c}\n"

        prompt = prompt_service.render(
            "rerank",
            top_k=top_k,
            query=query,
            candidate_text=candidate_text,
        )

        try:
            response = self.llm.invoke(prompt)
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                rankings = json.loads(json_match.group())
                reranked = []
                for r in rankings[:top_k]:
                    idx = r.get("index", 0) - 1
                    if 0 <= idx < len(candidates):
                        reranked.append(candidates[idx])
                if reranked:
                    logger.info(f"Rerank 完成，回傳 {len(reranked)} 筆")
                    return reranked
        except Exception as e:
            logger.warning(f"Rerank 失敗: {e}")

        logger.warning("Rerank fallback: 回傳原始排序")
        return candidates[:top_k]

    # ------------------------------------------------------------------
    # 解釋搜尋結果
    # ------------------------------------------------------------------
    def explain_match(self, query: str, candidates: List[Dict]) -> str:
        """用 LLM 解釋候選結果與查詢的相關性"""
        candidate_text = ""
        for i, c in enumerate(candidates, 1):
            label = c.get("term_eng") or c.get("fsn") or c.get("term", "")
            code = c.get("code") or c.get("concept_id", "")
            candidate_text += (
                f"  {i}. [{code}] {label}"
                f" | 相似度: {c.get('similarity', 0):.4f}\n"
            )

        prompt = prompt_service.render(
            "explain_match",
            query=query,
            candidate_text=candidate_text,
        )

        try:
            return self.llm.invoke(prompt)
        except Exception as e:
            logger.error(f"LLM explain 失敗: {e}")
            return f"LLM 回應失敗: {e}"
