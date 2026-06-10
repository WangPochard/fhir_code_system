"""
Prompt 管理服務
- 從資料庫讀取 prompt template
- 記憶體 cache 減少 DB 查詢
- render() 負責變數替換
"""

from sqlalchemy import text

from app.database import SnomedSession
from app.logger import get_logger

logger = get_logger(__name__)


class PromptService:
    def __init__(self):
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        """取得 prompt template，優先從 cache 讀取"""
        if name in self._cache:
            return self._cache[name]

        db = SnomedSession()
        try:
            row = db.execute(
                text("SELECT content FROM prompt_templates WHERE name = :name AND is_active = TRUE"),
                {"name": name},
            ).fetchone()
        finally:
            db.close()

        if row is None:
            raise ValueError(f"Prompt '{name}' 不存在或未啟用")

        self._cache[name] = row[0]
        logger.info(f"Prompt '{name}' 已載入至 cache")
        return row[0]

    def render(self, name: str, **kwargs) -> str:
        """取得 template 並套入變數

        template 內使用 {variable} 作為變數佔位符，
        literal 的大括號請用 {{ 和 }} 跳脫（與 Python format() 規則相同）
        """
        template = self.get(name)
        return template.format(**kwargs)

    def invalidate(self, name: str | None = None) -> None:
        """清除 cache（name 為 None 時清除全部）"""
        if name:
            self._cache.pop(name, None)
        else:
            self._cache.clear()
        logger.info(f"Prompt cache 已清除: {name or 'ALL'}")


prompt_service = PromptService()
