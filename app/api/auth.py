"""Static bearer-token auth dependency. Every route depends on it; production swaps it for
OIDC/JWT or mTLS without touching the handlers."""

from __future__ import annotations

import secrets

from fastapi import Header

from app.api.errors import APIError
from app.config import get_settings


async def verify_token(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(401, "UNAUTHORIZED", "missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    if not secrets.compare_digest(token, settings.api_bearer_token):
        raise APIError(401, "UNAUTHORIZED", "invalid bearer token")
