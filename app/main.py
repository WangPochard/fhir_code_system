import secrets

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.response import err, error_code_for_status, ErrorCode
from app.snomed.routers import router as snomed_router
from app.icd10.routers import router as icd10_router
from app.rxnorm.routers import router as rxnorm_router
from app.loinc.routers import router as loinc_router
from app.errorbot.router import router as errorbot_router
from app.agent.routers import router as agent_router

settings = get_settings()
security = HTTPBasic()

app = FastAPI(
    title="醫療術語標準化 API",
    description="SNOMED CT 概念識別 / ICD-10 PCS 編碼搜尋 / RxNorm 藥品查詢 / LOINC 檢驗碼對應",
    version="1.0.1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# _api_prefix = f"/api/v{app.version.split('.')[0]}"
_api_prefix = "/api/ai"
app.include_router(snomed_router, prefix=_api_prefix)
app.include_router(icd10_router, prefix=_api_prefix)
app.include_router(rxnorm_router, prefix=_api_prefix)
app.include_router(loinc_router, prefix=_api_prefix)
app.include_router(errorbot_router, prefix=f"{_api_prefix}/errorbot", tags=["ErrorBot"])
app.include_router(agent_router, prefix=_api_prefix)


# ------------------------------------------------------------------
# Swagger 帳密驗證
# ------------------------------------------------------------------
def verify_swagger_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.swagger_username)
    correct_password = secrets.compare_digest(credentials.password, settings.swagger_password)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="帳號或密碼錯誤",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/docs", include_in_schema=False)
async def get_documentation(username: str = Depends(verify_swagger_credentials)):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_ui_parameters={
            "persistAuthorization": True,
            "syntaxHighlight.theme": "obsidian",
            "defaultModelsExpandDepth": -1,
            "docExpansion": "list",
            "filter": True,
        },
    )


@app.get("/redoc", include_in_schema=False)
async def get_redoc_documentation(username: str = Depends(verify_swagger_credentials)):
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - ReDoc",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(username: str = Depends(verify_swagger_credentials)):
    return JSONResponse(
        get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
    )


# ------------------------------------------------------------------
# 統一 Exception Handler
# ------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "請求失敗"
    code = error_code_for_status(exc.status_code)
    headers = dict(exc.headers) if exc.headers else {}
    return JSONResponse(
        status_code=exc.status_code,
        content=err(message, code),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        loc = error.get("loc", [])
        field = ".".join(str(x) for x in loc if x not in ("body", "query", "path"))
        errors.append({"field": field or "unknown", "message": error.get("msg", "")})
    return JSONResponse(
        status_code=422,
        content=err("參數驗證失敗", ErrorCode.INVALID_PARAMETER, errors),
    )


# ------------------------------------------------------------------
# Health check（不需驗證）
# ------------------------------------------------------------------
@app.get("/api/ai/health", tags=["System"])
async def health():
    return {
        "status": "ok",
        "version": app.version,
        "app": app.title,
    }
