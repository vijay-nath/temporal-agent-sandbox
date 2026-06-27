"""Single API error type → rendered as one consistent error envelope (see main.py)."""

from __future__ import annotations


class APIError(Exception):
    """Raised by routes/dependencies; converted to the JSON error envelope centrally."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
