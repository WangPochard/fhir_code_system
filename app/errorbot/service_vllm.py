import requests

from app.config import get_settings
from app.logger import get_logger
from .service import ValidationExplainService

logger = get_logger("rag.errorbot")
settings = get_settings()


class VllmValidationExplainService(ValidationExplainService):
    def __init__(self):
        self.base_url = settings.vllm_base_url.rstrip("/")
        self.model = settings.vllm_model
        self.temperature = settings.vllm_temperature
        self.timeout = (settings.vllm_connect_timeout, settings.vllm_read_timeout)
        logger.info(f"VllmValidationExplainService: {self.model} @ {self.base_url}")

    def _call_llm(self, prompt: str) -> str:
        resp = requests.post(
            self.base_url,
            headers={"Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": 2000,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
