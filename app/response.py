"""
統一 API 回應格式
- 成功: {"message", "timestamp", "data"}
- 失敗: {"message", "timestamp", "error_code", "errors"}
"""

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder

_TZ_TAIPEI = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(_TZ_TAIPEI).isoformat(timespec="seconds")


class ErrorCode:
    """錯誤碼定義"""

    # 1xxx: 參數相關錯誤
    INVALID_PARAMETER = "ERROR-1001"
    MISSING_PARAMETER = "ERROR-1002"
    INVALID_FORMAT = "ERROR-1003"

    # 2xxx: 資源相關錯誤
    RESOURCE_NOT_FOUND = "ERROR-2001"
    RESOURCE_ALREADY_EXISTS = "ERROR-2002"
    RESOURCE_CONFLICT = "ERROR-2003"

    # 3xxx: 權限相關錯誤
    UNAUTHORIZED = "ERROR-3001"
    FORBIDDEN = "ERROR-3002"
    PERMISSION_DENIED = "ERROR-3003"
    EXPIRED_TOKEN = "ERROR-3004"
    INVALID_TOKEN = "ERROR-3005"
    INVALID_ACTIVATION_TOKEN = "ERROR-3006"
    EXPIRED_ACTIVATION_TOKEN = "ERROR-3007"
    EMAIL_VERIFY_ERROR = "ERROR-3008"

    # 4xxx: Keycloak 相關錯誤
    KEYCLOAK_CONNECTION_ERROR = "ERROR-4001"
    KEYCLOAK_CLIENT_NOT_FOUND = "ERROR-4002"
    KEYCLOAK_USER_NOT_FOUND = "ERROR-4003"
    KEYCLOAK_PERMISSION_ERROR = "ERROR-4004"
    KEYCLOAK_SETUP_ERROR = "ERROR-4005"
    TOKEN_EXCHANGE_ERROR = "ERROR-4006"
    INVALID_CREDENTIALS = "ERROR-4007"

    # 5xxx: 系統相關錯誤
    INTERNAL_SERVER_ERROR = "ERROR-5001"
    DATABASE_ERROR = "ERROR-5002"
    EXTERNAL_SERVICE_ERROR = "ERROR-5003"

    # 6xxx: 使用者 API（預留）
    USER_DISABLED = "ERROR-6001"
    USER_NOT_FOUND = "ERROR-6002"
    ACCOUNT_LOCKED = "ERROR-6003"


# ------------------------------------------------------------------
# HTTP status → 預設 error_code 對照
# ------------------------------------------------------------------
_STATUS_TO_ERROR_CODE = {
    400: ErrorCode.INVALID_PARAMETER,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    409: ErrorCode.RESOURCE_CONFLICT,
    422: ErrorCode.INVALID_PARAMETER,
    500: ErrorCode.INTERNAL_SERVER_ERROR,
    502: ErrorCode.EXTERNAL_SERVICE_ERROR,
    503: ErrorCode.EXTERNAL_SERVICE_ERROR,
}


def error_code_for_status(status: int) -> str:
    return _STATUS_TO_ERROR_CODE.get(status, ErrorCode.INTERNAL_SERVER_ERROR)


# ------------------------------------------------------------------
# 回應建構 helper
# ------------------------------------------------------------------
def ok(data: Any, message: str = "查詢完成") -> dict:
    """建構成功回應"""
    return {
        "message": message,
        "timestamp": _now(),
        "data": jsonable_encoder(data),
    }


def err(
    message: str,
    error_code: str,
    errors: list[dict] | None = None,
) -> dict:
    """建構錯誤回應"""
    return {
        "message": message,
        "timestamp": _now(),
        "error_code": error_code,
        "errors": errors or [],
    }
