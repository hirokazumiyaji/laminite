"""Single source of truth for the installed Laminite version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("laminite")
except PackageNotFoundError:  # pragma: no cover - editable/incomplete installs only
    __version__ = "0.0.0"
