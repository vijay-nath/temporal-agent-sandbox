"""Structured JSON logging. A filter injects run_id/tenant_id and the active trace/span ids
onto every record so logs and traces join on the same keys."""

from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging

from opentelemetry import trace

# Set at request/activity entry; read by the filter on every record.
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id", default=None
)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class CorrelationFilter(logging.Filter):
    """Attach run_id/tenant_id (from contextvars) and trace_id/span_id (from OTel)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.tenant_id = tenant_id_var.get()
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = None
            record.span_id = None
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "tenant_id": getattr(record, "tenant_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
        }
        # Promote structured extras passed via logger(..., extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    """Idempotently install a JSON stdout handler with the correlation filter."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    handler.addFilter(CorrelationFilter())
    root.addHandler(handler)
    # uvicorn access logs are dropped in favor of our structured records.
    logging.getLogger("uvicorn.access").handlers = []
