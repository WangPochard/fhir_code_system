from langchain_community.llms import Ollama

from app.config import get_settings
from app.logger import get_logger
from .service import ValidationExplainService

logger = get_logger("rag.errorbot")
settings = get_settings()


class OllamaValidationExplainService(ValidationExplainService):
    def __init__(self, temperature: float = 0.1):
        from langchain_community.llms import Ollama
        self._llm = Ollama(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            temperature=temperature,
        )
        logger.info(f"OllamaValidationExplainService: {settings.llm_model} @ {settings.llm_base_url}")

    def _call_llm(self, prompt: str) -> str:
        return self._llm.invoke(prompt)
