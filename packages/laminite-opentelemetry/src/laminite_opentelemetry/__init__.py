"""OpenTelemetry instrumentation for Laminite."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from laminite_opentelemetry.instrumentor import instrument_app

__all__ = ["instrument_app"]

try:
    __version__ = version("laminite-opentelemetry")
except PackageNotFoundError:  # pragma: no cover - editable/incomplete installs only
    __version__ = "0.0.0"
