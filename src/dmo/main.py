import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    yield
    if _cache is not None:
        await _cache.close()
    from dmo.db import engine
    await engine.dispose()


app = FastAPI(
    title="DMO On-Premise API",
    description="Provider-agnostic data store and query API for tourism/POI data",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
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
    REQUEST_DURATION.labels(method=method, endpoint=endpoint, status=status).observe(duration)
    REQUEST_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
    return response


app.include_router(router)
app.include_router(health_router)
app.include_router(metrics_router)
register_exception_handlers(app)


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    try:
        return await asyncio.wait_for(
            call_next(request), timeout=settings.request_timeout_seconds
        )
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
