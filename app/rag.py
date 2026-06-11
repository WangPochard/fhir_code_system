"""
通用 RAG 服務
=============
SQL / DB Session / result mapper 全部從外面傳入，
不同資料來源（SNOMED CT, ICD-10, RxNorm...）共用同一套搜尋邏輯。

用法：
    rag = RAGService(
        db_session=SnomedSession,
        search_sql="SELECT ... WHERE embedding <=> CAST(:qvec AS vector) ...",
        result_mapper=lambda row: {"concept_id": row[0], ...},
        stats_sql="SELECT COUNT(*) FROM ...",
    )
    results = rag.similarity_search("chest pain", k=10)
"""

from typing import List, Dict, Callable, Optional
from sqlalchemy import text as sa_text

from app.logger import logger
from app.embedding import EmbeddingService
from app.llm.llm import LLMService, get_llm_service


class RAGService:

    def __init__(
        self,
        db_session,
        search_sql: str,
        result_mapper: Callable,
        stats_sql: str = None,
        model_name: str = None,
        group_key: str = None,
        llm_service: LLMService = None,
    ):
        """
        Parameters
        ----------
        db_session : sessionmaker
            SQLAlchemy Session factory（例如 SnomedSession, ICD10Session）
        search_sql : str
            向量搜尋 SQL，必須包含 :qvec, :threshold, :k 三個參數
        result_mapper : callable(row) -> dict
            將 SQL 結果的每一列轉成 dict
        stats_sql : str, optional
            統計用 SQL
        model_name : str, optional
            Embedding 模型名稱
        group_key : str, optional
            如果設定，搜尋結果會按此 key 合併（取最高 similarity）
        llm_service : LLMService, optional
            外部傳入的 LLM 服務，不傳則自動建立
        """
        self.db_session = db_session
        self.search_sql = search_sql
        self.result_mapper = result_mapper
        self.stats_sql = stats_sql
        self.group_key = group_key

        self.embedding = EmbeddingService(model_name=model_name)
        self.llm = llm_service or get_llm_service()

    # ------------------------------------------------------------------
    # 向量搜尋
    # ------------------------------------------------------------------
    def similarity_search(
        self, query: str, k: int = 10, threshold: float = 0.0,
        extra_params: dict = None,
    ) -> List[Dict]:
        """執行向量搜尋，回傳 result_mapper 轉換後的 dict list"""
        logger.info(f"搜尋: {query[:50]}...")

        query_vec = self.embedding.embed_query(query)
        query_vec_str = "[" + ",".join(map(str, query_vec)) + "]"

        params = {"qvec": query_vec_str, "threshold": threshold, "k": k}
        if extra_params:
            params.update(extra_params)

        db = self.db_session()
        try:
            rows = db.execute(
                sa_text(self.search_sql),
                params,
            ).fetchall()

            docs = [self.result_mapper(row) for row in rows]

            if docs:
                logger.info(f"找到 {len(docs)} 個結果, 最高相似度: {docs[0].get('similarity', 0):.3f}")
            else:
                logger.warning("沒有找到相關文件")

            return docs
        except Exception as e:
            logger.error(f"搜尋失敗: {e}")
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 合併（同 key 取最高 similarity）
    # ------------------------------------------------------------------
    def group_results(self, results: List[Dict]) -> List[Dict]:
        """按 group_key 合併結果，取最高 similarity"""
        if not self.group_key:
            return results

        grouped: Dict[str, Dict] = {}
        for doc in results:
            key = doc.get(self.group_key)
            if not key:
                continue
            if key not in grouped:
                grouped[key] = {**doc, "best_similarity": doc["similarity"]}
            elif doc["similarity"] > grouped[key]["best_similarity"]:
                grouped[key]["best_similarity"] = doc["similarity"]

        return sorted(grouped.values(), key=lambda x: x["best_similarity"], reverse=True)

    # ------------------------------------------------------------------
    # 完整流程：NER → 搜尋 → LLM 評分
    # ------------------------------------------------------------------
    def identify(
        self,
        clinical_text: str,
        icd10_cm_code: str = "",
        icd10_cm_label: str = "",
        top_k: int = 3,
        threshold: float = 0.0,
        tw_valueset: Optional[str] = None,
    ) -> Dict:
        _SEARCH_K = 30

        logger.info(f"概念識別: {clinical_text[:80]}...")
        if tw_valueset:
            logger.info(f"tw_valueset filter: {tw_valueset}")

        # Stage 1: NER 萃取術語
        if len(clinical_text) > 1500:
            term_items = self.llm.extract_ner_with_reason_chunked(clinical_text, max_chars=1500)
        else:
            term_items = self.llm.extract_ner_with_reason(clinical_text)

        # Stage 2: 每個術語向量搜尋 → LLM 評分
        identified_concepts = []
        for item in term_items:
            term = item.get("term", "")
            if not term:
                continue

            raw = self.similarity_search(
                query=term, k=_SEARCH_K, threshold=threshold,
                extra_params={"tw_valueset": tw_valueset},
            )
            grouped = self.group_results(raw)

            scored = self.llm.rank_snomed_candidates(
                term=term,
                candidates=grouped[: top_k * 2],
                icd10_cm_code=icd10_cm_code,
                icd10_cm_label=icd10_cm_label,
            )

            candidates = [
                {
                    "rank": c.get("rank", i + 1),
                    "concept_id": c.get("concept_id", ""),
                    "fsn": c.get("fsn"),
                    "confidence_pct": c.get("confidence_pct", 0.0),
                }
                for i, c in enumerate(scored[:top_k])
            ]

            identified_concepts.append({
                "term": term,
                "reason": scored[0].get("reason", "") if scored else "",
                "candidates": candidates,
            })
            logger.info(f"術語 {term!r} → {len(candidates)} 個候選")

        return {
            "clinical_text": clinical_text,
            "identified_concepts": identified_concepts,
        }

    # ------------------------------------------------------------------
    # 搜尋 + LLM 解釋
    # ------------------------------------------------------------------
    def search_with_explain(
        self, query: str, k: int = 10, threshold: float = 0.0
    ) -> Dict:
        candidates = self.similarity_search(query=query, k=k, threshold=threshold)
        explanation = self.llm.explain_match(query, candidates) if candidates else ""
        return {
            "query": query,
            "candidates": candidates,
            "explanation": explanation,
        }

    # ------------------------------------------------------------------
    # 統計
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict:
        if not self.stats_sql:
            return {"embedding_model": self.embedding.model_name}

        db = self.db_session()
        try:
            row = db.execute(sa_text(self.stats_sql)).fetchone()
            stats = {f"col_{i}": v for i, v in enumerate(row)} if row else {}
            stats["embedding_model"] = self.embedding.model_name
            return stats
        finally:
            db.close()
