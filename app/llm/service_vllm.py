import requests

from app.config import get_settings
from app.logger import get_logger
from app.llm.llm import LLMService

logger = get_logger(__name__)


class VllmLLMService(LLMService):
    def __init__(self):
        s = get_settings()
        self._base_url = s.vllm_base_url
        self._model = s.vllm_model
        self._timeout = (s.vllm_connect_timeout, s.vllm_read_timeout)
        logger.info(f"VllmLLMService: {self._model} @ {self._base_url}")

    def _post(self, prompt: str, temperature: float) -> str:
        resp = requests.post(
            self._base_url,
            headers={"Content-Type": "application/json"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": 2000,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _call_llm(self, prompt: str) -> str:
        return self._post(prompt, temperature=0.1)

    def _call_llm_score(self, prompt: str) -> str:
        return self._post(prompt, temperature=0)
