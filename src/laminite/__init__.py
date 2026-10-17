"""Laminite — a minimal ASGI web framework."""

from importlib.metadata import PackageNotFoundError, version

from laminite.app import Laminite
from laminite.exceptions import HTTPException
from laminite.params import Body, Cookie, Header, Query
from laminite.request import Request
from laminite.response import JSONResponse, Response, StreamingResponse
from laminite.routing import APIRouter

try:
    __version__ = version("laminite")
except PackageNotFoundError:  # pragma: no cover - editable/incomplete installs only
    __version__ = "0.0.0"

__all__ = [
    "APIRouter",
    "Body",
    "Cookie",
    "HTTPException",
    "Header",
    "JSONResponse",
    "Laminite",
    "Query",
    "Request",
    "Response",
    "StreamingResponse",
]
