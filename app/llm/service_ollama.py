from langchain_community.llms import Ollama

from app.config import get_settings
from app.logger import get_logger
from app.llm.llm import LLMService

logger = get_logger(__name__)


class OllamaLLMService(LLMService):
    def __init__(self):
        s = get_settings()
        self._llm = Ollama(
            model=s.llm_model,
            base_url=s.llm_base_url,
            temperature=0.1,
        )
        self._llm_score = Ollama(
            model=s.llm_model,
            base_url=s.llm_base_url,
            temperature=0,
        )
        logger.info(f"OllamaLLMService: {s.llm_model} @ {s.llm_base_url}")

    def _call_llm(self, prompt: str) -> str:
        return self._llm.invoke(prompt)

    def _call_llm_score(self, prompt: str) -> str:
        return self._llm_score.invoke(prompt)
