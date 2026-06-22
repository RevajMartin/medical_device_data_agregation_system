"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from src.config import settings
from src.database import close_db
from src.routes import (
    admin_router,
    aggregations_router,
    devices_router,
    ingestion_router,
    patients_router,
)
from src.schemas.responses import HealthResponse

logger = logging.getLogger("medical_data.api")

openapi_tags = [
    {"name": "ingestion", "description": "Submit physiological measurements from medical devices."},
    {"name": "devices", "description": "Register devices and manage API keys."},
    {"name": "patients", "description": "Patient risk scores and async scoring requests."},
    {
        "name": "aggregations",
        "description": "Time-windowed aggregations (avg/min/max) per patient.",
    },
    {"name": "admin", "description": "Dead-letter queue inspection and job replay."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("%s starting up", settings.APP_NAME)
    yield
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="Medical Device Data Aggregation System — Backend API for real-time physiological data",
    version="1.0.0",
    debug=settings.DEBUG,
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)

# X-Device-Key is a custom header, not a credential (cookie/HTTP-Auth), so
# allow_credentials stays False (default) when allow_origins is a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion_router)
app.include_router(devices_router)
app.include_router(aggregations_router)
app.include_router(patients_router)
app.include_router(admin_router)

# Prometheus metrics at /metrics: request rate, per-status-code counts and latency
# histograms (scraped by Managed Prometheus). Ungrouped status codes so 5xx vs 4xx
# vs 2xx are distinguishable.
Instrumentator(should_group_status_codes=False).instrument(app).expose(app, include_in_schema=False)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log any uncaught error with context and return a generic 500 (no internals leaked)."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint for container orchestration."""
    return HealthResponse(status="healthy", app=settings.APP_NAME)
