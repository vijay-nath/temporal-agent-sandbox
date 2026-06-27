"""FastAPI application factory (the control plane / only internet-facing component)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import APIError
from app.api.routes_runs import router
from app.config import get_settings
from app.observability.logging import configure_logging
from app.observability.tracing import setup_tracing
from app.temporal.client import create_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(f"{s.otel_service_name}-api", s.log_level)
    setup_tracing(f"{s.otel_service_name}-api", s.otel_exporter_otlp_endpoint)
    app.state.temporal = await create_client()
    yield


def _envelope(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers=headers,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="temporal-agent-sandbox", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError):
        return _envelope(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return _envelope(request, 422, "VALIDATION_ERROR", str(exc.errors()))

    @app.get("/healthz")
    async def healthz() -> dict:
        # liveness only; does not probe dependencies
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request):
        # readiness: probe Temporal so a load balancer doesn't route traffic that would 503
        client = getattr(request.app.state, "temporal", None)
        if client is None:
            return JSONResponse(
                status_code=503, content={"status": "not-ready", "reason": "temporal not connected"}
            )
        try:
            await asyncio.wait_for(client.service_client.check_health(), timeout=2)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=503, content={"status": "not-ready", "reason": str(exc)}
            )
        return {"status": "ready"}

    return app


app = create_app()
