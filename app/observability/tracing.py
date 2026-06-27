"""OpenTelemetry setup and span helpers.

One trace per run, rooted at the API request, with a span per activity. Spans are emitted
from the API and activities only — never from replayed workflow code; the SDK's
TracingInterceptor carries context across the Temporal boundary.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

# Langfuse maps this span attribute to an observation type.
LANGFUSE_OBSERVATION_TYPE = "langfuse.observation.type"

_initialized = False


def setup_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Idempotently configure the global tracer provider with an OTLP/gRPC exporter."""
    global _initialized
    if _initialized:
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer(name: str = "agent-sandbox") -> trace.Tracer:
    return trace.get_tracer(name)


# --- span helpers -----------------------------------------------------------


def set_observation_type(span: Span, obs_type: str) -> None:
    span.set_attribute(LANGFUSE_OBSERVATION_TYPE, obs_type)


def record_error(span: Span, exc: BaseException, *, retryable: bool, terminal: bool) -> None:
    span.record_exception(exc)
    span.set_attribute("error.type", type(exc).__name__)
    span.set_attribute("retryable", retryable)
    span.set_attribute("failure.terminal", terminal)
    span.set_status(Status(StatusCode.ERROR, str(exc)))


def set_attrs(span: Span, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)
