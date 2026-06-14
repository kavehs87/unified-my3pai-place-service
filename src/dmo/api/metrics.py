from fastapi import APIRouter
from prometheus_client import generate_latest

metrics_router = APIRouter()


@metrics_router.get("/metrics", tags=["System"])
async def get_metrics():
    return generate_latest(), 200, {"Content-Type": "text/plain; charset=utf-8"}
