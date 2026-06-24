"""
日誌配置
- 自動輪轉
- 保留 30 天
- 分級別輸出
"""

import logging
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# 日誌目錄
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# 日誌格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class CustomFormatter(logging.Formatter):
    """帶顏色的終端輸出格式"""

    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: grey + LOG_FORMAT + reset,
        logging.INFO: blue + LOG_FORMAT + reset,
        logging.WARNING: yellow + LOG_FORMAT + reset,
        logging.ERROR: red + LOG_FORMAT + reset,
        logging.CRITICAL: bold_red + LOG_FORMAT + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, DATE_FORMAT)
        return formatter.format(record)

_logger_initialized = False

def setup_logger(name: str = "rag", level: int = logging.INFO, console: bool = True, file: bool = True) -> logging.Logger:
    """
    設置 logger

    Args:
        name: logger 名稱
        level: 日誌級別
        console: 是否輸出到終端
        file: 是否輸出到檔案

    Returns:
        logging.Logger
    """
    global _logger_initialized

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if _logger_initialized:
        return logger

    _logger_initialized = True

    # root logger 同步設定，讓 app.* 子 logger 的 log 能向上傳遞並被接收
    root = logging.getLogger()
    root.setLevel(level)

    # 1. 終端輸出 (有顏色)
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(CustomFormatter())
        logger.addHandler(console_handler)
        root.addHandler(console_handler)

    # 2. 檔案輸出 - INFO 級別以上
    if file:
        info_handler = TimedRotatingFileHandler(
            filename=LOG_DIR / "app.log",
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(
            logging.Formatter(LOG_FORMAT, DATE_FORMAT)
        )
        info_handler.suffix = "%Y-%m-%d"
        logger.addHandler(info_handler)

        # 3. 錯誤日誌 - ERROR 級別以上
        error_handler = TimedRotatingFileHandler(
            filename=LOG_DIR / "error.log",
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(
            logging.Formatter(LOG_FORMAT, DATE_FORMAT)
        )
        error_handler.suffix = "%Y-%m-%d"
        logger.addHandler(error_handler)

    return logger


def get_logger(name: str = None) -> logging.Logger:
    """
    取得 logger (自動繼承父 logger 設定)

    使用範例:
    >>> from app.logger import get_logger
    >>> logger = get_logger(__name__)
    >>> logger.info("Hello")
    """
    if not _logger_initialized:
        setup_logger("rag")

    if name is None:
        name = "rag"

    return logging.getLogger(name)


# 初始化預設 logger
logger = setup_logger()
