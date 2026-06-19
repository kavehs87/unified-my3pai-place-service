import asyncio
import os
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from dmo.admin.router import router as admin_router
from dmo.api.health import health_router
from dmo.api.metrics import metrics_router
from dmo.api.router import router
from dmo.config import settings
from dmo.exceptions import register_exception_handlers
from dmo.logging import setup_logging
from dmo.metrics import REQUEST_DURATION, REQUEST_TOTAL
from dmo.middleware.rate_limit import RateLimiterMiddleware
from dmo.middleware.request_id import RequestIDMiddleware
from dmo.services.cache import _cache

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    if not settings.api_key:
        raise ValueError("API_KEY environment variable is required")
    if settings.admin_username == "admin" and settings.admin_password == "admin":
        logger.warning(
            "admin_default_credentials",
            message="Admin UI using default credentials - change ADMIN_USERNAME/ADMIN_PASSWORD",
        )
    from dmo.db import get_engine

    get_engine()
    yield
    if _cache is not None:
        await _cache.close()
    from dmo.db import _engine

    if _engine is not None:
        await _engine.dispose()


app = FastAPI(
    title="DMO On-Premise API",
    description="Provider-agnostic data store and query API for tourism/POI data",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "Read",
            "description": "Public read endpoints — search, nearby, map, detail, classifications",
        },
        {
            "name": "Write",
            "description": "Authenticated write endpoints — create, update, delete (requires X-API-Key)",
        },
        {"name": "System", "description": "Health and metrics endpoints"},
    ],
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimiterMiddleware)

if settings.allowed_origins == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    origins = [o.strip() for o in settings.allowed_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


logger = structlog.get_logger()


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    endpoint = request.url.path
    if endpoint in ("/health", "/metrics", "/docs", "/redoc", "/openapi.json"):
        return response
    status = str(response.status_code)
    method = request.method
    elapsed_ms = duration * 1000
    REQUEST_DURATION.labels(method=method, endpoint=endpoint, status=status).observe(duration)
    REQUEST_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
    if elapsed_ms > settings.slow_request_threshold_ms:
        logger.warning(
            "slow_request",
            path=endpoint,
            method=method,
            elapsed_ms=round(elapsed_ms, 2),
            status=status,
            request_id=getattr(request.state, "request_id", ""),
        )
    return response


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    if request.url.path in ("/health", "/metrics", "/docs", "/redoc", "/openapi.json"):
        return await call_next(request)
    try:
        return await asyncio.wait_for(call_next(request), timeout=settings.request_timeout_seconds)
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "error": "Gateway Timeout",
                "message": "Request timed out",
                "code": 504,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )


app.include_router(admin_router)

_static_dir = os.path.join(os.path.dirname(__file__), "admin", "static")
app.mount("/admin/static", StaticFiles(directory=_static_dir), name="admin_static")

app.include_router(router)
app.include_router(health_router)
app.include_router(metrics_router)
register_exception_handlers(app)
