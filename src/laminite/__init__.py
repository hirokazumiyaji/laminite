"""Laminite — a minimal ASGI web framework."""

from importlib.metadata import PackageNotFoundError, version

from laminite.app import Laminite
from laminite.exceptions import HTTPException
from laminite.params import Body, Header, Query
from laminite.request import Request
from laminite.response import JSONResponse, Response, StreamingResponse

try:
    __version__ = version("laminite")
except PackageNotFoundError:  # pragma: no cover - editable/incomplete installs only
    __version__ = "0.0.0"

__all__ = [
    "Body",
    "HTTPException",
    "Header",
    "JSONResponse",
    "Laminite",
    "Query",
    "Request",
    "Response",
    "StreamingResponse",
]
