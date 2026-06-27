"""Temporal client factory shared by the API and worker. The TracingInterceptor links the
API, workflow and activity spans into one trace; workers created from this client inherit it."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

from app.config import get_settings


async def create_client() -> Client:
    s = get_settings()
    return await Client.connect(
        s.temporal_host,
        namespace=s.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
