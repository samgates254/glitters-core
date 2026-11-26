"""Domain failures for lease services. Views map these to HTTP later."""

from __future__ import annotations


class TenancyError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
