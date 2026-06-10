from typing import List
from sentence_transformers import SentenceTransformer

try:
    from .config import get_settings
    from .logger import logger
except ImportError:
    from config import get_settings
    from logger import logger

settings = get_settings()


class EmbeddingService:
    """文字轉向量服務，使用 sentence-transformers 本地模型"""

    def __init__(self, model_name: str = None):
        self.model_name = model_name or settings.sentence_transformer_model

        logger.info(f"載入 Embedding 模型: {self.model_name}")
        self.model = SentenceTransformer(self.model_name)
        self.vector_dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Embedding 維度: {self.vector_dim}")

    def embed_query(self, query: str) -> List[float]:
        """單筆文字轉向量"""
        vector = self.model.encode(query, normalize_embeddings=True)
        return vector.tolist()

    def embed_documents(
        self, texts: List[str], batch_size: int = 128
    ) -> List[List[float]]:
        """批次文字轉向量"""
        vectors = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        return vectors.tolist()
