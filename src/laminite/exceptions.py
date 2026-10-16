"""HTTPException and standard error helpers."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from laminite.response import validate_header_name, validate_header_value


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
        if headers is not None:
            for key, value in headers.items():
                validate_header_name(key)
                validate_header_value(value)
            self.headers = dict(headers)
        else:
            self.headers = None
        super().__init__(detail)
