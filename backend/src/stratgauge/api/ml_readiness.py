"""HTTP error contract for the read-only ML readiness endpoint."""

from __future__ import annotations


class MLReadinessApiError(Exception):
    """Expected machine-learning readiness API failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
