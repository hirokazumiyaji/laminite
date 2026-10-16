"""HTTPException and standard error helpers."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any


class HTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if detail is None:
            try:
                detail = HTTPStatus(status_code).phrase
            except ValueError:
                detail = "Error"
        self.status_code = status_code
        self.detail = detail
        self.headers = dict(headers) if headers is not None else None
        super().__init__(detail)
